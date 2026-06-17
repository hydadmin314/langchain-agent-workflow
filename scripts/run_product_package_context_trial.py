from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = Path("data") / "product_doc_agent" / "package_trials"
SUPPORTED_EXTENSIONS = {".docx", ".xlsx", ".xls", ".pdf", ".txt"}
IGNORED_NAMES = {"Thumbs.db", ".DS_Store"}


def main() -> int:
    ensure_project_root_on_path()
    args = build_arg_parser().parse_args()
    return asyncio.run(run_async(args))


def ensure_project_root_on_path() -> None:
    project_root = str(PROJECT_ROOT)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="临时验证产品资料包的模块上下文分块效果。")
    parser.add_argument("--package-dir", required=True, help="产品资料包目录，例如 上网/电信/精品专线。")
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT), help="调试输出目录。")
    parser.add_argument("--max-context-chars", type=int, default=60000, help="传给模块上下文构建器的总预算。")
    parser.add_argument("--max-concurrency", type=int, default=1, help="调用大模型抽取时的模块并发数。")
    parser.add_argument(
        "--modules",
        default="",
        help="只抽取指定模块，逗号分隔；为空时抽取全部模块。",
    )
    parser.add_argument("--extract", action="store_true", help="生成上下文后调用现有大模型抽取器，输出试验 JSON。")
    parser.add_argument("--skip-existing-output", action="store_true", help="如果调试文件已存在则跳过重写。")
    return parser


async def run_async(args: argparse.Namespace) -> int:
    from agent.product_doc_agent.markdown_context_splitter import (
        build_module_contexts_from_markdown,
        render_module_contexts_debug,
    )
    from agent.product_doc_agent.markdown_renderer import MarkdownRenderer

    package_dir = Path(args.package_dir).resolve()
    data_root = Path(args.data_root).resolve()
    if not package_dir.exists() or not package_dir.is_dir():
        raise FileNotFoundError(f"产品资料包目录不存在: {package_dir}")

    package_id = build_package_id(package_dir)
    output_dir = data_root / package_id
    output_dir.mkdir(parents=True, exist_ok=True)

    files = collect_package_files(package_dir)
    renderer = MarkdownRenderer()
    combined_markdown, render_warnings = render_package_markdown(files, renderer)

    combined_path = output_dir / f"{package_id}_combined_markdown.md"
    contexts_path = output_dir / f"{package_id}_module_contexts.md"
    manifest_path = output_dir / f"{package_id}_manifest.json"

    if not args.skip_existing_output or not combined_path.exists():
        combined_path.write_text(combined_markdown, encoding="utf-8")

    module_contexts = build_module_contexts_from_markdown(
        combined_markdown,
        max_chars=args.max_context_chars,
    )
    if not args.skip_existing_output or not contexts_path.exists():
        contexts_path.write_text(render_module_contexts_debug(module_contexts), encoding="utf-8")

    manifest = build_manifest(package_id, package_dir, files, render_warnings, combined_path, contexts_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    result: dict[str, Any] = {
        "package_id": package_id,
        "package_dir": str(package_dir),
        "file_count": len(files),
        "combined_markdown_path": str(combined_path),
        "module_contexts_path": str(contexts_path),
        "manifest_path": str(manifest_path),
        "warnings": render_warnings,
    }

    if args.extract:
        extraction_path = await extract_package_trial_json(
            module_contexts,
            package_id=package_id,
            package_dir=package_dir,
            output_dir=output_dir,
            max_context_chars=args.max_context_chars,
            max_concurrency=args.max_concurrency,
            modules=parse_modules(args.modules),
        )
        result["trial_extraction_path"] = str(extraction_path)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def collect_package_files(package_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file() or should_ignore_file(path):
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        files.append(path)
    return files


def should_ignore_file(path: Path) -> bool:
    name = path.name
    if name in IGNORED_NAMES or name.startswith("~$"):
        return True
    return "__pycache__" in path.parts


def render_package_markdown(files: list[Path], renderer: Any) -> tuple[str, list[str]]:
    sections: list[str] = []
    warnings: list[str] = []
    for file_path in files:
        role = classify_doc_role(file_path)
        marker = {
            "source_file": file_path.name,
            "source_path": str(file_path),
            "source_file_type": file_path.suffix.lower().lstrip("."),
            "doc_role": role,
        }
        sections.append(f"<!-- product_doc_source: {json.dumps(marker, ensure_ascii=False)} -->")
        try:
            markdown = renderer.render_file(file_path)
        except Exception as exc:
            warnings.append(f"{file_path}: {exc.__class__.__name__}: {exc}")
            markdown = f"<!-- 文件转换失败：{exc.__class__.__name__}: {exc} -->"
        sections.append(markdown.strip())
    return "\n\n".join(section for section in sections if section.strip()), warnings


def classify_doc_role(path: Path) -> str:
    text = normalize_match_text("/".join(path.parts[-4:]))
    name = normalize_match_text(path.name)

    if contains_any(name, ("产品介绍", "产品说明")):
        return "product_intro"
    if contains_any(name, ("资费", "价格", "报价")):
        return "pricing_table"
    if contains_any(name, ("关键字", "关键词")):
        return "product_keywords"
    if contains_any(name, ("申请表", "申请登记表", "需求表", "受理单", "客户信息表")):
        return "application_form" if "客户基本信息" not in name else "customer_info_form"
    if contains_any(name, ("申请手续", "手续提示", "办理提示", "操作流程")):
        return "procedure_hint"
    if contains_any(name, ("服务合同", "业务合同", "合同")):
        return "contract_rule"
    if contains_any(name, ("授权委托", "委托书")):
        return "authorization_template"
    if contains_any(name, ("担保书", "担保")):
        return "guarantee_template"
    if contains_any(name, ("风险告知", "免责函", "风险", "免责")):
        return "risk_notice"
    if contains_any(name, ("业务变更", "升降速", "移机", "变更")):
        return "change_rule"
    if contains_any(name, ("拆机", "退款", "注销")):
        return "termination_rule"
    if contains_any(text, ("润网填写", "商机", "结酬", "审批参考", "操作流程")):
        return "internal_process"
    return "unknown"


def contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(normalize_match_text(term) in text for term in terms)


def normalize_match_text(value: str) -> str:
    return "".join(str(value or "").lower().split())


def build_package_id(package_dir: Path) -> str:
    key = str(package_dir.resolve()).encode("utf-8", errors="ignore")
    return "pkg_" + hashlib.sha1(key).hexdigest()[:12]


def build_manifest(
    package_id: str,
    package_dir: Path,
    files: list[Path],
    warnings: list[str],
    combined_path: Path,
    contexts_path: Path,
) -> dict[str, Any]:
    return {
        "package_id": package_id,
        "package_dir": str(package_dir),
        "files": [
            {
                "source_path": str(path),
                "filename": path.name,
                "source_file_type": path.suffix.lower().lstrip("."),
                "doc_role": classify_doc_role(path),
            }
            for path in files
        ],
        "warnings": warnings,
        "combined_markdown_path": str(combined_path),
        "module_contexts_path": str(contexts_path),
    }


async def extract_package_trial_json(
    module_contexts: dict[str, str],
    *,
    package_id: str,
    package_dir: Path,
    output_dir: Path,
    max_context_chars: int,
    max_concurrency: int,
    modules: list[str] | None,
) -> Path:
    from agent.product_doc_agent.llm_extractor import EXTRACTION_MODULES, ProductDocumentLLMExtractor
    from agent.product_doc_agent.merger import ProductDocumentMerger
    from agent.product_doc_agent.schema_normalizer import ProductDocumentNormalizer
    from agent.product_doc_agent.validator import ProductDocumentValidator

    module_names = modules or EXTRACTION_MODULES
    extractor = ProductDocumentLLMExtractor(
        max_context_chars=max_context_chars,
        max_concurrency=max_concurrency,
    )
    module_outputs = await extractor.extract_modules_async(
        module_contexts,
        modules=module_names,
        continue_on_error=True,
        max_concurrency=max_concurrency,
    )

    product_document = ProductDocumentMerger().merge(
        module_outputs,
        document_metadata={
            "document_id": package_id,
            "source_path": str(package_dir),
            "filename": package_dir.name,
            "source_file_type": "product_package",
            "source_file_hash": "",
            "modified_at": "",
        },
    )
    product_document["extraction_meta"]["method"] = "package_context_trial"

    normalization_result = ProductDocumentNormalizer().normalize_product_document(product_document)
    product_document = normalization_result.product_document
    issues = [
        issue for issue in normalization_result.issues if issue.get("expose_to_review") is True
    ]
    module_errors = module_outputs.get("__module_errors__", [])
    for module_error in module_errors if isinstance(module_errors, list) else []:
        if isinstance(module_error, dict):
            issues.append(
                {
                    "severity": "error",
                    "path": f"llm_module.{module_error.get('module', '')}",
                    "message": str(module_error.get("error", "模块抽取失败")),
                }
            )
    validator = ProductDocumentValidator()
    issues.extend(validator.validate(product_document))
    product_document = validator.attach_issues(product_document, issues)

    output_path = output_dir / f"{package_id}_trial_extraction_{module_output_suffix(modules)}.json"
    output_path.write_text(json.dumps(product_document, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def parse_modules(value: str) -> list[str] | None:
    modules = [item.strip() for item in str(value or "").split(",") if item.strip()]
    return modules or None


def module_output_suffix(modules: list[str] | None) -> str:
    if not modules:
        return "all"
    raw = "_".join(modules)
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in raw)


if __name__ == "__main__":
    raise SystemExit(main())
