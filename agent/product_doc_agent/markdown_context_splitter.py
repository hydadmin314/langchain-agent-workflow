from __future__ import annotations

import json
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
    source_file: str = ""
    source_path: str = ""
    doc_role: str = ""


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
    3. 如果输入中包含 product_doc_source 注释，则按文件角色做产品包级证据路由。
    4. base_package 和 optional_packages 仍优先取申请表套餐区，避免越界到后面的营销规则。
    5. fee_and_term_rules 同时取套餐区、资费表和规则区中所有金额、费用、协议期、违约金相关内容。
    """

    blocks = parse_markdown_blocks(markdown)
    slices = build_document_slices(blocks)
    package_mode = has_source_metadata(blocks)

    if package_mode:
        return build_package_module_contexts(blocks, max_chars=max_chars)

    contexts = {
        "document_info": render_context(
            [
                *slices.head,
                *filter_blocks(blocks, is_document_info_context),
            ],
            include_source_comments=package_mode,
        ),
        "parties_and_application": render_context(
            [
                *slices.head,
                *slices.application,
                *filter_blocks(blocks, is_parties_context),
            ],
            include_source_comments=package_mode,
        ),
        "base_package": render_context(slices.base_package_area),
        "optional_packages": render_context(
            [
                *slices.optional_package_area,
                *filter_blocks(blocks, is_optional_package_context),
            ],
            include_source_comments=package_mode,
        ),
        "fee_and_term_rules": render_context(
            [
                *filter_blocks(slices.package_area, is_fee_related),
                *filter_blocks(slices.rule_area, is_fee_rule),
                *filter_blocks(blocks, is_fee_context),
            ],
            include_source_comments=package_mode,
        ),
        "agreement_rules": render_context(
            [
                *filter_blocks(slices.rule_area, is_agreement_rule),
                *filter_blocks(blocks, is_agreement_context),
            ],
            include_source_comments=package_mode,
        ),
        "application_materials": render_context(
            [
                *filter_blocks(slices.rule_area, is_material_rule),
                *filter_blocks(blocks, is_material_context),
            ],
            include_source_comments=package_mode,
        ),
        "eligibility_and_constraints": render_context(
            [
                *filter_blocks(slices.rule_area, is_constraint_rule),
                *filter_blocks(blocks, is_constraint_context),
            ],
            include_source_comments=package_mode,
        ),
        # 产品介绍、关键词、内部流程、模板类文件的补充事实先进入 supplemental_rules。
        # 等正式 schema 升级到 supplementary_info 后，再拆成 product_intro/product_keywords/pricing_info。
        "supplemental_rules": render_context(
            [
                *slices.supplemental,
                *filter_blocks(blocks, is_supplemental_context),
            ],
            include_source_comments=package_mode,
        ),
    }

    # 多文件产品包里，基础套餐通常散落在申请表、产品介绍、资费表中；
    # 保留旧 slices 的同时补一层角色/关键词召回，避免只抓到申请表空白字段。
    contexts["base_package"] = render_context(
        [
            *slices.base_package_area,
            *filter_blocks(blocks, is_base_package_context),
        ],
        include_source_comments=package_mode,
    )

    return {
        module_name: limit_markdown_context(
            contexts.get(module_name, ""),
            max_chars=min(max_chars, MODULE_CONTEXT_CHAR_LIMITS[module_name]),
        )
        for module_name in EXTRACTION_MODULES
    }


def build_package_module_contexts(blocks: list[MarkdownBlock], *, max_chars: int) -> dict[str, str]:
    """产品资料包模式：完全按文件角色和关键词路由，不沿用单文档的位置切片。"""

    contexts = {
        "document_info": render_package_context(blocks, "document_info", is_document_info_context),
        "parties_and_application": render_package_context(blocks, "parties_and_application", is_parties_context),
        "base_package": render_package_context(blocks, "base_package", is_base_package_context),
        "optional_packages": render_package_context(blocks, "optional_packages", is_optional_package_context),
        "fee_and_term_rules": render_package_context(blocks, "fee_and_term_rules", is_fee_context),
        "agreement_rules": render_package_context(blocks, "agreement_rules", is_agreement_context),
        "application_materials": render_package_context(blocks, "application_materials", is_material_context),
        "eligibility_and_constraints": render_package_context(blocks, "eligibility_and_constraints", is_constraint_context),
        "supplemental_rules": render_package_context(blocks, "supplemental_rules", is_supplemental_context),
    }

    return {
        module_name: limit_markdown_context(
            contexts.get(module_name, ""),
            max_chars=min(max_chars, MODULE_CONTEXT_CHAR_LIMITS[module_name]),
        )
        for module_name in EXTRACTION_MODULES
    }


def render_package_context(
    blocks: list[MarkdownBlock],
    module_name: str,
    predicate: Callable[[MarkdownBlock], bool],
) -> str:
    """产品包模块上下文渲染：先过滤，再按该模块最可信的文件角色排序。"""

    selected = filter_blocks(blocks, predicate)
    selected = prioritize_blocks_for_module(module_name, selected)
    return render_context(selected, include_source_comments=True)


def prioritize_blocks_for_module(module_name: str, blocks: list[MarkdownBlock]) -> list[MarkdownBlock]:
    """按模块给来源角色排优先级，避免低价值申请表长规则挤掉高价值补充文件。"""

    role_order = MODULE_ROLE_PRIORITY.get(module_name, ())
    if not role_order:
        return blocks
    priority = {role: index for index, role in enumerate(role_order)}
    fallback = len(role_order)
    return sorted(blocks, key=lambda block: (priority.get(block.doc_role, fallback), block.index))


def parse_markdown_blocks(markdown: str) -> list[MarkdownBlock]:
    """按 Markdown 结构切块；表格按数据行拆细。"""

    blocks: list[MarkdownBlock] = []
    buffer: list[str] = []
    buffer_type = ""
    current_source: dict[str, str] = {}

    def flush() -> None:
        nonlocal buffer, buffer_type
        text = "\n".join(line for line in buffer if line.strip()).strip()
        if text:
            append_markdown_block(
                blocks,
                buffer_type or "paragraph",
                text,
                source_file=current_source.get("source_file", ""),
                source_path=current_source.get("source_path", ""),
                doc_role=current_source.get("doc_role", ""),
            )
        buffer = []
        buffer_type = ""

    for raw_line in normalize_markdown(markdown).splitlines():
        line = raw_line.rstrip()
        source_meta = parse_source_marker(line)
        if source_meta is not None:
            flush()
            current_source = source_meta
            continue

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


def append_markdown_block(
    blocks: list[MarkdownBlock],
    block_type: str,
    text: str,
    *,
    source_file: str = "",
    source_path: str = "",
    doc_role: str = "",
) -> None:
    """追加 Markdown 块；表格拆成行，避免一个大表格同时落入多个模块。"""

    if block_type != "table":
        blocks.append(
            MarkdownBlock(
                index=len(blocks),
                block_type=block_type,
                text=text,
                source_file=source_file,
                source_path=source_path,
                doc_role=doc_role,
            )
        )
        return

    for row in (line for line in text.splitlines() if line.strip()):
        if is_markdown_separator_row(row) or is_empty_table_row(row):
            continue
        blocks.append(
            MarkdownBlock(
                index=len(blocks),
                block_type="table_row",
                text=row,
                source_file=source_file,
                source_path=source_path,
                doc_role=doc_role,
            )
        )


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


def render_context(blocks: list[MarkdownBlock], *, include_source_comments: bool = False) -> str:
    """渲染模块上下文；不再添加内部 # 标题，避免 debug 里标题重复。"""

    unique_blocks = dedupe_blocks(blocks)
    if not unique_blocks:
        return "<!-- 当前主文档没有适合该模块的 Markdown 片段。 -->"
    return "\n\n".join(
        render_block_with_optional_source(block, include_source_comments=include_source_comments)
        for block in unique_blocks
    ).strip()


def render_block_with_optional_source(block: MarkdownBlock, *, include_source_comments: bool) -> str:
    """产品包模式下，在块前加轻量来源注释，帮助 LLM 区分资费表/手续/合同等来源。"""

    if not include_source_comments or not (block.source_file or block.doc_role):
        return block.text
    source_parts = []
    if block.source_file:
        source_parts.append(f"source_file={block.source_file}")
    if block.doc_role:
        source_parts.append(f"doc_role={block.doc_role}")
    return f"<!-- {'; '.join(source_parts)} -->\n{block.text}"


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
    # MarkItDown 转 xlsx 时空单元格有时会渲染成 NaN；这里当作空格处理，避免干扰资费行判断。
    text = re.sub(r"\bNaN\b", "", text)
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
        key = "|".join(
            (
                normalize_for_match(block.doc_role),
                normalize_for_match(block.source_file),
                normalize_for_match(block.text),
            )
        )
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


def parse_source_marker(line: str) -> dict[str, str] | None:
    """解析产品包合并 Markdown 中的来源注释。

    支持格式：
    <!-- product_doc_source: {"source_file":"2 资费表.xlsx","doc_role":"pricing_table"} -->
    """

    stripped = line.strip()
    match = re.fullmatch(r"<!--\s*product_doc_source:\s*(\{.*\})\s*-->", stripped)
    if not match:
        return None
    try:
        raw = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        "source_file": str(raw.get("source_file", "") or ""),
        "source_path": str(raw.get("source_path", "") or ""),
        "doc_role": str(raw.get("doc_role", "") or ""),
    }


def has_source_metadata(blocks: list[MarkdownBlock]) -> bool:
    return any(block.source_file or block.doc_role for block in blocks)


def has_role(block: MarkdownBlock, *roles: str) -> bool:
    return block.doc_role in roles


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


def is_document_info_context(block: MarkdownBlock) -> bool:
    if has_role(block, "product_intro", "product_keywords", "application_form"):
        return block.block_type == "heading" or contains_any(block.text, DOCUMENT_INFO_TERMS)
    if block.doc_role:
        return False
    return contains_any(block.text, DOCUMENT_INFO_TERMS)


def is_parties_context(block: MarkdownBlock) -> bool:
    if has_role(block, "application_form", "customer_info_form", "procedure_hint"):
        return contains_any(block.text, PARTIES_TERMS)
    if block.doc_role:
        return False
    return contains_any(block.text, PARTIES_TERMS)


def is_base_package_context(block: MarkdownBlock) -> bool:
    if has_role(block, "product_intro"):
        return contains_any(block.text, (*BASE_PACKAGE_TERMS, *PRODUCT_INTRO_TERMS))
    if has_role(block, "pricing_table"):
        return contains_any(block.text, (*BASE_PACKAGE_TERMS, *BUSINESS_FEE_TERMS, *FEE_TERMS))
    if has_role(block, "application_form"):
        return contains_any(block.text, BASE_PACKAGE_TERMS)
    if block.doc_role:
        return False
    return is_base_package_related(block)


def is_optional_package_context(block: MarkdownBlock) -> bool:
    if has_role(block, "pricing_table", "application_form", "product_intro"):
        return contains_any(block.text, OPTIONAL_PACKAGE_TERMS)
    if block.doc_role:
        return False
    return is_optional_package_related(block)


def is_fee_context(block: MarkdownBlock) -> bool:
    if has_role(block, "pricing_table"):
        return contains_any(block.text, (*FEE_TERMS, *BUSINESS_FEE_TERMS, *ONE_TIME_FEE_TERMS))
    if has_role(block, "application_form", "contract_rule", "procedure_hint", "termination_rule", "change_rule"):
        return contains_any(block.text, FEE_TERMS)
    if block.doc_role:
        return False
    return is_fee_rule(block)


def is_agreement_context(block: MarkdownBlock) -> bool:
    if has_role(block, "contract_rule", "change_rule", "termination_rule", "risk_notice", "application_form"):
        return contains_any(block.text, AGREEMENT_TERMS)
    return is_agreement_rule(block)


def is_material_context(block: MarkdownBlock) -> bool:
    if has_role(
        block,
        "procedure_hint",
        "guarantee_template",
        "authorization_template",
        "customer_info_form",
        "risk_notice",
        "contract_rule",
        "application_form",
        "termination_rule",
    ):
        return contains_any(block.text, MATERIAL_TERMS)
    return is_material_rule(block)


def is_constraint_context(block: MarkdownBlock) -> bool:
    if has_role(
        block,
        "procedure_hint",
        "guarantee_template",
        "risk_notice",
        "contract_rule",
        "termination_rule",
        "change_rule",
        "application_form",
    ):
        return contains_any(block.text, (*CONSTRAINT_TERMS, *RISK_TERMS))
    return is_constraint_rule(block)


def is_supplemental_context(block: MarkdownBlock) -> bool:
    if has_role(block, "product_intro", "product_keywords", "internal_process", "material_template"):
        return True
    if has_role(block, "procedure_hint", "risk_notice"):
        return contains_any(block.text, (*PROCEDURE_TERMS, *RISK_TERMS))
    return is_supplemental_section_start(block)


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

DOCUMENT_INFO_TERMS = (
    "产品名称",
    "产品介绍",
    "产品说明",
    "业务名称",
    "营销活动名称",
    "生效",
    "起",
    "版本",
    "适用区域",
    "电信",
    "联通",
    "移动",
)

PARTIES_TERMS = (
    "客户",
    "企业",
    "企业全称",
    "企业代码",
    "统一社会信用代码",
    "所属行业",
    "企业规模",
    "计算机数量",
    "经办人",
    "身份证",
    "身份证号码",
    "联系人",
    "联系地址",
    "联系电话",
    "电话",
    "安装地址",
    "邮编",
    "e-mail",
    "email",
    "邮箱",
    "传真",
    "营业执照",
    "付款方式",
    "账单",
    "服务商",
)

PRODUCT_INTRO_TERMS = (
    "产品介绍",
    "产品定义",
    "应用场景",
    "适用场景",
    "产品优势",
    "业务特点",
    "目标客户",
)

BUSINESS_FEE_TERMS = (
    "月租",
    "月费",
    "月基本费",
    "年付",
    "两年付",
    "2年付",
    "授权价",
    "端口费",
    "长途电路",
    "标准资费",
)

ONE_TIME_FEE_TERMS = (
    "初装费",
    "接入费",
    "一次性",
    "新装",
    "升降速",
    "移机",
    "调测费",
    "安装费",
)

PROCEDURE_TERMS = (
    "申请手续",
    "办理手续",
    "办理材料",
    "所需材料",
    "提交材料",
    "机打",
    "盖章",
    "签字",
)

RISK_TERMS = (
    "风险",
    "免责",
    "告知",
    "担保",
    "押金",
    "外地公司",
    "承诺",
    "责任",
)

MODULE_ROLE_PRIORITY: dict[str, tuple[str, ...]] = {
    "document_info": (
        "application_form",
        "product_intro",
        "product_keywords",
        "pricing_table",
    ),
    "parties_and_application": (
        "application_form",
        "customer_info_form",
        "procedure_hint",
    ),
    "base_package": (
        "pricing_table",
        "application_form",
        "product_intro",
    ),
    "optional_packages": (
        "pricing_table",
        "application_form",
        "product_intro",
    ),
    "fee_and_term_rules": (
        "pricing_table",
        "application_form",
        "contract_rule",
        "change_rule",
        "termination_rule",
        "procedure_hint",
    ),
    "agreement_rules": (
        "contract_rule",
        "risk_notice",
        "termination_rule",
        "change_rule",
        "application_form",
    ),
    "application_materials": (
        "procedure_hint",
        "authorization_template",
        "guarantee_template",
        "customer_info_form",
        "risk_notice",
        "termination_rule",
        "application_form",
        "contract_rule",
        "change_rule",
    ),
    "eligibility_and_constraints": (
        "procedure_hint",
        "guarantee_template",
        "risk_notice",
        "contract_rule",
        "application_form",
        "pricing_table",
        "change_rule",
        "termination_rule",
    ),
    "supplemental_rules": (
        "product_intro",
        "product_keywords",
        "procedure_hint",
        "risk_notice",
        "internal_process",
        "material_template",
    ),
}
