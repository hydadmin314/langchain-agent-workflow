from __future__ import annotations

from typing import Any

from agent.product_doc_agent.evidence import EvidenceIndex
from agent.product_doc_agent.extractors import DomainExtractor, make_fact
from agent.product_doc_agent.llm_client import LLMClient
from agent.product_doc_agent.models import ExtractedFact, NormalizedDocument

ALLOWED_DOMAINS = {"product", "plan", "option", "fee", "rule", "form_field", "compliance", "required_document"}


class LLMStructuredExtractor(DomainExtractor):
    domain = "llm"

    def __init__(self, client: LLMClient, max_evidence_items: int = 160) -> None:
        self.client = client
        self.max_evidence_items = max_evidence_items

    def extract(self, release_id: str, normalized: NormalizedDocument, evidence: EvidenceIndex) -> list[ExtractedFact]:
        evidence_items = build_evidence_context(evidence, self.max_evidence_items)
        valid_evidence_ids = {item["evidence_id"] for item in evidence_items}
        payload = self.client.complete_json(build_extraction_prompt(evidence_items))
        facts: list[ExtractedFact] = []
        for item in payload.get("facts", []):
            if not isinstance(item, dict):
                continue
            domain = str(item.get("domain", "")).strip()
            field = str(item.get("field", "")).strip()
            value = item.get("value")
            evidence_ids = [str(eid) for eid in item.get("evidence_ids", []) if str(eid) in valid_evidence_ids]
            if domain not in ALLOWED_DOMAINS or not field or value in (None, "") or not evidence_ids:
                continue
            facts.append(make_fact(release_id, domain, field, value, evidence_ids, confidence=float(item.get("confidence", 0.65)), source="llm"))
        return facts


def build_evidence_context(evidence: EvidenceIndex, max_items: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in evidence.all():
        if item.evidence_type not in {"paragraph", "table_row", "checkbox", "blank_field"} or not item.text.strip():
            continue
        items.append({"evidence_id": item.evidence_id, "type": item.evidence_type, "text": item.text[:500]})
        if len(items) >= max_items:
            break
    return items


def build_extraction_prompt(evidence_items: list[dict[str, Any]]) -> str:
    evidence_text = "\n".join(f"{item['evidence_id']} [{item['type']}]: {item['text']}" for item in evidence_items)
    return f"""
你是产品文档结构化解析助手。请基于 evidence 补充抽取结构化 facts。
规则：只能使用 evidence 内容；每条 fact 必须绑定输入中的 evidence_id；输出只能是 JSON。
允许 domain: product, plan, option, fee, rule, form_field, compliance, required_document。
格式：{{"facts":[{{"domain":"plan","field":"base_plan_option","value":"...","evidence_ids":["ev_p0001"],"confidence":0.8}}]}}
Evidence:
{evidence_text}
""".strip()
