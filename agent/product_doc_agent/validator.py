from __future__ import annotations

import re
from typing import Any

from schema import PRODUCT_DOCUMENT_JSON_SCHEMA


ALLOWED_META_KEYS = {"generated_at", "source_file", "method", "validation_issues", "validation_issue_count"}
ALLOWED_CURRENCIES = {"", "CNY", None}


class ProductDocumentValidator:
    """新 schema 的确定性校验器。

    校验只发现结构、来源、资费和关键模块完整性问题，不做大模型语义判断。
    """

    def validate(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        issues = validate_against_schema(product_document, PRODUCT_DOCUMENT_JSON_SCHEMA)
        issues.extend(validate_extraction_meta(product_document))
        issues.extend(validate_source_files(product_document))
        issues.extend(validate_pricing(product_document))
        issues.extend(validate_required_module_completeness(product_document))
        issues.extend(validate_cross_source_pricing_duplicates(product_document))
        return issues

    def attach_issues(self, product_document: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
        meta = product_document.setdefault("extraction_meta", {})
        existing = meta.get("validation_issues", [])
        if not isinstance(existing, list):
            existing = []
        meta["validation_issues"] = [*existing, *issues]
        meta["validation_issue_count"] = len(meta["validation_issues"])
        return product_document


def validate_extraction_meta(product_document: dict[str, Any]) -> list[dict[str, Any]]:
    meta = product_document.get("extraction_meta")
    if not isinstance(meta, dict):
        return [make_issue("error", "extraction_meta", "extraction_meta 必须是对象")]

    issues: list[dict[str, Any]] = []
    extra_keys = sorted(set(meta) - ALLOWED_META_KEYS)
    for key in extra_keys:
        issues.append(make_issue("error", f"extraction_meta.{key}", "extraction_meta 出现未定义字段"))
    for key in ALLOWED_META_KEYS:
        if key not in meta:
            issues.append(make_issue("error", f"extraction_meta.{key}", "extraction_meta 缺少必需字段"))
    validation_issues = meta.get("validation_issues", [])
    if isinstance(validation_issues, list) and meta.get("validation_issue_count") != len(validation_issues):
        issues.append(make_issue("warning", "extraction_meta.validation_issue_count", "validation_issue_count 与问题数量不一致"))
    return issues


def validate_source_files(product_document: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for path, value in iter_nodes(product_document):
        if not isinstance(value, dict) or "source_file" not in value:
            continue
        source_file = str(value.get("source_file") or "").strip()
        if not source_file:
            issues.append(make_issue("warning", f"{path}.source_file", "缺少来源文件"))
        elif looks_like_title_instead_of_file(source_file):
            issues.append(make_issue("warning", f"{path}.source_file", "source_file 疑似被模型填成标题而不是真实文件路径"))
    return issues


def validate_pricing(product_document: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    pricing_paths = [
        ("application_form_info.pricing_info", product_document.get("application_form_info", {}).get("pricing_info", {})),
        ("supplementary_info.pricing_info", product_document.get("supplementary_info", {}).get("pricing_info", {})),
    ]
    for pricing_path, pricing_info in pricing_paths:
        if not isinstance(pricing_info, dict):
            issues.append(make_issue("error", pricing_path, "资费模块必须是对象"))
            continue
        for list_key in ("one_time_fees", "base_package_prices", "addon_prices", "fee_and_term_rules", "discount_policy"):
            items = pricing_info.get(list_key, [])
            if not isinstance(items, list):
                issues.append(make_issue("error", f"{pricing_path}.{list_key}", "资费列表必须是数组"))
                continue
            for index, item in enumerate(items):
                if isinstance(item, dict):
                    issues.extend(validate_pricing_item(item, f"{pricing_path}.{list_key}[{index}]", list_key))
    return issues


def validate_pricing_item(item: dict[str, Any], path: str, list_key: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for key in ("price", "amount", "standard_price", "discount_rate", "discounted_price"):
        if key in item and item.get(key) is not None and not is_number(item.get(key)):
            issues.append(make_issue("error", f"{path}.{key}", "金额或数字字段必须是 number 或 null"))
    if "currency" in item and item.get("currency") not in ALLOWED_CURRENCIES:
        issues.append(make_issue("warning", f"{path}.currency", "币种应归一化为 CNY 或空值"))
    if list_key in {"base_package_prices", "addon_prices"} and item.get("price") is not None:
        if not str(item.get("billing_period") or "").strip():
            issues.append(make_issue("warning", f"{path}.billing_period", "价格不为空时应有计费周期"))
        if not str(item.get("unit") or "").strip():
            issues.append(make_issue("warning", f"{path}.unit", "价格不为空时应有价格单位"))
    if list_key == "base_package_prices" and not str(item.get("speed") or "").strip():
        issues.append(make_issue("warning", f"{path}.speed", "基础套餐缺少速率"))
    return issues


def validate_required_module_completeness(product_document: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    app = product_document.get("application_form_info", {})
    sup = product_document.get("supplementary_info", {})
    if not isinstance(app, dict) or not isinstance(sup, dict):
        return issues

    document_info = app.get("document_info", {})
    parties = app.get("parties_and_application", {})
    if isinstance(document_info, dict) and is_mostly_empty_object(document_info, ignore_keys={"document_id", "source_file"}):
        issues.append(make_issue("warning", "application_form_info.document_info", "申请表基本信息基本为空，可能是切块或文档转换质量不足"))
    fields = parties.get("application_fields", {}) if isinstance(parties, dict) else {}
    required = fields.get("required", []) if isinstance(fields, dict) else []
    optional = fields.get("optional", []) if isinstance(fields, dict) else []
    if not required and not optional:
        issues.append(make_issue("warning", "application_form_info.parties_and_application.application_fields", "申请表没有抽到字段信息"))

    if is_mostly_empty_object(sup.get("product_intro", {})):
        issues.append(make_issue("warning", "supplementary_info.product_intro", "产品介绍为空"))
    if is_mostly_empty_object(sup.get("product_keywords", {})):
        issues.append(make_issue("warning", "supplementary_info.product_keywords", "产品关键词为空"))
    pricing = sup.get("pricing_info", {})
    if isinstance(pricing, dict) and not any(pricing.get(key) for key in ("one_time_fees", "base_package_prices", "addon_prices")):
        issues.append(make_issue("warning", "supplementary_info.pricing_info", "资费表没有抽到有效资费"))
    return issues


def validate_cross_source_pricing_duplicates(product_document: dict[str, Any]) -> list[dict[str, Any]]:
    app_pricing = product_document.get("application_form_info", {}).get("pricing_info", {})
    sup_pricing = product_document.get("supplementary_info", {}).get("pricing_info", {})
    if not isinstance(app_pricing, dict) or not isinstance(sup_pricing, dict):
        return []

    issues: list[dict[str, Any]] = []
    for key in ("one_time_fees", "base_package_prices", "addon_prices", "discount_policy"):
        app_items = app_pricing.get(key, [])
        sup_items = sup_pricing.get(key, [])
        if not isinstance(app_items, list) or not isinstance(sup_items, list):
            continue
        sup_signatures = {cross_source_pricing_signature(item, key) for item in sup_items if isinstance(item, dict)}
        for index, item in enumerate(app_items):
            if isinstance(item, dict) and cross_source_pricing_signature(item, key) in sup_signatures:
                issues.append(
                    make_issue(
                        "warning",
                        f"application_form_info.pricing_info.{key}[{index}]",
                        "与补充资费表重复；归一化后应优先保留资费表",
                    )
                )
    return issues


def validate_against_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    expected_type = schema.get("type")
    if not matches_type(value, expected_type):
        return [make_issue("error", path, f"类型不匹配，期望 {expected_type}，实际 {type(value).__name__}")]

    effective_type = effective_schema_type(expected_type, value)
    if effective_type == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                issues.append(make_issue("error", f"{path}.{key}", "缺少 schema 必需字段"))
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    issues.append(make_issue("error", f"{path}.{key}", "出现 schema 未定义字段"))
        for key, child in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                issues.extend(validate_against_schema(child, child_schema, f"{path}.{key}"))
    elif effective_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                issues.extend(validate_against_schema(item, item_schema, f"{path}[{index}]"))
    return issues


def iter_nodes(value: Any, path: str = "$"):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from iter_nodes(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_nodes(child, f"{path}[{index}]")


def matches_type(value: Any, expected_type: Any) -> bool:
    if expected_type is None:
        return True
    if isinstance(expected_type, list):
        return any(matches_single_type(value, item) for item in expected_type)
    return matches_single_type(value, expected_type)


def matches_single_type(value: Any, expected_type: str) -> bool:
    if expected_type == "null":
        return value is None
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return is_number(value)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    return True


def effective_schema_type(expected_type: Any, value: Any) -> str | None:
    if isinstance(expected_type, str):
        return expected_type
    if not isinstance(expected_type, list):
        return None
    for item in expected_type:
        if matches_single_type(value, item):
            return item
    return None


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def looks_like_title_instead_of_file(value: str) -> bool:
    if re.search(r"[\\/]", value) or re.search(r"\.(docx?|xlsx?|pdf|txt)$", value, flags=re.IGNORECASE):
        return False
    return len(value) >= 20


def is_mostly_empty_object(value: Any, *, ignore_keys: set[str] | None = None) -> bool:
    if not isinstance(value, dict):
        return True
    ignore_keys = ignore_keys or set()
    for key, child in value.items():
        if key in ignore_keys:
            continue
        if isinstance(child, dict):
            if not is_mostly_empty_object(child):
                return False
        elif isinstance(child, list):
            if child:
                return False
        elif child not in ("", None, False):
            return False
    return True


def pricing_signature(item: dict[str, Any]) -> str:
    parts = [
        item.get("name", ""),
        item.get("speed", ""),
        item.get("spec", ""),
        item.get("price", item.get("amount", "")),
        item.get("billing_period", item.get("period", "")),
        item.get("unit", ""),
    ]
    return re.sub(r"\s+", "", "|".join(str(part) for part in parts if part not in ("", None)))


def cross_source_pricing_signature(item: dict[str, Any], list_key: str) -> str:
    if list_key == "base_package_prices":
        parts = [normalize_speed_for_signature(item.get("speed", "")), item.get("price", ""), item.get("billing_period", "")]
    elif list_key == "one_time_fees":
        parts = [item.get("amount", ""), item.get("unit", "")]
    else:
        return pricing_signature(item)
    return re.sub(r"\s+", "", "|".join(str(part) for part in parts if part not in ("", None)))


def make_issue(severity: str, path: str, message: str) -> dict[str, str]:
    return {"severity": severity, "path": path, "message": message}


def normalize_speed_for_signature(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "")).upper()
    symmetric = re.fullmatch(r"(\d+(?:\.\d+)?[MG])/\1", text)
    if symmetric:
        return symmetric.group(1)
    slash_match = re.fullmatch(r"(\d+(?:\.\d+)?[MG])/(\d+(?:\.\d+)?[MG])", text)
    if slash_match and slash_match.group(1) == slash_match.group(2):
        return slash_match.group(1)
    return text
