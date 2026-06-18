from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.product_doc_agent.document_classifier import (
    APPLICATION_FORM,
    APPLICATION_MATERIALS,
    PRICING_SHEET,
    PRODUCT_INTRO,
    PRODUCT_KEYWORDS,
    UNKNOWN,
)
from agent.product_doc_agent.llm_extractor import EXTRACTION_MODULES


MODULE_CONTEXT_CHAR_LIMITS: dict[str, int] = {
    "application_form_info.document_info": 8000,
    "application_form_info.parties_and_application": 10000,
    "application_form_info.pricing_info": 16000,
    "application_form_info.agreement_rules": 14000,
    "application_form_info.eligibility_and_constraints": 12000,
    "supplementary_info.product_intro": 16000,
    "supplementary_info.product_keywords": 8000,
    "supplementary_info.pricing_info": 20000,
    "supplementary_info.application_materials": 16000,
}

FULL_DOCUMENT_ROLES = {
    PRODUCT_INTRO,
    PRODUCT_KEYWORDS,
    PRICING_SHEET,
    APPLICATION_MATERIALS,
}

# 申请表内部按业务信息类型多标签分发；一个 block 可以进入多个模块，宁可多给上下文也不要漏。
DOCUMENT_INFO_TERMS = ("申请表", "登记表", "产品名称", "套餐名称", "运营商", "服务归属", "版本", "生效", "文档")
PARTIES_APPLICATION_TERMS = (
    "代理商",
    "业务人员",
    "客户",
    "客户名称",
    "企业全称",
    "统一社会信用代码",
    "经办人",
    "联系人",
    "联系电话",
    "手机",
    "邮箱",
    "安装地址",
    "账单地址",
    "付款方式",
    "企业规模",
    "计算机数量",
    "必填",
    "选填",
)
PRICING_TERMS = (
    "套餐",
    "资费",
    "价格",
    "费用",
    "月付",
    "年付",
    "一次性",
    "初装费",
    "接入费",
    "安装费",
    "施工费",
    "速率",
    "上行",
    "下行",
    "语音",
    "权益",
    "增值",
    "可选",
    "折扣",
    "协议期",
)
AGREEMENT_TERMS = (
    "协议",
    "条款",
    "违约",
    "退订",
    "终止",
    "售后",
    "欠费",
    "暂停",
    "注销",
    "赔付",
    "SLA",
    "服务承诺",
    "客户义务",
    "服务商义务",
    "补充协议",
    "冲突的，以",
)
CONSTRAINT_TERMS = (
    "准入",
    "限制",
    "适用条件",
    "不适用",
    "不得",
    "不能",
    "不可",
    "仅限",
    "外地",
    "担保",
    "承诺书",
    "IP数量",
    "实名",
    "资质",
    "需补材料",
    "需提供",
    "不可办理",
    "不得办理",
    "ISP",
    "IDC",
    "CDN",
)

# 冲突消解词：同一句同时命中两个模块时，用这些词判断它更像哪一类。
AGREEMENT_PRIORITY_TERMS = (
    "协议期",
    "违约",
    "退订",
    "终止",
    "欠费",
    "暂停",
    "注销",
    "售后",
    "SLA",
    "赔付",
    "补充协议",
    "冲突的，以",
)
CONSTRAINT_PRIORITY_TERMS = (
    "仅限",
    "不适用",
    "不得办理",
    "不可办理",
    "外地",
    "担保",
    "需提供",
    "需补",
    "资质",
    "实名",
    "ISP",
    "IDC",
    "CDN",
)


@dataclass(frozen=True)
class MarkdownBlock:
    """Markdown 切块后的最小上下文单元。"""

    index: int
    block_type: str
    text: str


def build_module_contexts_from_markdown(
    markdown: str,
    *,
    max_chars: int,
    modules: Sequence[str] | None = None,
    document_role: str = UNKNOWN,
) -> dict[str, str]:
    """按文档角色为新 schema 模块准备 Markdown 上下文。

    第一版策略：
    1. 产品介绍、关键词、资费表、申请手续提示这类单一职责文件，整文给对应模块。
    2. 申请表是综合文档，按关键词把 block 多标签分发给 5 个申请表模块。
    3. unknown 不构建上下文，避免不支持文件误进入抽取流程。
    """

    module_names = list(modules or EXTRACTION_MODULES)
    if not module_names or document_role == UNKNOWN:
        return {}

    if document_role in FULL_DOCUMENT_ROLES:
        return build_full_document_contexts(markdown, module_names, max_chars=max_chars)

    if document_role == APPLICATION_FORM:
        return build_application_form_contexts(markdown, module_names, max_chars=max_chars)

    return {}


def build_full_document_contexts(markdown: str, modules: Sequence[str], *, max_chars: int) -> dict[str, str]:
    """补充资料文件已经按文件名归类，整份 Markdown 直接给目标模块。"""

    normalized_markdown = normalize_markdown(markdown)
    return {
        module_name: limit_context_for_module(normalized_markdown, module_name, max_chars=max_chars)
        for module_name in modules
    }


def build_application_form_contexts(markdown: str, modules: Sequence[str], *, max_chars: int) -> dict[str, str]:
    """申请表内部按业务信息类型切块；同一块可以进入多个模块。"""

    blocks = parse_markdown_blocks(markdown)
    head_blocks = select_document_head(blocks)
    document_info_blocks = select_document_info_blocks(markdown)
    pricing_start_index = first_block_index(blocks, is_pricing_section_start)
    notes_start_index = first_block_index(blocks, is_form_notes_section_start)
    form_field_blocks = blocks[:pricing_start_index] if pricing_start_index is not None else blocks
    pricing_blocks = slice_blocks(blocks, pricing_start_index, notes_start_index)
    rule_blocks = blocks[notes_start_index:] if notes_start_index is not None else []
    contexts = {
        "application_form_info.document_info": render_context(
            document_info_blocks
        ),
        "application_form_info.parties_and_application": render_context(
            [*head_blocks, *filter_blocks(form_field_blocks, lambda block: contains_any(block.text, PARTIES_APPLICATION_TERMS))]
        ),
        "application_form_info.pricing_info": render_context(
            filter_blocks(pricing_blocks, lambda block: contains_any(block.text, PRICING_TERMS))
        ),
        "application_form_info.agreement_rules": render_context(
            filter_blocks(rule_blocks, is_agreement_context_block)
        ),
        "application_form_info.eligibility_and_constraints": render_context(
            filter_blocks(rule_blocks, is_constraint_context_block)
        ),
    }
    return {
        module_name: limit_context_for_module(contexts.get(module_name, ""), module_name, max_chars=max_chars)
        for module_name in modules
        if module_name in contexts
    }


def is_document_info_block(block: MarkdownBlock) -> bool:
    """文档基本信息只从开头附近取，避免把正文里的申请表/登记表字样误收进去。"""

    return contains_any(block.text, DOCUMENT_INFO_TERMS)


def is_pricing_section_start(block: MarkdownBlock) -> bool:
    """识别申请表中套餐/资费区起点；客户和代理商字段必须在这个边界前结束。"""

    return contains_any(block.text, ("基础套餐申请信息", "套餐申请信息", "产品套餐费用信息", "资费信息"))


def is_form_notes_section_start(block: MarkdownBlock) -> bool:
    """识别填表说明/办理说明起点；套餐资费区必须在这个边界前结束。"""

    return contains_any(block.text, ("填表说明", "填写说明", "办理说明", "注意事项", "业务服务协议", "客户承诺"))


def is_agreement_context_block(block: MarkdownBlock) -> bool:
    """协议规则：办理后的履约、退订、违约、欠费、售后、SLA 等规则。"""

    text = block.text
    if not contains_any(text, AGREEMENT_TERMS):
        return False
    if contains_any(text, CONSTRAINT_PRIORITY_TERMS) and not contains_any(text, AGREEMENT_PRIORITY_TERMS):
        return False
    return True


def is_constraint_context_block(block: MarkdownBlock) -> bool:
    """准入限制：办理前的适用性、不适用、资质、补材料等规则。"""

    text = block.text
    if not contains_any(text, CONSTRAINT_TERMS):
        return False
    if contains_any(text, AGREEMENT_PRIORITY_TERMS) and not contains_any(text, CONSTRAINT_PRIORITY_TERMS):
        return False
    return True


def first_block_index(blocks: list[MarkdownBlock], predicate: Callable[[MarkdownBlock], bool]) -> int | None:
    """返回第一个命中块的位置；没有命中时返回 None。"""

    for index, block in enumerate(blocks):
        if predicate(block):
            return index
    return None


def slice_blocks(blocks: list[MarkdownBlock], start: int | None, end: int | None) -> list[MarkdownBlock]:
    """按起止边界切块；边界缺失时使用合理兜底。"""

    start_index = start if start is not None else 0
    end_index = end if end is not None else len(blocks)
    if end_index < start_index:
        end_index = len(blocks)
    return blocks[start_index:end_index]


def select_document_info_blocks(markdown: str) -> list[MarkdownBlock]:
    """文档基本信息优先取第一条有效标题，避免 MarkItDown 把标题和表格粘成大块。"""

    for line in normalize_markdown(markdown).splitlines():
        text = line.strip().strip("|").strip()
        if not text or is_markdown_separator_row(line) or is_empty_table_row(line):
            continue
        return [MarkdownBlock(index=0, block_type="heading", text=text)]
    return []


def parse_markdown_blocks(markdown: str) -> list[MarkdownBlock]:
    """按 Markdown 结构切块；表格按数据行拆细，避免整张大表同时污染多个模块。"""

    blocks: list[MarkdownBlock] = []
    buffer: list[str] = []
    buffer_type = ""

    def flush() -> None:
        nonlocal buffer, buffer_type
        text = "\n".join(line for line in buffer if line.strip()).strip()
        if text:
            append_markdown_block(blocks, buffer_type or "paragraph", text)
        buffer = []
        buffer_type = ""

    for raw_line in normalize_markdown(markdown).splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            flush()
            continue
        line_type = classify_markdown_line(line)
        if buffer and line_type != buffer_type:
            flush()
        buffer_type = line_type
        buffer.append(line)

    flush()
    return blocks


def append_markdown_block(blocks: list[MarkdownBlock], block_type: str, text: str) -> None:
    """追加 Markdown 块；表格拆成行，保留表头和数据行的原始文本。"""

    if block_type != "table":
        blocks.append(MarkdownBlock(index=len(blocks), block_type=block_type, text=text))
        return

    for row in (line for line in text.splitlines() if line.strip()):
        if is_markdown_separator_row(row) or is_empty_table_row(row):
            continue
        blocks.append(MarkdownBlock(index=len(blocks), block_type="table_row", text=row))


def select_document_head(blocks: list[MarkdownBlock], *, limit: int = 5) -> list[MarkdownBlock]:
    """保留文档开头标题和说明，作为 document_info 与表单字段抽取的辅助上下文。"""

    result: list[MarkdownBlock] = []
    for block in blocks:
        if block.block_type == "table_row":
            break
        result.append(block)
        if len(result) >= limit:
            break
    return result


def filter_blocks(blocks: list[MarkdownBlock], predicate: Callable[[MarkdownBlock], bool]) -> list[MarkdownBlock]:
    """按关键词过滤块；长段落会先拆成较短句子再判断归属。"""

    result: list[MarkdownBlock] = []
    for block in blocks:
        result.extend(candidate for candidate in split_long_block(block) if predicate(candidate))
    return result


def split_long_block(block: MarkdownBlock) -> list[MarkdownBlock]:
    """规则段落拆成短句，降低协议规则和准入限制互相污染的概率。"""

    text = block.text.strip()
    if block.block_type == "table_row" and len(text) < 450:
        return [block]
    parts = split_context_text(text)
    if len(parts) <= 1:
        return [block]
    return [MarkdownBlock(index=block.index, block_type="paragraph", text=part) for part in parts]


def split_context_text(text: str) -> list[str]:
    """按换行和句末标点拆分上下文，保留短规则的独立判断机会。"""

    normalized = re.sub(r"(?=\s(?:\d{1,2}|[一二三四五六七八九十]{1,3})[.、）)]\s*)", "\n", text)
    line_parts = clean_parts(normalized.splitlines())
    parts: list[str] = []
    for line in line_parts:
        parts.extend(clean_parts(re.split(r"(?<=[。；;])\s*", re.sub(r"\s+", " ", line))))
    return parts


def render_context(blocks: list[MarkdownBlock]) -> str:
    """渲染模块上下文；空上下文保留注释，方便 debug 发现切块缺口。"""

    unique_blocks = dedupe_blocks(blocks)
    if not unique_blocks:
        return "<!-- 当前文档没有适合该模块的 Markdown 片段。-->"
    return "\n\n".join(block.text for block in unique_blocks).strip()


def render_module_contexts_debug(module_contexts: dict[str, str]) -> str:
    """生成调试 Markdown，方便人工检查每个新 schema 模块拿到的上下文。"""

    sections = ["# Product Document Module Contexts", ""]
    ordered_modules = [module for module in EXTRACTION_MODULES if module in module_contexts]
    ordered_modules.extend(module for module in module_contexts if module not in ordered_modules)
    for module_name in ordered_modules:
        sections.extend(["", f"## {module_name}", "", module_contexts.get(module_name, "")])
    return "\n".join(sections)


def render_product_module_contexts_debug(
    *,
    product_folder: str | Path,
    document_items: Sequence[dict[str, Any]],
) -> str:
    """生成产品目录级切块调试 Markdown，供后续目录级流程落盘审查。"""

    sections = [
        f"# 产品目录切块预览：{product_folder}",
        "",
        f"- source_folder: {product_folder}",
        f"- document_count: {len(document_items)}",
        "",
    ]
    for item in document_items:
        contexts = item.get("module_contexts", {})
        sections.extend(
            [
                f"## 文件：{item.get('filename', '')}",
                "",
                f"- role: {item.get('role', '')}",
                f"- normalized_name: {item.get('normalized_name', '')}",
                f"- target_modules: {item.get('target_modules', [])}",
            ]
        )
        if item.get("converted_source_file"):
            sections.append(f"- converted_source_file: {item.get('converted_source_file')}")
        if "markdown_chars" in item:
            sections.append(f"- markdown_chars: {item.get('markdown_chars')}")
        sections.append("")

        error = item.get("error")
        if error:
            sections.extend(["### 转换失败", "", str(error), ""])
            continue

        for module_name, context in contexts.items():
            sections.extend([f"### {module_name}", "", str(context).strip(), ""])
    return "\n".join(sections)


def limit_context_for_module(markdown: str, module_name: str, *, max_chars: int) -> str:
    """按模块预算截断上下文，避免单个模块 prompt 过长。"""

    module_limit = MODULE_CONTEXT_CHAR_LIMITS.get(module_name, max_chars)
    return limit_markdown_context(markdown, max_chars=min(max_chars, module_limit))


def limit_markdown_context(markdown: str, *, max_chars: int) -> str:
    """按模块预算截断 Markdown。"""

    if len(markdown) <= max_chars:
        return markdown
    warning = "\n\n<!-- 内容超过当前模块 max_chars，后续 Markdown 已截断。-->"
    budget = max(0, max_chars - len(warning))
    return markdown[:budget].rstrip() + warning


def normalize_markdown(markdown: str) -> str:
    """清理 Markdown 中的图片占位和多余空白。"""

    text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"!\[[^\]]*]\([^)]*\)", "", text)
    text = re.sub(r"\*\*\s*\*\*", "", text)
    return text.strip()


def classify_markdown_line(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("#"):
        return "heading"
    if looks_like_markdown_table_row(stripped):
        return "table"
    if stripped.startswith(("-", "*", "+")) or re.match(r"^\d+[.)、]", stripped):
        return "list"
    return "paragraph"


def looks_like_markdown_table_row(line: str) -> bool:
    return line.startswith("|") and line.endswith("|") and line.count("|") >= 2


def is_markdown_separator_row(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def is_empty_table_row(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return not any(cell for cell in cells)


def dedupe_blocks(blocks: list[MarkdownBlock]) -> list[MarkdownBlock]:
    """按文本去重，保留首次出现顺序。"""

    result: list[MarkdownBlock] = []
    seen: set[str] = set()
    for block in blocks:
        key = normalize_for_match(block.text)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(block)
    return result


def contains_any(text: str, terms: tuple[str, ...]) -> bool:
    normalized = normalize_for_match(text)
    return any(normalize_for_match(term) in normalized for term in terms)


def normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def clean_parts(parts: list[str]) -> list[str]:
    return [part.strip() for part in parts if part and part.strip()]
