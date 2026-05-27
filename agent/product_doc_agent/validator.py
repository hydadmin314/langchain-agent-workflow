from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from schema.schema import PRODUCT_DOCUMENT_JSON_SCHEMA


EVIDENCE_LIST_KEYS = {
    "optional_packages",
    "fee_and_term_rules",
    "agreement_rules",
    "application_materials",
    "eligibility_and_constraints",
    "supplemental_rules",
}

ALLOWED_CURRENCIES = {"", "CNY", None}
INACTIVE_STATUS_HINTS = ("停用", "停止申请", "停止使用", "已停", "废止")


class ProductDocumentValidator:
    """Deterministic validation after LLM extraction and normalization.

    The validator intentionally avoids business semantic judgement. It checks
    only stable program rules: schema shape, evidence metadata, scalar formats,
    and explicit contradictions that can be verified without understanding a
    carrier-specific product.
    """

    def validate(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        issues = validate_against_schema(product_document, PRODUCT_DOCUMENT_JSON_SCHEMA)
        issues.extend(self._validate_evidence(product_document))
        issues.extend(self._validate_scalar_formats(product_document))
        issues.extend(self._validate_deterministic_consistency(product_document))
        issues.extend(self._validate_cross_module_duplicates(product_document))
        return issues

    def attach_issues(self, product_document: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
        meta = product_document.setdefault("extraction_meta", {})
        existing = meta.get("validation_issues", [])
        if not isinstance(existing, list):
            existing = []
        meta["validation_issues"] = [*existing, *issues]
        meta["validation_issue_count"] = len(meta["validation_issues"])
        meta.setdefault("schema_warnings", [])
        return product_document

    def _validate_evidence(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for key in EVIDENCE_LIST_KEYS:
            for index, item in enumerate(product_document.get(key, [])):
                if not isinstance(item, dict):
                    continue
                issues.extend(validate_item_evidence(item, f"{key}[{index}]"))

        for index, item in enumerate(product_document.get("base_package", {}).get("packages", [])):
            if isinstance(item, dict):
                issues.extend(validate_item_evidence(item, f"base_package.packages[{index}]"))

        for index, item in enumerate(product_document.get("base_package", {}).get("included_items", [])):
            if isinstance(item, dict):
                issues.extend(validate_item_evidence(item, f"base_package.included_items[{index}]"))

        for index, item in enumerate(product_document.get("base_package", {}).get("service_attributes", [])):
            if isinstance(item, dict):
                issues.extend(validate_item_evidence(item, f"base_package.service_attributes[{index}]"))
        return issues

    def _validate_scalar_formats(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for path, item in iter_dicts(product_document):
            if "confidence" in item:
                confidence = item.get("confidence")
                if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
                    issues.append(
                        {
                            "severity": "warning",
                            "path": f"{path}.confidence",
                            "message": "confidence must be a number between 0 and 1",
                            "actual": confidence,
                        }
                    )

            if "currency" in item and item.get("currency") not in ALLOWED_CURRENCIES:
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"{path}.currency",
                        "message": "currency should be normalized to CNY or left empty",
                        "actual": item.get("currency"),
                    }
                )

            if "price" in item and item.get("price") is not None and not is_number(item.get("price")):
                issues.append(
                    {
                        "severity": "error",
                        "path": f"{path}.price",
                        "message": "price must be numeric when present",
                        "actual": item.get("price"),
                    }
                )

            if "amount" in item and item.get("amount") is not None and not is_number(item.get("amount")):
                issues.append(
                    {
                        "severity": "error",
                        "path": f"{path}.amount",
                        "message": "amount must be numeric when present",
                        "actual": item.get("amount"),
                    }
                )

            contract_period = str(item.get("contract_period", "")).strip()
            if contract_period and looks_like_billing_unit(contract_period):
                issues.append(
                    {
                        "severity": "error",
                        "path": f"{path}.contract_period",
                        "message": "contract_period looks like a billing/unit value, not an agreement term",
                        "actual": contract_period,
                    }
                )

        document_info = product_document.get("document_info", {})
        if isinstance(document_info, dict):
            for key in ("effective_from", "effective_to"):
                value = document_info.get(key)
                if value and not looks_like_date(value):
                    issues.append(
                        {
                            "severity": "warning",
                            "path": f"document_info.{key}",
                            "message": "date value is not in a recognizable format",
                            "actual": value,
                        }
                    )
        return issues

    def _validate_deterministic_consistency(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        document_info = product_document.get("document_info", {})
        base_package = product_document.get("base_package", {})
        if not isinstance(document_info, dict):
            return issues

        filename = str(document_info.get("filename", ""))
        source_path = str(document_info.get("source_path", ""))
        document_status = str(document_info.get("document_status", ""))
        effective_from = str(document_info.get("effective_from", ""))

        if any(hint in f"{filename} {source_path}" for hint in INACTIVE_STATUS_HINTS) and document_status == "active":
            issues.append(
                {
                    "severity": "error",
                    "path": "document_info.document_status",
                    "message": "path or filename indicates inactive document but document_status is active",
                }
            )

        if is_future_date(effective_from) and document_status == "active":
            issues.append(
                {
                    "severity": "warning",
                    "path": "document_info.document_status",
                    "message": "effective_from is in the future; document_status should be reviewed before publishing",
                }
            )

        if isinstance(base_package, dict):
            expected_voice = voice_hint_from_text(f"{filename} {source_path}")
            if expected_voice is not None:
                for index, package in enumerate(base_package.get("packages", [])):
                    if not isinstance(package, dict):
                        continue
                    actual_voice = package.get("has_voice")
                    if actual_voice is not None and actual_voice is not expected_voice:
                        issues.append(
                            {
                                "severity": "error",
                                "path": f"base_package.packages[{index}].has_voice",
                                "message": "has_voice contradicts filename or source path",
                                "expected": expected_voice,
                                "actual": actual_voice,
                            }
                        )
        return issues

    def _validate_cross_module_duplicates(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        application_names, application_evidence = collect_application_field_signatures(product_document)
        issues: list[dict[str, Any]] = []
        for index, item in enumerate(product_document.get("base_package", {}).get("service_attributes", [])):
            if not isinstance(item, dict):
                continue
            name = canonical_name(str(item.get("attribute_name") or item.get("label") or item.get("name") or ""))
            evidence = normalize_compact_text(str(item.get("source_evidence", "")))
            if evidence and evidence in application_evidence:
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"base_package.service_attributes[{index}]",
                        "message": "service attribute duplicates an application field source_evidence",
                    }
                )
            elif name and name in application_names and is_customer_application_name(name):
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"base_package.service_attributes[{index}]",
                        "message": f"customer application field should not be duplicated in base package attributes: {name}",
                    }
                )
        return issues


def validate_item_evidence(item: dict[str, Any], path: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not item.get("source_evidence"):
        issues.append({"severity": "warning", "path": f"{path}.source_evidence", "message": "missing source evidence"})

    location = item.get("source_location")
    if item.get("source_evidence") and isinstance(location, dict) and not location.get("path"):
        issues.append({"severity": "warning", "path": f"{path}.source_location.path", "message": "missing source file path"})

    if "confidence" not in item:
        issues.append({"severity": "warning", "path": f"{path}.confidence", "message": "missing confidence"})
    return issues


def validate_against_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[dict[str, Any]]:
    """Recursively validate extracted JSON against the local schema definition."""
    issues: list[dict[str, Any]] = []
    expected_type = schema.get("type")

    if not _matches_type(value, expected_type):
        issues.append(
            {
                "severity": "error",
                "path": path,
                "message": "type mismatch",
                "expected": expected_type,
                "actual": type(value).__name__,
            }
        )
        return issues

    enum_values = schema.get("enum")
    if enum_values is not None and value not in enum_values:
        issues.append(
            {
                "severity": "error",
                "path": path,
                "message": "value is not in allowed enum",
                "expected": enum_values,
                "actual": value,
            }
        )

    effective_type = _effective_schema_type(expected_type, value)
    if effective_type == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                issues.append(
                    {
                        "severity": "error",
                        "path": f"{path}.{key}",
                        "message": "missing required schema field",
                    }
                )
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    issues.append(
                        {
                            "severity": "error",
                            "path": f"{path}.{key}",
                            "message": "unexpected field not defined in schema",
                        }
                    )
        for key, child_value in value.items():
            child_schema = properties.get(key)
            if child_schema is None:
                continue
            issues.extend(validate_against_schema(child_value, child_schema, f"{path}.{key}"))

    if effective_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                issues.extend(validate_against_schema(item, item_schema, f"{path}[{index}]"))

    return issues


def collect_application_field_signatures(product_document: dict[str, Any]) -> tuple[set[str], set[str]]:
    fields = product_document.get("parties_and_application", {}).get("application_fields", {})
    names: set[str] = set()
    evidence_values: set[str] = set()
    if not isinstance(fields, dict):
        return names, evidence_values

    for key in ("required", "optional"):
        for item in fields.get(key, []):
            if not isinstance(item, dict):
                continue
            label = canonical_name(str(item.get("label", "")))
            field_key = canonical_name(str(item.get("field_key", "")))
            evidence = normalize_compact_text(str(item.get("source_evidence", "")))
            if label:
                names.add(label)
            if field_key:
                names.add(field_key)
            if evidence:
                evidence_values.add(evidence)
    return names, evidence_values


def iter_dicts(value: Any, path: str = "$"):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from iter_dicts(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_dicts(child, f"{path}[{index}]")


def _matches_type(value: Any, expected_type: Any) -> bool:
    if expected_type is None:
        return True
    if isinstance(expected_type, list):
        return any(_matches_single_type(value, item) for item in expected_type)
    return _matches_single_type(value, expected_type)


def _matches_single_type(value: Any, expected_type: str) -> bool:
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


def _effective_schema_type(expected_type: Any, value: Any) -> str | None:
    if isinstance(expected_type, str):
        return expected_type
    if not isinstance(expected_type, list):
        return None
    for item in expected_type:
        if _matches_single_type(value, item):
            return item
    return None


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def looks_like_billing_unit(value: str) -> bool:
    compact = normalize_compact_text(value)
    invalid_values = {"线", "次", "月", "年", "元", "元/月", "元/年", "月/线", "年/线", "元/月/线", "元/年/线"}
    if compact in invalid_values:
        return True
    return bool(re.fullmatch(r"[./\\-]*(线|次|月|年)", compact))


def looks_like_date(value: Any) -> bool:
    text = str(value).strip()
    if not text:
        return True
    return bool(
        re.fullmatch(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", text)
        or re.fullmatch(r"\d{8}", text)
    )


def is_future_date(value: str) -> bool:
    parsed = parse_date(value)
    return bool(parsed and parsed > date.today())


def parse_date(value: str) -> date | None:
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").replace(".", "-")
    if re.fullmatch(r"\d{8}", normalized):
        normalized = f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:]}"
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").date()
    except ValueError:
        return None


def voice_hint_from_text(value: str) -> bool | None:
    if "不带语音" in value or "无语音" in value:
        return False
    if "带语音" in value or "含语音" in value:
        return True
    return None


def is_customer_application_name(value: str) -> bool:
    names = {
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
    return value in names


def canonical_name(value: str) -> str:
    return normalize_compact_text(value).replace("*", "").replace("□", "")


def normalize_compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")
