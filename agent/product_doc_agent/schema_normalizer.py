from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from schema.schema import get_module_json_schema


APPLICATION_ATTRIBUTE_HINTS = {
    "企业规模",
    "计算机数量",
    "经办人",
    "联系电话",
    "身份证号码",
    "邮编",
    "付款方式",
    "账单地址",
    "安装地址",
    "企业全称",
    "统一社会信用代码",
}

INACTIVE_STATUS_HINTS = ("停用", "停止申请", "停止使用", "已停", "废止", "下线")
INVALID_CONTRACT_PERIOD_VALUES = {"线", "次", "月", "年", "元", "元/月", "元/年", "月/线", "年/线", "元/月/线", "元/年/线"}


def normalize_to_module_schema(module_name: str, value: Any) -> Any:
    """Align an LLM module response to the module schema without adding facts."""
    schema = get_module_json_schema(module_name)
    unwrapped = unwrap_module_payload(module_name, value, schema)
    return normalize_value(unwrapped, schema)


def unwrap_module_payload(module_name: str, value: Any, schema: dict[str, Any]) -> Any:
    if not isinstance(value, dict):
        return value
    if module_name not in value:
        return value

    inner = value[module_name]
    expected_type = schema.get("type")
    if expected_type == "array" and isinstance(inner, list):
        return inner
    if expected_type == "object" and isinstance(inner, dict):
        return inner
    return value


def normalize_value(value: Any, schema: dict[str, Any]) -> Any:
    expected_type = schema.get("type")
    effective_type = choose_type(expected_type, value)

    if effective_type == "object":
        normalized = normalize_object(value, schema)
    elif effective_type == "array":
        normalized = normalize_array(value, schema)
    elif effective_type == "string":
        normalized = "" if value is None else str(value)
    elif effective_type == "number":
        normalized = normalize_number(value, default=0.8 if is_confidence_schema(schema) else 0.0)
    elif effective_type == "integer":
        normalized = normalize_integer(value)
    elif effective_type == "boolean":
        normalized = value if isinstance(value, bool) else False
    elif effective_type == "null":
        normalized = None
    else:
        normalized = value

    return normalize_enum(normalized, schema)


def normalize_object(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}

    properties = schema.get("properties", {})
    if not isinstance(properties, dict) or not properties:
        return dict(value)

    result: dict[str, Any] = {}
    required = schema.get("required", [])
    allowed_keys = set(properties)

    for key, child_value in value.items():
        if key in properties:
            result[key] = normalize_value(child_value, properties[key])
        elif schema.get("additionalProperties") is not False:
            result[key] = child_value

    for key in required:
        if key not in result and key in properties:
            result[key] = default_for_schema(properties[key])

    if schema.get("additionalProperties") is False:
        return {key: result[key] for key in result if key in allowed_keys}
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


def default_for_schema(schema: dict[str, Any]) -> Any:
    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        non_null_types = [item for item in expected_type if item != "null"]
        if not non_null_types:
            return None
        if "boolean" in non_null_types:
            return None
        if "number" in non_null_types or "integer" in non_null_types:
            return None
        expected_type = non_null_types[0]

    if expected_type == "object":
        return normalize_object({}, schema)
    if expected_type == "array":
        return []
    if expected_type == "string":
        return ""
    if expected_type == "number":
        return 0.8 if is_confidence_schema(schema) else 0.0
    if expected_type == "integer":
        return 0
    if expected_type == "boolean":
        return False
    return None


def choose_type(expected_type: Any, value: Any) -> str | None:
    if isinstance(expected_type, str):
        return expected_type
    if not isinstance(expected_type, list):
        return None

    for item in expected_type:
        if matches_type(value, item):
            return item

    non_null_types = [item for item in expected_type if item != "null"]
    if isinstance(value, str):
        if "number" in non_null_types and re.search(r"-?\d+(?:\.\d+)?", value):
            return "number"
        if "integer" in non_null_types and re.search(r"-?\d+", value):
            return "integer"
    if value is None and "null" in expected_type:
        return "null"
    if "null" in expected_type:
        return "null"
    return non_null_types[0] if non_null_types else "null"


def matches_type(value: Any, expected_type: str) -> bool:
    if expected_type == "null":
        return value is None
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
    return True


def normalize_number(value: Any, *, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if match:
            return float(match.group(0))
    return default


def normalize_integer(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        match = re.search(r"-?\d+", value)
        if match:
            return int(match.group(0))
    return 0


def is_confidence_schema(schema: dict[str, Any]) -> bool:
    description = str(schema.get("description", "")).lower()
    return "confidence" in description or "置信" in description


def normalize_enum(value: Any, schema: dict[str, Any]) -> Any:
    enum_values = schema.get("enum")
    if not isinstance(enum_values, list) or value in enum_values:
        return value
    if "" in enum_values:
        return ""
    return enum_values[0] if enum_values else value


@dataclass(frozen=True)
class ProductDocumentNormalizationResult:
    product_document: dict[str, Any]
    issues: list[dict[str, Any]]


class ProductDocumentNormalizer:
    """Normalize a merged product document without adding unsupported business facts."""

    def normalize_product_document(self, product_document: dict[str, Any]) -> ProductDocumentNormalizationResult:
        issues: list[dict[str, Any]] = []
        self._normalize_extraction_meta(product_document)
        issues.extend(self._fill_document_status(product_document))
        issues.extend(self._remove_duplicate_application_attributes(product_document))
        issues.extend(self._remove_duplicate_application_constraints(product_document))
        issues.extend(self._remove_duplicate_service_attributes(product_document))
        issues.extend(self._remove_optional_items_from_included_items(product_document))
        issues.extend(self._split_composite_optional_packages(product_document))
        issues.extend(self._normalize_currency_values(product_document))
        issues.extend(self._normalize_optional_package_price_items(product_document))
        issues.extend(self._normalize_contract_period(product_document))
        self._fill_source_location_paths(product_document)
        return ProductDocumentNormalizationResult(product_document=product_document, issues=issues)

    def _normalize_extraction_meta(self, product_document: dict[str, Any]) -> None:
        meta = product_document.get("extraction_meta")
        if not isinstance(meta, dict):
            return

        normalized_issues: list[dict[str, Any]] = []
        for index, item in enumerate(meta.get("validation_issues", [])):
            if isinstance(item, dict):
                normalized_issues.append(item)
                continue
            if item is None:
                continue
            normalized_issues.append(
                {
                    "severity": "warning",
                    "path": f"llm_self_check.validation_issues[{index}]",
                    "message": str(item),
                }
            )
        meta["validation_issues"] = normalized_issues
        meta["validation_issue_count"] = len(normalized_issues)

        normalized_warnings: list[str] = []
        for item in meta.get("schema_warnings", []):
            if item is not None:
                normalized_warnings.append(str(item))
        meta["schema_warnings"] = normalized_warnings

    def _fill_document_status(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        document_info = product_document.get("document_info", {})
        if not isinstance(document_info, dict) or document_info.get("document_status"):
            return []

        filename = str(document_info.get("filename", ""))
        source_path = str(document_info.get("source_path", ""))
        effective_from = parse_date(str(document_info.get("effective_from", "")))
        inferred_status = ""

        if any(hint in filename or hint in source_path for hint in INACTIVE_STATUS_HINTS):
            inferred_status = "inactive"
        elif effective_from is None or effective_from <= date.today():
            inferred_status = "active"

        if not inferred_status:
            return []

        document_info["document_status"] = inferred_status
        return []

    def _remove_duplicate_application_attributes(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        base_package = product_document.get("base_package", {})
        if not isinstance(base_package, dict):
            return []

        service_attributes = base_package.get("service_attributes", [])
        if not isinstance(service_attributes, list):
            return []

        application_names, application_evidence = collect_application_field_signatures(product_document)
        kept: list[Any] = []
        issues: list[dict[str, Any]] = []

        for index, item in enumerate(service_attributes):
            if not isinstance(item, dict):
                kept.append(item)
                continue

            name = canonical_name(first_non_empty(item, ("attribute_name", "label", "name", "field_key")))
            evidence = compact_text(str(item.get("source_evidence", "")))
            duplicated = (
                bool(evidence and evidence in application_evidence)
                or bool(name and name in application_names and is_application_attribute(name, evidence))
            )

            if duplicated:
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"base_package.service_attributes[{index}]",
                        "message": f"removed duplicated application field from base package service attributes: {name or 'unknown'}",
                    }
                )
                continue
            kept.append(item)

        base_package["service_attributes"] = kept
        return issues

    def _remove_duplicate_service_attributes(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        base_package = product_document.get("base_package", {})
        service_attributes = base_package.get("service_attributes", []) if isinstance(base_package, dict) else []
        if not isinstance(service_attributes, list):
            return []

        seen: set[tuple[str, str]] = set()
        kept: list[Any] = []
        issues: list[dict[str, Any]] = []
        for index, item in enumerate(service_attributes):
            if not isinstance(item, dict):
                kept.append(item)
                continue
            name = canonical_name(first_non_empty(item, ("attribute_name", "label", "name", "field_key")))
            evidence = compact_text(str(item.get("source_evidence", "")))
            key = (name, evidence)
            if name and key in seen:
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"base_package.service_attributes[{index}]",
                        "message": f"removed duplicated base package service attribute: {name}",
                    }
                )
                continue
            seen.add(key)
            kept.append(item)

        base_package["service_attributes"] = kept
        return issues

    def _remove_optional_items_from_included_items(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        base_package = product_document.get("base_package", {})
        included_items = base_package.get("included_items", []) if isinstance(base_package, dict) else []
        if not isinstance(included_items, list):
            return []

        optional_text = collect_optional_package_text(product_document)
        kept: list[Any] = []
        issues: list[dict[str, Any]] = []
        for index, item in enumerate(included_items):
            if not isinstance(item, dict):
                kept.append(item)
                continue
            text = compact_text(" ".join(str(value) for value in item.values()))
            if looks_like_paid_optional_item(text) and overlaps_optional_text(text, optional_text):
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"base_package.included_items[{index}]",
                        "message": "removed paid optional item from base package included items",
                    }
                )
                continue
            kept.append(item)

        base_package["included_items"] = kept
        return issues

    def _normalize_optional_package_price_items(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        optional_packages = product_document.get("optional_packages", [])
        if not isinstance(optional_packages, list):
            return []

        issues: list[dict[str, Any]] = []
        for package_index, item in enumerate(optional_packages):
            if not isinstance(item, dict):
                continue

            price_items = item.get("price_items")
            if not isinstance(price_items, list):
                price_items = []

            source_text = optional_package_price_source_text(item)
            parsed_prices = extract_price_items(source_text, default_name=str(item.get("name", "")))
            if parsed_prices and (not price_items or all(is_empty_price_item(price_item) for price_item in price_items if isinstance(price_item, dict))):
                item["price_items"] = parsed_prices
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"optional_packages[{package_index}].price_items",
                        "message": "filled optional package price_items from source evidence",
                    }
                )
                continue

            changed = False
            parsed_index = 0
            for price_item in price_items:
                if not isinstance(price_item, dict):
                    continue
                if price_item.get("price") is not None:
                    continue
                evidence = str(price_item.get("source_evidence", ""))
                parsed = extract_price_items(evidence, default_name=str(price_item.get("item_name") or item.get("name", "")))
                if not parsed and parsed_index < len(parsed_prices):
                    parsed = [parsed_prices[parsed_index]]
                    parsed_index += 1
                if not parsed:
                    continue
                fill_price_item(price_item, parsed[0])
                changed = True

            if changed:
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"optional_packages[{package_index}].price_items",
                        "message": "normalized optional package price item amounts from source evidence",
                    }
                )
        return issues

    def _normalize_currency_values(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for path, item in iter_dicts(product_document):
            currency = item.get("currency")
            normalized_currency = normalize_currency(currency)
            if normalized_currency != currency:
                item["currency"] = normalized_currency
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"{path}.currency",
                        "message": f"normalized currency value to {normalized_currency}",
                    }
                )
        return issues

    def _remove_duplicate_application_constraints(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        constraints = product_document.get("eligibility_and_constraints", [])
        if not isinstance(constraints, list):
            return []

        application_names, application_evidence = collect_application_field_signatures(product_document)
        kept: list[Any] = []
        issues: list[dict[str, Any]] = []

        for index, item in enumerate(constraints):
            if not isinstance(item, dict):
                kept.append(item)
                continue

            name = canonical_name(first_non_empty(item, ("name", "condition", "description")))
            evidence = compact_text(str(item.get("source_evidence", "")))
            duplicated = (
                bool(evidence and evidence in application_evidence)
                or any(application_name and application_name in name for application_name in application_names if is_application_attribute(application_name, evidence))
            )

            if duplicated and not looks_like_real_constraint(item):
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"eligibility_and_constraints[{index}]",
                        "message": "removed duplicated application field from eligibility constraints",
                    }
                )
                continue
            kept.append(item)

        product_document["eligibility_and_constraints"] = kept
        return issues

    def _split_composite_optional_packages(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        optional_packages = product_document.get("optional_packages", [])
        if not isinstance(optional_packages, list):
            return []

        result: list[Any] = []
        issues: list[dict[str, Any]] = []
        for index, item in enumerate(optional_packages):
            split_items = split_optional_package_if_composite(item)
            if split_items is None:
                result.append(item)
                continue
            result.extend(split_items)
            issues.append(
                {
                    "severity": "warning",
                    "path": f"optional_packages[{index}]",
                    "message": "split composite optional package into separate package objects",
                }
            )

        product_document["optional_packages"] = result
        return issues

    def _normalize_contract_period(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        packages = product_document.get("base_package", {}).get("packages", [])
        if not isinstance(packages, list):
            return []

        issues: list[dict[str, Any]] = []
        for index, item in enumerate(packages):
            if not isinstance(item, dict):
                continue
            value = str(item.get("contract_period", "")).strip()
            if not value or is_valid_contract_period(value):
                continue
            item["contract_period"] = ""
            issues.append(
                {
                    "severity": "warning",
                    "path": f"base_package.packages[{index}].contract_period",
                    "message": f"contract_period looked like a billing/unit value and was cleared: {value}",
                }
            )
        return issues

    def _fill_source_location_paths(self, product_document: dict[str, Any]) -> None:
        source_path = str(product_document.get("document_info", {}).get("source_path", ""))
        if not source_path:
            return
        fill_source_path(product_document, source_path)


def collect_application_field_signatures(product_document: dict[str, Any]) -> tuple[set[str], set[str]]:
    fields = product_document.get("parties_and_application", {}).get("application_fields", {})
    names: set[str] = set()
    evidence_values: set[str] = set()
    if not isinstance(fields, dict):
        return names, evidence_values

    for bucket in ("required", "optional"):
        for item in fields.get(bucket, []):
            if not isinstance(item, dict):
                continue
            label = canonical_name(str(item.get("label", "")))
            field_key = canonical_name(str(item.get("field_key", "")))
            evidence = compact_text(str(item.get("source_evidence", "")))
            if label:
                names.add(label)
            if field_key:
                names.add(field_key)
            if evidence:
                evidence_values.add(evidence)
    return names, evidence_values


def is_application_attribute(name: str, evidence: str) -> bool:
    return name in APPLICATION_ATTRIBUTE_HINTS or evidence.startswith("*") or "客户" in evidence or "经办人" in evidence


def split_optional_package_if_composite(item: Any) -> list[dict[str, Any]] | None:
    if not isinstance(item, dict):
        return None

    name = str(item.get("name", "")).strip()
    if "/" not in name and "／" not in name:
        return None

    parts = [part.strip() for part in re.split(r"[/／]", name) if part.strip()]
    if not 2 <= len(parts) <= 4 or any(looks_like_technical_ratio(part) for part in parts):
        return None

    options = item.get("options", [])
    if not isinstance(options, list) or len(options) < len(parts):
        return None

    option_texts = [str(option) for option in options]
    matched_options: list[list[str]] = []
    for part in parts:
        key = option_match_key(part)
        matches = [option for option in option_texts if key and key in compact_text(option)]
        if not matches:
            return None
        matched_options.append(matches)

    split_items: list[dict[str, Any]] = []
    for part, matches in zip(parts, matched_options, strict=False):
        clone = dict(item)
        clone["name"] = part
        clone["options"] = matches
        clone["source_evidence"] = " | ".join([part, *matches])
        split_items.append(clone)
    return split_items


def collect_optional_package_text(product_document: dict[str, Any]) -> str:
    values: list[str] = []
    for item in product_document.get("optional_packages", []):
        if isinstance(item, dict):
            values.append(compact_text(" ".join(str(value) for value in item.values())))
    return "\n".join(values)


def looks_like_paid_optional_item(text: str) -> bool:
    return bool(re.search(r"可付费|付费申请|费用增加|升级|增值|可选", text))


def overlaps_optional_text(text: str, optional_text: str) -> bool:
    if not text or not optional_text:
        return False
    for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{4,}", text):
        if token in optional_text:
            return True
    return False


def optional_package_price_source_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("name", "description", "fee_summary", "source_evidence"):
        value = item.get(key)
        if value:
            parts.append(str(value))
    for option in item.get("options", []):
        parts.append(str(option))
    for price_item in item.get("price_items", []):
        if isinstance(price_item, dict):
            parts.extend(str(price_item.get(key, "")) for key in ("item_name", "source_evidence"))
        else:
            parts.append(str(price_item))
    return "\n".join(part for part in parts if part)


PRICE_PATTERN = re.compile(
    r"(?P<amount>\d+(?:\.\d+)?)\s*元\s*(?:/|／)?\s*(?P<period>2年|两年|年|月|线|号|次|分钟|条|GB|MB)?",
    flags=re.IGNORECASE,
)


def extract_price_items(text: str, *, default_name: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[tuple[float, str, str]] = set()
    for match in PRICE_PATTERN.finditer(text or ""):
        amount = float(match.group("amount"))
        period = normalize_billing_period(match.group("period") or "")
        evidence = surrounding_text(text, match.start(), match.end())
        key = (amount, period, compact_text(evidence))
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "item_name": infer_price_item_name(evidence, default_name),
                "price": amount,
                "currency": "CNY",
                "billing_period": period,
                "source_evidence": evidence,
                "confidence": 0.85,
            }
        )
    return results


def normalize_billing_period(value: str) -> str:
    if value in {"两年"}:
        return "2年"
    return value


def surrounding_text(text: str, start: int, end: int, *, window: int = 36) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - window) : min(len(text), end + window)]).strip()


def infer_price_item_name(evidence: str, default_name: str) -> str:
    prefix = re.split(r"\d+(?:\.\d+)?\s*元", evidence, maxsplit=1)[0]
    prefix = re.sub(r"[：:，,。；;\s]*$", "", prefix).strip(" □【】()（）")
    return prefix[-40:] if prefix else default_name


def is_empty_price_item(value: Any) -> bool:
    return not isinstance(value, dict) or value.get("price") is None


def fill_price_item(target: dict[str, Any], source: dict[str, Any]) -> None:
    target["price"] = source.get("price")
    target.setdefault("currency", source.get("currency", "CNY"))
    if not target.get("currency"):
        target["currency"] = source.get("currency", "CNY")
    if not target.get("billing_period"):
        target["billing_period"] = source.get("billing_period", "")
    if not target.get("item_name"):
        target["item_name"] = source.get("item_name", "")
    if not target.get("source_evidence"):
        target["source_evidence"] = source.get("source_evidence", "")
    if not target.get("confidence"):
        target["confidence"] = source.get("confidence", 0.85)


def iter_dicts(value: Any, path: str = "$"):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from iter_dicts(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_dicts(child, f"{path}[{index}]")


def normalize_currency(value: Any) -> Any:
    if value in {"元", "人民币", "RMB", "CNY", "¥"}:
        return "CNY"
    return value


def option_match_key(value: str) -> str:
    text = re.sub(r"[（(].*?[）)]", "", value)
    return compact_text(text)


def looks_like_technical_ratio(value: str) -> bool:
    return bool(re.search(r"\d+\s*[MG]|[上下]行|IPv[46]|\d+\s*[:：]\s*\d+", value, flags=re.IGNORECASE))


def looks_like_real_constraint(item: dict[str, Any]) -> bool:
    text = compact_text(
        " ".join(
            str(item.get(key, ""))
            for key in ("constraint_type", "name", "description", "condition", "result", "source_evidence")
        )
    )
    if not text:
        return False
    if "blocks_recommendation" in item and item.get("blocks_recommendation") is True:
        return True
    strong_constraint_keywords = (
        "不得",
        "不能",
        "不可",
        "不适用",
        "仅限",
        "需提供",
        "需满足",
        "实名",
        "违法",
        "违规",
        "停用",
        "停止",
        "押金",
        "担保",
        "拓扑",
        "IP地址",
    )
    option_only_keywords = ("必须在", "中选择", "□")
    if any(keyword in text for keyword in option_only_keywords) and not any(keyword in text for keyword in strong_constraint_keywords):
        return False
    return any(keyword in text for keyword in strong_constraint_keywords)


def is_valid_contract_period(value: str) -> bool:
    compact = compact_text(value)
    if compact in INVALID_CONTRACT_PERIOD_VALUES:
        return False
    if re.fullmatch(r"[./\\-]*\s*(线|次|月|年)\s*", value):
        return False
    return bool(re.search(r"\d+\s*(个月|月|年)|一\s*年|二\s*年|两\s*年|三\s*年|协议期|有效期|至", value))


def fill_source_path(value: Any, source_path: str) -> None:
    if isinstance(value, dict):
        location = value.get("source_location")
        if isinstance(location, dict) and not location.get("path"):
            location["path"] = source_path
        for child in value.values():
            fill_source_path(child, source_path)
    elif isinstance(value, list):
        for child in value:
            fill_source_path(child, source_path)


def parse_date(value: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def first_non_empty(value: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        item = value.get(key)
        if item:
            return str(item)
    return ""


def canonical_name(value: str) -> str:
    return compact_text(value).replace("*", "").replace("□", "")


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")
