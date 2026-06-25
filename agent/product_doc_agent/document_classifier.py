from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


APPLICATION_FORM = "application_form"
PRODUCT_INTRO = "product_intro"
PRODUCT_KEYWORDS = "product_keywords"
PRICING_SHEET = "pricing_sheet"
APPLICATION_MATERIALS = "application_materials"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class DocumentRoleRule:
    """文件名分类规则：命中 pattern 后归入对应 role。"""

    role: str
    pattern: str
    description: str


@dataclass(frozen=True)
class DocumentClassification:
    """单个文档的分类结果，以及后续应该抽取的新 schema 模块。"""

    source_path: str
    filename: str
    normalized_name: str
    role: str
    target_modules: tuple[str, ...]
    matched_pattern: str = ""
    description: str = ""


ROLE_TARGET_MODULES: dict[str, tuple[str, ...]] = {
    # 申请表负责 schema 中 application_form_info 下的五个模块。
    APPLICATION_FORM: (
        "application_form_info.document_info",
        "application_form_info.parties_and_application",
        "application_form_info.pricing_info",
        "application_form_info.agreement_rules",
        "application_form_info.eligibility_and_constraints",
    ),
    # 补充资料按文件角色进入 supplementary_info 下的对应模块。
    PRODUCT_INTRO: ("supplementary_info.product_intro",),
    PRODUCT_KEYWORDS: ("supplementary_info.product_keywords",),
    PRICING_SHEET: ("supplementary_info.pricing_info",),
    APPLICATION_MATERIALS: ("supplementary_info.application_materials",),
    UNKNOWN: (),
}


DEFAULT_ROLE_RULES: tuple[DocumentRoleRule, ...] = (
    # 顺序刻意收窄：先匹配更具体的“申请手续提示”，避免后续扩展“申请”时误判为申请表。
    DocumentRoleRule(APPLICATION_MATERIALS, r"(申请手续提示|申请手续|手续提示)", "申请手续提示"),
    DocumentRoleRule(PRODUCT_KEYWORDS, r"(关键字|关键词)", "产品关键词"),
    DocumentRoleRule(PRICING_SHEET, r"(资费表|资费)", "资费表"),
    DocumentRoleRule(PRODUCT_INTRO, r"(产品介绍|产品说明|业务介绍)", "产品介绍"),
    DocumentRoleRule(APPLICATION_FORM, r"(申请表|申请登记表|新装申请表)", "申请表"),
)


def normalize_document_name(name: str | Path) -> str:
    """把已归一化文件名再做轻量清洗，方便稳定正则匹配。"""

    path = Path(name)
    stem = path.stem if path.suffix else str(name)
    normalized = stem.strip()
    normalized = re.sub(r"^\s*\d+(?:[.\-_－—]\d+)*[）)、.．\-_－—\s]*", "", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized


def classify_document(
    path: str | Path,
    *,
    rules: Iterable[DocumentRoleRule] = DEFAULT_ROLE_RULES,
) -> DocumentClassification:
    """根据文件名正则判断文档角色；未命中时保持 unknown，避免误抽。"""

    source = Path(path)
    filename = source.name
    if filename.startswith("~$"):
        return DocumentClassification(
            source_path=str(source),
            filename=filename,
            normalized_name=normalize_document_name(filename),
            role=UNKNOWN,
            target_modules=get_target_modules(UNKNOWN),
        )

    normalized_name = normalize_document_name(filename)

    for rule in rules:
        if re.search(rule.pattern, normalized_name, flags=re.IGNORECASE):
            return DocumentClassification(
                source_path=str(source),
                filename=filename,
                normalized_name=normalized_name,
                role=rule.role,
                target_modules=get_target_modules(rule.role),
                matched_pattern=rule.pattern,
                description=rule.description,
            )

    return DocumentClassification(
        source_path=str(source),
        filename=filename,
        normalized_name=normalized_name,
        role=UNKNOWN,
        target_modules=get_target_modules(UNKNOWN),
    )


def get_target_modules(role: str) -> tuple[str, ...]:
    """把文档角色映射为需要抽取的新 schema 模块。"""

    return ROLE_TARGET_MODULES.get(role, ROLE_TARGET_MODULES[UNKNOWN])


__all__ = [
    "APPLICATION_FORM",
    "APPLICATION_MATERIALS",
    "DEFAULT_ROLE_RULES",
    "DocumentClassification",
    "DocumentRoleRule",
    "PRICING_SHEET",
    "PRODUCT_INTRO",
    "PRODUCT_KEYWORDS",
    "ROLE_TARGET_MODULES",
    "UNKNOWN",
    "classify_document",
    "get_target_modules",
    "normalize_document_name",
]
