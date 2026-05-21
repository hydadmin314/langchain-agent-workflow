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
            if "拨号" in checkbox.label and any(token in checkbox.label for token in ["元/月", "元/年", "元/2年"])
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
        facts.extend(self._checkbox_group_facts(release_id, normalized, evidence))
        for checkbox in normalized.checkboxes:
            facts.append(make_fact(release_id, self.domain, "checkbox", {"label": checkbox.label, "checked": checkbox.checked}, [evidence.evidence_for_checkbox(checkbox.id).evidence_id]))
        return facts

    def _checkbox_group_facts(self, release_id: str, normalized: NormalizedDocument, evidence: EvidenceIndex) -> list[ExtractedFact]:
        facts: list[ExtractedFact] = []
        checkboxes_by_row: dict[tuple[str, int], list[Any]] = {}
        for checkbox in normalized.checkboxes:
            if checkbox.table_id is None or checkbox.row is None:
                continue
            checkboxes_by_row.setdefault((checkbox.table_id, checkbox.row), []).append(checkbox)

        for table in normalized.tables:
            for row_index, row in enumerate(table.rows):
                row_checkboxes = checkboxes_by_row.get((table.id, row_index), [])
                if len(row_checkboxes) < 2:
                    continue
                first_checkbox_col = min(checkbox.col or 0 for checkbox in row_checkboxes)
                label = nearest_left_label(row, first_checkbox_col)
                if not label:
                    continue
                options = [
                    checkbox_option_value(checkbox, row[checkbox.col or 0])
                    for checkbox in sorted(row_checkboxes, key=lambda item: (item.col or 0, item.id))
                ]
                for option, checkbox in zip(options, sorted(row_checkboxes, key=lambda item: (item.col or 0, item.id))):
                    option["evidence_id"] = evidence.evidence_for_checkbox(checkbox.id).evidence_id
                    option["checkbox_id"] = checkbox.id
                evidence_ids = [evidence.evidence_for_table_row(table.id, row_index).evidence_id]
                evidence_ids.extend(option["evidence_id"] for option in options)
                facts.append(
                    make_fact(
                        release_id,
                        self.domain,
                        "checkbox_group",
                        {
                            "label": label,
                            "required": left_label_is_required(row, first_checkbox_col),
                            "options": options,
                            "table_id": table.id,
                            "row": row_index,
                        },
                        evidence_ids,
                    )
                )
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


def nearest_left_label(row: list[str], first_checkbox_col: int) -> str:
    for cell in reversed(row[:first_checkbox_col]):
        label = clean_field_label(cell)
        if label:
            return label
    return ""


def clean_field_label(value: str) -> str:
    text = re.sub(r"\s+", "", value or "").strip()
    text = re.sub(r"^[*＊]+", "", text)
    return text.strip(" :：")


def left_label_is_required(row: list[str], first_checkbox_col: int) -> bool:
    return any((cell or "").strip().startswith(("*", "＊")) for cell in row[:first_checkbox_col])


def checkbox_option_value(checkbox: Any, cell_text: str) -> dict[str, Any]:
    raw_label = checkbox.label
    value = {
        "label": clean_option_label(raw_label),
        "raw_label": raw_label,
        "checked": checkbox.checked,
    }
    context = checkbox_context(cell_text, raw_label)
    if context:
        value["context"] = context
    input_fields = input_fields_from_option(raw_label)
    if input_fields:
        value["input_fields"] = input_fields
    return value


def checkbox_context(cell_text: str, option_label: str) -> str:
    normalized_option = normalize_compact(option_label)
    for line in cell_text.splitlines():
        if normalized_option not in normalize_compact(line):
            continue
        prefix = re.split(r"[□☐■☑☒]", line, maxsplit=1)[0]
        prefix = clean_field_label(prefix)
        if prefix:
            return prefix
    return ""


def input_fields_from_option(label: str) -> list[dict[str, str]]:
    fields = []
    for match in re.finditer(r"(_{2,}|\[\s*\])", label):
        before = label[: match.start()]
        after = label[match.end() :]
        field_label = infer_input_label(before)
        fields.append(
            {
                "label": field_label,
                "placeholder": match.group(1),
                "suffix": infer_input_suffix(after),
            }
        )
    return fields


def infer_input_label(text: str) -> str:
    text = re.sub(r"[□☐■☑☒]", "", text)
    text = text.rstrip(" ：:")
    for delimiter in ["：", ":", "（", "(", "，", ",", " "]:
        if delimiter in text:
            text = text.rsplit(delimiter, 1)[-1]
    return clean_field_label(text)


def infer_input_suffix(text: str) -> str:
    suffix = re.split(r"[，,。；;\s）)]", text.strip(), maxsplit=1)[0]
    return suffix[:8]


def clean_option_label(label: str) -> str:
    cleaned = label
    for match in reversed(list(re.finditer(r"(_{2,}|\[\s*\])", cleaned))):
        before = cleaned[: match.start()]
        start = max(before.rfind(delimiter) for delimiter in ["，", ",", "（", "(", " "])
        if start < 0:
            start = before.rfind("：")
        if start < 0:
            start = before.rfind(":")
        if start < 0:
            start = match.start()
        end = match.end()
        while end < len(cleaned) and cleaned[end] not in "，,；;。 ":
            end += 1
        cleaned = cleaned[:start] + cleaned[end:]
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.strip(" ：:，,（）()")


def normalize_compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


DEFAULT_EXTRACTORS: list[DomainExtractor] = [ProductExtractor(), PlanExtractor(), OptionExtractor(), FeeExtractor(), RuleExtractor(), FormFieldExtractor(), ComplianceExtractor(), RequiredDocumentExtractor()]
