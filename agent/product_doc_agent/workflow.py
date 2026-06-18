from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from config.settings import PRODUCT_DOC_RAW_ROOT
from agent.product_doc_agent.document_classifier import UNKNOWN, classify_document
from agent.product_doc_agent.document_loader import DocumentLoader, LoadedDocument
from agent.product_doc_agent.llm_extractor import EXTRACTION_MODULES, ProductDocumentLLMExtractor, run_async_from_sync
from agent.product_doc_agent.markdown_context_splitter import (
    build_module_contexts_from_markdown,
    render_product_module_contexts_debug,
    render_module_contexts_debug,
)
from agent.product_doc_agent.markdown_renderer import (
    DEFAULT_OCR_DIRECT_FILE_TYPES,
    DEFAULT_OCR_FALLBACK_FILE_TYPES,
    MarkdownRenderer,
    normalize_file_types,
)
from agent.product_doc_agent.merger import ProductDocumentMerger
from agent.product_doc_agent.ocr_flow.debug_writer import OCRFlowDebugWriter
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


@dataclass(frozen=True)
class ProductFolderWorkflowResult:
    product_id: str
    review_path: Path
    source_folder: Path
    document_count: int
    module_count: int
    debug_context_path: Path | None = None


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
        enable_ocr_flow: bool = True,
        ocr_page_dpi: int = 160,
        ocr_markdown_min_chars: int = 50,
        ocr_direct_file_types: Collection[str] | None = None,
        ocr_fallback_file_types: Collection[str] | None = None,
        enable_post_processing: bool = False,
        raw_root: str | Path | None = None,
        enable_doc_conversion: bool = True,
    ) -> None:
        self.data_root = Path(data_root)
        self.raw_root = Path(raw_root or PRODUCT_DOC_RAW_ROOT).resolve()
        self.review_dir = self.data_root / "review"
        self.published_dir = self.data_root / "published"
        self.debug_dir = self.data_root / "debug"
        self.loader = DocumentLoader()
        self.markdown_renderer = MarkdownRenderer(
            enable_ocr_flow=enable_ocr_flow,
            ocr_page_dpi=ocr_page_dpi,
            ocr_markdown_min_chars=ocr_markdown_min_chars,
            ocr_direct_file_types=ocr_direct_file_types,
            ocr_fallback_file_types=ocr_fallback_file_types,
        )
        self.ocr_debug_writer = OCRFlowDebugWriter(debug_root=self.debug_dir / "ocr")
        self.extractor = ProductDocumentLLMExtractor(
            llm=llm,
            max_context_chars=max_context_chars,
            max_concurrency=max_concurrency,
        )
        self.merger = ProductDocumentMerger()
        self.normalizer = ProductDocumentNormalizer()
        self.validator = ProductDocumentValidator()
        self.enable_self_check = enable_self_check
        self.enable_post_processing = enable_post_processing
        self.enable_debug_markdown = enable_debug_markdown
        self.enable_ocr_flow = enable_ocr_flow
        self.enable_doc_conversion = enable_doc_conversion
        self.ocr_markdown_min_chars = max(0, ocr_markdown_min_chars)
        self.ocr_direct_file_types = normalize_file_types(
            ocr_direct_file_types or DEFAULT_OCR_DIRECT_FILE_TYPES
        )
        self.ocr_fallback_file_types = normalize_file_types(
            ocr_fallback_file_types or DEFAULT_OCR_FALLBACK_FILE_TYPES
        )

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
        # 1. 先把原始文档完整转换成 Markdown。
        # 不在这里截断整篇文档，避免靠后的协议、材料、补充规则在切模块前被丢弃。
        markdown_result = self.markdown_renderer.render_document_result(loaded_document)
        llm_markdown = markdown_result.markdown
        document_classification = classify_document(loaded_document.source_path)
        target_modules = list(document_classification.target_modules)
        if document_classification.role == UNKNOWN or not target_modules:
            raise ValueError(f"暂不支持的文档类型，无法构建抽取上下文：{loaded_document.filename}")

        # 2. 再按 schema 模块切出各自上下文，并在 splitter 内按模块预算截断。
        module_contexts = build_module_contexts_from_markdown(
            llm_markdown,
            max_chars=self.extractor.max_context_chars,
            modules=target_modules,
            document_role=document_classification.role,
        )

        # 调试开关：测试切块质量时保留 True；正式批量抽取时传 enable_debug_markdown=False 即可关闭两个 md 文件输出。
        if self.enable_debug_markdown:
            self.write_debug_markdown(loaded_document, llm_markdown, module_contexts)
            self.write_markdown_source_debug(loaded_document, markdown_result)

        # 3. 并发抽取 9 个模块；模块失败时保留错误并继续生成可审核 JSON。
        module_outputs = await self.extractor.extract_modules_async(
            module_contexts,
            modules=target_modules,
            continue_on_error=True,
        )
        module_errors = module_outputs.get("__module_errors__", [])
        # 4. 初次合并。当前 schema 改造阶段先不做全局归一化和程序校验，便于观察 LLM 原始抽取质量。
        product_document = self.merge_module_outputs(module_outputs, loaded_document)
        self.attach_markdown_result(product_document, markdown_result)

        if module_errors:
            append_validation_issues(
                product_document,
                [module_error_to_issue(module_error) for module_error in module_errors],
            )

        # 5. self_check 只负责语义质量检查；如果它要求返工，再回到对应模块 Markdown 重抽。
        self_check: dict[str, Any] = {}
        rework_requests: dict[str, str] = {}
        if self.enable_post_processing and self.enable_self_check and not module_errors:
            self_check = await self.extractor.self_check_async(product_document)
            rework_requests.update(collect_rework_requests(self_check))
            filtered_requests = filter_rework_requests(rework_requests, self_check)
            skipped_modules = sorted(set(rework_requests) - set(filtered_requests))
            if skipped_modules:
                logger.info(f"module rework skipped by policy: {skipped_modules}")
            rework_requests = filtered_requests

        if rework_requests:
            logger.info(f"module rework requested: {sorted(rework_requests)}")
            before_rework_document = product_document
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
            product_document = self.merge_module_outputs(module_outputs, loaded_document)
            self.attach_markdown_result(product_document, markdown_result)
            if self_check:
                product_document = self.merger.apply_self_check(product_document, self_check)
            append_validation_issues(
                product_document,
                [
                    {
                        "severity": "info",
                        "path": f"llm_rework.{module_name}",
                        "message": reason,
                    }
                    for module_name, reason in sorted(rework_requests.items())
                ],
            )
            if self.enable_debug_markdown:
                self.write_rework_comparison(
                    loaded_document,
                    before_rework_document,
                    product_document,
                    rework_requests,
                    rework_errors,
                    self_check,
                )
        elif self_check:
            product_document = self.merger.apply_self_check(product_document, self_check)

        if not self.enable_post_processing:
            return product_document

        normalization_result = self.normalizer.normalize_product_document(product_document)
        product_document = normalization_result.product_document
        append_validation_issues(product_document, normalization_result.issues)

        # 7. 最终只做一次程序校验，作为写入 review JSON 前的确定性底线。
        program_issues = self.validator.validate(product_document)
        program_issues.extend(module_error_to_issue(module_error) for module_error in module_errors)
        program_issues.extend(loader_warning_to_issue(warning) for warning in loaded_document.warnings)
        return self.validator.attach_issues(product_document, program_issues)

    async def run_product_folder_async(self, folder_path: str | Path) -> ProductFolderWorkflowResult:
        """产品目录级入口：分类文件、切块、按模块抽取并合并为一个新 schema JSON。"""

        folder = Path(folder_path).resolve()
        document_items: list[dict[str, Any]] = []
        module_outputs_list: list[dict[str, Any]] = []
        module_count = 0

        for path in sorted(item for item in folder.iterdir() if item.is_file()):
            classification = classify_document(path)
            if classification.role == UNKNOWN or not classification.target_modules:
                continue
            item: dict[str, Any] = {
                "filename": path.name,
                "source_file": str(path),
                "role": classification.role,
                "normalized_name": classification.normalized_name,
                "target_modules": list(classification.target_modules),
            }
            try:
                render_path = self.prepare_source_for_render(path)
                if render_path != path:
                    item["converted_source_file"] = str(render_path)
                loaded_document = await self.loader.load_async(render_path)
                markdown_result = self.markdown_renderer.render_document_result(loaded_document)
                module_contexts = build_module_contexts_from_markdown(
                    markdown_result.markdown,
                    max_chars=self.extractor.max_context_chars,
                    modules=classification.target_modules,
                    document_role=classification.role,
                )
                item["markdown_chars"] = len(markdown_result.markdown)
                item["module_contexts"] = module_contexts
                module_outputs = await self.extractor.extract_modules_async(
                    module_contexts,
                    modules=list(classification.target_modules),
                    continue_on_error=True,
                )
                module_outputs["__module_source_files__"] = {
                    module_name: str(path)
                    for module_name in classification.target_modules
                }
                item["module_outputs"] = module_outputs
                module_outputs_list.append(module_outputs)
                module_count += len([key for key in module_outputs if not key.startswith("__")])
            except Exception as exc:
                item["error"] = f"{exc.__class__.__name__}: {exc}"
            document_items.append(item)

        debug_context_path = None
        if self.enable_debug_markdown:
            debug_context_path = self.write_product_module_contexts_debug(folder, document_items)

        product_document = self.merger.merge_many(
            module_outputs_list,
            document_metadata={
                "source_folder": str(folder),
            },
        )
        append_validation_issues(product_document, collect_product_folder_issues(document_items))
        if self.enable_post_processing:
            normalization_result = self.normalizer.normalize_product_document(product_document)
            product_document = normalization_result.product_document
            append_validation_issues(product_document, normalization_result.issues)
            product_document = self.validator.attach_issues(
                product_document,
                self.validator.validate(product_document),
            )
        product_id = build_product_folder_id(folder)
        review_path = self.write_review_json(product_document, product_id)
        return ProductFolderWorkflowResult(
            product_id=product_id,
            review_path=review_path,
            source_folder=folder,
            document_count=len(document_items),
            module_count=module_count,
            debug_context_path=debug_context_path,
        )

    def run_product_folder(self, folder_path: str | Path) -> ProductFolderWorkflowResult:
        """同步产品目录级入口，方便测试脚本直接调用。"""

        return run_async_from_sync(self.run_product_folder_async(folder_path))

    def prepare_source_for_render(self, source_path: Path) -> Path:
        """准备实际进入 MarkdownRenderer 的文件；老 .doc 先转成 .docx。"""

        if source_path.suffix.lower() != ".doc":
            return source_path
        if not self.enable_doc_conversion:
            raise RuntimeError("老 Word .doc 文件需要开启 enable_doc_conversion 后才能转换抽取。")
        return self.convert_doc_to_docx(source_path)

    def convert_doc_to_docx(self, source_path: Path) -> Path:
        """用本机 Microsoft Word COM 转换 .doc，只保留 converted_docx 结果。"""

        output_path = self.converted_docx_path(source_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists() and output_path.stat().st_mtime >= source_path.stat().st_mtime:
            return output_path

        try:
            import pythoncom
            import win32com.client
        except ImportError as exc:
            raise RuntimeError("缺少 Word COM 依赖，请先安装 pywin32：pip install pywin32") from exc

        word = None
        document = None
        pythoncom.CoInitialize()
        try:
            with tempfile.TemporaryDirectory(prefix="product_doc_agent_doc_") as temp_dir:
                work_doc_path = copy_doc_to_ascii_temp(source_path, Path(temp_dir))
                word = win32com.client.DispatchEx("Word.Application")
                word.Visible = False
                word.DisplayAlerts = 0
                document = word.Documents.Open(str(work_doc_path), ReadOnly=True, AddToRecentFiles=False)
                # FileFormat=16 表示 wdFormatXMLDocument，即 .docx。
                document.SaveAs2(str(output_path), FileFormat=16)
            return output_path
        finally:
            if document is not None:
                document.Close(False)
            if word is not None:
                word.Quit()
            pythoncom.CoUninitialize()

    def converted_docx_path(self, source_path: Path) -> Path:
        """按 PRODUCT_DOC_RAW_ROOT 的相对目录保存转换结果。"""

        try:
            relative_path = source_path.resolve().relative_to(self.raw_root)
        except ValueError:
            relative_path = Path(safe_debug_name(source_path.with_suffix(""))).with_suffix(source_path.suffix)
        return (self.data_root / "converted_docx" / relative_path.with_suffix(".docx")).resolve()

    def attach_markdown_result(self, product_document: dict[str, Any], markdown_result: Any) -> None:
        """正式 JSON 不写 Markdown 调试信息；调试产物只保存在 debug 目录。"""

    def merge_module_outputs(
        self,
        module_outputs: dict[str, Any],
        loaded_document: LoadedDocument,
    ) -> dict[str, Any]:
        """合并模块结果；当前阶段不做全局归一化和校验。"""

        return self.merger.merge(
            module_outputs,
            document_metadata=loaded_document_metadata(loaded_document),
        )
        
    def merge_and_normalize(
        self,
        module_outputs: dict[str, Any],
        loaded_document: LoadedDocument,
    ) -> dict[str, Any]:
        """兼容旧调用：合并模块结果并执行归一化。"""

        product_document = self.merge_module_outputs(module_outputs, loaded_document)
        normalization_result = self.normalizer.normalize_product_document(product_document)
        product_document = normalization_result.product_document
        exposed_issues = [
            issue for issue in normalization_result.issues if issue.get("expose_to_review") is True
        ]
        if exposed_issues:
            append_validation_issues(product_document, exposed_issues)
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

    def write_markdown_source_debug(self, loaded_document: LoadedDocument, markdown_result: Any) -> None:
        if markdown_result.source != "vision_ocr_markdown" or not markdown_result.pages:
            return
        metadata = {
            "source_path": loaded_document.source_path,
            "markdown_source": markdown_result.source,
            "markdown_path": str(self.debug_dir / f"{loaded_document.document_id}_llm_markdown.md"),
            "module_context_path": str(self.debug_dir / f"{loaded_document.document_id}_module_contexts.md"),
            **markdown_result.metadata,
        }
        self.ocr_debug_writer.write(
            document_id=loaded_document.document_id,
            pages=markdown_result.pages,
            metadata=metadata,
        )

    def write_product_module_contexts_debug(
        self,
        product_folder: str | Path,
        document_items: list[dict[str, Any]],
    ) -> Path:
        """保存产品目录级切块 Markdown，后续目录级抽取流程复用这个 debug 产物。"""

        product_folder = Path(product_folder)
        debug_dir = self.debug_dir / "context_splitter_by_product"
        debug_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{safe_debug_name(product_folder)}_module_contexts.md"
        path = debug_dir / filename
        path.write_text(
            render_product_module_contexts_debug(
                product_folder=product_folder,
                document_items=document_items,
            ),
            encoding="utf-8",
        )
        logger.info(f"Product module contexts debug file written: {path}")
        return path

    def write_rework_comparison(
        self,
        loaded_document: LoadedDocument,
        before_document: dict[str, Any],
        after_document: dict[str, Any],
        rework_requests: dict[str, str],
        rework_errors: list[dict[str, str]],
        self_check: dict[str, Any],
    ) -> None:
        """保存返工前后对比，帮助判断 self_check 返工是否值得。"""

        self.debug_dir.mkdir(parents=True, exist_ok=True)
        path = self.debug_dir / f"{loaded_document.document_id}_rework_comparison.json"
        comparison = build_rework_comparison(
            before_document,
            after_document,
            rework_requests,
            rework_errors,
            self_check,
        )
        path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"LLM rework comparison written: {path}")

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


def safe_debug_name(path: Path) -> str:
    """把产品目录路径转成适合 debug 文件名的短名称。"""

    raw_name = "_".join(part for part in path.parts[-3:]) or path.name or "product"
    safe_name = "".join(char if char.isalnum() or char in "-_." else "_" for char in raw_name)
    return safe_name[:120] or "product"


def build_product_folder_id(folder: Path) -> str:
    """根据产品目录路径生成稳定的 review JSON 文件名。"""

    import hashlib

    digest = hashlib.sha1(str(folder.resolve()).encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"product_{digest}"


def copy_doc_to_ascii_temp(source_path: Path, temp_dir: Path) -> Path:
    """把 .doc 复制到纯英文临时路径，降低 Word COM 打开中文路径失败的概率。"""

    import hashlib

    digest = hashlib.sha1(str(source_path.resolve()).encode("utf-8", errors="ignore")).hexdigest()[:16]
    temp_dir.mkdir(parents=True, exist_ok=True)
    work_doc_path = temp_dir / f"source_{digest}.doc"
    shutil.copy2(source_path, work_doc_path)
    return work_doc_path


def module_error_to_issue(module_error: dict[str, Any]) -> dict[str, str]:
    """把模块失败信息转成统一校验问题。"""

    return {
        "severity": "error",
        "path": f"llm_module.{module_error.get('module', '')}",
        "message": str(module_error.get("error", "模块抽取失败")),
    }


def collect_product_folder_issues(document_items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """汇总产品目录级处理错误，统一写入 extraction_meta.validation_issues。"""

    issues: list[dict[str, str]] = []
    for item in document_items:
        if item.get("error"):
            issues.append(
                {
                    "severity": "error",
                    "path": f"document.{item.get('filename', '')}",
                    "message": str(item.get("error", "")),
                }
            )
        module_outputs = item.get("module_outputs", {})
        if isinstance(module_outputs, dict):
            issues.extend(
                module_error_to_issue(module_error)
                for module_error in module_outputs.get("__module_errors__", [])
                if isinstance(module_error, dict)
            )
    return issues


def append_validation_issues(product_document: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    """追加校验问题并同步维护 validation_issue_count。"""

    if not issues:
        return
    meta = product_document.setdefault("extraction_meta", {})
    validation_issues = meta.setdefault("validation_issues", [])
    validation_issues.extend(issues)
    meta["validation_issue_count"] = len(validation_issues)


def loader_warning_to_issue(warning: str) -> dict[str, str]:
    """把文档加载告警转成统一校验问题。"""

    return {"severity": "warning", "path": "document_loader", "message": warning}


def collect_rework_requests(self_check: dict[str, Any]) -> dict[str, str]:
    """从 self_check 结果中提取需要返工的模块及原因。"""

    llm_self_check = self_check.get("llm_self_check", {}) if isinstance(self_check, dict) else {}
    if not isinstance(llm_self_check, dict):
        return {}

    requests: dict[str, str] = {}
    issue_reasons = collect_self_check_issue_reasons(self_check)
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
            requests[module_name] = combine_rework_reasons(
                reason or "大模型自检要求返工。",
                issue_reasons.get(module_name, []),
            )

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
            requests[module_name] = combine_rework_reasons(
                message or "大模型自检发现校验问题。",
                issue_reasons.get(module_name, []),
            )
    return requests


def filter_rework_requests(requests: dict[str, str], self_check: dict[str, Any]) -> dict[str, str]:
    """按返工策略过滤 self_check 请求，避免把确定性问题交给 LLM 重抽。"""

    if not requests:
        return {}

    issues_by_module = collect_self_check_issues(self_check)
    filtered: dict[str, str] = {}
    for module_name, reason in requests.items():
        if should_rework_module(module_name, issues_by_module.get(module_name, []), reason):
            filtered[module_name] = reason
    return filtered


def collect_self_check_issues(self_check: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """按模块收集 self_check issue，供返工策略判断。"""

    issues_by_module: dict[str, list[dict[str, Any]]] = {}
    for issue in self_check.get("validation_issues", []) if isinstance(self_check, dict) else []:
        if not isinstance(issue, dict):
            continue
        module_name = module_from_issue_path(str(issue.get("path", "")))
        if module_name:
            issues_by_module.setdefault(module_name, []).append(issue)
    return issues_by_module


def should_rework_module(module_name: str, issues: list[dict[str, Any]], reason: str) -> bool:
    """判断某个模块是否真的适合 LLM 返工。"""

    issue_text = "\n".join(
        " ".join(str(issue.get(key, "")) for key in ("path", "message"))
        for issue in issues
    )
    combined_text = f"{reason}\n{issue_text}"

    if module_name == "base_package":
        return contains_any(
            combined_text,
            (
                "contract_period",
                "协议期",
                "service_attributes",
                "基础属性",
                "included_items",
                "套餐内",
                "包含",
                "赠送",
                "漏抽",
                "缺少",
            ),
        )

    if module_name == "optional_packages":
        can_improve_package_shape = contains_any(
            combined_text,
            (
                "漏抽",
                "缺少",
                "未抽",
                "为空",
                "empty",
                "missing",
                "omitted",
                "合并",
                "拆分",
                "应拆分",
                "merged",
                "split",
                "只输出一条",
            ),
        )
        if issues and all(is_price_period_issue(issue) for issue in issues) and not can_improve_package_shape:
            return False
        return can_improve_package_shape

    if module_name == "fee_and_term_rules":
        if is_indirect_fee_rework_request(combined_text, issues):
            return False
        if issues and all(is_price_period_issue(issue) or mentions_other_module(issue) for issue in issues):
            return False
        return contains_any(combined_text, ("漏抽", "缺少", "未抽", "合并", "拆分", "missing", "omitted", "merged"))

    return False


def is_price_period_issue(issue: dict[str, Any]) -> bool:
    """价格计费周期错配属于确定性修正，不触发 LLM 返工。"""

    text = " ".join(str(issue.get(key, "")) for key in ("path", "message"))
    return "billing_period" in text or "price_items" in text and contains_any(text, ("元/月", "元/年", "计费周期"))


def mentions_other_module(issue: dict[str, Any]) -> bool:
    """issue 主要指向其它模块时，不让当前模块陪跑返工。"""

    text = str(issue.get("message", ""))
    return any(module in text for module in EXTRACTION_MODULES if module != module_from_issue_path(str(issue.get("path", ""))))


def is_indirect_fee_rework_request(text: str, issues: list[dict[str, Any]]) -> bool:
    """fee_and_term_rules 本身没缺规则、只是被其它模块牵连时不返工。"""

    if not contains_any(text, ("optional_packages", "price_items", "可选包", "可选产品")):
        return False
    return not contains_any(
        text,
        (
            "fee_and_term_rules 缺少",
            "fee_and_term_rules 漏抽",
            "fee_and_term_rules missing",
            "fee_and_term_rules omitted",
        ),
    ) or all(mentions_other_module(issue) or is_price_period_issue(issue) for issue in issues)


def contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    """判断文本是否包含任一关键词。"""

    return any(keyword in text for keyword in keywords)


def collect_self_check_issue_reasons(self_check: dict[str, Any]) -> dict[str, list[str]]:
    """按模块收集 self_check 中的具体问题，作为返工时的纠偏依据。"""

    reasons: dict[str, list[str]] = {}
    for issue in self_check.get("validation_issues", []) if isinstance(self_check, dict) else []:
        if not isinstance(issue, dict):
            continue
        module_name = module_from_issue_path(str(issue.get("path", "")))
        if not module_name:
            continue
        severity = str(issue.get("severity", "")).strip()
        path = str(issue.get("path", "")).strip()
        message = str(issue.get("message", "")).strip()
        reason_parts = [part for part in (severity, path, message) if part]
        if reason_parts:
            reasons.setdefault(module_name, []).append(" | ".join(reason_parts))
    return reasons


def combine_rework_reasons(primary_reason: str, issue_reasons: list[str]) -> str:
    """把返工模块原因和该模块下的具体自检问题合并，减少模型返工时自由发挥。"""

    lines: list[str] = []
    if primary_reason.strip():
        lines.append(primary_reason.strip())
    for reason in issue_reasons:
        if reason and reason not in lines:
            lines.append(reason)
    return "\n".join(f"- {line}" for line in lines) if lines else "大模型自检要求返工。"


def module_from_issue_path(path: str) -> str:
    """从 self_check issue path 推断所属模块。"""

    normalized = path.strip().lstrip("$.")
    top_level = normalized.split(".", 1)[0].split("[", 1)[0]
    return top_level if top_level in EXTRACTION_MODULES else ""


def build_rework_comparison(
    before_document: dict[str, Any],
    after_document: dict[str, Any],
    rework_requests: dict[str, str],
    rework_errors: list[dict[str, str]],
    self_check: dict[str, Any],
) -> dict[str, Any]:
    """生成返工前后对比数据，只比较 self_check 要求返工的模块。"""

    errors_by_module = {
        str(item.get("module", "")): str(item.get("error", ""))
        for item in rework_errors
        if isinstance(item, dict)
    }
    modules: dict[str, Any] = {}
    for module_name, reason in sorted(rework_requests.items()):
        before_value = before_document.get(module_name)
        after_value = after_document.get(module_name)
        modules[module_name] = {
            "reason": reason,
            "rework_error": errors_by_module.get(module_name, ""),
            "changed": before_value != after_value,
            "before_summary": summarize_module_value(before_value),
            "after_summary": summarize_module_value(after_value),
            "before": before_value,
            "after": after_value,
        }

    return {
        "说明": "该文件用于判断 self_check 触发的模块返工是否带来有效变化。",
        "self_check": {
            "needs_rework": self_check.get("llm_self_check", {}).get("needs_rework", False)
            if isinstance(self_check.get("llm_self_check", {}), dict)
            else False,
            "rework_modules": self_check.get("llm_self_check", {}).get("rework_modules", [])
            if isinstance(self_check.get("llm_self_check", {}), dict)
            else [],
            "validation_issues": self_check.get("validation_issues", []),
            "schema_warnings": self_check.get("schema_warnings", []),
        },
        "modules": modules,
    }


def summarize_module_value(value: Any) -> dict[str, Any]:
    """生成模块内容摘要，避免只看完整 JSON 时不容易判断变化。"""

    if isinstance(value, list):
        return {
            "type": "list",
            "count": len(value),
            "non_empty_count": sum(1 for item in value if not is_empty_value(item)),
        }
    if isinstance(value, dict):
        summary: dict[str, Any] = {"type": "object", "keys": sorted(value.keys())}
        for key, child in value.items():
            if isinstance(child, list):
                summary[f"{key}_count"] = len(child)
            elif isinstance(child, dict):
                summary[f"{key}_keys"] = sorted(child.keys())
        return summary
    return {"type": type(value).__name__, "empty": is_empty_value(value)}


def is_empty_value(value: Any) -> bool:
    """判断对比摘要里的空值。"""

    if value in ("", None, 0, 0.0, False):
        return True
    if isinstance(value, list):
        return not value
    if isinstance(value, dict):
        return not value
    return False
