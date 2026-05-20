from __future__ import annotations

import re
import uuid
from typing import Any, Iterable

from agent.product_doc_agent.evidence import EvidenceIndex
from agent.product_doc_agent.models import ExtractedFact, NormalizedDocument


class DomainExtractor:
    domain = "base"

    def extract(self, release_id: str, normalized: NormalizedDocument, evidence: EvidenceIndex) -> list[ExtractedFact]:
        raise NotImplementedError


class ProductExtractor(DomainExtractor):
    domain = "product"

    def extract(self, release_id: str, normalized: NormalizedDocument, evidence: EvidenceIndex) -> list[ExtractedFact]:
        facts: list[ExtractedFact] = []
        for paragraph in normalized.paragraphs[:10]:
            if "套餐申请登记表" in paragraph.text or "营销规则" in paragraph.text:
                product_name = re.sub(r"[（(].*?[）)]", "", paragraph.text).replace("套餐申请登记表", "").replace("套餐营销规则", "")
                facts.append(make_fact(release_id, self.domain, "product_name", product_name, [evidence.evidence_for_paragraph(paragraph.id).evidence_id]))
                version_match = re.search(r"[（(](20\d{2}\s*/?\s*[A-Z]?)[）)]", paragraph.text, re.IGNORECASE)
                if version_match:
                    facts.append(make_fact(release_id, self.domain, "version_label", version_match.group(1).replace(" ", ""), [evidence.evidence_for_paragraph(paragraph.id).evidence_id]))
                break
        for paragraph in normalized.paragraphs[:10]:
            if "中国电信" in paragraph.text:
                facts.append(make_fact(release_id, self.domain, "operator", paragraph.text, [evidence.evidence_for_paragraph(paragraph.id).evidence_id]))
                break
        return facts


class PlanExtractor(DomainExtractor):
    domain = "plan"

    def extract(self, release_id: str, normalized: NormalizedDocument, evidence: EvidenceIndex) -> list[ExtractedFact]:
        facts = [
            make_fact(release_id, self.domain, "base_plan_option", checkbox.label, [evidence.evidence_for_checkbox(checkbox.id).evidence_id])
            for checkbox in normalized.checkboxes
            if any(token in checkbox.label for token in ["元/月", "元/年", "元/2年"])
        ]
        for paragraph in normalized.paragraphs:
            if "协议期" in paragraph.text and ("一年" in paragraph.text or "二年" in paragraph.text):
                facts.append(make_fact(release_id, self.domain, "agreement_period_rule", paragraph.text, [evidence.evidence_for_paragraph(paragraph.id).evidence_id]))
                break
        return facts


class OptionExtractor(DomainExtractor):
    domain = "option"

    def extract(self, release_id: str, normalized: NormalizedDocument, evidence: EvidenceIndex) -> list[ExtractedFact]:
        facts: list[ExtractedFact] = []
        for paragraph in normalized.paragraphs:
            if any(token in paragraph.text for token in ["上行升速", "固话", "商云通", "移动业务", "副卡"]):
                if "□" in paragraph.text or "月基本费" in paragraph.text or "申请线数" in paragraph.text:
                    facts.append(make_fact(release_id, self.domain, "option_item", paragraph.text, [evidence.evidence_for_paragraph(paragraph.id).evidence_id]))
        return facts


class FeeExtractor(DomainExtractor):
    domain = "fee"

    def extract(self, release_id: str, normalized: NormalizedDocument, evidence: EvidenceIndex) -> list[ExtractedFact]:
        facts: list[ExtractedFact] = []
        for paragraph in normalized.paragraphs:
            if "元" not in paragraph.text:
                continue
            field = "fee_rule"
            if "一次性费用" in paragraph.text:
                field = "one_time_fee"
            elif "超出" in paragraph.text or "资费" in paragraph.text:
                field = "overage_fee"
            elif "违约金" in paragraph.text:
                field = "penalty_fee"
            facts.append(make_fact(release_id, self.domain, field, paragraph.text, [evidence.evidence_for_paragraph(paragraph.id).evidence_id]))
        return facts


class RuleExtractor(DomainExtractor):
    domain = "rule"

    def extract(self, release_id: str, normalized: NormalizedDocument, evidence: EvidenceIndex) -> list[ExtractedFact]:
        keywords = ["不得", "必须", "应", "需", "协议期", "生效", "终止", "注销", "欠费"]
        return [
            make_fact(release_id, self.domain, "business_rule", paragraph.text, [evidence.evidence_for_paragraph(paragraph.id).evidence_id])
            for paragraph in normalized.paragraphs
            if any(keyword in paragraph.text for keyword in keywords) and len(paragraph.text) >= 18
        ]


class FormFieldExtractor(DomainExtractor):
    domain = "form_field"

    def extract(self, release_id: str, normalized: NormalizedDocument, evidence: EvidenceIndex) -> list[ExtractedFact]:
        facts: list[ExtractedFact] = []
        for paragraph in normalized.paragraphs:
            text = paragraph.text.strip()
            if text.startswith("*") or text in {"身份证号码", "经办人职务", "E-MAIL", "传真", "日期"}:
                facts.append(make_fact(release_id, self.domain, "field", {"label": text.lstrip("*").strip(), "required": text.startswith("*")}, [evidence.evidence_for_paragraph(paragraph.id).evidence_id]))
        for blank in normalized.blank_fields:
            facts.append(make_fact(release_id, self.domain, "blank_field", {"label": blank.label, "placeholder": blank.placeholder}, [evidence.evidence_for_blank(blank.id).evidence_id]))
        for checkbox in normalized.checkboxes:
            facts.append(make_fact(release_id, self.domain, "checkbox", {"label": checkbox.label, "checked": checkbox.checked}, [evidence.evidence_for_checkbox(checkbox.id).evidence_id]))
        return facts


class ComplianceExtractor(DomainExtractor):
    domain = "compliance"

    def extract(self, release_id: str, normalized: NormalizedDocument, evidence: EvidenceIndex) -> list[ExtractedFact]:
        return [
            make_fact(release_id, self.domain, "compliance_clause", paragraph.text, [evidence.evidence_for_paragraph(paragraph.id).evidence_id])
            for paragraph in normalized.paragraphs
            if any(token in paragraph.text for token in ["网络安全", "信息安全", "个人信息", "承诺", "法律", "法规", "实名制"])
        ]


class RequiredDocumentExtractor(DomainExtractor):
    domain = "required_document"

    def extract(self, release_id: str, normalized: NormalizedDocument, evidence: EvidenceIndex) -> list[ExtractedFact]:
        return [
            make_fact(release_id, self.domain, "required_material", paragraph.text, [evidence.evidence_for_paragraph(paragraph.id).evidence_id])
            for paragraph in normalized.paragraphs
            if any(token in paragraph.text for token in ["复印件", "营业执照", "身份证", "介绍信", "资质文件", "材料"])
        ]


class ExtractionMerger:
    def merge(self, groups: Iterable[list[ExtractedFact]]) -> list[ExtractedFact]:
        merged: list[ExtractedFact] = []
        seen: set[tuple[str, str, str]] = set()
        for facts in groups:
            for fact in facts:
                key = (fact.domain, fact.field, repr(fact.value))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(fact)
        return merged


def make_fact(release_id: str, domain: str, field: str, value: Any, evidence_ids: list[str], confidence: float = 1.0, source: str = "rule") -> ExtractedFact:
    return ExtractedFact(f"fact_{uuid.uuid4().hex[:12]}", release_id, domain, field, value, evidence_ids, confidence, source)


DEFAULT_EXTRACTORS: list[DomainExtractor] = [ProductExtractor(), PlanExtractor(), OptionExtractor(), FeeExtractor(), RuleExtractor(), FormFieldExtractor(), ComplianceExtractor(), RequiredDocumentExtractor()]
