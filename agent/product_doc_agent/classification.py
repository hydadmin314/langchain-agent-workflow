from __future__ import annotations

import re

from agent.product_doc_agent.models import NormalizedDocument, RawDocument


class ProductFamilyMatcher:
    def match(self, doc: RawDocument, normalized: NormalizedDocument) -> RawDocument:
        text = "\n".join(paragraph.text for paragraph in normalized.paragraphs[:20])
        doc.product_family = infer_product_family(text, doc.file_name)
        doc.region = infer_region(text, doc.file_name)
        doc.effective_date = infer_effective_date(text, doc.file_name)
        doc.version = infer_version(text, doc.file_name)
        doc.variant = infer_variant(text, doc.file_name)
        return doc


class DocumentClassifier:
    def classify(self, normalized: NormalizedDocument) -> str:
        text = "\n".join(paragraph.text for paragraph in normalized.paragraphs[:80])
        if "申请登记表" in text:
            return "product_application_form"
        if "营销规则" in text:
            return "marketing_rule"
        if "网络及信息安全承诺书" in text:
            return "network_security_commitment"
        if "授权委托" in text:
            return "authorization_letter"
        return "unknown"


def infer_product_family(text: str, file_name: str) -> str:
    joined = f"{text}\n{file_name}"
    if "智云上海专线基础版" in joined:
        return "zhiyun_shanghai_dedicated_line_basic"
    if "智云" in joined:
        return "zhiyun"
    return "unknown_product_family"


def infer_region(text: str, file_name: str) -> str | None:
    joined = f"{text}\n{file_name}".lower()
    if "上海" in joined or "shanghai" in joined:
        return "上海"
    return None


def infer_effective_date(text: str, file_name: str) -> str | None:
    joined = f"{file_name}\n{text}"
    patterns = [
        r"[【\[]?(20\d{2})(\d{2})(\d{2})\s*起[】\]]?",
        r"[【\[]?(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\s*起[】\]]?",
        r"生效(?:时间|日期)?[:：]?\s*(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, joined)
        if match:
            year, month, day = match.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"
    return None


def infer_version(text: str, file_name: str) -> str:
    effective_date = infer_effective_date(text, file_name)
    if effective_date:
        return f"effective_{effective_date.replace('-', '')}"
    joined = f"{text}\n{file_name}"
    match = re.search(r"(20\d{2})\s*[/_-]?\s*([A-Z])?", joined, flags=re.IGNORECASE)
    if match:
        suffix = match.group(2) or ""
        return f"{match.group(1)}{('/' + suffix.upper()) if suffix else ''}"
    return "unversioned"


def infer_variant(text: str, file_name: str) -> str:
    joined = f"{text}\n{file_name}"
    parts: list[str] = []
    if "上海" in joined or "shanghai" in joined.lower():
        parts.append("shanghai")
    if "打折" in joined or "折扣" in joined:
        parts.append("discount")
    if "不带语音" in joined:
        parts.append("without_voice")
    elif "带语音" in joined:
        parts.append("with_voice")
    else:
        parts.append("default")
    return "-".join(parts)
