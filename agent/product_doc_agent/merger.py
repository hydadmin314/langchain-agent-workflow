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
        meta["llm_self_check"] = self_check.get("llm_self_check", {})
        meta["validation_issues"] = ensure_list(self_check.get("validation_issues", []))
        meta["schema_warnings"] = ensure_list(self_check.get("schema_warnings", []))
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
