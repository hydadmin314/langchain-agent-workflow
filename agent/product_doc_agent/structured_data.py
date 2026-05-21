from __future__ import annotations

import re
from typing import Any

from agent.product_doc_agent.models import ExtractedFact, RawDocument


class BusinessStructuredDataBuilder:
    """Build a generic human-review view from atomic facts.

    This layer intentionally avoids product-specific keyword rules. New document
    types should still land in the same review schema as long as extractors emit
    facts with domain, field, value, evidence_ids, and source.
    """

    schema_version = "generic_review_v1"

    def build(self, facts: list[ExtractedFact], raw_doc: RawDocument, document_type: str) -> dict[str, Any]:
        fact_items = [fact_to_review_item(fact) for fact in facts]
        facts_by_domain = group_items(fact_items, key="domain")
        facts_by_field = group_items(fact_items, key="path")

        return {
            "schema_version": self.schema_version,
            "document": self._document(raw_doc, document_type),
            "summary": self._summary(facts, raw_doc),
            "product_info": self._product_info(facts, raw_doc, document_type),
            "business_objects": self._business_objects(fact_items),
            "charges": self._charges(facts),
            "rules": self._domain_items(fact_items, "rule"),
            "form": self._form(facts),
            "compliance": self._domain_items(fact_items, "compliance"),
            "required_documents": self._domain_items(fact_items, "required_document"),
            "facts_by_domain": facts_by_domain,
            "facts_by_field": facts_by_field,
            "review_notes": {
                "atomic_fact_count": len(facts),
                "fact_source_counts": count_by(facts, "source"),
                "domain_counts": count_by(facts, "domain"),
                "field_counts": count_paths(facts),
            },
        }

    def _document(self, raw_doc: RawDocument, document_type: str) -> dict[str, Any]:
        return {
            "doc_id": raw_doc.doc_id,
            "file_name": raw_doc.file_name,
            "source_path": raw_doc.source_path,
            "stored_path": raw_doc.stored_path,
            "sha256": raw_doc.sha256,
            "document_type": document_type,
            "product_family": raw_doc.product_family,
            "region": raw_doc.region,
            "version": raw_doc.version,
            "effective_date": raw_doc.effective_date,
            "variant": raw_doc.variant,
        }

    def _summary(self, facts: list[ExtractedFact], raw_doc: RawDocument) -> dict[str, Any]:
        return {
            "title": first_string_value(facts, ["product.product_name", "document.title"]) or raw_doc.file_name,
            "owner": first_string_value(facts, ["product.operator", "organization.name"]),
            "version": raw_doc.version,
            "effective_date": raw_doc.effective_date,
            "variant": raw_doc.variant,
            "high_level_evidence_ids": unique_evidence_ids(
                fact
                for fact in facts
                if fact.domain in {"product", "document"} or fact.field in {"product_name", "title", "operator"}
            ),
        }

    def _business_objects(self, fact_items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        excluded = {"product", "fee", "rule", "form_field", "compliance", "required_document"}
        result: dict[str, list[dict[str, Any]]] = {}
        for item in fact_items:
            domain = item["domain"]
            if domain in excluded:
                continue
            result.setdefault(domain, []).append(item)
        return {domain: dedupe_review_items(items) for domain, items in result.items()}

    def _product_info(self, facts: list[ExtractedFact], raw_doc: RawDocument, document_type: str) -> dict[str, Any]:
        defaults = {
            "document_type": document_type,
            "product_family": raw_doc.product_family,
            "region": raw_doc.region,
            "version": raw_doc.version,
            "effective_date": raw_doc.effective_date,
            "variant": raw_doc.variant,
        }
        return merge_domain_to_record(facts, domain="product", defaults=defaults)

    def _charges(self, facts: list[ExtractedFact]) -> list[dict[str, Any]]:
        items = []
        for fact in facts:
            if fact.domain != "fee":
                continue
            value = fact.value
            raw_text = value.get("raw_text") if isinstance(value, dict) else string_value(value)
            amounts = value.get("amounts", []) if isinstance(value, dict) else extract_amounts(raw_text)
            items.append(
                {
                    "path": f"{fact.domain}.{fact.field}",
                    "charge_type": fact.field,
                    "raw_text": raw_text,
                    "amounts": amounts,
                    "evidence_ids": fact.evidence_ids,
                    "fact_ids": [fact.fact_id],
                    "source": fact.source,
                    "confidence": fact.confidence,
                }
            )
        return dedupe_review_items(items)

    def _form(self, facts: list[ExtractedFact]) -> dict[str, list[dict[str, Any]]]:
        form = {
            "fields": [],
            "checkbox_groups": [],
            "checkboxes": [],
            "blank_fields": [],
            "other": [],
        }
        grouped_checkbox_evidence_ids = checkbox_evidence_ids_in_groups(facts)
        grouped_checkbox_labels = checkbox_labels_in_groups(facts)
        grouped_input_placeholders = input_placeholders_in_checkbox_groups(facts)
        for fact in facts:
            if fact.domain != "form_field":
                continue
            item = fact_to_review_item(fact)
            if fact.field == "field":
                form["fields"].append(item)
            elif fact.field == "checkbox_group":
                form["checkbox_groups"].append(item)
            elif fact.field == "checkbox":
                if set(fact.evidence_ids).issubset(grouped_checkbox_evidence_ids):
                    continue
                if checkbox_label(fact) in grouped_checkbox_labels:
                    continue
                form["checkboxes"].append(item)
            elif fact.field == "blank_field":
                if blank_placeholder(fact) in grouped_input_placeholders:
                    continue
                form["blank_fields"].append(item)
            else:
                form["other"].append(item)
        return {name: dedupe_review_items(items) for name, items in form.items()}

    def _domain_items(self, fact_items: list[dict[str, Any]], domain: str) -> list[dict[str, Any]]:
        return dedupe_review_items([item for item in fact_items if item["domain"] == domain])


def merge_domain_to_record(
    facts: list[ExtractedFact],
    domain: str,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    domain_facts = [fact for fact in facts if fact.domain == domain]
    fields: dict[str, Any] = {}
    for fact in domain_facts:
        candidate = fact_to_field_candidate(fact)
        existing = fields.get(fact.field)
        if existing is None:
            fields[fact.field] = candidate
            continue
        fields[fact.field] = merge_field_candidate(existing, candidate)

    return {
        "domain": domain,
        "fields": fields,
        "defaults": {key: value for key, value in (defaults or {}).items() if value not in (None, "")},
        "evidence_ids": unique_evidence_ids(domain_facts),
        "fact_ids": [fact.fact_id for fact in domain_facts],
        "field_count": len(fields),
        "candidate_count": sum(len(value.get("candidates", [])) for value in fields.values()),
    }


def fact_to_field_candidate(fact: ExtractedFact) -> dict[str, Any]:
    return {
        "value": fact.value,
        "raw_text": string_value(fact.value),
        "evidence_ids": list(fact.evidence_ids),
        "fact_ids": [fact.fact_id],
        "sources": [fact.source],
        "confidence": fact.confidence,
        "candidates": [
            {
                "value": fact.value,
                "raw_text": string_value(fact.value),
                "evidence_ids": list(fact.evidence_ids),
                "fact_id": fact.fact_id,
                "source": fact.source,
                "confidence": fact.confidence,
            }
        ],
    }


def merge_field_candidate(existing: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    if existing.get("raw_text") == candidate.get("raw_text"):
        existing["evidence_ids"] = merge_unique(existing.get("evidence_ids", []), candidate.get("evidence_ids", []))
        existing["fact_ids"] = merge_unique(existing.get("fact_ids", []), candidate.get("fact_ids", []))
        existing["sources"] = merge_unique(existing.get("sources", []), candidate.get("sources", []))
        existing["confidence"] = max(float(existing.get("confidence", 0)), float(candidate.get("confidence", 0)))
        existing["candidates"] = dedupe_candidates(existing.get("candidates", []) + candidate.get("candidates", []))
        return existing

    candidates = dedupe_candidates(existing.get("candidates", []) + candidate.get("candidates", []))
    selected = max(candidates, key=lambda item: float(item.get("confidence", 0)))
    return {
        "value": selected.get("value"),
        "raw_text": selected.get("raw_text", ""),
        "evidence_ids": merge_unique(existing.get("evidence_ids", []), candidate.get("evidence_ids", [])),
        "fact_ids": merge_unique(existing.get("fact_ids", []), candidate.get("fact_ids", [])),
        "sources": merge_unique(existing.get("sources", []), candidate.get("sources", [])),
        "confidence": float(selected.get("confidence", 0)),
        "candidates": candidates,
        "needs_review": True,
    }


def fact_to_review_item(fact: ExtractedFact) -> dict[str, Any]:
    return {
        "path": f"{fact.domain}.{fact.field}",
        "domain": fact.domain,
        "field": fact.field,
        "title": make_title(fact),
        "value": fact.value,
        "raw_text": string_value(fact.value),
        "evidence_ids": fact.evidence_ids,
        "fact_ids": [fact.fact_id],
        "source": fact.source,
        "confidence": fact.confidence,
    }


def checkbox_evidence_ids_in_groups(facts: list[ExtractedFact]) -> set[str]:
    result: set[str] = set()
    for fact in facts:
        if fact.domain != "form_field" or fact.field != "checkbox_group":
            continue
        value = fact.value
        if not isinstance(value, dict):
            continue
        for option in value.get("options", []):
            if isinstance(option, dict) and option.get("evidence_id"):
                result.add(str(option["evidence_id"]))
    return result


def checkbox_labels_in_groups(facts: list[ExtractedFact]) -> set[str]:
    result: set[str] = set()
    for fact in facts:
        if fact.domain != "form_field" or fact.field != "checkbox_group":
            continue
        value = fact.value
        if not isinstance(value, dict):
            continue
        for option in value.get("options", []):
            if isinstance(option, dict) and option.get("label"):
                result.add(normalize_label(str(option["label"])))
            if isinstance(option, dict) and option.get("raw_label"):
                result.add(normalize_label(str(option["raw_label"])))
    return result


def input_placeholders_in_checkbox_groups(facts: list[ExtractedFact]) -> set[str]:
    result: set[str] = set()
    for fact in facts:
        if fact.domain != "form_field" or fact.field != "checkbox_group":
            continue
        value = fact.value
        if not isinstance(value, dict):
            continue
        for option in value.get("options", []):
            if not isinstance(option, dict):
                continue
            for input_field in option.get("input_fields", []):
                if isinstance(input_field, dict) and input_field.get("placeholder"):
                    result.add(str(input_field["placeholder"]))
    return result


def checkbox_label(fact: ExtractedFact) -> str:
    value = fact.value
    if isinstance(value, dict):
        return normalize_label(str(value.get("label", "")))
    return normalize_label(string_value(value))


def blank_placeholder(fact: ExtractedFact) -> str:
    value = fact.value
    if isinstance(value, dict):
        return str(value.get("placeholder", ""))
    return ""


def normalize_label(value: str) -> str:
    return re.sub(r"\s+", "", value or "").strip()


def make_title(fact: ExtractedFact) -> str:
    value = fact.value
    if isinstance(value, dict):
        for key in ["name", "label", "title", "raw_text"]:
            if value.get(key):
                return truncate(str(value[key]), 80)
    text = string_value(value)
    if text:
        return truncate(text, 80)
    return f"{fact.domain}.{fact.field}"


def first_string_value(facts: list[ExtractedFact], paths: list[str]) -> str | None:
    path_set = set(paths)
    for fact in facts:
        if f"{fact.domain}.{fact.field}" not in path_set:
            continue
        value = fact.value
        if isinstance(value, dict):
            for key in ["name", "label", "title", "raw_text"]:
                if value.get(key):
                    return str(value[key])
        text = string_value(value)
        if text:
            return text
    return None


def string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        if "label" in value and "options" in value:
            return str(value.get("label") or "").strip()
        raw_text = value.get("raw_text")
        if raw_text is not None:
            return str(raw_text).strip()
        return " ".join(str(v) for v in value.values() if v not in (None, ""))
    return str(value).strip()


def extract_amounts(text: str) -> list[dict[str, str]]:
    amounts = []
    patterns = [
        r"(?P<amount>\d+(?:\.\d+)?)\s*(?P<currency>元|CNY|RMB|USD|\$)\s*/?\s*(?P<unit>[\w\u4e00-\u9fff/]+)?",
        r"(?P<currency>元|CNY|RMB|USD|\$)\s*(?P<amount>\d+(?:\.\d+)?)\s*/?\s*(?P<unit>[\w\u4e00-\u9fff/]+)?",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            amounts.append(
                {
                    "amount": match.group("amount"),
                    "currency": normalize_currency(match.group("currency")),
                    "unit": (match.group("unit") or "").strip(" ，,；;。"),
                }
            )
        if amounts:
            break
    return amounts


def normalize_currency(currency: str) -> str:
    upper = currency.upper()
    if currency == "元" or upper in {"CNY", "RMB"}:
        return "CNY"
    if currency == "$" or upper == "USD":
        return "USD"
    return currency


def group_items(items: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(str(item.get(key, "unknown")), []).append(item)
    return {group: dedupe_review_items(values) for group, values in grouped.items()}


def dedupe_review_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for item in items:
        key = (
            item.get("path"),
            item.get("raw_text"),
            tuple(item.get("evidence_ids", [])),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for item in candidates:
        key = (item.get("raw_text"), tuple(item.get("evidence_ids", [])))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def merge_unique(left: list[Any], right: list[Any]) -> list[Any]:
    result = []
    seen = set()
    for item in left + right:
        key = repr(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def unique_evidence_ids(facts: Any) -> list[str]:
    result = []
    seen = set()
    for fact in facts:
        for evidence_id in fact.evidence_ids:
            if evidence_id in seen:
                continue
            seen.add(evidence_id)
            result.append(evidence_id)
    return result


def count_by(facts: list[ExtractedFact], attr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fact in facts:
        key = str(getattr(fact, attr))
        counts[key] = counts.get(key, 0) + 1
    return counts


def count_paths(facts: list[ExtractedFact]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fact in facts:
        path = f"{fact.domain}.{fact.field}"
        counts[path] = counts.get(path, 0) + 1
    return counts


def truncate(text: str, max_length: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "..."
