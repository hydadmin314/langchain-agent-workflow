from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from agent.product_doc_agent.document_loader import DocumentLoader, LoadedDocument
from agent.product_doc_agent.llm_extractor import EXTRACTION_MODULES, ProductDocumentLLMExtractor, run_async_from_sync
from agent.product_doc_agent.markdown_context_splitter import (
    build_module_contexts_from_markdown,
    render_module_contexts_debug,
)
from agent.product_doc_agent.markdown_renderer import MarkdownRenderer
from agent.product_doc_agent.merger import ProductDocumentMerger
from agent.product_doc_agent.schema_normalizer import ProductDocumentNormalizer
from agent.product_doc_agent.validator import ProductDocumentValidator
from utils.logger import logger


DEFAULT_DATA_ROOT = Path("data") / "product_doc_agent"


@dataclass(frozen=True)
class WorkflowResult:
    document_id: str
    review_path: Path
    validation_issue_count: int
    block_count: int


class ProductDocAgentWorkflow:
    """产品文档结构化抽取主流程。"""

    def __init__(
        self,
        *,
        data_root: str | Path = DEFAULT_DATA_ROOT,
        llm: BaseChatModel | None = None,
        max_context_chars: int = 60000,
        max_concurrency: int | None = None,
        enable_self_check: bool = True,
        enable_debug_markdown: bool = True,
    ) -> None:
        self.data_root = Path(data_root)
        self.review_dir = self.data_root / "review"
        self.published_dir = self.data_root / "published"
        self.debug_dir = self.data_root / "debug"
        self.loader = DocumentLoader()
        self.markdown_renderer = MarkdownRenderer()
        self.extractor = ProductDocumentLLMExtractor(
            llm=llm,
            max_context_chars=max_context_chars,
            max_concurrency=max_concurrency,
        )
        self.merger = ProductDocumentMerger()
        self.normalizer = ProductDocumentNormalizer()
        self.validator = ProductDocumentValidator()
        self.enable_self_check = enable_self_check
        self.enable_debug_markdown = enable_debug_markdown

    def run(self, file_path: str | Path) -> WorkflowResult:
        """同步入口，方便测试脚本直接调用。"""
        return run_async_from_sync(self.run_async(file_path))

    async def run_async(self, file_path: str | Path) -> WorkflowResult:
        """异步执行完整抽取流程。"""
        loaded_document = await self.loader.load_async(file_path)
        product_document = await self.extract_loaded_document_async(loaded_document)
        review_path = self.write_review_json(product_document, loaded_document.document_id)
        validation_issue_count = product_document.get("extraction_meta", {}).get("validation_issue_count", 0)
        logger.info(
            f"产品文档抽取完成，document_id={loaded_document.document_id}, review_path={review_path}"
        )
        return WorkflowResult(
            document_id=loaded_document.document_id,
            review_path=review_path,
            validation_issue_count=validation_issue_count,
            block_count=len(loaded_document.blocks),
        )

    def extract_loaded_document(self, loaded_document: LoadedDocument) -> dict[str, Any]:
        """同步抽取已加载文档，主要用于调试和单元测试。"""
        return run_async_from_sync(self.extract_loaded_document_async(loaded_document))

    async def extract_loaded_document_async(self, loaded_document: LoadedDocument) -> dict[str, Any]:
        """文档解析 -> Markdown -> 模块切块 -> LLM 抽取 -> 合并 -> 校验。"""
        # 1. 先把原始文档转换成给大模型看的 Markdown。
        llm_markdown = self.markdown_renderer.render_document(
            loaded_document,
            max_chars=self.extractor.max_context_chars,
        )
        # 2. 再按 schema 模块切出各自上下文，避免 9 个模块都吃完整文档。
        module_contexts = build_module_contexts_from_markdown(
            llm_markdown,
            max_chars=self.extractor.max_context_chars,
        )

        # 调试开关：测试切块质量时保留 True；正式批量抽取时传 enable_debug_markdown=False 即可关闭两个 md 文件输出。
        if self.enable_debug_markdown:
            self.write_debug_markdown(loaded_document, llm_markdown, module_contexts)

        # 3. 并发抽取 9 个模块；模块失败时保留错误并继续生成可审核 JSON。
        module_outputs = await self.extractor.extract_modules_async(
            module_contexts,
            modules=EXTRACTION_MODULES,
            continue_on_error=True,
        )
        module_errors = module_outputs.get("__module_errors__", [])
        # 4. 初次合并并归一化，得到 self_check 可检查的完整 JSON。
        product_document = self.merge_and_normalize(module_outputs, loaded_document)

        if module_errors:
            product_document["extraction_meta"]["schema_warnings"].append("部分模块抽取失败，已跳过 LLM 自检。")

        # 5. self_check 只负责语义质量检查；如果它要求返工，再回到对应模块 Markdown 重抽。
        self_check: dict[str, Any] = {}
        rework_requests: dict[str, str] = {}
        if self.enable_self_check and not module_errors:
            self_check = await self.extractor.self_check_async(product_document)
            rework_requests.update(collect_rework_requests(self_check))

        if rework_requests:
            logger.info(f"module rework requested: {sorted(rework_requests)}")
            # 6. 返工只覆盖有问题的模块，其它模块沿用初抽结果。
            rework_outputs = await self.extractor.rework_modules_async(
                module_contexts,
                rework_requests,
                module_outputs,
                continue_on_error=True,
            )
            rework_errors = rework_outputs.pop("__module_errors__", [])
            module_outputs.update(rework_outputs)
            module_errors.extend(rework_errors)
            product_document = self.merge_and_normalize(module_outputs, loaded_document)
            if self_check:
                product_document = self.merger.apply_self_check(product_document, self_check)
            product_document["extraction_meta"].setdefault("llm_self_check", {})[
                "rework_applied_modules"
            ] = sorted(rework_outputs)
            product_document["extraction_meta"].setdefault("llm_self_check", {})[
                "rework_requests"
            ] = [
                {"module": module_name, "reason": reason}
                for module_name, reason in sorted(rework_requests.items())
            ]
        elif self_check:
            product_document = self.merger.apply_self_check(product_document, self_check)

        # 7. 最终只做一次程序校验，作为写入 review JSON 前的确定性底线。
        program_issues = self.validator.validate(product_document)
        program_issues.extend(module_error_to_issue(module_error) for module_error in module_errors)
        program_issues.extend(loader_warning_to_issue(warning) for warning in loaded_document.warnings)
        return self.validator.attach_issues(product_document, program_issues)

    def merge_and_normalize(
        self,
        module_outputs: dict[str, Any],
        loaded_document: LoadedDocument,
    ) -> dict[str, Any]:
        """合并模块结果并执行归一化；初抽和返工后都走同一条路径。"""

        product_document = self.merger.merge(
            module_outputs,
            document_metadata=loaded_document_metadata(loaded_document),
        )
        normalization_result = self.normalizer.normalize_product_document(product_document)
        product_document = normalization_result.product_document
        exposed_issues = [
            issue for issue in normalization_result.issues if issue.get("expose_to_review") is True
        ]
        if exposed_issues:
            schema_warnings = product_document["extraction_meta"].setdefault("schema_warnings", [])
            schema_warnings.extend(issue.get("message", str(issue)) for issue in exposed_issues)
        return product_document

    def write_review_json(self, product_document: dict[str, Any], document_id: str) -> Path:
        """写入待审核 JSON。"""
        self.review_dir.mkdir(parents=True, exist_ok=True)
        path = self.review_dir / f"{document_id}.json"
        path.write_text(json.dumps(product_document, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_debug_markdown(
        self,
        loaded_document: LoadedDocument,
        llm_markdown: str,
        module_contexts: dict[str, str],
    ) -> None:
        """保存 Markdown 调试文件，方便检查切块质量。"""
        self.debug_dir.mkdir(parents=True, exist_ok=True)

        full_path = self.debug_dir / f"{loaded_document.document_id}_llm_markdown.md"
        full_path.write_text(llm_markdown, encoding="utf-8")

        module_path = self.debug_dir / f"{loaded_document.document_id}_module_contexts.md"
        module_path.write_text(render_module_contexts_debug(module_contexts), encoding="utf-8")
        logger.info(f"LLM markdown debug files written: {full_path}, {module_path}")

    def publish(self, document_id: str) -> Path:
        """把审核后的 JSON 发布到 published 目录。"""
        review_path = self.review_dir / f"{document_id}.json"
        if not review_path.exists():
            raise FileNotFoundError(f"Review JSON not found: {review_path}")
        self.published_dir.mkdir(parents=True, exist_ok=True)
        published_path = self.published_dir / review_path.name
        shutil.copy2(review_path, published_path)
        return published_path


def loaded_document_metadata(loaded_document: LoadedDocument) -> dict[str, Any]:
    """生成合并阶段需要的文档元数据。"""
    return {
        "document_id": loaded_document.document_id,
        "source_path": loaded_document.source_path,
        "filename": loaded_document.filename,
        "source_file_type": loaded_document.source_file_type,
        "source_file_hash": loaded_document.source_file_hash,
        "modified_at": loaded_document.modified_at,
    }


def module_error_to_issue(module_error: dict[str, Any]) -> dict[str, str]:
    """把模块失败信息转成统一校验问题。"""

    return {
        "severity": "error",
        "path": f"llm_module.{module_error.get('module', '')}",
        "message": str(module_error.get("error", "模块抽取失败")),
    }


def loader_warning_to_issue(warning: str) -> dict[str, str]:
    """把文档加载告警转成统一校验问题。"""

    return {"severity": "warning", "path": "document_loader", "message": warning}


def collect_rework_requests(self_check: dict[str, Any]) -> dict[str, str]:
    """从 self_check 结果中提取需要返工的模块及原因。"""

    llm_self_check = self_check.get("llm_self_check", {}) if isinstance(self_check, dict) else {}
    if not isinstance(llm_self_check, dict):
        return {}

    requests: dict[str, str] = {}
    for item in llm_self_check.get("rework_modules", []):
        module_name = ""
        reason = ""
        if isinstance(item, str):
            module_name = item
            reason = "大模型自检要求返工。"
        elif isinstance(item, dict):
            module_name = str(item.get("module", "")).strip()
            reason = str(item.get("reason") or item.get("message") or item.get("description") or "").strip()
        if module_name in EXTRACTION_MODULES:
            requests[module_name] = reason or "大模型自检要求返工。"

    # 兼容模型只写 validation_issues、漏写 rework_modules 的情况。
    # path 的第一段和模块名一致时，也可以安全地触发对应模块返工。
    for issue in self_check.get("validation_issues", []) if isinstance(self_check, dict) else []:
        if not isinstance(issue, dict):
            continue
        severity = str(issue.get("severity", "")).lower()
        message = str(issue.get("message", ""))
        if severity != "error" and not any(marker in message for marker in ("返工", "重抽", "重新抽取", "rework")):
            continue
        module_name = module_from_issue_path(str(issue.get("path", "")))
        if module_name and module_name not in requests:
            requests[module_name] = message or "大模型自检发现校验问题。"
    return requests


def module_from_issue_path(path: str) -> str:
    """从 self_check issue path 推断所属模块。"""

    normalized = path.strip().lstrip("$.")
    top_level = normalized.split(".", 1)[0].split("[", 1)[0]
    return top_level if top_level in EXTRACTION_MODULES else ""
