from __future__ import annotations

from copy import deepcopy
from typing import Any

from schema.schema import TOP_LEVEL_LIST_KEYS, make_empty_product_document


class ProductDocumentMerger:
    """Merge module-level LLM outputs into the unified product document schema."""

    def merge(
        self,
        module_outputs: dict[str, Any],
        *,
        document_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        product_document = make_empty_product_document()
        document_metadata = document_metadata or {}

        self._merge_document_info(product_document, module_outputs.get("document_info", {}), document_metadata)
        self._merge_object(product_document, "parties_and_application", module_outputs.get("parties_and_application", {}))
        self._merge_object(product_document, "base_package", module_outputs.get("base_package", {}))

        for key in TOP_LEVEL_LIST_KEYS:
            product_document[key] = ensure_list(module_outputs.get(key, []))

        meta = product_document["extraction_meta"]
        meta["source_file"] = document_metadata.get("source_path", "")
        meta["source_file_hash"] = document_metadata.get("source_file_hash", "")
        meta["method"] = "llm_schema_module_extraction"
        meta["review_status"] = "draft"
        return product_document

    def apply_self_check(self, product_document: dict[str, Any], self_check: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(product_document)
        meta = result.setdefault("extraction_meta", {})
        llm_self_check = self_check.get("llm_self_check", {})
        if not isinstance(llm_self_check, dict):
            llm_self_check = {}
        llm_self_check["validation_issues"] = normalize_validation_issues(self_check.get("validation_issues", []))
        llm_self_check["schema_warnings"] = normalize_schema_warnings(self_check.get("schema_warnings", []))
        meta["llm_self_check"] = llm_self_check
        meta.setdefault("validation_issues", [])
        meta.setdefault("schema_warnings", [])
        meta["validation_issue_count"] = len(meta["validation_issues"])
        return result

    def _merge_document_info(
        self,
        product_document: dict[str, Any],
        document_info: Any,
        document_metadata: dict[str, Any],
    ) -> None:
        if isinstance(document_info, dict):
            self._merge_object(product_document, "document_info", document_info)
        target = product_document["document_info"]
        target["document_id"] = target.get("document_id") or document_metadata.get("document_id", "")
        target["filename"] = target.get("filename") or document_metadata.get("filename", "")
        target["source_path"] = target.get("source_path") or document_metadata.get("source_path", "")
        target["source_file_type"] = target.get("source_file_type") or document_metadata.get("source_file_type", "")

    def _merge_object(self, product_document: dict[str, Any], key: str, value: Any) -> None:
        if not isinstance(value, dict):
            return
        target = product_document[key]
        for field_name, field_value in value.items():
            if field_name not in target:
                continue
            target[field_name] = field_value


def ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def normalize_validation_issues(value: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for index, item in enumerate(ensure_list(value)):
        if isinstance(item, dict):
            if is_non_actionable_self_check_issue(item):
                continue
            issues.append(item)
            continue
        if item is None:
            continue
        issues.append(
            {
                "severity": "warning",
                "path": f"llm_self_check.validation_issues[{index}]",
                "message": str(item),
            }
        )
    return issues


def is_non_actionable_self_check_issue(issue: dict[str, Any]) -> bool:
    message = str(issue.get("message", "")).lower()
    if not message:
        return False

    if "not allowed" not in message and "\u4e0d\u5141\u8bb8" not in message:
        permissive_markers = ("allowed", "acceptable", "no error", "disregard", "\u53ef\u63a5\u53d7", "\u4e0d\u662f\u9519\u8bef", "\u53ef\u5ffd\u7565")
        if any(marker in message for marker in permissive_markers):
            return True

    invented_schema_markers = (
        "float but",
        "should be int",
        "integer amount",
        "decimal point",
        "plain string labels",
        "options must be plain",
        "missing 'column_index'",
        "source_location missing 'column_index'",
        "confidence must reflect objective",
        "correct usage",
    )
    if any(marker in message for marker in invented_schema_markers):
        return True

    inference_markers = ("likely mislabeled", "expected annual", "pricing logic", "appears to be annual")
    if any(marker in message for marker in inference_markers):
        return True

    false_positive_markers = (
        "acceptable",
        "not an error",
        "disregard",
        "skip",
        "no warning needed",
        "allowed per spec",
        "permitted per spec",
        "\u53ef\u63a5\u53d7",
        "\u4e0d\u662f\u9519\u8bef",
        "\u65e0\u9700\u5904\u7406",
        "\u53ef\u5ffd\u7565",
    )
    return any(marker in message for marker in false_positive_markers)


def normalize_schema_warnings(value: Any) -> list[str]:
    warnings: list[str] = []
    for item in ensure_list(value):
        if item is None:
            continue
        warning = item if isinstance(item, str) else str(item)
        if is_non_actionable_self_check_issue({"message": warning}):
            continue
        warnings.append(warning)
    return warnings
