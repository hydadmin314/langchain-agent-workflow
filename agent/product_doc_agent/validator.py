from __future__ import annotations

import re
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
MULTI_ITEM_PATTERNS = (
    r"1[、.．].*2[、.．]",
    r"□[^□]{1,80}□",
    r"①.*②",
    r"（1）.*（2）",
)


class ProductDocumentValidator:
    """Program-level validation after LLM extraction and self-check."""

    def validate(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        issues = validate_against_schema(product_document, PRODUCT_DOCUMENT_JSON_SCHEMA)
        issues.extend(self._validate_evidence(product_document))
        issues.extend(self._validate_possible_un_split_lists(product_document))
        issues.extend(self._validate_semantic_values(product_document))
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

    def validate_source_coverage(self, product_document: dict[str, Any], source_blocks: list[Any]) -> list[dict[str, Any]]:
        """Check whether high-priority source sections are represented in the extracted JSON."""
        issues: list[dict[str, Any]] = []
        application_blocks = blocks_before_marker(source_blocks, marker_patterns=("营销规则", "客户特别关注"))
        if not application_blocks:
            return issues

        issues.extend(self._validate_application_field_coverage(product_document, application_blocks))
        issues.extend(self._validate_package_coverage(product_document, application_blocks))
        issues.extend(self._validate_optional_package_coverage(product_document, application_blocks))
        return issues

    def _validate_evidence(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for key in EVIDENCE_LIST_KEYS:
            for index, item in enumerate(product_document.get(key, [])):
                if not isinstance(item, dict):
                    continue
                if not item.get("source_evidence"):
                    issues.append({"severity": "warning", "path": f"{key}[{index}].source_evidence", "message": "missing source evidence"})
                location = item.get("source_location")
                if item.get("source_evidence") and isinstance(location, dict) and not location.get("path"):
                    issues.append({"severity": "warning", "path": f"{key}[{index}].source_location.path", "message": "missing source file path"})
                if "confidence" not in item:
                    issues.append({"severity": "warning", "path": f"{key}[{index}].confidence", "message": "missing confidence"})

        for index, item in enumerate(product_document.get("base_package", {}).get("packages", [])):
            if not isinstance(item, dict):
                continue
            if not item.get("source_evidence"):
                issues.append({"severity": "warning", "path": f"base_package.packages[{index}].source_evidence", "message": "missing source evidence"})
            location = item.get("source_location")
            if item.get("source_evidence") and isinstance(location, dict) and not location.get("path"):
                issues.append({"severity": "warning", "path": f"base_package.packages[{index}].source_location.path", "message": "missing source file path"})
        return issues

    def _validate_possible_un_split_lists(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for key in EVIDENCE_LIST_KEYS:
            for index, item in enumerate(product_document.get(key, [])):
                if not isinstance(item, dict):
                    continue
                if key == "optional_packages" and not looks_like_composite_optional_package(item):
                    continue
                text = " ".join(str(item.get(field, "")) for field in ("description", "source_evidence"))
                if looks_like_multiple_items(text):
                    issues.append(
                        {
                            "severity": "warning",
                            "path": f"{key}[{index}]",
                            "message": "item may contain multiple facts; split into multiple objects if they are separate business records",
                        }
                    )
        return issues

    def _validate_semantic_values(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        document_status = product_document.get("document_info", {}).get("document_status", "")
        if not document_status:
            issues.append(
                {
                    "severity": "warning",
                    "path": "document_info.document_status",
                    "message": "document_status is empty; infer active/inactive before review or publishing",
                }
            )

        for index, item in enumerate(product_document.get("base_package", {}).get("packages", [])):
            if not isinstance(item, dict):
                continue
            contract_period = str(item.get("contract_period", "")).strip()
            if contract_period and looks_like_billing_unit(contract_period):
                issues.append(
                    {
                        "severity": "error",
                        "path": f"base_package.packages[{index}].contract_period",
                        "message": "contract_period looks like a billing/unit value, not a real agreement term",
                        "actual": contract_period,
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

    def _validate_application_field_coverage(self, product_document: dict[str, Any], blocks: list[Any]) -> list[dict[str, Any]]:
        source_labels = collect_required_application_labels(blocks)
        if not source_labels:
            return []
        extracted_text = extracted_application_field_text(product_document)
        extracted_compact_text = normalize_compact_text(extracted_text)
        issues: list[dict[str, Any]] = []
        for label in source_labels:
            if label and normalize_compact_text(label) not in extracted_compact_text:
                issues.append(
                    {
                        "severity": "warning",
                        "path": "parties_and_application.application_fields",
                        "message": f"high-priority application field from source was not extracted: {label}",
                    }
                )
        return issues

    def _validate_package_coverage(self, product_document: dict[str, Any], blocks: list[Any]) -> list[dict[str, Any]]:
        source_text = "\n".join(getattr(block, "text", "") for block in blocks)
        packages = product_document.get("base_package", {}).get("packages", [])
        if "基础套餐申请信息" in source_text and not packages:
            return [
                {
                    "severity": "error",
                    "path": "base_package.packages",
                    "message": "source contains base package application section but no package was extracted",
                }
            ]
        return []

    def _validate_optional_package_coverage(self, product_document: dict[str, Any], blocks: list[Any]) -> list[dict[str, Any]]:
        expected_names = collect_optional_package_row_names(blocks)
        if not expected_names:
            return []
        extracted_text = "\n".join(
            " ".join(str(value) for value in item.values())
            for item in product_document.get("optional_packages", [])
            if isinstance(item, dict)
        )
        issues: list[dict[str, Any]] = []
        for name in expected_names:
            if name and name not in extracted_text:
                issues.append(
                    {
                        "severity": "warning",
                        "path": "optional_packages",
                        "message": f"high-priority optional package row from source was not extracted: {name}",
                    }
                )
        return issues


def validate_against_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[dict[str, Any]]:
    """Recursively validate extracted JSON against the local schema definition.

    This intentionally covers the parts we rely on for review JSON quality:
    required fields, extra fields, array/object structure, scalar types, and enums.
    It avoids adding an external jsonschema dependency to keep the MVP lightweight.
    """
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


def blocks_before_marker(source_blocks: list[Any], marker_patterns: tuple[str, ...]) -> list[Any]:
    result: list[Any] = []
    for block in source_blocks:
        text = getattr(block, "text", "")
        if any(pattern in text for pattern in marker_patterns):
            break
        result.append(block)
    return result


def collect_required_application_labels(blocks: list[Any]) -> list[str]:
    labels: list[str] = []
    for block in blocks:
        if getattr(block, "block_type", "") != "table_row":
            continue
        text = getattr(block, "text", "")
        if "基础套餐申请信息" in text:
            break
        cells = getattr(block, "metadata", {}).get("cells", [])
        for cell in cells:
            label = extract_required_label(str(cell))
            if label and label not in labels:
                labels.append(label)
    return labels


def extract_required_label(cell_text: str) -> str:
    text = cell_text.strip()
    if "*" not in text:
        return ""
    text = text.split("□", 1)[0]
    text = text.split("：", 1)[0]
    text = text.split(":", 1)[0]
    text = text.replace("*", "")
    text = re.sub(r"[_\s]+", "", text)
    return text if 2 <= len(text) <= 30 else ""


def extracted_application_field_text(product_document: dict[str, Any]) -> str:
    fields = product_document.get("parties_and_application", {}).get("application_fields", {})
    values: list[str] = []
    if isinstance(fields, dict):
        for key in ("required", "optional"):
            for item in fields.get(key, []):
                if not isinstance(item, dict):
                    continue
                values.extend(str(item.get(field, "")) for field in ("label", "field_key", "source_evidence"))
    return "\n".join(values)


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


def collect_optional_package_row_names(blocks: list[Any]) -> list[str]:
    names: list[str] = []
    in_package_section = False
    for block in blocks:
        if getattr(block, "block_type", "") != "table_row":
            continue
        text = getattr(block, "text", "")
        if "基础套餐申请信息" in text:
            in_package_section = True
            continue
        if not in_package_section:
            continue
        if "填表说明" in text:
            break
        cells = getattr(block, "metadata", {}).get("cells", [])
        if not cells:
            continue
        name = normalize_optional_row_name(str(cells[0]))
        if name and name not in {"基础套餐", "套餐类型"} and name not in names:
            names.append(name)
    return names


def normalize_optional_row_name(value: str) -> str:
    text = value.strip()
    text = text.split("（", 1)[0].strip()
    text = re.sub(r"月基本费.*$", "", text).strip()
    text = re.sub(r"\s+", "", text)
    return text if 2 <= len(text) <= 30 else ""


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


def looks_like_multiple_items(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return False
    return any(re.search(pattern, compact) for pattern in MULTI_ITEM_PATTERNS)


def looks_like_composite_optional_package(item: dict[str, Any]) -> bool:
    name = str(item.get("name", ""))
    if "/" in name or "／" in name:
        return True
    source = str(item.get("source_evidence", ""))
    return bool(re.search(r"固话.*[/／].*商云通|商云通.*[/／].*固话", source))


def looks_like_billing_unit(value: str) -> bool:
    compact = normalize_compact_text(value)
    invalid_values = {"线", "次", "月", "年", "元", "元/月", "元/年", "月/线", "年/线", "元/月/线", "元/年/线"}
    if compact in invalid_values:
        return True
    return bool(re.fullmatch(r"[./\\-]*(线|次|月|年)", compact))


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
