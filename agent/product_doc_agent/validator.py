from __future__ import annotations

import re
from typing import Any

from schema.schema import TOP_LEVEL_LIST_KEYS, validate_product_document


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

    def validate(self, product_document: dict[str, Any]) -> list[dict[str, str]]:
        issues = validate_product_document(product_document)
        issues.extend(self._validate_list_types(product_document))
        issues.extend(self._validate_evidence(product_document))
        issues.extend(self._validate_possible_un_split_lists(product_document))
        return issues

    def attach_issues(self, product_document: dict[str, Any], issues: list[dict[str, str]]) -> dict[str, Any]:
        meta = product_document.setdefault("extraction_meta", {})
        existing = meta.get("validation_issues", [])
        if not isinstance(existing, list):
            existing = []
        meta["validation_issues"] = [*existing, *issues]
        meta["validation_issue_count"] = len(meta["validation_issues"])
        meta.setdefault("schema_warnings", [])
        return product_document

    def _validate_list_types(self, product_document: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        for key in TOP_LEVEL_LIST_KEYS:
            if not isinstance(product_document.get(key), list):
                issues.append({"severity": "error", "path": key, "message": "schema list field must be a list"})
        base_package = product_document.get("base_package", {})
        if isinstance(base_package, dict):
            for key in ("packages", "included_items", "service_attributes"):
                if not isinstance(base_package.get(key), list):
                    issues.append({"severity": "error", "path": f"base_package.{key}", "message": "must be a list"})
        return issues

    def _validate_evidence(self, product_document: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        for key in EVIDENCE_LIST_KEYS:
            for index, item in enumerate(product_document.get(key, [])):
                if not isinstance(item, dict):
                    continue
                if not item.get("source_evidence"):
                    issues.append({"severity": "warning", "path": f"{key}[{index}].source_evidence", "message": "missing source evidence"})
                if "confidence" not in item:
                    issues.append({"severity": "warning", "path": f"{key}[{index}].confidence", "message": "missing confidence"})

        for index, item in enumerate(product_document.get("base_package", {}).get("packages", [])):
            if not isinstance(item, dict):
                continue
            if not item.get("source_evidence"):
                issues.append({"severity": "warning", "path": f"base_package.packages[{index}].source_evidence", "message": "missing source evidence"})
        return issues

    def _validate_possible_un_split_lists(self, product_document: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        for key in EVIDENCE_LIST_KEYS:
            for index, item in enumerate(product_document.get(key, [])):
                if not isinstance(item, dict):
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


def looks_like_multiple_items(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return False
    return any(re.search(pattern, compact) for pattern in MULTI_ITEM_PATTERNS)
