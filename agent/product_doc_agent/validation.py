from __future__ import annotations

from agent.product_doc_agent.llm_client import LLMClient
from agent.product_doc_agent.models import Release, ValidationIssue


class SchemaValidator:
    def validate(self, release: Release) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not release.release_id:
            issues.append(ValidationIssue("release_id_required", "release_id is required"))
        if not release.doc_id:
            issues.append(ValidationIssue("doc_id_required", "doc_id is required"))
        if not release.product_family or release.product_family == "unknown_product_family":
            issues.append(ValidationIssue("product_family_unknown", "product_family could not be inferred", "warning"))
        evidence_ids = {evidence.evidence_id for evidence in release.evidences}
        product_name_count = 0
        for fact in release.facts:
            if fact.release_id != release.release_id:
                issues.append(ValidationIssue("fact_release_id_mismatch", "fact.release_id must match release.release_id", fact_id=fact.fact_id))
            if not fact.evidence_ids:
                issues.append(ValidationIssue("fact_missing_evidence", "critical fact must bind evidence", fact_id=fact.fact_id))
            for evidence_id in fact.evidence_ids:
                if evidence_id not in evidence_ids:
                    issues.append(ValidationIssue("fact_evidence_not_found", f"evidence not found: {evidence_id}", fact_id=fact.fact_id))
            if fact.domain == "product" and fact.field == "product_name":
                product_name_count += 1
        if product_name_count == 0:
            issues.append(ValidationIssue("product_name_required", "product_name is required"))
        return issues


class ConflictDetector:
    def detect(self, release: Release) -> list[ValidationIssue]:
        values: dict[tuple[str, str], set[str]] = {}
        for fact in release.facts:
            if fact.field in {"product_name", "version_label"}:
                values.setdefault((fact.domain, fact.field), set()).add(str(fact.value))
        return [
            ValidationIssue("simple_conflict_detected", f"Multiple values for {domain}.{field}: {sorted(items)}", "warning")
            for (domain, field), items in values.items()
            if len(items) > 1
        ]


class AISelfReviewer:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client

    def review(self, release: Release) -> list[ValidationIssue]:
        if self.client is None:
            return []
        try:
            payload = self.client.complete_json(build_self_review_prompt(release))
        except Exception as exc:
            return [ValidationIssue("ai_self_review_failed", f"AI self review failed: {exc}", "warning")]
        issues: list[ValidationIssue] = []
        for item in payload.get("issues", []):
            if isinstance(item, dict) and item.get("message"):
                issues.append(ValidationIssue(str(item.get("code", "ai_semantic_warning")), str(item["message"]), "warning", str(item.get("fact_id")) if item.get("fact_id") else None))
        return issues


def build_self_review_prompt(release: Release) -> str:
    facts = [{"fact_id": f.fact_id, "domain": f.domain, "field": f.field, "value": f.value, "evidence_ids": f.evidence_ids, "source": f.source} for f in release.facts[:120]]
    return f"""
你是产品文档抽取结果的语义自检助手。只能提出 warning，不能决定发布或拒绝。
重点检查费用、套餐/可选包、协议期、违约金、必填表单字段、合规条款风险。
输出 JSON：{{"issues":[{{"code":"ai_semantic_warning","message":"...","fact_id":"可选"}}]}}
Release: {{"release_id":"{release.release_id}","version":"{release.version}","effective_date":"{release.effective_date}","variant":"{release.variant}"}}
Facts: {facts}
""".strip()
