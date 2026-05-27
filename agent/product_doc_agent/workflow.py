from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from agent.product_doc_agent.document_loader import DocumentBlock, DocumentLoader, LoadedDocument
from agent.product_doc_agent.llm_extractor import EXTRACTION_MODULES, ProductDocumentLLMExtractor, run_async_from_sync
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
    """End-to-end workflow for LLM-based product document extraction."""

    def __init__(
        self,
        *,
        data_root: str | Path = DEFAULT_DATA_ROOT,
        llm: BaseChatModel | None = None,
        max_context_chars: int = 60000,
        max_concurrency: int = 3,
        enable_self_check: bool = True,
    ) -> None:
        self.data_root = Path(data_root)
        self.review_dir = self.data_root / "review"
        self.published_dir = self.data_root / "published"
        self.loader = DocumentLoader()
        self.extractor = ProductDocumentLLMExtractor(
            llm=llm,
            max_context_chars=max_context_chars,
            max_concurrency=max_concurrency,
        )
        self.merger = ProductDocumentMerger()
        self.normalizer = ProductDocumentNormalizer()
        self.validator = ProductDocumentValidator()
        self.enable_self_check = enable_self_check

    def run(self, file_path: str | Path) -> WorkflowResult:
        return run_async_from_sync(self.run_async(file_path))

    async def run_async(self, file_path: str | Path) -> WorkflowResult:
        loaded_document = await self.loader.load_async(file_path)
        product_document = await self.extract_loaded_document_async(loaded_document)
        review_path = self.write_review_json(product_document, loaded_document.document_id)
        validation_issue_count = product_document.get("extraction_meta", {}).get("validation_issue_count", 0)
        logger.info(f"产品文档抽取完成，document_id={loaded_document.document_id}, review_path={review_path}")
        return WorkflowResult(
            document_id=loaded_document.document_id,
            review_path=review_path,
            validation_issue_count=validation_issue_count,
            block_count=len(loaded_document.blocks),
        )

    def extract_loaded_document(self, loaded_document: LoadedDocument) -> dict[str, Any]:
        return run_async_from_sync(self.extract_loaded_document_async(loaded_document))

    async def extract_loaded_document_async(self, loaded_document: LoadedDocument) -> dict[str, Any]:
        module_contexts = build_module_contexts(loaded_document, max_chars=self.extractor.max_context_chars)
        module_outputs = await self.extractor.extract_modules_async(
            module_contexts,
            modules=EXTRACTION_MODULES,
            continue_on_error=True,
        )
        product_document = self.merger.merge(module_outputs, document_metadata=loaded_document_metadata(loaded_document))

        module_errors = module_outputs.get("__module_errors__", [])
        if self.enable_self_check and not module_errors:
            self_check = await self.extractor.self_check_async(product_document)
            product_document = self.merger.apply_self_check(product_document, self_check)
        elif module_errors:
            product_document["extraction_meta"]["schema_warnings"].append("部分模块抽取失败，已跳过 LLM 自检。")

        normalization_result = self.normalizer.normalize_product_document(product_document)
        product_document = normalization_result.product_document
        if normalization_result.issues:
            schema_warnings = product_document["extraction_meta"].setdefault("schema_warnings", [])
            schema_warnings.extend(issue.get("message", str(issue)) for issue in normalization_result.issues)

        program_issues = self.validator.validate(product_document)
        program_issues.extend(self.validator.validate_source_coverage(product_document, loaded_document.blocks))
        for module_error in module_errors:
            program_issues.append(
                {
                    "severity": "error",
                    "path": f"llm_module.{module_error.get('module', '')}",
                    "message": module_error.get("error", "module extraction failed"),
                }
            )
        if loaded_document.warnings:
            program_issues.extend(
                {"severity": "warning", "path": "document_loader", "message": warning}
                for warning in loaded_document.warnings
            )
        return self.validator.attach_issues(product_document, program_issues)

    def write_review_json(self, product_document: dict[str, Any], document_id: str) -> Path:
        self.review_dir.mkdir(parents=True, exist_ok=True)
        path = self.review_dir / f"{document_id}.json"
        path.write_text(json.dumps(product_document, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def publish(self, document_id: str) -> Path:
        review_path = self.review_dir / f"{document_id}.json"
        if not review_path.exists():
            raise FileNotFoundError(f"Review JSON not found: {review_path}")
        self.published_dir.mkdir(parents=True, exist_ok=True)
        published_path = self.published_dir / review_path.name
        shutil.copy2(review_path, published_path)
        return published_path


def loaded_document_metadata(loaded_document: LoadedDocument) -> dict[str, Any]:
    return {
        "document_id": loaded_document.document_id,
        "source_path": loaded_document.source_path,
        "filename": loaded_document.filename,
        "source_file_type": loaded_document.source_file_type,
        "source_file_hash": loaded_document.source_file_hash,
        "modified_at": loaded_document.modified_at,
    }


MODULE_CONTEXT_RULES: dict[str, dict[str, Any]] = {
    "document_info": {
        "keywords": ("中国电信", "上海", "申请登记表", "营销活动", "套餐", "版本", "生效", "起", "不带语音", "带语音"),
        "head_blocks": 30,
        "max_chars": 8000,
    },
    "parties_and_application": {
        "keywords": ("企业全称", "统一社会信用代码", "企业代码", "企业规模", "经办人", "联系人", "付款方式", "客户", "填写", "服务商", "热线"),
        "head_blocks": 14,
        "stop_markers": ("填表说明", "套餐营销规则", "客户特别关注"),
        "max_chars": 4200,
    },
    "base_package": {
        "keywords": ("基础套餐", "套餐类型", "企业规模", "计算机数量", "速率", "上行", "下行", "不带语音", "带语音"),
        "head_blocks": 16,
        "stop_markers": ("填表说明", "套餐营销规则", "客户特别关注"),
        "max_chars": 5200,
    },
    "optional_packages": {
        "keywords": ("可选", "增值", "权益", "配套", "上行升速", "移动业务", "固话", "商云通", "云享", "安全大脑", "企业云盘"),
        "head_blocks": 16,
        "stop_markers": ("填表说明", "套餐营销规则", "客户特别关注"),
        "max_chars": 6200,
    },
    "fee_and_term_rules": {
        "keywords": ("元", "费用", "资费", "月费", "月租", "年付", "一次性", "押金", "安装调测费", "手续费", "协议期", "违约金", "折扣"),
        "head_blocks": 12,
        "max_chars": 12000,
    },
    "agreement_rules": {
        "keywords": ("填表说明", "协议", "合同", "违约", "退订", "注销", "SLA", "赔偿", "售后", "保修", "限制", "不可", "不能", "不得", "必须", "承诺", "实名制"),
        "head_blocks": 8,
        "max_chars": 8000,
    },
    "application_materials": {
        "keywords": ("营业执照", "身份证", "授权", "委托书", "担保", "申请表", "承诺书", "报备表", "复印件", "盖章", "签字", "材料", "须附"),
        "head_blocks": 12,
        "max_chars": 10000,
    },
    "eligibility_and_constraints": {
        "keywords": ("仅限", "不适用", "不能", "不得", "必须", "条件", "要求", "IP", "外地", "停用", "阻止", "限制", "同地址", "存量"),
        "head_blocks": 12,
        "max_chars": 10000,
    },
    "supplemental_rules": {
        "keywords": ("附件", "附录", "承诺书", "报备表", "SLA", "责任人", "证件", "公司信息", "备案", "网络安全", "表"),
        "head_blocks": 10,
        "max_chars": 12000,
    },
}


def build_module_contexts(loaded_document: LoadedDocument, *, max_chars: int) -> dict[str, str]:
    return {
        module_name: render_blocks(
            select_blocks_for_module(loaded_document.blocks, rule),
            max_chars=min(max_chars, int(rule["max_chars"])),
        )
        for module_name, rule in MODULE_CONTEXT_RULES.items()
    }


def select_blocks_for_module(blocks: list[DocumentBlock], rule: dict[str, Any]) -> list[DocumentBlock]:
    selected: list[DocumentBlock] = []
    head_blocks = int(rule.get("head_blocks", 0))
    keywords = tuple(str(keyword).lower() for keyword in rule.get("keywords", ()))
    stop_markers = tuple(str(marker).lower() for marker in rule.get("stop_markers", ()))
    for index, block in enumerate(blocks):
        text = block.text.lower()
        if stop_markers and any(marker in text for marker in stop_markers):
            break
        if index < head_blocks or any(keyword in text for keyword in keywords):
            selected.append(block)
    return dedupe_blocks(selected)


def dedupe_blocks(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    seen: set[str] = set()
    result: list[DocumentBlock] = []
    for block in blocks:
        if block.block_id in seen:
            continue
        seen.add(block.block_id)
        result.append(block)
    return result


def render_blocks(blocks: list[DocumentBlock], *, max_chars: int) -> str:
    parts: list[str] = []
    total = 0
    for block in blocks:
        item = {
            "block_id": block.block_id,
            "block_type": block.block_type,
            "source_location": block.source_location,
            "text": block.text,
        }
        rendered = repr(item)
        if total + len(rendered) > max_chars:
            parts.append(
                repr(
                    {
                        "block_id": "context_truncated",
                        "block_type": "warning",
                        "source_location": {},
                        "text": "模块相关上下文较长，后续块已截断。",
                    }
                )
            )
            break
        parts.append(rendered)
        total += len(rendered)
    return "\n".join(parts)
