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
            "fee_info": self._fee_info(facts),
            "rule_book": self._rule_book(fact_items),
            "form_schema": self._form_schema(facts),
            "compliance_info": self._compliance_info(fact_items),
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

    def _fee_info(self, facts: list[ExtractedFact]) -> dict[str, Any]:
        charges = self._charges(facts)
        categories = {
            "base_plan_prices": [],
            "voice_cloud_phone_fees": [],
            "mobile_overage_fees": [],
            "speed_upgrade_packages": [],
            "one_time_fees": [],
            "penalty_rules": [],
            "device_compensation": [],
            "other_fees": [],
        }
        for charge in charges:
            categories[fee_category(charge)].append(charge)
        return grouped_review_object("fee", categories)

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

    def _form_schema(self, facts: list[ExtractedFact]) -> dict[str, Any]:
        form = self._form(facts)
        selection_labels = {
            normalize_label(item.get("value", {}).get("label", ""))
            for item in form["checkbox_groups"]
            if isinstance(item.get("value"), dict)
        }
        text_fields = [
            item
            for item in form["fields"]
            if normalize_label(item.get("value", {}).get("label", "")) not in selection_labels
        ]
        categories = {
            "customer_fields": [],
            "contact_fields": [],
            "billing_fields": [],
            "authorization_fields": [],
            "selection_fields": form["checkbox_groups"],
            "blank_fields": form["blank_fields"],
            "other_fields": [],
        }
        for field in text_fields:
            categories[form_field_category(field)].append(field)
        if form["checkboxes"]:
            categories["other_fields"].extend(form["checkboxes"])
        if form["other"]:
            categories["other_fields"].extend(form["other"])
        return grouped_review_object("form", categories)

    def _rule_book(self, fact_items: list[dict[str, Any]]) -> dict[str, Any]:
        rules = self._domain_items(fact_items, "rule")
        categories = {
            "form_instructions": [],
            "package_rules": [],
            "eligibility_rules": [],
            "lifecycle_rules": [],
            "option_rules": [],
            "billing_rules": [],
            "termination_rules": [],
            "compliance_related": [],
            "authorization_rules": [],
            "other_rules": [],
        }
        for rule in rules:
            categories[rule_category(rule)].append(rule)
        return grouped_review_object("rule", categories)

    def _compliance_info(self, fact_items: list[dict[str, Any]]) -> dict[str, Any]:
        clauses = self._domain_items(fact_items, "compliance")
        categories = {
            "real_name_requirements": [],
            "network_security_commitment": [],
            "personal_information_protection": [],
            "voice_service_compliance": [],
            "internet_access_compliance": [],
            "filing_and_license_requirements": [],
            "incident_response": [],
            "liability_and_termination": [],
            "security_contact": [],
            "service_agreement_acknowledgement": [],
            "other_compliance": [],
        }
        for clause in clauses:
            categories[compliance_category(clause)].append(clause)
        return grouped_review_object("compliance", categories)

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


def grouped_review_object(name: str, categories: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    normalized = {key: dedupe_review_items(items) for key, items in categories.items()}
    non_empty = {key: items for key, items in normalized.items() if items}
    all_items = [item for items in non_empty.values() for item in items]
    return {
        "object": name,
        "categories": non_empty,
        "category_counts": {key: len(items) for key, items in non_empty.items()},
        "item_count": len(all_items),
        "evidence_ids": merge_unique([], [evidence_id for item in all_items for evidence_id in item.get("evidence_ids", [])]),
        "fact_ids": merge_unique([], [fact_id for item in all_items for fact_id in item.get("fact_ids", [])]),
    }


def fee_category(charge: dict[str, Any]) -> str:
    text = charge_text(charge)
    charge_type = str(charge.get("charge_type", ""))
    if "上行升速" in text:
        return "speed_upgrade_packages"
    if "一次性" in text or charge_type == "one_time_fee":
        return "one_time_fees"
    if "违约金" in text or charge_type == "penalty_fee":
        return "penalty_rules"
    if "Navigator" in text or "网关" in text or "设备" in text:
        return "device_compensation"
    if "拨号" in text or "基础套餐" in text:
        return "base_plan_prices"
    if any(token in text for token in ["固话", "商云通", "国内通话2500", "45元/月/线"]):
        return "voice_cloud_phone_fees"
    if any(token in text for token in ["移动", "主卡", "副卡", "国内短/彩信", "国内流量", "0.15元", "0.1元/条", "3元/GB", "断网"]):
        return "mobile_overage_fees"
    return "other_fees"


def form_field_category(field: dict[str, Any]) -> str:
    value = field.get("value", {})
    label = value.get("label", "") if isinstance(value, dict) else string_value(value)
    compact = normalize_label(label)
    if any(token in compact for token in ["经办人", "身份证", "联系电话", "EMAIL", "E-MAIL", "传真"]):
        return "contact_fields"
    if any(token in compact for token in ["账单", "付款", "邮编"]):
        return "billing_fields"
    if any(token in compact for token in ["委托", "授权", "员工"]):
        return "authorization_fields"
    if any(token in compact for token in ["企业", "统一社会信用代码", "安装地址"]):
        return "customer_fields"
    return "other_fields"


def rule_category(rule: dict[str, Any]) -> str:
    text = review_text(rule)
    if any(token in text for token in ["填写", "登记表", "打“√”", "带*项"]):
        return "form_instructions"
    if any(token in text for token in ["实名", "网络安全", "信息安全", "个人信息", "法律", "法规", "备案", "不得将电路", "不得通过宽带"]):
        return "compliance_related"
    if any(token in text for token in ["委托", "经办人", "代为办理"]):
        return "authorization_rules"
    if any(token in text for token in ["违约金", "退出", "提前终止", "终止", "注销", "拆机", "停机"]):
        return "termination_rules"
    if any(token in text for token in ["欠费", "不得参加", "不能选择", "限一线", "仅限"]):
        return "eligibility_rules"
    if any(token in text for token in ["协议期", "生效", "首月", "次月", "期满"]):
        return "lifecycle_rules"
    if any(token in text for token in ["固话", "商云通", "移动业务", "副卡", "上行升速", "可选"]):
        return "option_rules"
    if any(token in text for token in ["资费", "费用", "收费", "月基本费", "套餐费"]):
        return "billing_rules"
    if any(token in text for token in ["套餐", "宽带", "业务"]):
        return "package_rules"
    return "other_rules"


def compliance_category(clause: dict[str, Any]) -> str:
    text = review_text(clause)
    if any(token in text for token in ["实名", "真实身份"]):
        return "real_name_requirements"
    if any(token in text for token in ["服务协议", "营销活动规则", "请认真阅读"]):
        return "service_agreement_acknowledgement"
    if any(token in text for token in ["个人信息", "数据"]):
        return "personal_information_protection"
    if any(token in text for token in ["语音", "外呼", "号码", "录音"]):
        return "voice_service_compliance"
    if any(token in text for token in ["互联网", "备案", "许可证", "80", "8080", "443", "网站"]):
        return "internet_access_compliance"
    if any(token in text for token in ["资质", "营业执照", "证明文件", "许可证"]):
        return "filing_and_license_requirements"
    if any(token in text for token in ["事故", "24小时", "应急", "报告"]):
        return "incident_response"
    if any(token in text for token in ["责任", "赔偿", "暂停", "解除", "终止", "投诉", "举报", "违反"]):
        return "liability_and_termination"
    if any(token in text for token in ["信息安全责任人", "承诺单位", "盖章"]):
        return "security_contact"
    if any(token in text for token in ["网络安全", "承诺", "法律", "法规", "不得"]):
        return "network_security_commitment"
    return "other_compliance"


def review_text(item: dict[str, Any]) -> str:
    return str(item.get("raw_text") or item.get("title") or item.get("value") or "")


def charge_text(charge: dict[str, Any]) -> str:
    return str(charge.get("raw_text") or charge.get("title") or "")


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
