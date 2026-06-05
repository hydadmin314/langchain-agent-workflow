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


# 第 13 类是售后/办理流程，不应该混进普通新销售套餐推荐。
PROCESS_IDENTITY_KEYWORDS = (
    "变更",
    "续约",
    "拆机",
    "撤单",
    "移机",
    "过户",
    "更名",
    "退费",
    "退款",
    "停机",
    "销户",
    "付款方式",
    "账单",
    "材料",
    "手续",
    "流程",
    "受理单",
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

# 移动通信类需求如果完全没有这些证据，基本就是泛召回噪声。
MOBILE_EVIDENCE_KEYWORDS = (
    "5G",
    "流量",
    "手机卡",
    "主卡",
    "副卡",
    "固移融合",
    "SIM",
    "语音流量",
    "移动业务",
    "移动套餐",
    "移动通信",
)

# 用于识别“主体更像宽带/专线，但包含少量移动权益”的候选，先保留并提示风险。
BROADBAND_EVIDENCE_KEYWORDS = (
    "宽带",
    "专线",
    "互联网专线",
    "精品专线",
    "商务专线",
    "IPMAN",
    "BGP",
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

        # 规则 2：第 13 类属于办理/变更/续约/拆机等流程，不混入普通新销售套餐。
        if is_service_process(category_decision):
            # 这里只看标题、路径、文档类型等身份字段，不看正文条款。
            # 普通套餐合同里也可能出现“拆机/变更/违约”，不能因此当成办理流程文档。
            identity_text = build_product_identity_text(product)
            matched_text = " ".join(candidate.matched_keywords)
            if not contains_any(identity_text, PROCESS_IDENTITY_KEYWORDS) and not contains_any(
                matched_text,
                PROCESS_IDENTITY_KEYWORDS,
            ):
                reasons.append(
                    FilterReason(
                        code="service_process_mismatch",
                        severity="error",
                        message="当前需求是办理/变更/续约/拆机类流程，但该候选缺少流程或手续相关证据。",
                        evidence=short_evidence(
                            f"{identity_text} {matched_text}",
                            PROCESS_IDENTITY_KEYWORDS,
                        ),
                    )
                )
                return FilteredCandidate(candidate=candidate, decision="remove", filter_reasons=reasons)

        # 规则 3：客户明确要求固定 IP 时，候选没有固定 IP/BGP/IPMAN 等证据则提示风险。
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

        # 规则 4：海外访问类需求如果没有海外/国际/精品专线等证据，先保留但打风险。
        if category_decision.primary_category_id == "4" and not contains_any(product_text, OVERSEAS_EVIDENCE_KEYWORDS):
            reasons.append(
                FilterReason(
                    code="missing_overseas_evidence",
                    severity="warning",
                    message="客户需求为海外访问/跨境加速，但该候选缺少海外、国际、跨境、BGP、IPMAN、精品专线、智能专线等证据。",
                    evidence=short_evidence(product_text, OVERSEAS_EVIDENCE_KEYWORDS),
                )
            )
            risk_tags.append("overseas_capability_needs_confirmation")

        # 规则 5：移动流量类完全没有移动证据时移除；若主体像宽带/专线，则保留并提示。
        if category_decision.primary_category_id == "7":
            identity_text = build_product_identity_text(product)
            has_mobile_identity = contains_any(identity_text, MOBILE_EVIDENCE_KEYWORDS)
            has_mobile_evidence = contains_any(product_text, MOBILE_EVIDENCE_KEYWORDS)
            looks_like_broadband_bundle = contains_any(product_text, BROADBAND_EVIDENCE_KEYWORDS)
            if not has_mobile_evidence:
                reasons.append(
                    FilterReason(
                        code="mobile_category_without_mobile_evidence",
                        severity="error",
                        message="当前需求是移动通信/流量/固移融合，但该候选没有移动、5G、流量、手机卡等证据。",
                        evidence=short_evidence(product_text, MOBILE_EVIDENCE_KEYWORDS),
                    )
                )
                return FilteredCandidate(candidate=candidate, decision="remove", filter_reasons=reasons)

            if not has_mobile_identity and not looks_like_broadband_bundle:
                reasons.append(
                    FilterReason(
                        code="mobile_category_weak_body_evidence",
                        severity="error",
                        message="当前需求是移动通信/流量，但移动证据只出现在正文弱相关位置，产品标题、路径或产品族没有移动业务证据。",
                        evidence=short_evidence(product_text, MOBILE_EVIDENCE_KEYWORDS),
                    )
                )
                return FilteredCandidate(candidate=candidate, decision="remove", filter_reasons=reasons)

            if not has_mobile_identity and looks_like_broadband_bundle:
                reasons.append(
                    FilterReason(
                        code="mobile_candidate_may_be_broadband_bundle",
                        severity="warning",
                        message="该候选包含移动权益证据，但主体可能是宽带/专线套餐，需确认是否适合作为移动流量主推荐。",
                        evidence=short_evidence(product_text, BROADBAND_EVIDENCE_KEYWORDS),
                    )
                )
                risk_tags.append("mobile_bundle_needs_confirmation")

        # 规则 6：客户给出预算时，如果候选没有任何价格或费用规则，提示后续报价风险。
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


def is_service_process(category_decision: DemandCategoryDecision) -> bool:
    """判断当前需求是否属于办理/变更/续约/拆机等流程类。"""

    return (
        category_decision.primary_category_id == "13"
        or category_decision.recommendation_mode == "service_process"
    )


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
