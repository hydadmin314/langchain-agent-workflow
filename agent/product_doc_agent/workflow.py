from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from agent.product_doc_agent.document_loader import DocumentBlock, DocumentLoader, LoadedDocument
from agent.product_doc_agent.llm_extractor import EXTRACTION_MODULES, ProductDocumentLLMExtractor, run_async_from_sync
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
    """End-to-end workflow for LLM-based product document extraction."""

    def __init__(
        self,
        *,
        data_root: str | Path = DEFAULT_DATA_ROOT,
        llm: BaseChatModel | None = None,
        max_context_chars: int = 60000,
        max_concurrency: int | None = None,
        enable_self_check: bool = True,
    ) -> None:
        self.data_root = Path(data_root)
        self.review_dir = self.data_root / "review"
        self.published_dir = self.data_root / "published"
        self.debug_dir = self.data_root / "debug"
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
        self.write_debug_markdown(loaded_document, module_contexts)
        module_outputs = await self.extractor.extract_modules_async(
            module_contexts,
            modules=EXTRACTION_MODULES,
            continue_on_error=True,
        )
        product_document = self.merger.merge(module_outputs, document_metadata=loaded_document_metadata(loaded_document))

        module_errors = module_outputs.get("__module_errors__", [])
        if module_errors:
            product_document["extraction_meta"]["schema_warnings"].append("部分模块抽取失败，已跳过 LLM 自检。")

        normalization_result = self.normalizer.normalize_product_document(product_document)
        product_document = normalization_result.product_document
        exposed_normalization_issues = [
            issue for issue in normalization_result.issues if issue.get("expose_to_review") is True
        ]
        if exposed_normalization_issues:
            schema_warnings = product_document["extraction_meta"].setdefault("schema_warnings", [])
            schema_warnings.extend(issue.get("message", str(issue)) for issue in exposed_normalization_issues)

        if self.enable_self_check and not module_errors:
            self_check = await self.extractor.self_check_async(product_document)
            product_document = self.merger.apply_self_check(product_document, self_check)

        program_issues = self.validator.validate(product_document)
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

    def write_debug_markdown(self, loaded_document: LoadedDocument, module_contexts: dict[str, str]) -> None:
        """Persist Markdown debug files so reviewers can inspect the LLM input."""
        self.debug_dir.mkdir(parents=True, exist_ok=True)

        full_markdown = MarkdownRenderer().render_document(loaded_document, max_chars=None)
        full_path = self.debug_dir / f"{loaded_document.document_id}_llm_markdown.md"
        full_path.write_text(full_markdown, encoding="utf-8")

        module_path = self.debug_dir / f"{loaded_document.document_id}_module_contexts.md"
        module_path.write_text(render_module_contexts_debug(module_contexts), encoding="utf-8")
        logger.info(f"LLM markdown debug files written: {full_path}, {module_path}")

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
        "keywords": ("可选", "增值", "权益", "配套", "上行升速", "移动业务", "固话", "商云通", "云享", "安全大脑", "企业云盘", "入云专线"),
        "head_blocks": 8,
        "stop_markers": ("填表说明", "套餐营销规则", "客户特别关注"),
        "max_chars": 11000,
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
    return MarkdownRenderer().render_blocks(blocks, max_chars=max_chars)


def render_module_contexts_debug(module_contexts: dict[str, str]) -> str:
    sections: list[str] = [
        "# Product Document Module Contexts",
        "",
        "<!-- Each section below is one module prompt context built from the full LLM Markdown by document order and table structure. -->",
    ]
    for module_name, context in module_contexts.items():
        sections.extend(["", f"## {module_name}", "", context or "<!-- empty module context -->"])
    return "\n".join(sections)


@dataclass(frozen=True)
class StructuralSlices:
    document_info: list[DocumentBlock]
    application_fields: list[DocumentBlock]
    base_package: list[DocumentBlock]
    base_package_rule_blocks: list[DocumentBlock]
    optional_packages: list[DocumentBlock]
    fee_rule_blocks: list[DocumentBlock]
    agreement_rule_blocks: list[DocumentBlock]
    material_rule_blocks: list[DocumentBlock]
    constraint_rule_blocks: list[DocumentBlock]


def build_module_contexts(loaded_document: LoadedDocument, *, max_chars: int) -> dict[str, str]:
    """Build module contexts from the full Markdown source using structural slices."""
    slices = build_structural_slices(loaded_document)
    return {
        "document_info": render_context(loaded_document, slices.document_info, max_chars=min(max_chars, 7000)),
        "parties_and_application": render_context(
            loaded_document,
            slices.application_fields,
            max_chars=min(max_chars, 7000),
        ),
        "base_package": render_context(
            loaded_document,
            [*slices.document_info, *slices.base_package, *slices.base_package_rule_blocks],
            max_chars=min(max_chars, 10000),
        ),
        "optional_packages": render_context(loaded_document, slices.optional_packages, max_chars=min(max_chars, 9000)),
        "fee_and_term_rules": render_context(
            loaded_document,
            [*slices.base_package, *slices.optional_packages, *slices.fee_rule_blocks],
            max_chars=min(max_chars, 12000),
        ),
        "agreement_rules": render_context(
            loaded_document,
            slices.agreement_rule_blocks,
            max_chars=min(max_chars, 12000),
        ),
        "application_materials": render_context(
            loaded_document,
            slices.material_rule_blocks,
            max_chars=min(max_chars, 9000),
        ),
        "eligibility_and_constraints": render_context(
            loaded_document,
            slices.constraint_rule_blocks,
            max_chars=min(max_chars, 9000),
        ),
        "supplemental_rules": render_context(loaded_document, [], max_chars=min(max_chars, 4000)),
    }


def build_structural_slices(loaded_document: LoadedDocument) -> StructuralSlices:
    blocks = loaded_document.blocks
    document_info = select_document_info_blocks(blocks)
    application_fields = dedupe_blocks([*document_info, *select_application_field_blocks(blocks)])
    package_rows = select_package_table_blocks(blocks)
    base_package = [block for block in package_rows if is_base_package_block(block)]
    optional_packages = [block for block in package_rows if is_optional_package_block(block)]
    rule_source_blocks = [block for block in blocks if block.block_type != "table_row" or is_note_block(block)]

    return StructuralSlices(
        document_info=dedupe_blocks(document_info),
        application_fields=application_fields,
        base_package=dedupe_blocks(base_package or package_rows),
        base_package_rule_blocks=dedupe_blocks(select_rule_blocks(rule_source_blocks, BASE_PACKAGE_RULE_TERMS)),
        optional_packages=dedupe_blocks(optional_packages),
        fee_rule_blocks=dedupe_blocks(select_rule_blocks(rule_source_blocks, FEE_RULE_TERMS)),
        agreement_rule_blocks=dedupe_blocks(select_rule_blocks(rule_source_blocks, AGREEMENT_RULE_TERMS)),
        material_rule_blocks=dedupe_blocks(select_rule_blocks(rule_source_blocks, MATERIAL_RULE_TERMS)),
        constraint_rule_blocks=dedupe_blocks(select_rule_blocks(rule_source_blocks, CONSTRAINT_RULE_TERMS)),
    )


def select_document_info_blocks(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    return [block for block in blocks[:8] if block.block_type == "paragraph"]


def select_application_field_blocks(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    first_package_index = first_index(blocks, is_package_start_block)
    if first_package_index is None:
        first_package_index = first_index(blocks, is_note_block) or len(blocks)
    return [
        block
        for block in blocks[:first_package_index]
        if block.block_type == "table_row" and not is_package_related_text(block.text)
    ]


def select_package_table_blocks(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    start = first_index(blocks, is_package_start_block)
    if start is None:
        return []
    relative_end = first_index(blocks[start:], is_note_block)
    end = start + relative_end if relative_end is not None else len(blocks)
    return [block for block in blocks[start:end] if block.block_type == "table_row"]


def select_rule_blocks(blocks: list[DocumentBlock], terms: tuple[str, ...]) -> list[DocumentBlock]:
    selected: list[DocumentBlock] = []
    for block in blocks:
        for candidate in iter_rule_candidate_blocks(block):
            if contains_any(candidate.text, terms):
                selected.append(candidate)
    return selected


def iter_rule_candidate_blocks(block: DocumentBlock) -> list[DocumentBlock]:
    """Split mixed long rule cells into smaller clauses for module routing.

    Some Word forms put many unrelated clauses into one table cell such as
    "填表说明". Sending that whole cell to every module that matches one keyword
    creates duplicated context and encourages duplicated extraction. The split is
    generic: preserve normal blocks as-is, but break very long text into
    sentence/list-level derived blocks that keep the original source location.
    """
    text = str(block.text or "").strip()
    if len(text) < 500:
        return [block]

    parts = split_rule_text(text)
    if len(parts) <= 1:
        return [block]

    result: list[DocumentBlock] = []
    for index, part in enumerate(parts):
        source_location = dict(block.source_location)
        source_location["detail_index"] = index
        metadata = dict(block.metadata)
        metadata["parent_block_id"] = block.block_id
        metadata["derived_from_long_block"] = True
        result.append(
            DocumentBlock(
                block_id=f"{block.block_id}_part_{index:03d}",
                block_type="paragraph",
                text=part,
                source_location=source_location,
                metadata=metadata,
            )
        )
    return result


def split_rule_text(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return []

    list_marker_pattern = re.compile(
        r"(?=(?:^|\s)(?:\d{1,2}|[一二三四五六七八九十]{1,3}|[a-zA-Z])[、.．)）])"
    )
    marked_parts = clean_context_parts(list_marker_pattern.split(normalized))
    if len(marked_parts) > 1:
        return marked_parts
    return clean_context_parts(re.split(r"(?<=[。；;])\s*", normalized))


def clean_context_parts(parts: list[str]) -> list[str]:
    return [part.strip() for part in parts if part and part.strip()]


def first_index(blocks: list[DocumentBlock], predicate: Any) -> int | None:
    for index, block in enumerate(blocks):
        if predicate(block):
            return index
    return None


def is_package_start_block(block: DocumentBlock) -> bool:
    return contains_any(block.text, PACKAGE_START_TERMS)


def is_note_block(block: DocumentBlock) -> bool:
    return contains_any(block.text, NOTE_TERMS)


def is_base_package_block(block: DocumentBlock) -> bool:
    return contains_any(block.text, BASE_PACKAGE_TERMS) and not contains_any(block.text, OPTIONAL_PACKAGE_TERMS)


def is_optional_package_block(block: DocumentBlock) -> bool:
    return contains_any(block.text, OPTIONAL_PACKAGE_TERMS)


def is_package_related_text(text: str) -> bool:
    return contains_any(text, (*PACKAGE_START_TERMS, *BASE_PACKAGE_TERMS, *OPTIONAL_PACKAGE_TERMS))


def contains_any(text: str, terms: tuple[str, ...]) -> bool:
    normalized = normalize_context_text(text)
    return any(term in normalized for term in terms)


def render_context(loaded_document: LoadedDocument, blocks: list[DocumentBlock], *, max_chars: int) -> str:
    header = "\n".join(render_context_header(loaded_document))
    body_budget = max(0, max_chars - len(header) - 2)
    body = MarkdownRenderer().render_blocks(blocks, max_chars=body_budget)
    return f"{header}\n\n{body}".strip()


def render_context_header(loaded_document: LoadedDocument) -> list[str]:
    metadata = loaded_document_metadata(loaded_document)
    return [
        "# Product Document Context",
        "",
        "<!-- document_metadata: " + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + " -->",
    ]


def normalize_context_text(text: str) -> str:
    return "".join(str(text or "").split()).lower()


PACKAGE_START_TERMS = ("\u57fa\u7840\u5957\u9910\u7533\u8bf7\u4fe1\u606f", "\u5957\u9910\u7533\u8bf7\u4fe1\u606f", "\u4ea7\u54c1\u7533\u8bf7\u4fe1\u606f", "\u4e1a\u52a1\u7533\u8bf7\u4fe1\u606f")
NOTE_TERMS = ("\u586b\u8868\u8bf4\u660e", "\u586b\u5199\u8bf4\u660e", "\u529e\u7406\u8bf4\u660e", "\u6ce8\u610f\u4e8b\u9879")
BASE_PACKAGE_TERMS = ("\u57fa\u7840\u5957\u9910", "\u5957\u9910\u7c7b\u578b", "\u901f\u7387", "\u5e26\u5bbd", "\u4e0d\u5e26\u8bed\u97f3", "\u5e26\u8bed\u97f3")
BASE_PACKAGE_RULE_TERMS = ("\u57fa\u7840\u5957\u9910", "\u5957\u9910", "\u534f\u8bae\u671f", "\u751f\u6548", "\u5bbd\u5e26", "\u901f\u7387", "\u540c\u540d\u540c\u5740")
OPTIONAL_PACKAGE_TERMS = ("\u53ef\u9009", "\u6743\u76ca", "\u589e\u503c", "\u56fa\u8bdd", "\u5546\u4e91\u901a", "\u79fb\u52a8\u4e1a\u52a1", "\u4e0a\u884c\u5347\u901f", "\u5347\u901f\u5305", "\u8ba2\u8d2d")
FEE_RULE_TERMS = ("\u8d39\u7528", "\u8d44\u8d39", "\u6708\u8d39", "\u6708\u57fa\u672c\u8d39", "\u6708\u4ed8", "\u5e74\u4ed8", "\u4e00\u6b21\u6027", "\u62bc\u91d1", "\u5b89\u88c5", "\u8c03\u6d4b", "\u624b\u7eed\u8d39", "\u534f\u8bae\u671f", "\u8fdd\u7ea6\u91d1", "\u6298\u6263", "\u5143")
AGREEMENT_RULE_TERMS = ("\u534f\u8bae", "\u5408\u540c", "\u8fdd\u7ea6", "\u9000\u8ba2", "\u6ce8\u9500", "\u62c6\u673a", "\u8d54\u507f", "\u552e\u540e", "\u4fdd\u4fee", "\u5b9e\u540d", "\u627f\u8bfa", "\u7ec8\u6b62", "\u53d8\u66f4")
MATERIAL_RULE_TERMS = ("\u8425\u4e1a\u6267\u7167", "\u8eab\u4efd\u8bc1", "\u6388\u6743", "\u59d4\u6258\u4e66", "\u62c5\u4fdd", "\u7533\u8bf7\u8868", "\u627f\u8bfa\u4e66", "\u544a\u77e5\u4e66", "\u590d\u5370\u4ef6", "\u76d6\u7ae0", "\u7b7e\u5b57", "\u6750\u6599", "\u8bc1\u4ef6")
CONSTRAINT_RULE_TERMS = ("\u4ec5\u9650", "\u4e0d\u9002\u7528", "\u4e0d\u80fd", "\u4e0d\u5f97", "\u4e0d\u53ef", "\u5fc5\u987b", "\u6761\u4ef6", "\u8981\u6c42", "\u9650\u5236", "\u6b20\u8d39", "\u505c\u7528", "\u505c\u6b62", "\u540c\u540d", "\u540c\u5740", "\u5408\u5e76\u5f00\u8d26")
