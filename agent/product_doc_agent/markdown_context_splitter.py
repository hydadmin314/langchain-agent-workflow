from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from agent.product_doc_agent.llm_extractor import EXTRACTION_MODULES


MODULE_CONTEXT_CHAR_LIMITS: dict[str, int] = {
    "document_info": 8000,
    "parties_and_application": 8000,
    "base_package": 12000,
    "optional_packages": 12000,
    "fee_and_term_rules": 14000,
    "agreement_rules": 14000,
    "application_materials": 12000,
    "eligibility_and_constraints": 12000,
    "supplemental_rules": 6000,
}


@dataclass(frozen=True)
class MarkdownBlock:
    """Markdown 切块后的最小上下文单元。"""

    index: int
    block_type: str
    text: str


@dataclass(frozen=True)
class MarkdownDocumentSlices:
    """按文档结构划分出的业务区域。"""

    head: list[MarkdownBlock]
    application: list[MarkdownBlock]
    package_area: list[MarkdownBlock]
    base_package_area: list[MarkdownBlock]
    optional_package_area: list[MarkdownBlock]
    rule_area: list[MarkdownBlock]
    supplemental: list[MarkdownBlock]


def build_module_contexts_from_markdown(markdown: str, *, max_chars: int) -> dict[str, str]:
    """把 MarkItDown Markdown 切成 9 个 schema 模块各自需要的上下文。

    当前策略尽量保持通用：
    1. 先去掉图片和空表格行，减少无意义 token。
    2. 再按 Markdown 表格行、标题、段落切成小块。
    3. base_package 和 optional_packages 只取套餐申请区，避免越界到后面的营销规则。
    4. fee_and_term_rules 同时取套餐区和规则区中所有金额、费用、协议期、违约金相关内容。
    """

    blocks = parse_markdown_blocks(markdown)
    slices = build_document_slices(blocks)

    contexts = {
        "document_info": render_context(slices.head),
        "parties_and_application": render_context([*slices.head, *slices.application]),
        "base_package": render_context(slices.base_package_area),
        "optional_packages": render_context(slices.optional_package_area),
        "fee_and_term_rules": render_context(
            [
                *filter_blocks(slices.package_area, is_fee_related),
                *filter_blocks(slices.rule_area, is_fee_rule),
            ],
        ),
        "agreement_rules": render_context(filter_blocks(slices.rule_area, is_agreement_rule)),
        "application_materials": render_context(filter_blocks(slices.rule_area, is_material_rule)),
        "eligibility_and_constraints": render_context(filter_blocks(slices.rule_area, is_constraint_rule)),
        # 主文档内部的承诺书、协议和规则不进入 supplemental_rules；该字段后续用于其它文件合并。
        "supplemental_rules": render_context(slices.supplemental),
    }

    return {
        module_name: limit_markdown_context(
            contexts.get(module_name, ""),
            max_chars=min(max_chars, MODULE_CONTEXT_CHAR_LIMITS[module_name]),
        )
        for module_name in EXTRACTION_MODULES
    }


def parse_markdown_blocks(markdown: str) -> list[MarkdownBlock]:
    """按 Markdown 结构切块；表格按数据行拆细。"""

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
    """追加 Markdown 块；表格拆成行，避免一个大表格同时落入多个模块。"""

    if block_type != "table":
        blocks.append(MarkdownBlock(index=len(blocks), block_type=block_type, text=text))
        return

    for row in (line for line in text.splitlines() if line.strip()):
        if is_markdown_separator_row(row) or is_empty_table_row(row):
            continue
        blocks.append(MarkdownBlock(index=len(blocks), block_type="table_row", text=row))


def build_document_slices(blocks: list[MarkdownBlock]) -> MarkdownDocumentSlices:
    """识别表单前部、套餐申请区、营销规则区和补充区。"""

    package_start = first_index(blocks, is_package_start_block)
    rule_start = first_index(blocks, is_rule_section_start)
    # 只有独立标题形式的“附件/附录”才算补充材料起点；正文里的“见附录填写”不能截断主文档。
    supplemental_start = first_index(blocks, is_supplemental_heading_block)

    application_end = min_existing(package_start, rule_start, supplemental_start, default=len(blocks))
    package_end = min_existing(rule_start, supplemental_start, default=len(blocks))
    rule_end = supplemental_start if supplemental_start is not None else len(blocks)
    package_area = blocks[package_start:package_end] if package_start is not None else []
    optional_start = first_index(package_area, is_optional_package_start_block)
    base_package_area = package_area[:optional_start] if optional_start is not None else package_area
    optional_package_area = package_area[optional_start:] if optional_start is not None else []

    return MarkdownDocumentSlices(
        head=select_document_head(blocks, stop_index=application_end),
        application=blocks[:application_end],
        package_area=package_area,
        base_package_area=base_package_area,
        optional_package_area=optional_package_area,
        rule_area=blocks[rule_start:rule_end] if rule_start is not None else blocks[application_end:rule_end],
        supplemental=[],
    )


def select_document_head(blocks: list[MarkdownBlock], *, stop_index: int) -> list[MarkdownBlock]:
    """只保留文档开头的标题/公司文本，不把客户字段表格塞进 document_info。"""

    result: list[MarkdownBlock] = []
    for block in blocks[:stop_index]:
        if block.block_type == "table_row":
            break
        result.append(block)
        if len(result) >= 4:
            break
    return result


def render_context(blocks: list[MarkdownBlock]) -> str:
    """渲染模块上下文；不再添加内部 # 标题，避免 debug 里标题重复。"""

    unique_blocks = dedupe_blocks(blocks)
    if not unique_blocks:
        return "<!-- 当前主文档没有适合该模块的 Markdown 片段。 -->"
    return "\n\n".join(block.text for block in unique_blocks).strip()


def render_module_contexts_debug(module_contexts: dict[str, str]) -> str:
    """生成调试文件，便于检查每个模块实际拿到的 Markdown。"""

    sections = ["# Product Document Module Contexts", ""]
    for module_name in EXTRACTION_MODULES:
        sections.extend(["", f"## {module_name}", "", module_contexts.get(module_name, "")])
    return "\n".join(sections)


def filter_blocks(blocks: list[MarkdownBlock], predicate: Callable[[MarkdownBlock], bool]) -> list[MarkdownBlock]:
    """过滤块；长规则块会先拆成更小的句子再判断归属。"""

    result: list[MarkdownBlock] = []
    for block in blocks:
        result.extend(candidate for candidate in split_long_rule_block(block) if predicate(candidate))
    return result


def split_long_rule_block(block: MarkdownBlock) -> list[MarkdownBlock]:
    """把长规则段拆成短句，降低跨模块重复。"""

    text = block.text.strip()
    if block.block_type == "table_row" or len(text) < 450:
        return [block]

    parts = split_rule_text(text)
    if len(parts) <= 1:
        return [block]
    return [MarkdownBlock(index=block.index, block_type="paragraph", text=part) for part in parts]


def split_rule_text(text: str) -> list[str]:
    """按条款编号、分号和句号拆分规则文本。"""

    normalized = re.sub(r"\s+", " ", text).strip()
    marker_pattern = re.compile(r"(?=(?:^|\s)(?:\d{1,2}|[一二三四五六七八九十]{1,3}|[a-zA-Z])[、.．）)]\s*)")
    marked_parts = clean_parts(marker_pattern.split(normalized))
    if len(marked_parts) > 1:
        return marked_parts
    return clean_parts(re.split(r"(?<=[。；;])\s*", normalized))


def normalize_markdown(markdown: str) -> str:
    """清理 MarkItDown 原文中的图片和多余空白。"""

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


def first_index(blocks: list[MarkdownBlock], predicate: Callable[[MarkdownBlock], bool]) -> int | None:
    for index, block in enumerate(blocks):
        if predicate(block):
            return index
    return None


def min_existing(*values: int | None, default: int) -> int:
    existing = [value for value in values if value is not None]
    return min(existing) if existing else default


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


def limit_markdown_context(markdown: str, *, max_chars: int) -> str:
    """按模块预算截断 Markdown。"""

    if len(markdown) <= max_chars:
        return markdown
    warning = "\n\n<!-- 内容超过当前模块 max_chars，后续 Markdown 已截断。 -->"
    budget = max(0, max_chars - len(warning))
    return markdown[:budget].rstrip() + warning


def contains_any(text: str, terms: tuple[str, ...]) -> bool:
    normalized = normalize_for_match(text)
    return any(normalize_for_match(term) in normalized for term in terms)


def normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def is_package_start_block(block: MarkdownBlock) -> bool:
    return contains_any(block.text, PACKAGE_START_TERMS)


def is_rule_section_start(block: MarkdownBlock) -> bool:
    return contains_any(block.text, RULE_SECTION_START_TERMS)


def is_supplemental_section_start(block: MarkdownBlock) -> bool:
    return contains_any(block.text, SUPPLEMENTAL_SECTION_START_TERMS)


def is_supplemental_heading_block(block: MarkdownBlock) -> bool:
    return block.block_type == "heading" and is_supplemental_section_start(block)


def is_optional_package_start_block(block: MarkdownBlock) -> bool:
    return contains_any(block.text, OPTIONAL_PACKAGE_START_TERMS)


def is_base_package_related(block: MarkdownBlock) -> bool:
    return contains_any(block.text, BASE_PACKAGE_TERMS) and not is_optional_package_related(block)


def is_optional_package_related(block: MarkdownBlock) -> bool:
    return contains_any(block.text, OPTIONAL_PACKAGE_TERMS)


def is_fee_related(block: MarkdownBlock) -> bool:
    return contains_any(block.text, FEE_TERMS)


def is_fee_rule(block: MarkdownBlock) -> bool:
    return contains_any(block.text, FEE_TERMS)


def is_agreement_rule(block: MarkdownBlock) -> bool:
    return contains_any(block.text, AGREEMENT_TERMS)


def is_material_rule(block: MarkdownBlock) -> bool:
    return contains_any(block.text, MATERIAL_TERMS)


def is_constraint_rule(block: MarkdownBlock) -> bool:
    return contains_any(block.text, CONSTRAINT_TERMS)


def clean_parts(parts: list[str]) -> list[str]:
    return [part.strip() for part in parts if part and part.strip()]


PACKAGE_START_TERMS = (
    "基础套餐申请信息",
    "套餐申请信息",
    "产品申请信息",
    "业务申请信息",
    "基础套餐",
)

RULE_SECTION_START_TERMS = (
    "填表说明",
    "填写说明",
    "办理说明",
    "注意事项",
    "营销活动规则",
    "业务服务协议",
    "服务协议",
    "客户承诺",
)

SUPPLEMENTAL_SECTION_START_TERMS = (
    "附件",
    "附录",
    "补充协议",
    "补充规则",
)

BASE_PACKAGE_TERMS = (
    "基础套餐",
    "套餐类型",
    "速率",
    "带宽",
    "上行",
    "下行",
    "接口标准",
    "sla",
)

OPTIONAL_PACKAGE_TERMS = (
    "可选",
    "权益",
    "增值",
    "配套",
    "升速包",
    "上行升速",
    "固话",
    "商云通",
    "移动业务",
    "云电脑",
    "企业云盘",
    "安全大脑",
    "入云专线",
    "wifi",
)

OPTIONAL_PACKAGE_START_TERMS = (
    "可选增值服务",
    "可选产品申请信息",
    "可选产品",
    "可选包",
    "增值服务",
    "固话",
    "商云通",
    "移动业务",
    "上行升速包",
    "天翼企业云盘",
    "员工居家办公",
    "天翼安全大脑",
    "入云专线",
)

FEE_TERMS = (
    "元",
    "费用",
    "资费",
    "月费",
    "月基本费",
    "月租",
    "年付",
    "月付",
    "一次性",
    "押金",
    "安装",
    "调测",
    "手续费",
    "折扣",
    "协议期",
    "违约金",
    "赔偿",
    "按天",
)

AGREEMENT_TERMS = (
    "协议",
    "合同",
    "违约",
    "退订",
    "注销",
    "拆机",
    "赔偿",
    "售后",
    "保修",
    "实名",
    "承诺",
    "终止",
    "暂停",
    "解除",
    "责任",
)

MATERIAL_TERMS = (
    "营业执照",
    "身份证",
    "授权",
    "委托书",
    "担保",
    "申请表",
    "承诺书",
    "告知书",
    "备案表",
    "拓扑图",
    "复印件",
    "盖章",
    "签字",
    "材料",
    "证件",
)

CONSTRAINT_TERMS = (
    "仅限",
    "不适用",
    "不能",
    "不得",
    "不可",
    "必须",
    "条件",
    "要求",
    "限制",
    "欠费",
    "停用",
    "停止",
    "同名",
    "同址",
    "合并开账",
    "实名",
)
