from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from schema import TOP_LEVEL_SCHEMA_KEYS, get_module_json_schema


SUPPLEMENTARY_SOURCE_HINTS = {
    "product_intro": ("产品介绍", "產品介紹", "intro"),
    "product_keywords": ("关键词", "关键字", "關鍵詞", "keyword"),
    "pricing_info": ("资费表", "資費表", "price", "pricing", ".xlsx", ".xls"),
    "application_materials": ("申请手续提示", "申请材料", "手续提示", "materials"),
}


@dataclass(frozen=True)
class ProductDocumentNormalizationResult:
    product_document: dict[str, Any]
    issues: list[dict[str, Any]]


def normalize_to_module_schema(module_name: str, value: Any) -> Any:
    """把单模块输出对齐到模块 schema，供 LLM 抽取阶段复用。"""

    schema = get_module_json_schema(module_name)
    unwrapped = unwrap_module_payload(module_name, value, schema)
    return normalize_value(unwrapped, schema)


def unwrap_module_payload(module_name: str, value: Any, schema: dict[str, Any]) -> Any:
    """兼容模型额外包了一层模块名的情况。"""

    if not isinstance(value, dict):
        return value
    wrapper_keys = [module_name, module_name.rsplit(".", 1)[-1]]
    wrapper_key = next((key for key in wrapper_keys if key in value), "")
    if not wrapper_key:
        return value

    inner = value[wrapper_key]
    expected_type = schema.get("type")
    if expected_type == "array" and isinstance(inner, list):
        return inner
    if expected_type == "object" and isinstance(inner, dict):
        return inner
    return value


def normalize_value(value: Any, schema: dict[str, Any]) -> Any:
    """按 JSON schema 做轻量类型兜底，不新增业务事实。"""

    expected_type = schema.get("type")
    effective_type = choose_type(expected_type, value)

    if effective_type == "object":
        return normalize_object(value, schema)
    if effective_type == "array":
        return normalize_array(value, schema)
    if effective_type == "string":
        return "" if value is None else str(value)
    if effective_type == "number":
        return normalize_number(value)
    if effective_type == "integer":
        number = normalize_number(value)
        return int(number) if number is not None else 0
    if effective_type == "boolean":
        return value if isinstance(value, bool) else False
    if effective_type == "null":
        return None
    return value


def normalize_object(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return dict(value)

    result: dict[str, Any] = {}
    for key, child_schema in properties.items():
        result[key] = normalize_value(value.get(key), child_schema)
    return result


def normalize_array(value: Any, schema: dict[str, Any]) -> list[Any]:
    if value is None:
        items: list[Any] = []
    elif isinstance(value, list):
        items = value
    else:
        items = [value]

    item_schema = schema.get("items")
    if not isinstance(item_schema, dict):
        return items
    return [normalize_value(item, item_schema) for item in items]


def choose_type(expected_type: Any, value: Any) -> str | None:
    if isinstance(expected_type, str):
        return expected_type
    if not isinstance(expected_type, list):
        return None
    if value is None and "null" in expected_type:
        return "null"
    for item in expected_type:
        if item != "null" and matches_type(value, item):
            return item
    if isinstance(value, str) and "number" in expected_type and re.search(r"-?\d+(?:\.\d+)?", value):
        return "number"
    non_null = [item for item in expected_type if item != "null"]
    return non_null[0] if non_null else "null"


def matches_type(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    return value is None if expected_type == "null" else True


def normalize_number(value: Any) -> float | None:
    if value in ("", None, "NaN", "nan"):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if match:
            return float(match.group(0))
    return None


class ProductDocumentNormalizer:
    """新 schema 的轻量归一化器。

    归一化只做确定性清洗和去重；当申请表与补充资料重复时，以产品介绍、关键词、资费表、
    申请手续提示这四类专门文件为主，申请表内容只作为兜底或保留在申请表模块中。
    """

    def normalize_product_document(self, product_document: dict[str, Any]) -> ProductDocumentNormalizationResult:
        data = deepcopy(product_document)
        issues: list[dict[str, Any]] = []

        normalize_extraction_meta(data)
        issues.extend(normalize_string_noise(data))
        issues.extend(normalize_pricing_info(data.get("application_form_info", {}).get("pricing_info", {}), "application_form_info.pricing_info"))
        issues.extend(normalize_pricing_info(data.get("supplementary_info", {}).get("pricing_info", {}), "supplementary_info.pricing_info"))
        issues.extend(dedupe_pricing_with_supplementary_priority(data))
        issues.extend(dedupe_list_module(data, ("application_form_info", "agreement_rules"), "application_form_info.agreement_rules"))
        issues.extend(dedupe_list_module(data, ("application_form_info", "eligibility_and_constraints"), "application_form_info.eligibility_and_constraints"))
        issues.extend(dedupe_list_module(data, ("supplementary_info", "application_materials"), "supplementary_info.application_materials"))
        normalize_source_files(data)
        normalize_issue_count(data)
        return ProductDocumentNormalizationResult(product_document=data, issues=issues)


def normalize_extraction_meta(data: dict[str, Any]) -> None:
    """让 extraction_meta 只保留用户定义的 5 个字段。"""

    meta = data.get("extraction_meta") if isinstance(data.get("extraction_meta"), dict) else {}
    validation_issues = meta.get("validation_issues", [])
    if not isinstance(validation_issues, list):
        validation_issues = []
    data["extraction_meta"] = {
        "generated_at": str(meta.get("generated_at", "")),
        "source_file": str(meta.get("source_file", "")),
        "method": str(meta.get("method", "")),
        "validation_issues": [item for item in validation_issues if isinstance(item, dict)],
        "validation_issue_count": 0,
    }
    normalize_issue_count(data)


def normalize_string_noise(value: Any, path: str = "$") -> list[dict[str, Any]]:
    """清理模型和表格转换常见噪声，如 NaN 和多余空白。"""

    issues: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in list(value.items()):
            child_path = f"{path}.{key}"
            if isinstance(child, str):
                cleaned = clean_text(child)
                if cleaned != child:
                    value[key] = cleaned
            else:
                issues.extend(normalize_string_noise(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(normalize_string_noise(child, f"{path}[{index}]"))
    return issues


def clean_text(value: str) -> str:
    text = value.replace("\u00a0", " ").strip()
    if text.lower() == "nan":
        return ""
    text = re.sub(r"\bNaN\b", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_pricing_info(pricing_info: Any, path: str) -> list[dict[str, Any]]:
    """统一资费模块中的币种、周期、单位和重复行。"""

    if not isinstance(pricing_info, dict):
        return []
    issues: list[dict[str, Any]] = []
    for list_key in ("one_time_fees", "base_package_prices", "addon_prices", "fee_and_term_rules", "discount_policy"):
        items = pricing_info.get(list_key, [])
        if not isinstance(items, list):
            pricing_info[list_key] = []
            issues.append(make_issue("warning", f"{path}.{list_key}", "资费列表不是数组，已置为空数组"))
            continue
        if list_key == "base_package_prices":
            items, expanded_issues = expand_base_package_prices_from_raw_rows(items, f"{path}.{list_key}")
            pricing_info[list_key] = items
            issues.extend(expanded_issues)
        for index, item in enumerate(items):
            if isinstance(item, dict):
                issues.extend(normalize_pricing_item(item, f"{path}.{list_key}[{index}]"))
        pricing_info[list_key], removed = dedupe_items(items, price_signature)
        for index in removed:
            issues.append(make_issue("warning", f"{path}.{list_key}[{index}]", "删除同一资费模块内的重复记录"))
    return issues


def expand_base_package_prices_from_raw_rows(items: list[Any], path: str) -> tuple[list[Any], list[dict[str, Any]]]:
    """资费表一行含月付/年付/2年付多列时，补成多条套餐价格。"""

    expanded: list[Any] = list(items)
    existing = {cross_source_pricing_signature(item, "base_package_prices") for item in items if isinstance(item, dict)}
    issues: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        raw_text = str(item.get("raw_text", ""))
        cells = split_markdown_row(raw_text)
        if len(cells) < 4:
            continue
        monthly_price = normalize_number(cells[1] if len(cells) > 1 else item.get("price"))
        if monthly_price is None:
            continue
        speed = item.get("speed") or cells[0]
        candidates = [
            ("年", cells[2] if len(cells) > 2 else ""),
            ("2年", cells[3] if len(cells) > 3 else ""),
        ]
        for period, price_text in candidates:
            price = normalize_number(price_text)
            if price is None or not looks_like_period_price(monthly_price, price, period):
                continue
            clone = deepcopy(item)
            clone["name"] = append_period_to_name(str(item.get("name", "")), period)
            clone["speed"] = speed
            clone["price"] = price
            clone["billing_period"] = period
            clone["unit"] = normalize_unit("", period)
            clone["raw_text"] = raw_text
            signature = cross_source_pricing_signature(clone, "base_package_prices")
            if signature in existing:
                continue
            existing.add(signature)
            expanded.append(clone)
            issues.append(make_issue("warning", f"{path}[{index}]", f"从资费表同一行补全{period}套餐价格"))
    return expanded, issues


def looks_like_period_price(monthly_price: float, candidate_price: float, period: str) -> bool:
    """避免把折扣月租列误判成年付/2年付列。"""

    if period == "年":
        return candidate_price >= monthly_price * 6
    if period == "2年":
        return candidate_price >= monthly_price * 12
    return True


def split_markdown_row(value: str) -> list[str]:
    """拆 MarkItDown 表格行，忽略首尾空列和 NaN。"""

    text = str(value or "").strip()
    if "|" not in text:
        return []
    cells = [clean_text(cell.strip()) for cell in text.strip("|").split("|")]
    return [cell for cell in cells]


def append_period_to_name(name: str, period: str) -> str:
    text = str(name or "").strip()
    if not text:
        return period
    if period in text:
        return text
    return f"{text}-{period}付"


def normalize_pricing_item(item: dict[str, Any], path: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if "currency" in item:
        normalized_currency = normalize_currency(item.get("currency"))
        if normalized_currency != item.get("currency"):
            item["currency"] = normalized_currency
    if "billing_period" in item:
        normalized_period = normalize_period(item.get("billing_period"))
        if normalized_period != item.get("billing_period"):
            item["billing_period"] = normalized_period
    if "unit" in item:
        normalized_unit = normalize_unit(item.get("unit"), item.get("billing_period"))
        if normalized_unit != item.get("unit"):
            item["unit"] = normalized_unit
    for key in ("price", "amount", "standard_price", "discount_rate", "discounted_price"):
        if key in item:
            normalized_number = normalize_number(item.get(key))
            if normalized_number != item.get(key):
                item[key] = normalized_number
    for key in ("included_items", "conditions", "applicable_to", "required_with"):
        if isinstance(item.get(key), list):
            item[key] = dedupe_strings(item[key])
    return issues


def normalize_currency(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"", "NAN"}:
        return ""
    if text in {"元", "人民币", "RMB", "CNY", "￥", "¥"}:
        return "CNY"
    return str(value or "").strip()


def normalize_period(value: Any) -> str:
    text = compact(str(value or ""))
    mapping = {
        "月": "月",
        "月付": "月",
        "每月": "月",
        "元/月": "月",
        "年": "年",
        "年付": "年",
        "每年": "年",
        "元/年": "年",
        "2年": "2年",
        "两年": "2年",
        "二年": "2年",
        "2年付": "2年",
        "一次性": "一次性",
        "次": "一次性",
    }
    return mapping.get(text, str(value or "").strip())


def normalize_unit(value: Any, billing_period: Any) -> str:
    text = compact(str(value or ""))
    period = normalize_period(billing_period)
    if text in {"", "元", "/月", "元/月"} and period in {"月", "年", "2年", "一次性"}:
        return f"元/{period}" if period != "一次性" else "元/次"
    if text in {"/年", "元/年"}:
        return "元/年"
    if text in {"/2年", "元/2年"}:
        return "元/2年"
    if text in {"月", "年", "2年"}:
        return f"元/{text}"
    if text in {"次", "一次性"}:
        return "元/次"
    return str(value or "").strip()


def dedupe_pricing_with_supplementary_priority(data: dict[str, Any]) -> list[dict[str, Any]]:
    """申请表与资费表重复时，以资费表为主，申请表删除重复项。"""

    issues: list[dict[str, Any]] = []
    app_pricing = data.get("application_form_info", {}).get("pricing_info", {})
    sup_pricing = data.get("supplementary_info", {}).get("pricing_info", {})
    if not isinstance(app_pricing, dict) or not isinstance(sup_pricing, dict):
        return issues

    for key in ("one_time_fees", "base_package_prices", "addon_prices", "discount_policy"):
        app_items = app_pricing.get(key, [])
        sup_items = sup_pricing.get(key, [])
        if not isinstance(app_items, list) or not isinstance(sup_items, list):
            continue
        sup_signatures = {cross_source_pricing_signature(item, key) for item in sup_items if isinstance(item, dict)}
        kept = []
        for index, item in enumerate(app_items):
            if isinstance(item, dict) and cross_source_pricing_signature(item, key) in sup_signatures:
                issues.append(
                    make_issue(
                        "warning",
                        f"application_form_info.pricing_info.{key}[{index}]",
                        "与补充资费表重复，按来源优先级保留资费表记录",
                    )
                )
                continue
            kept.append(item)
        app_pricing[key] = kept
    return issues


def dedupe_list_module(data: dict[str, Any], path_parts: tuple[str, str], path: str) -> list[dict[str, Any]]:
    parent = data.get(path_parts[0], {})
    if not isinstance(parent, dict) or not isinstance(parent.get(path_parts[1]), list):
        return []
    items, removed = dedupe_items(parent[path_parts[1]], generic_signature)
    parent[path_parts[1]] = items
    return [make_issue("warning", f"{path}[{index}]", "删除重复记录") for index in removed]


def dedupe_items(items: list[Any], signature_fn) -> tuple[list[Any], list[int]]:
    kept: list[Any] = []
    seen: set[str] = set()
    removed: list[int] = []
    for index, item in enumerate(items):
        signature = signature_fn(item)
        if signature and signature in seen:
            removed.append(index)
            continue
        if signature:
            seen.add(signature)
        kept.append(item)
    return kept, removed


def price_signature(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    parts = [
        item.get("name", ""),
        item.get("speed", ""),
        item.get("spec", ""),
        item.get("price", item.get("amount", "")),
        item.get("billing_period", item.get("period", "")),
        item.get("unit", ""),
    ]
    raw_signature = "|".join(str(part) for part in parts if part not in ("", None))
    if raw_signature:
        return compact(raw_signature)
    return compact(str(item.get("raw_text", "")))


def cross_source_pricing_signature(item: Any, list_key: str) -> str:
    """跨来源比较用签名，避免名称差异影响重复判断。"""

    if not isinstance(item, dict):
        return ""
    if list_key == "base_package_prices":
        return compact(
            "|".join(
                part
                for part in (
                    normalize_speed_for_signature(item.get("speed", "")),
                    str(item.get("price", "")),
                    normalize_period(item.get("billing_period", "")),
                )
                if part not in ("", None)
            )
        )
    if list_key == "one_time_fees":
        return compact(
            "|".join(
                str(item.get(key, ""))
                for key in ("amount", "unit")
                if item.get(key) not in ("", None)
            )
        )
    return price_signature(item)


def generic_signature(item: Any) -> str:
    if not isinstance(item, dict):
        return compact(str(item))
    text = " ".join(
        str(item.get(key, ""))
        for key in ("name", "description", "condition", "result", "raw_text", "material_type")
    )
    return compact(text)


def normalize_source_files(data: dict[str, Any]) -> None:
    """清理 source_file 空白；真实来源补齐主要由 merger 负责。"""

    for item in iter_nodes(data):
        if isinstance(item, dict) and "source_file" in item:
            item["source_file"] = str(item.get("source_file") or "").strip()


def iter_nodes(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from iter_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_nodes(child)


def dedupe_strings(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            result.append(value)
            continue
        cleaned = clean_text(value)
        key = compact(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def normalize_issue_count(data: dict[str, Any]) -> None:
    meta = data.setdefault("extraction_meta", {})
    issues = meta.get("validation_issues", [])
    if not isinstance(issues, list):
        issues = []
    deduped = dedupe_issues(issues)
    meta["validation_issues"] = deduped
    meta["validation_issue_count"] = len(deduped)


def dedupe_issues(issues: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        key = (
            str(issue.get("severity", "")),
            str(issue.get("path", "")),
            str(issue.get("message", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


def make_issue(severity: str, path: str, message: str) -> dict[str, str]:
    return {"severity": severity, "path": path, "message": message}


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def normalize_speed_for_signature(value: Any) -> str:
    """把 50M/50M 和 50M 这类对称速率统一成 50M。"""

    text = compact(str(value or "")).upper()
    symmetric = re.fullmatch(r"(\d+(?:\.\d+)?[MG])/\1", text)
    if symmetric:
        return symmetric.group(1)
    slash_match = re.fullmatch(r"(\d+(?:\.\d+)?[MG])/(\d+(?:\.\d+)?[MG])", text)
    if slash_match and slash_match.group(1) == slash_match.group(2):
        return slash_match.group(1)
    return text


def source_rank(source_file: str, module_hint: str = "") -> int:
    """来源优先级：四类补充资料高于申请表。"""

    text = source_file.lower()
    hints = SUPPLEMENTARY_SOURCE_HINTS.get(module_hint, ())
    if any(hint.lower() in text for hint in hints):
        return 100
    if any(hint in source_file for hint in ("申请表", "登记表", "需求表")):
        return 10
    return 50 if source_file else 0
