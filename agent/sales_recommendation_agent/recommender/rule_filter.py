from __future__ import annotations

from collections.abc import Iterable

from agent.sales_recommendation_agent.intent_parser.models import CustomerDemand, DemandCategoryDecision
from agent.sales_recommendation_agent.product_repository.models import ProductCandidate
from agent.sales_recommendation_agent.recommender.models import (
    CandidateFilterResult,
    CandidateRetrievalResult,
    FilterReason,
    FilteredCandidate,
    RetrievedCandidate,
)


# 固定 IP 需求只看明确证据，不把普通“专线”直接等同为固定 IP。
FIXED_IP_EVIDENCE_KEYWORDS = (
    "固定IP",
    "固定 IP",
    "公网IP",
    "公网 IP",
    "公网地址",
    "IP地址",
    "IP 地址",
    "BGP",
    "IPMAN",
    "备案",
)

# 海外访问需要有海外/跨境或高质量互联网接入类证据；第一版只打风险，不硬删。
OVERSEAS_EVIDENCE_KEYWORDS = (
    "海外",
    "国外",
    "国际",
    "跨境",
    "美国",
    "新加坡",
    "日本",
    "香港",
    "BGP",
    "IPMAN",
    "精品专线",
    "智能专线",
    "SD-WAN",
)


class CandidateRuleFilter:
    """候选产品确定性过滤器。

    本层只处理“非常确定”的过滤和风险打标，不做复杂语义判断，也不调用大模型。
    """

    def apply(
        self,
        *,
        demand: CustomerDemand,
        category_decision: DemandCategoryDecision,
        retrieval_result: CandidateRetrievalResult,
    ) -> CandidateFilterResult:
        kept: list[FilteredCandidate] = []
        removed: list[FilteredCandidate] = []

        for candidate in retrieval_result.candidates:
            filtered = self._filter_one(
                candidate=candidate,
                demand=demand,
                category_decision=category_decision,
            )
            if filtered.decision == "remove":
                removed.append(filtered)
            else:
                kept.append(filtered)

        return CandidateFilterResult(
            primary_category_id=retrieval_result.primary_category_id,
            primary_category_name=retrieval_result.primary_category_name,
            recommendation_mode=retrieval_result.recommendation_mode,
            total_candidates=len(retrieval_result.candidates),
            kept_count=len(kept),
            removed_count=len(removed),
            kept_candidates=kept,
            removed_candidates=removed,
            global_warnings=self._build_global_warnings(
                retrieval_result=retrieval_result,
                kept_candidates=kept,
            ),
            clarify_questions=retrieval_result.clarify_questions,
        )

    def _filter_one(
        self,
        *,
        candidate: RetrievedCandidate,
        demand: CustomerDemand,
        category_decision: DemandCategoryDecision,
    ) -> FilteredCandidate:
        product = candidate.product
        product_text = build_product_text(product)
        reasons: list[FilterReason] = []
        risk_tags: list[str] = []
        decision = "keep"

        # 规则 1：非 active 产品不进入推荐池。停用判断以后统一依赖 document_status/is_active。
        if not product.is_active:
            reasons.append(
                FilterReason(
                    code="inactive_product",
                    severity="error",
                    message="产品文档状态不是 active，已从推荐候选中移除。",
                    evidence=product.document_status,
                )
            )
            return FilteredCandidate(candidate=candidate, decision="remove", filter_reasons=reasons)

        # 规则 2：客户明确要求固定 IP 时，候选没有固定 IP/BGP/IPMAN 等证据则提示风险。
        if demand.requires_fixed_ip and not contains_any(product_text, FIXED_IP_EVIDENCE_KEYWORDS):
            reasons.append(
                FilterReason(
                    code="missing_fixed_ip_evidence",
                    severity="warning",
                    message="客户要求固定公网 IP，但该候选缺少固定IP/公网IP/IP地址/BGP/IPMAN 等证据，需人工确认是否支持。",
                    evidence=short_evidence(product_text, FIXED_IP_EVIDENCE_KEYWORDS),
                )
            )
            risk_tags.append("fixed_ip_needs_confirmation")

        # 规则 3：海外访问只是需求属性，不是主分类；有海外诉求但候选缺少证据时先保留并提示风险。
        if demand.overseas_access is True and not contains_any(product_text, OVERSEAS_EVIDENCE_KEYWORDS):
            reasons.append(
                FilterReason(
                    code="missing_overseas_evidence",
                    severity="warning",
                    message="客户提到海外/跨境访问诉求，但该候选缺少海外、国际、跨境、BGP、IPMAN、精品专线、智能专线等证据。",
                    evidence=short_evidence(product_text, OVERSEAS_EVIDENCE_KEYWORDS),
                )
            )
            risk_tags.append("overseas_capability_needs_confirmation")

        # 规则 4：客户给出预算时，如果候选没有任何价格或费用规则，提示后续报价风险。
        if demand.budget_amount is not None and not has_price_evidence(product):
            reasons.append(
                FilterReason(
                    code="budget_without_price_evidence",
                    severity="warning",
                    message="客户已给出预算，但该候选缺少价格、费用规则或金额证据，暂时无法判断预算匹配。",
                    evidence="budget=%s" % demand.budget,
                )
            )
            risk_tags.append("price_missing")

        return FilteredCandidate(
            candidate=candidate,
            decision=decision,
            filter_reasons=reasons,
            risk_tags=dedupe(risk_tags),
        )

    def _build_global_warnings(
        self,
        *,
        retrieval_result: CandidateRetrievalResult,
        kept_candidates: list[FilteredCandidate],
    ) -> list[str]:
        warnings: list[str] = []
        if retrieval_result.candidates and not kept_candidates:
            warnings.append("Rule Filter 已移除全部召回候选，建议销售补充需求或扩大候选范围。")
        if not retrieval_result.candidates and retrieval_result.clarify_questions:
            warnings.append("当前没有召回候选，已返回追问信息。")
        return warnings
def has_price_evidence(product: ProductCandidate) -> bool:
    """判断候选是否具备基础价格证据。

    后续 Calculator 没有完成前，这里只做宽松判断：套餐价格、费用规则金额、可选包价格明细任一存在即可。
    """

    for package in product.packages:
        if package.price is not None:
            return True
        if contains_any(package.source_evidence, ("元", "月租费", "费用", "资费", "价格")):
            return True

    for fee_rule in product.fee_rules:
        if fee_rule.amount is not None:
            return True
        if contains_any(fee_rule.source_evidence + fee_rule.description, ("元", "费用", "资费", "价格", "月费")):
            return True

    for optional in product.optional_packages:
        if optional.price_items:
            return True
        if contains_any(optional.fee_summary + optional.source_evidence, ("元", "费用", "资费", "价格", "月费")):
            return True

    return False


def build_product_text(product: ProductCandidate) -> str:
    """把候选产品中可检索的字段拼成文本，供确定性规则做关键词判断。"""

    values: list[str] = [
        product.document_id,
        product.title,
        product.product_name,
        product.product_family,
        product.carrier,
        product.region,
        product.document_type,
        product.document_status,
        product.filename,
        product.source_path,
        product.category_path,
        *product.category_levels,
        *product.keywords,
    ]

    for package in product.packages:
        values.extend(
            [
                package.package_name,
                package.package_code,
                package.speed,
                package.upstream_speed,
                package.downstream_speed,
                package.billing_period,
                package.contract_period,
                package.source_evidence,
                *package.applicable_conditions,
            ]
        )
        values.extend(str(item) for item in package.included_items)

    for optional in product.optional_packages:
        values.extend(
            [
                optional.name,
                optional.package_type,
                optional.category,
                optional.description,
                optional.fee_summary,
                optional.source_evidence,
                *optional.applicable_conditions,
                *(str(item) for item in optional.options),
                *(str(item) for item in optional.price_items),
            ]
        )

    for fee_rule in product.fee_rules:
        values.extend(
            [
                fee_rule.rule_type,
                fee_rule.name,
                fee_rule.description,
                fee_rule.billing_period,
                fee_rule.contract_period,
                fee_rule.source_evidence,
                *fee_rule.conditions,
                *fee_rule.applies_to,
            ]
        )

    for constraint in product.constraints:
        values.extend(
            [
                constraint.constraint_type,
                constraint.name,
                constraint.description,
                constraint.condition,
                constraint.result,
                constraint.source_evidence,
                *constraint.applies_to,
            ]
        )

    return " ".join(value for value in values if value)


def build_product_identity_text(product: ProductCandidate) -> str:
    """只拼接产品身份字段，避免正文条款干扰流程类过滤。"""

    values = [
        product.document_id,
        product.title,
        product.product_name,
        product.product_family,
        product.document_type,
        product.filename,
        product.source_path,
        product.category_path,
        *product.category_levels,
    ]
    return " ".join(value for value in values if value)


def contains_any(text: str, keywords: Iterable[str]) -> bool:
    """大小写不敏感的关键词命中判断。"""

    normalized = text.lower()
    return any(keyword.lower() in normalized for keyword in keywords if keyword)


def short_evidence(text: str, keywords: Iterable[str], *, max_length: int = 160) -> str:
    """截取命中关键词附近的短证据，避免前端展示过长文本。"""

    normalized = text.lower()
    for keyword in keywords:
        if not keyword:
            continue
        index = normalized.find(keyword.lower())
        if index >= 0:
            start = max(0, index - 40)
            end = min(len(text), index + len(keyword) + 80)
            return text[start:end][:max_length]
    return ""


def dedupe(values: Iterable[str]) -> list[str]:
    """保持顺序去重。"""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
