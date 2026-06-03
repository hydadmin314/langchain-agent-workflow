from __future__ import annotations

import os
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
    """把大模型模块输出对齐到模块 schema，不新增业务事实。"""

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
    """归一化合并后的产品文档，不新增无依据的业务事实。"""

    def normalize_product_document(self, product_document: dict[str, Any]) -> ProductDocumentNormalizationResult:
        """执行字段清洗、去重、标准化和轻量纠偏。"""

        issues: list[dict[str, Any]] = []
        self._normalize_extraction_meta(product_document)
        issues.extend(self._fill_document_status(product_document))
        self._fill_source_location_paths(product_document)
        issues.extend(self._disambiguate_duplicate_application_labels(product_document))
        issues.extend(self._normalize_application_fields(product_document))
        issues.extend(self._align_application_fields_by_required_marker(product_document))
        issues.extend(self._remove_form_field_materials(product_document))
        issues.extend(self._remove_duplicate_application_attributes(product_document))
        issues.extend(self._remove_duplicate_application_constraints(product_document))
        issues.extend(self._filter_eligibility_constraints(product_document))
        issues.extend(self._summarize_material_list_constraints(product_document))
        issues.extend(self._add_package_specific_applies_to(product_document))
        issues.extend(self._remove_same_document_supplemental_rules(product_document))
        issues.extend(self._remove_empty_supplemental_rules(product_document))
        issues.extend(self._remove_duplicate_service_attributes(product_document))
        issues.extend(self._remove_empty_or_redundant_service_attributes(product_document))
        issues.extend(self._move_misplaced_service_attributes(product_document))
        issues.extend(self._normalize_included_item_names(product_document))
        issues.extend(self._move_condition_included_items_to_packages(product_document))
        issues.extend(self._dedupe_condition_lists(product_document))
        issues.extend(self._remove_optional_items_from_included_items(product_document))
        issues.extend(self._split_composite_optional_packages(product_document))
        issues.extend(self._normalize_currency_values(product_document))
        issues.extend(self._normalize_period_values(product_document))
        issues.extend(self._normalize_billing_period_from_evidence(product_document))
        issues.extend(self._fill_nested_source_locations(product_document))
        issues.extend(self._clean_editorial_markers(product_document))
        issues.extend(self._normalize_optional_package_price_items(product_document))
        issues.extend(self._normalize_price_item_shape(product_document))
        issues.extend(self._normalize_contract_period(product_document))
        issues.extend(self._normalize_base_package_variant_keys(product_document))
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

    def _normalize_application_fields(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        fields = product_document.get("parties_and_application", {}).get("application_fields", {})
        if not isinstance(fields, dict):
            return []

        issues: list[dict[str, Any]] = []
        for bucket in ("required", "optional"):
            items = fields.get(bucket, [])
            if not isinstance(items, list):
                continue

            kept: list[Any] = []
            seen: set[tuple[str, str, str]] = set()
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    kept.append(item)
                    continue

                if not item.get("value_type"):
                    item["value_type"] = infer_application_value_type(item)
                    issues.append(
                        {
                            "severity": "warning",
                            "path": f"parties_and_application.application_fields.{bucket}[{index}].value_type",
                            "message": f"filled empty application field value_type with {item['value_type']}",
                        }
                    )

                cleaned_evidence = clean_application_field_evidence(item)
                if cleaned_evidence and cleaned_evidence != item.get("source_evidence"):
                    item["source_evidence"] = cleaned_evidence
                    issues.append(
                        {
                            "severity": "warning",
                            "path": f"parties_and_application.application_fields.{bucket}[{index}].source_evidence",
                            "message": "cleaned application field source_evidence to the current field only",
                        }
                    )

                key = (
                    canonical_name(str(item.get("field_key", "")) or str(item.get("label", ""))),
                    canonical_name(str(item.get("label", ""))),
                    compact_text(str(item.get("source_evidence", ""))),
                )
                if key[0] and key in seen:
                    issues.append(
                        {
                            "severity": "warning",
                            "path": f"parties_and_application.application_fields.{bucket}[{index}]",
                            "message": f"removed duplicated application field: {item.get('label', '')}",
                        }
                    )
                    continue
                seen.add(key)
                kept.append(item)

            fields[bucket] = kept
        return issues

    def _align_application_fields_by_required_marker(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        fields = product_document.get("parties_and_application", {}).get("application_fields", {})
        if not isinstance(fields, dict):
            return []

        required_items = fields.get("required", [])
        optional_items = fields.get("optional", [])
        if not isinstance(required_items, list) or not isinstance(optional_items, list):
            return []

        marker_count = sum(1 for item in required_items if isinstance(item, dict) and has_required_marker(item))
        if marker_count < 2:
            return []

        kept_required: list[Any] = []
        moved_optional: list[Any] = []
        issues: list[dict[str, Any]] = []
        for index, item in enumerate(required_items):
            if not isinstance(item, dict) or has_required_marker(item):
                kept_required.append(item)
                continue
            if has_explicit_required_text(item):
                kept_required.append(item)
                continue
            clone = dict(item)
            clone["required"] = False
            moved_optional.append(clone)
            issues.append(
                {
                    "severity": "warning",
                    "path": f"parties_and_application.application_fields.required[{index}]",
                    "message": "moved application field without required marker to optional fields",
                }
            )

        fields["required"] = kept_required
        fields["optional"] = dedupe_application_fields([*optional_items, *moved_optional])
        return issues

    def _remove_form_field_materials(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        materials = product_document.get("application_materials", [])
        if not isinstance(materials, list):
            return []

        kept: list[Any] = []
        issues: list[dict[str, Any]] = []
        for index, item in enumerate(materials):
            if not isinstance(item, dict):
                kept.append(item)
                continue
            if is_form_field_material_false_positive(item):
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"application_materials[{index}]",
                        "message": "removed form field label that was misclassified as application material",
                    }
                )
                continue
            kept.append(item)
        product_document["application_materials"] = kept
        return issues

    def _disambiguate_duplicate_application_labels(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        fields = product_document.get("parties_and_application", {}).get("application_fields", {})
        if not isinstance(fields, dict):
            return []

        entries: list[tuple[str, int, dict[str, Any]]] = []
        for bucket in ("required", "optional"):
            items = fields.get(bucket, [])
            if not isinstance(items, list):
                continue
            for index, item in enumerate(items):
                if isinstance(item, dict):
                    entries.append((bucket, index, item))

        label_counts: dict[str, int] = {}
        for _, _, item in entries:
            label_key = canonical_name(str(item.get("label", "")))
            if label_key:
                label_counts[label_key] = label_counts.get(label_key, 0) + 1
        row_contexts = collect_application_row_contexts(entries)

        issues: list[dict[str, Any]] = []
        for bucket, index, item in entries:
            label = str(item.get("label", "")).strip()
            label_key = canonical_name(label)
            if not label_key or label_counts.get(label_key, 0) <= 1:
                continue
            if "（" in label or "(" in label:
                continue

            context = infer_application_field_context(item) or row_contexts.get(application_row_key(item), "")
            if not context:
                continue

            item["label"] = f"{label}（{context}）"
            item["field_key"] = disambiguate_field_key(str(item.get("field_key", "")), context)
            issues.append(
                {
                    "severity": "warning",
                    "path": f"parties_and_application.application_fields.{bucket}[{index}].label",
                    "message": f"disambiguated duplicated application field label with context: {context}",
                }
            )
        return issues

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

    def _remove_empty_or_redundant_service_attributes(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        base_package = product_document.get("base_package", {})
        service_attributes = base_package.get("service_attributes", []) if isinstance(base_package, dict) else []
        if not isinstance(service_attributes, list):
            return []

        kept: list[Any] = []
        issues: list[dict[str, Any]] = []
        for index, item in enumerate(service_attributes):
            if not isinstance(item, dict):
                kept.append(item)
                continue

            name = first_non_empty(item, ("attribute_name", "label", "name", "field_key"))
            value = first_non_empty(item, ("attribute_value", "value", "description"))
            evidence = str(item.get("source_evidence", "")).strip()
            confidence = item.get("confidence")

            if is_empty_service_attribute(name=name, value=value, evidence=evidence, confidence=confidence):
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"base_package.service_attributes[{index}]",
                        "message": "removed empty service attribute without source evidence",
                    }
                )
                continue

            if is_voice_presence_attribute(name):
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"base_package.service_attributes[{index}]",
                        "message": "removed voice presence service attribute; base_package.packages[*].has_voice is the canonical field",
                    }
                )
                continue
            kept.append(item)

        base_package["service_attributes"] = kept
        return issues

    def _move_misplaced_service_attributes(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        base_package = product_document.get("base_package", {})
        service_attributes = base_package.get("service_attributes", []) if isinstance(base_package, dict) else []
        if not isinstance(service_attributes, list):
            return []

        package_speeds = {
            compact_text(str(item.get("speed", "")))
            for item in base_package.get("packages", [])
            if isinstance(item, dict) and item.get("speed")
        }

        kept: list[Any] = []
        included_items = base_package.setdefault("included_items", [])
        if not isinstance(included_items, list):
            included_items = []
            base_package["included_items"] = included_items

        issues: list[dict[str, Any]] = []
        for index, item in enumerate(service_attributes):
            if not isinstance(item, dict):
                kept.append(item)
                continue

            name = first_non_empty(item, ("attribute_name", "label", "name", "field_key"))
            value = first_non_empty(item, ("attribute_value", "value", "description"))
            if is_speed_duplicate_attribute(name, value, package_speeds):
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"base_package.service_attributes[{index}]",
                        "message": "removed service attribute duplicated with package speed",
                    }
                )
                continue

            if looks_like_included_resource_attribute(name):
                included_items.append(service_attribute_to_included_item(item))
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"base_package.service_attributes[{index}]",
                        "message": "moved included-resource service attribute to included_items",
                    }
                )
                continue
            kept.append(item)

        base_package["service_attributes"] = kept
        return issues

    def _normalize_included_item_names(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        included_items = product_document.get("base_package", {}).get("included_items", [])
        if not isinstance(included_items, list):
            return []

        issues: list[dict[str, Any]] = []
        for index, item in enumerate(included_items):
            if not isinstance(item, dict):
                continue
            if item.get("item_name") or not item.get("name"):
                continue
            item["item_name"] = item.pop("name")
            issues.append(
                {
                    "severity": "warning",
                    "path": f"base_package.included_items[{index}].item_name",
                    "message": "renamed included item name to item_name",
                }
            )
        return issues

    def _move_condition_included_items_to_packages(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        base_package = product_document.get("base_package", {})
        included_items = base_package.get("included_items", []) if isinstance(base_package, dict) else []
        packages = base_package.get("packages", []) if isinstance(base_package, dict) else []
        if not isinstance(included_items, list) or not isinstance(packages, list):
            return []

        kept: list[Any] = []
        issues: list[dict[str, Any]] = []
        for index, item in enumerate(included_items):
            if not isinstance(item, dict) or not looks_like_condition_included_item(item):
                kept.append(item)
                continue

            condition = first_non_empty(item, ("description", "source_evidence", "item_name", "name"))
            target_package = find_package_by_source_location(packages, item.get("source_location", {}))
            if target_package is not None and condition:
                append_applicable_condition(target_package, condition)
            issues.append(
                {
                    "severity": "warning",
                    "path": f"base_package.included_items[{index}]",
                    "message": "moved condition-like included item to package applicable_conditions",
                }
            )
        base_package["included_items"] = kept
        return issues

    def _dedupe_condition_lists(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for path, item in iter_dicts(product_document):
            for key in ("applicable_conditions", "conditions"):
                values = item.get(key)
                if not isinstance(values, list):
                    continue
                deduped = dedupe_condition_values(values)
                if deduped == values:
                    continue
                item[key] = deduped
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"{path}.{key}",
                        "message": "removed duplicated or merged condition text",
                    }
                )
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
                if normalize_price_item_billing_period(price_item):
                    changed = True
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
            if add_option_context_to_price_evidence(item):
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"optional_packages[{package_index}].price_items",
                        "message": "added option context to short price item source_evidence",
                    }
                )
        return issues

    def _normalize_price_item_shape(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for package_index, package in enumerate(product_document.get("optional_packages", [])):
            if not isinstance(package, dict):
                continue
            price_items = package.get("price_items", [])
            if not isinstance(price_items, list):
                continue

            kept: list[Any] = []
            seen: set[tuple[Any, str, str]] = set()
            for item_index, price_item in enumerate(price_items):
                if not isinstance(price_item, dict):
                    kept.append(price_item)
                    continue
                if "item_name" in price_item:
                    price_item.pop("item_name", None)
                    issues.append(
                        {
                            "severity": "warning",
                            "path": f"optional_packages[{package_index}].price_items[{item_index}].item_name",
                            "message": "removed non-schema helper field item_name from price item",
                        }
                    )
                key = (
                    price_item.get("price"),
                    str(price_item.get("currency", "")),
                    str(price_item.get("billing_period", "")),
                )
                if key[0] is not None and key in seen:
                    issues.append(
                        {
                            "severity": "warning",
                            "path": f"optional_packages[{package_index}].price_items[{item_index}]",
                            "message": "removed duplicate price item in the same optional package",
                        }
                    )
                    continue
                seen.add(key)
                kept.append(price_item)
            package["price_items"] = kept
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

    def _normalize_period_values(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for path, item in iter_dicts(product_document):
            for key in ("billing_period", "contract_period"):
                value = item.get(key)
                if not isinstance(value, str) or not value.strip():
                    continue
                normalized_value = normalize_period_label(value)
                if normalized_value != value:
                    item[key] = normalized_value
                    issues.append(
                        {
                            "severity": "warning",
                            "path": f"{path}.{key}",
                            "message": f"normalized {key} to {normalized_value}",
                        }
                    )

            package_name = item.get("package_name")
            if isinstance(package_name, str) and package_name:
                normalized_name = normalize_period_suffix_in_name(package_name)
                if normalized_name != package_name:
                    item["package_name"] = normalized_name
                    issues.append(
                        {
                            "severity": "warning",
                            "path": f"{path}.package_name",
                            "message": "normalized period suffix in package_name",
                        }
                    )
        return issues

    def _normalize_billing_period_from_evidence(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for path, item in iter_dicts(product_document):
            if "billing_period" not in item:
                continue
            evidence = str(item.get("source_evidence", ""))
            inferred = infer_billing_period_for_item(item, evidence)
            if not inferred or item.get("billing_period") == inferred:
                continue
            item["billing_period"] = inferred
            issues.append(
                {
                    "severity": "warning",
                    "path": f"{path}.billing_period",
                    "message": f"normalized billing_period from source_evidence to {inferred}",
                }
            )
        return issues

    def _fill_nested_source_locations(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for path, item in iter_dicts(product_document):
            if not isinstance(item, dict):
                continue
            parent_location = item.get("source_location")
            if not isinstance(parent_location, dict):
                continue
            for key in ("price_items", "rules", "fields"):
                children = item.get(key)
                if not isinstance(children, list):
                    continue
                for child_index, child in enumerate(children):
                    if not isinstance(child, dict):
                        continue
                    if child.get("source_evidence") and not isinstance(child.get("source_location"), dict):
                        child["source_location"] = dict(parent_location)
                        issues.append(
                            {
                                "severity": "warning",
                                "path": f"{path}.{key}[{child_index}].source_location",
                                "message": "filled nested source_location from parent item",
                            }
                        )
        return issues

    def _clean_editorial_markers(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for path, item in iter_dicts(product_document):
            for key in ("name", "title", "package_name", "item_name"):
                value = item.get(key)
                if not isinstance(value, str) or not value:
                    continue
                cleaned = remove_editorial_markers(value)
                if cleaned == value:
                    continue
                item[key] = cleaned
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"{path}.{key}",
                        "message": "removed editorial marker from extracted label",
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

    def _remove_same_document_supplemental_rules(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        """只保留来自其它文件的 supplemental_rules。

        supplemental_rules 用于跨文件补充，例如外部价格表、折扣政策等。
        如果某条 supplemental_rule 的 source_path 与当前主文档相同，
        它实际是主文档内部章节，应交给普通规则/材料模块处理。
        """
        supplemental_rules = product_document.get("supplemental_rules", [])
        if not isinstance(supplemental_rules, list):
            return []

        main_source_path = normalized_path(product_document.get("document_info", {}).get("source_path", ""))
        if not main_source_path:
            return []

        kept: list[Any] = []
        issues: list[dict[str, Any]] = []
        for index, item in enumerate(supplemental_rules):
            if not isinstance(item, dict):
                kept.append(item)
                continue

            rule_source_path = normalized_path(extract_source_location_path(item))
            if rule_source_path and rule_source_path == main_source_path:
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"supplemental_rules[{index}]",
                        "message": "removed same-document supplemental rule; supplemental_rules only keeps rules from other source files",
                    }
                )
                continue
            kept.append(item)

        product_document["supplemental_rules"] = kept
        return issues

    def _remove_empty_supplemental_rules(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        supplemental_rules = product_document.get("supplemental_rules", [])
        if not isinstance(supplemental_rules, list):
            return []

        kept: list[Any] = []
        issues: list[dict[str, Any]] = []
        for index, item in enumerate(supplemental_rules):
            if isinstance(item, dict) and is_empty_schema_object(item):
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"supplemental_rules[{index}]",
                        "message": "removed empty supplemental rule object",
                    }
                )
                continue
            kept.append(item)
        product_document["supplemental_rules"] = kept
        return issues

    def _filter_eligibility_constraints(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        constraints = product_document.get("eligibility_and_constraints", [])
        if not isinstance(constraints, list):
            return []

        kept: list[Any] = []
        issues: list[dict[str, Any]] = []
        for index, item in enumerate(constraints):
            if not isinstance(item, dict):
                kept.append(item)
                continue
            if is_core_eligibility_constraint(item):
                kept.append(item)
                continue
            issues.append(
                {
                    "severity": "warning",
                    "path": f"eligibility_and_constraints[{index}]",
                    "message": "removed non-eligibility agreement/compliance clause from eligibility constraints",
                }
            )
        product_document["eligibility_and_constraints"] = kept
        return issues

    def _summarize_material_list_constraints(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        constraints = product_document.get("eligibility_and_constraints", [])
        materials = product_document.get("application_materials", [])
        if not isinstance(constraints, list) or not isinstance(materials, list):
            return []

        material_names = [str(item.get("material_name", "")).strip() for item in materials if isinstance(item, dict) and item.get("material_name")]
        if not material_names:
            return []

        issues: list[dict[str, Any]] = []
        for index, item in enumerate(constraints):
            if not isinstance(item, dict):
                continue
            text = str(item.get("description", "")) + str(item.get("source_evidence", ""))
            matched = [name for name in material_names if name and name in text]
            if len(matched) < 3:
                continue
            item["description"] = "\u5ba2\u6237\u9700\u5177\u5907\u5e76\u6309\u8981\u6c42\u63d0\u4f9b\u76f8\u5173\u8d44\u8d28\u8bc1\u660e\u6750\u6599\u3002"
            related_materials = item.get("related_materials", [])
            if not isinstance(related_materials, list):
                related_materials = []
            for name in matched:
                if name not in related_materials:
                    related_materials.append(name)
            item["related_materials"] = related_materials
            issues.append(
                {
                    "severity": "warning",
                    "path": f"eligibility_and_constraints[{index}].description",
                    "message": "summarized material-list constraint and moved material names to related_materials",
                }
            )
        return issues

    def _add_package_specific_applies_to(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        package_names = [
            str(item.get("package_name", "")).strip()
            for item in product_document.get("base_package", {}).get("packages", [])
            if isinstance(item, dict) and item.get("package_name")
        ]
        constraints = product_document.get("eligibility_and_constraints", [])
        if not package_names or not isinstance(constraints, list):
            return []

        issues: list[dict[str, Any]] = []
        for index, item in enumerate(constraints):
            if not isinstance(item, dict):
                continue
            evidence = str(item.get("source_evidence", ""))
            applies_to = item.get("applies_to", [])
            if not isinstance(applies_to, list):
                applies_to = []
            changed = False
            for name in package_names:
                if name and name in evidence and name not in applies_to:
                    applies_to.append(name)
                    changed = True
            if changed:
                item["applies_to"] = applies_to
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"eligibility_and_constraints[{index}].applies_to",
                        "message": "added package-specific applies_to from source evidence",
                    }
                )
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

    def _disambiguate_duplicate_base_package_names(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        packages = product_document.get("base_package", {}).get("packages", [])
        if not isinstance(packages, list):
            return []

        name_counts: dict[str, int] = {}
        for item in packages:
            if isinstance(item, dict):
                name = str(item.get("package_name", "")).strip()
                if name:
                    name_counts[name] = name_counts.get(name, 0) + 1

        issues: list[dict[str, Any]] = []
        used_names: set[str] = set()
        for index, item in enumerate(packages):
            if not isinstance(item, dict):
                continue
            name = str(item.get("package_name", "")).strip()
            if not name or name_counts.get(name, 0) <= 1:
                used_names.add(name)
                continue

            suffix = base_package_variant_suffix(item)
            candidate = f"{name}（{suffix}）" if suffix else name
            if candidate in used_names:
                price = item.get("price")
                candidate = f"{candidate}-{price:g}" if isinstance(price, (int, float)) and not isinstance(price, bool) else candidate
            if candidate == name:
                continue
            item["package_name"] = candidate
            used_names.add(candidate)
            issues.append(
                {
                    "severity": "warning",
                    "path": f"base_package.packages[{index}].package_name",
                    "message": "disambiguated duplicated base package name with billing variant",
                }
            )
        return issues

    def _normalize_base_package_variant_keys(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        packages = product_document.get("base_package", {}).get("packages", [])
        if not isinstance(packages, list):
            return []

        issues: list[dict[str, Any]] = []
        for index, item in enumerate(packages):
            if not isinstance(item, dict):
                continue

            name = str(item.get("package_name", "")).strip()
            cleaned_name = strip_variant_suffix_from_package_name(name)
            if cleaned_name and cleaned_name != name:
                item["package_name"] = cleaned_name
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"base_package.packages[{index}].package_name",
                        "message": "moved billing or price suffix out of package_name",
                    }
                )

            package_code = item.get("package_code")
            if package_code:
                normalized_code = normalize_package_code(str(package_code))
                if normalized_code != package_code:
                    item["package_code"] = normalized_code
                    issues.append(
                        {
                            "severity": "warning",
                            "path": f"base_package.packages[{index}].package_code",
                            "message": "normalized generated package_code formatting",
                        }
                    )
                continue
            item["package_code"] = build_package_variant_code(item, index)
            issues.append(
                {
                    "severity": "warning",
                    "path": f"base_package.packages[{index}].package_code",
                    "message": "filled package_code with deterministic package variant key",
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


def normalize_price_item_billing_period(price_item: dict[str, Any]) -> bool:
    """根据价格项自己的金额和证据，确定性修正计费周期。"""

    evidence = str(price_item.get("source_evidence", ""))
    inferred = infer_billing_period_for_item(price_item, evidence)
    if not inferred or price_item.get("billing_period") == inferred:
        return False
    price_item["billing_period"] = inferred
    return True


def infer_billing_period_for_item(item: dict[str, Any], evidence: str) -> str:
    """优先按当前金额在证据中的最近单位推断周期，避免一条证据里月费/年费互相干扰。"""

    amount = first_numeric_value(item.get("price"))
    if amount is None:
        amount = first_numeric_value(item.get("amount"))
    if amount is not None:
        inferred = infer_billing_period_for_amount(evidence, amount)
        if inferred:
            return inferred
    return infer_billing_period_from_evidence(evidence)


def infer_billing_period_for_amount(evidence: str, amount: float) -> str:
    """从“100元/月 1200元/年”这类证据中，按金额精确找到对应周期。"""

    text = str(evidence or "")
    if not text:
        return ""
    amount_pattern = format_amount_pattern(amount)
    pattern = re.compile(
        rf"(?<!\d){amount_pattern}\s*元\s*(?:/|／)?\s*(2年|两年|年|月|线|号|次|分钟|条|GB|MB)?",
        flags=re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        return ""
    return normalize_billing_period(match.group(1) or "")


def first_numeric_value(value: Any) -> float | None:
    """把模型输出的金额安全转成数字，用于周期纠偏。"""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        if match:
            return float(match.group(0))
    return None


def format_amount_pattern(amount: float) -> str:
    """生成兼容整数和小数写法的金额正则。"""

    if float(amount).is_integer():
        return rf"{int(amount)}(?:\.0+)?"
    return re.escape(f"{amount:g}")


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


def add_option_context_to_price_evidence(optional_package: dict[str, Any]) -> bool:
    options = [str(option).strip() for option in optional_package.get("options", []) if str(option).strip()]
    price_items = [item for item in optional_package.get("price_items", []) if isinstance(item, dict)]
    if not options or not price_items or len(price_items) % len(options) != 0:
        return False

    prices_per_option = len(price_items) // len(options)
    if prices_per_option <= 0:
        return False

    changed = False
    for index, price_item in enumerate(price_items):
        option = options[index // prices_per_option]
        evidence = str(price_item.get("source_evidence", "")).strip()
        if not evidence or compact_text(option) in compact_text(evidence):
            continue
        if is_short_price_evidence(evidence):
            price_item["source_evidence"] = f"{option}：{evidence}"
            if not price_item.get("item_name"):
                price_item["item_name"] = option
            changed = True
    return changed


def is_short_price_evidence(value: str) -> bool:
    text = compact_text(value)
    return bool(text and len(text) <= 24 and re.search(r"\d", text))


def iter_dicts(value: Any, path: str = "$"):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from iter_dicts(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_dicts(child, f"{path}[{index}]")


def infer_application_value_type(item: dict[str, Any]) -> str:
    options = item.get("options", [])
    if isinstance(options, list) and options:
        return "single_select"
    label = compact_text(str(item.get("label", "")))
    if any(token in label.lower() for token in ("email", "e-mail")):
        return "email"
    if any(token in label for token in ("\u7535\u8bdd", "\u624b\u673a", "\u4f20\u771f")):
        return "phone"
    return "text"


def has_required_marker(item: dict[str, Any]) -> bool:
    text = " ".join(str(item.get(key, "")) for key in ("label", "source_evidence"))
    return "*" in text or "＊" in text


def has_explicit_required_text(item: dict[str, Any]) -> bool:
    text = compact_text(" ".join(str(item.get(key, "")) for key in ("label", "source_evidence")))
    return any(marker in text for marker in ("\u5fc5\u586b", "\u5fc5\u987b\u586b\u5199", "\u5fc5\u987b\u63d0\u4f9b"))


def is_form_field_material_false_positive(item: dict[str, Any]) -> bool:
    name = compact_text(str(item.get("material_name", "")))
    evidence = compact_text(str(item.get("source_evidence", "")))
    if not name or not evidence:
        return False

    material_markers = ("\u590d\u5370\u4ef6", "\u539f\u4ef6", "\u626b\u63cf\u4ef6", "\u63d0\u4f9b", "\u63d0\u4ea4", "\u987b\u9644", "\u52a0\u76d6", "\u6750\u6599")
    field_markers = ("\u8eab\u4efd\u8bc1\u53f7\u7801", "\u7ecf\u529e\u4eba\u7b7e\u5b57", "\u5ba2\u6237\u76d6\u7ae0", "\u65e5\u671f")
    if "\u8eab\u4efd\u8bc1" in name and any(marker in evidence for marker in field_markers):
        return not any(marker in evidence for marker in material_markers)
    return False


def dedupe_application_fields(items: list[Any]) -> list[Any]:
    seen: set[tuple[str, str, str]] = set()
    result: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            result.append(item)
            continue
        key = (
            canonical_name(str(item.get("field_key", "")) or str(item.get("label", ""))),
            canonical_name(str(item.get("label", ""))),
            compact_text(str(item.get("source_evidence", ""))),
        )
        if key[0] and key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def clean_application_field_evidence(item: dict[str, Any]) -> str:
    label = str(item.get("label", "")).strip()
    evidence = str(item.get("source_evidence", "")).strip()
    if not label or not evidence:
        return evidence

    cells = split_evidence_cells(evidence)
    label_index = find_label_cell_index(cells, label)
    options = [str(option).strip() for option in item.get("options", []) if str(option).strip()]
    if label_index is None:
        if options:
            return f"{format_application_label(item)} | {' '.join(options)}"
        return evidence

    label_cell = cells[label_index].strip()
    value = item.get("value")
    value_text = "" if value is None else str(value).strip()

    if options:
        option_text = " ".join(options)
        if cell_contains_options(label_cell, options):
            return label_cell
        if option_text and option_text not in label_cell:
            return f"{label_cell} | {option_text}"
        return label_cell
    if value_text:
        return f"{label_cell} | {value_text}"
    return label_cell


def format_application_label(item: dict[str, Any]) -> str:
    label = str(item.get("label", "")).strip()
    if item.get("required") is True and label and not label.startswith("*"):
        return f"*{label}"
    return label


def split_evidence_cells(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s*[|｜]\s*", value or "") if part.strip()]


def cell_contains_options(cell: str, options: list[str]) -> bool:
    if not options:
        return False
    normalized_cell = normalize_option_text(cell)
    matched = sum(1 for option in options if normalize_option_text(option) in normalized_cell)
    return matched == len(options)


def normalize_option_text(value: str) -> str:
    return re.sub(r"[\s□■☑✓*＊:：|]+", "", value or "")


def find_label_cell_index(cells: list[str], label: str) -> int | None:
    target = canonical_name(label)
    if not target:
        return None
    for index, cell in enumerate(cells):
        if target and target in canonical_name(cell):
            return index
    return None


ADDRESS_CONTEXT_TERMS = (
    "\u5b89\u88c5\u5730\u5740",
    "\u8d26\u5355\u5730\u5740",
    "\u901a\u4fe1\u5730\u5740",
    "\u8054\u7cfb\u5730\u5740",
    "\u6ce8\u518c\u5730\u5740",
    "\u88c5\u673a\u5730\u5740",
)


def infer_application_field_context(item: dict[str, Any]) -> str:
    evidence = str(item.get("source_evidence", ""))
    label = str(item.get("label", ""))
    cells = split_evidence_cells(evidence)
    label_index = find_label_cell_index(cells, label)
    search_cells = cells
    if label_index is not None:
        search_cells = cells[:label_index] + cells[label_index + 1 :]
    for cell in reversed(search_cells):
        context = extract_address_context(cell)
        if context:
            return context
    return ""


def collect_application_row_contexts(entries: list[tuple[str, int, dict[str, Any]]]) -> dict[tuple[Any, Any], str]:
    contexts: dict[tuple[Any, Any], str] = {}
    for _, _, item in entries:
        context = extract_address_context(str(item.get("label", ""))) or extract_address_context(str(item.get("source_evidence", "")))
        if not context:
            continue
        key = application_row_key(item)
        if key != (None, None):
            contexts[key] = context
    return contexts


def application_row_key(item: dict[str, Any]) -> tuple[Any, Any]:
    location = item.get("source_location", {})
    if not isinstance(location, dict):
        return (None, None)
    return (location.get("table_index"), location.get("row_index"))


def extract_address_context(text: str) -> str:
    compact = compact_text(text)
    for term in ADDRESS_CONTEXT_TERMS:
        if compact_text(term) in compact:
            return term
    return ""


def disambiguate_field_key(field_key: str, context: str) -> str:
    key = (field_key or "").strip()
    context_key = {
        "\u5b89\u88c5\u5730\u5740": "installation",
        "\u88c5\u673a\u5730\u5740": "installation",
        "\u8d26\u5355\u5730\u5740": "billing",
        "\u901a\u4fe1\u5730\u5740": "mailing",
        "\u8054\u7cfb\u5730\u5740": "contact",
        "\u6ce8\u518c\u5730\u5740": "registered",
    }.get(context, "")
    if not context_key or key.startswith(f"{context_key}_") or context_key in key.split("_"):
        return key
    return f"{context_key}_{key}" if key else context_key


def is_empty_service_attribute(*, name: str, value: str, evidence: str, confidence: Any) -> bool:
    if evidence:
        return False
    if value:
        return False
    return confidence in ("", None, 0, 0.0)


def is_voice_presence_attribute(name: str) -> bool:
    compact = canonical_name(name)
    return any(marker in compact for marker in ("\u662f\u5426\u5e26\u8bed\u97f3", "\u6709\u65e0\u8bed\u97f3", "\u8bed\u97f3\u670d\u52a1", "\u5e26\u8bed\u97f3"))


def is_speed_duplicate_attribute(name: str, value: str, package_speeds: set[str]) -> bool:
    if not package_speeds:
        return False
    compact_name = canonical_name(name)
    compact_value = compact_text(value)
    if compact_name not in {"\u901f\u7387", "\u901f\u7387\u8303\u56f4", "\u5e26\u5bbd", "\u5e26\u5bbd\u8303\u56f4"}:
        return False
    return compact_value in package_speeds


def looks_like_included_resource_attribute(name: str) -> bool:
    compact = canonical_name(name)
    markers = ("\u8d60\u9001", "\u5305\u542b", "\u5185\u542b", "\u5957\u9910\u5185", "\u8d44\u6e90")
    return any(marker in compact for marker in markers)


def looks_like_condition_included_item(item: dict[str, Any]) -> bool:
    name = compact_text(first_non_empty(item, ("item_name", "name")))
    description = compact_text(first_non_empty(item, ("description", "source_evidence")))
    rule_markers = ("\u89c4\u5219", "\u6761\u4ef6", "\u9650\u5236", "\u9002\u7528", "\u8d85\u51fa", "\u4ee5\u4e0b", "\u4ee5\u4e0a")
    return any(marker in name or marker in description for marker in rule_markers)


def find_package_by_source_location(packages: list[Any], source_location: Any) -> dict[str, Any] | None:
    if not isinstance(source_location, dict):
        return None
    target_key = location_row_key(source_location)
    if target_key == (None, None):
        return None
    for package in packages:
        if not isinstance(package, dict):
            continue
        if location_row_key(package.get("source_location", {})) == target_key:
            return package
    return None


def location_row_key(source_location: Any) -> tuple[Any, Any]:
    if not isinstance(source_location, dict):
        return (None, None)
    return (source_location.get("table_index"), source_location.get("row_index"))


def append_applicable_condition(package: dict[str, Any], condition: str) -> None:
    conditions = package.get("applicable_conditions", [])
    if not isinstance(conditions, list):
        conditions = []
    if condition and condition not in conditions:
        conditions.append(condition)
    package["applicable_conditions"] = conditions


def dedupe_condition_values(values: list[Any]) -> list[Any]:
    expanded_values: list[Any] = []
    for value in values:
        if isinstance(value, str):
            expanded_values.extend(split_condition_value(value))
        else:
            expanded_values.append(value)

    string_values = [value for value in expanded_values if isinstance(value, str)]
    result: list[Any] = []
    seen: set[str] = set()
    for value in expanded_values:
        if not isinstance(value, str):
            result.append(value)
            continue
        compact = compact_text(value)
        if not compact or compact in seen:
            continue
        if is_merged_duplicate_condition(value, string_values):
            continue
        seen.add(compact)
        result.append(value)
    return result


def split_condition_value(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    if not should_split_condition(text):
        return [text]
    parts = [part.strip(" ，,；;") for part in re.split(r"[，,；;]", text) if part.strip(" ，,；;")]
    return parts if len(parts) > 1 else [text]


def should_split_condition(value: str) -> bool:
    markers = ("\u4ee5\u4e0b", "\u4ee5\u4e0a", "\u8d85\u51fa", "\u9700", "\u4e0d\u5f97", "\u5fc5\u987b", "\u9002\u7528", "\u9650")
    return any(marker in value for marker in markers) and bool(re.search(r"[，,；;]", value))


def is_merged_duplicate_condition(value: str, all_values: list[str]) -> bool:
    compact = compact_text(value)
    if not compact:
        return False
    shorter_matches = [
        other
        for other in all_values
        if other != value and len(compact_text(other)) >= 6 and compact_text(other) in compact
    ]
    return len(shorter_matches) >= 2


def service_attribute_to_included_item(item: dict[str, Any]) -> dict[str, Any]:
    name = first_non_empty(item, ("attribute_name", "label", "name", "field_key"))
    value = first_non_empty(item, ("attribute_value", "value", "description"))
    return {
        "item_name": name,
        "description": value,
        "source_evidence": item.get("source_evidence", ""),
        "source_location": item.get("source_location", {}),
        "confidence": item.get("confidence", 0.8),
    }


def base_package_variant_suffix(item: dict[str, Any]) -> str:
    billing_period = normalize_period_label(str(item.get("billing_period", "")).strip())
    contract_period = normalize_period_label(str(item.get("contract_period", "")).strip())
    if billing_period:
        return billing_period
    if contract_period:
        return contract_period
    price = item.get("price")
    if isinstance(price, (int, float)) and not isinstance(price, bool):
        return f"{price:g}"
    return ""


def normalize_period_label(value: str) -> str:
    normalized = compact_text(value).lower()
    mapping = {
        "month": "\u6708",
        "monthly": "\u6708",
        "\u6708\u4ed8": "\u6708",
        "\u6708": "\u6708",
        "year": "\u5e74",
        "annual": "\u5e74",
        "annually": "\u5e74",
        "yearly": "\u5e74",
        "biennially": "2\u5e74",
        "\u5e74\u4ed8": "\u5e74",
        "\u5e74": "\u5e74",
        "onetime": "\u4e00\u6b21\u6027",
        "one_time": "\u4e00\u6b21\u6027",
        "one-time": "\u4e00\u6b21\u6027",
        "2years": "2\u5e74",
        "twoyears": "2\u5e74",
        "everytwoyears": "2\u5e74",
        "biennial": "2\u5e74",
        "\u4e24\u5e74": "2\u5e74",
        "\u4e8c\u5e74": "2\u5e74",
        "2\u5e74": "2\u5e74",
    }
    duration_match = re.fullmatch(r"(\d+)(?:months|month)", normalized)
    if duration_match:
        return f"{duration_match.group(1)}\u4e2a\u6708"
    year_match = re.fullmatch(r"(\d+)(?:years|year)", normalized)
    if year_match:
        return f"{year_match.group(1)}\u5e74"
    return mapping.get(normalized, value)


def normalize_period_suffix_in_name(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        normalized = normalize_period_label(match.group(1))
        return f"\uff08{normalized}\uff09"

    return re.sub(r"\uff08\s*([A-Za-z0-9 ]+)\s*\uff09", replace, value)


def strip_variant_suffix_from_package_name(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"-\d+(?:\.\d+)?$", "", text)
    text = re.sub(r"\uff08\s*(?:\u6708|\u5e74|2\u5e74|month|monthly|year|yearly|2\s*years?)\s*\uff09$", "", text, flags=re.IGNORECASE)
    return text.strip()


def build_package_variant_code(item: dict[str, Any], index: int) -> str:
    parts = [
        str(item.get("package_name", "")).strip(),
        str(item.get("speed", "")).strip(),
        str(item.get("billing_period", "")).strip(),
        format_package_code_part(item.get("price")),
    ]
    key = "_".join(part for part in parts if part)
    key = re.sub(r"\s+", "", key)
    return key or f"package_{index + 1}"


def format_package_code_part(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def normalize_package_code(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"(?<=\d)\.0(?=_|$)", "", text)
    return re.sub(r"\s+", "", text)


def remove_editorial_markers(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"\s*[\uff08(]\s*(?:\u9519\u8bef\u6807\u6ce8|\u7591\u4f3c\u9519\u8bef|\u63a8\u6d4b|\u7591\u4f3c)\s*[\uff09)]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def infer_billing_period_from_evidence(evidence: str) -> str:
    text = str(evidence or "")
    if not text:
        return ""
    normalized = compact_text(text)
    if re.search(r"\u5143\s*/?\s*(?:2\u5e74|\u4e24\u5e74|\u4e8c\u5e74)", text):
        return "2\u5e74"
    if re.search(r"\u5143\s*/?\s*\u5e74", text):
        return "\u5e74"
    if re.search(r"\u5143\s*/?\s*\u6708", text):
        return "\u6708"
    if re.search(r"\u5143\s*/?\s*(?:\u7ebf)?\s*/?\s*\u6b21", text) or "\u4e00\u6b21\u6027" in normalized:
        return "\u4e00\u6b21\u6027"
    return ""


def is_empty_schema_object(value: dict[str, Any]) -> bool:
    for item in value.values():
        if isinstance(item, dict) and not is_empty_schema_object(item):
            return False
        if isinstance(item, list) and any(not is_empty_value(child) for child in item):
            return False
        if not is_empty_value(item):
            return False
    return True


def is_empty_value(value: Any) -> bool:
    return value in ("", None, 0, 0.0, False) or value == [] or value == {}


def is_core_eligibility_constraint(item: dict[str, Any]) -> bool:
    if item.get("blocks_recommendation") is True:
        return True

    constraint_type = str(item.get("constraint_type", "")).strip()
    text = compact_text(
        " ".join(
            str(item.get(key, ""))
            for key in ("constraint_type", "name", "description", "condition", "result", "source_evidence")
        )
    )
    if not text:
        return False
    if constraint_type in {"recommendation_blocker", "exclusion", "required_condition"}:
        return True
    if constraint_type == "compliance" and not contains_core_constraint_marker(text):
        return False
    return contains_core_constraint_marker(text)


def contains_core_constraint_marker(text: str) -> bool:
    markers = (
        "\u4ec5\u9650",
        "\u4e0d\u5f97",
        "\u4e0d\u80fd",
        "\u4e0d\u53ef",
        "\u4e0d\u9002\u7528",
        "\u6b20\u8d39",
        "\u505c\u7528",
        "\u505c\u6b62",
        "\u5fc5\u987b",
        "\u9700\u8981",
        "\u9700",
        "\u8981\u6c42",
        "\u540c\u540d",
        "\u540c\u5740",
        "\u5408\u5e76\u5f00\u8d26",
        "IP",
        "\u62c5\u4fdd",
        "\u62bc\u91d1",
        "\u963b\u6b62",
        "\u4e0d\u53ef\u63a8\u8350",
    )
    return any(marker in text for marker in markers)


def normalize_currency(value: Any) -> Any:
    if value in {"元", "人民币", "RMB", "CNY", "¥"}:
        return "CNY"
    if value in {"", None}:
        return value
    if isinstance(value, str) and value.strip():
        return ""
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


def extract_source_location_path(value: Any) -> str:
    if isinstance(value, dict):
        location = value.get("source_location")
        if isinstance(location, dict) and location.get("path"):
            return str(location.get("path", ""))
        for child in value.values():
            path = extract_source_location_path(child)
            if path:
                return path
    elif isinstance(value, list):
        for child in value:
            path = extract_source_location_path(child)
            if path:
                return path
    return ""


def normalized_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(text))


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
