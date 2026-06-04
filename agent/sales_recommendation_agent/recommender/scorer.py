from __future__ import annotations

from collections.abc import Iterable

from agent.sales_recommendation_agent.intent_parser.models import CustomerDemand, DemandCategoryDecision
from agent.sales_recommendation_agent.intent_parser.taxonomy import DEMAND_CATEGORY_RULES_BY_ID
from agent.sales_recommendation_agent.product_repository.models import ProductCandidate
from agent.sales_recommendation_agent.recommender.models import (
    CandidateFilterResult,
    CandidateScoreResult,
    FilterReason,
    FilteredCandidate,
    ScoredCandidate,
    ScoreReason,
)
from agent.sales_recommendation_agent.recommender.rule_filter import (
    FIXED_IP_EVIDENCE_KEYWORDS,
    MOBILE_EVIDENCE_KEYWORDS,
    OVERSEAS_EVIDENCE_KEYWORDS,
    PROCESS_IDENTITY_KEYWORDS,
    build_product_identity_text,
    build_product_text,
    contains_any,
    has_price_evidence,
    short_evidence,
)


# Rule Filter 的风险原因在排序阶段转成扣分。分值先保持克制，避免一个 warning 直接压死候选。
RISK_PENALTY_BY_CODE = {
    "missing_fixed_ip_evidence": -15.0,
    "missing_overseas_evidence": -12.0,
    "mobile_candidate_may_be_broadband_bundle": -8.0,
    "budget_without_price_evidence": -12.0,
}


class CandidateScorer:
    """候选产品程序排序器。

    Scorer 不调用大模型，只做稳定、可解释的打分排序。
    """

    def score(
        self,
        *,
        demand: CustomerDemand,
        category_decision: DemandCategoryDecision,
        filter_result: CandidateFilterResult,
        top_k: int | None = None,
    ) -> CandidateScoreResult:
        scored_candidates = [
            self._score_one(
                filtered_candidate=filtered,
                demand=demand,
                category_decision=category_decision,
            )
            for filtered in filter_result.kept_candidates
        ]
        scored_candidates.sort(key=lambda item: item.final_score, reverse=True)

        if top_k is not None:
            scored_candidates = scored_candidates[:top_k]

        return CandidateScoreResult(
            primary_category_id=filter_result.primary_category_id,
            primary_category_name=filter_result.primary_category_name,
            recommendation_mode=filter_result.recommendation_mode,
            total_candidates=filter_result.kept_count,
            scored_count=len(scored_candidates),
            scored_candidates=scored_candidates,
            clarify_questions=filter_result.clarify_questions,
            global_warnings=filter_result.global_warnings,
        )

    def _score_one(
        self,
        *,
        filtered_candidate: FilteredCandidate,
        demand: CustomerDemand,
        category_decision: DemandCategoryDecision,
    ) -> ScoredCandidate:
        candidate = filtered_candidate.candidate
        product = candidate.product
        product_text = build_product_text(product)
        score_reasons: list[ScoreReason] = []
        risk_penalties: list[ScoreReason] = []

        # 1. 继承召回阶段的粗相关性分，避免 Scorer 完全重复 Retriever 的工作。
        base_score = float(candidate.retrieval_score)
        score_reasons.append(
            ScoreReason(
                code="base_retrieval_score",
                score_delta=base_score,
                message="继承 Candidate Retriever 的召回相关性分。",
            )
        )

        # 2. 当前主分类对应的产品关键词命中越明确，排序越靠前。
        score_reasons.extend(
            self._score_exact_demand_keyword_identity_match(
                category_decision=category_decision,
                product=product,
            )
        )
        score_reasons.extend(
            self._score_primary_category_evidence(
                category_decision=category_decision,
                product_text=product_text,
            )
        )

        # 3. 客户明确说出的关键需求，用确定性证据加分。
        score_reasons.extend(
            self._score_demand_evidence(
                demand=demand,
                category_decision=category_decision,
                product=product,
                product_text=product_text,
            )
        )

        # 4. Rule Filter 的 warning 转成扣分，但保留候选，交给销售/LLM 解释风险。
        risk_penalties.extend(self._score_filter_risks(filtered_candidate.filter_reasons))

        final_score = sum(reason.score_delta for reason in score_reasons) + sum(
            penalty.score_delta for penalty in risk_penalties
        )
        return ScoredCandidate(
            filtered_candidate=filtered_candidate,
            final_score=round(final_score, 2),
            score_reasons=score_reasons,
            risk_penalties=risk_penalties,
        )

    def _score_exact_demand_keyword_identity_match(
        self,
        *,
        category_decision: DemandCategoryDecision,
        product: ProductCandidate,
    ) -> list[ScoreReason]:
        """客户明确命中的需求词，如果出现在产品身份字段中，应明显优先。

        例如客户说“云中继/30B+D”，标题或路径就是“云中继”的文档，
        应该排在“专线套餐里附带商云通权益”的候选前面。
        """

        identity_text = build_product_identity_text(product)
        demand_keywords: list[str] = []
        for match in category_decision.category_matches:
            if match.category_id == category_decision.primary_category_id:
                demand_keywords.extend(match.matched_keywords)

        hits = matched_keywords(identity_text, demand_keywords)
        if not hits:
            return []

        return [
            ScoreReason(
                code="exact_demand_keyword_identity_match",
                score_delta=18.0 + min(len(hits), 3) * 3.0,
                message="客户明确需求关键词命中产品标题、路径或产品族，优先级较高。",
                evidence=", ".join(hits[:6]),
            )
        ]

    def _score_primary_category_evidence(
        self,
        *,
        category_decision: DemandCategoryDecision,
        product_text: str,
    ) -> list[ScoreReason]:
        rule = DEMAND_CATEGORY_RULES_BY_ID.get(category_decision.primary_category_id)
        if not rule:
            return []

        reasons: list[ScoreReason] = []
        product_hits = matched_keywords(product_text, rule.product_keywords)
        path_hits = matched_keywords(product_text, rule.product_path_keywords)

        if product_hits:
            reasons.append(
                ScoreReason(
                    code="primary_category_product_keyword_match",
                    score_delta=12.0 + min(len(product_hits), 4) * 1.5,
                    message=f"候选产品命中主分类「{rule.category_name}」的产品关键词。",
                    evidence=", ".join(product_hits[:6]),
                )
            )

        if path_hits:
            reasons.append(
                ScoreReason(
                    code="primary_category_path_keyword_match",
                    score_delta=8.0 + min(len(path_hits), 3),
                    message=f"候选产品路径或产品族命中主分类「{rule.category_name}」。",
                    evidence=", ".join(path_hits[:6]),
                )
            )

        return reasons

    def _score_demand_evidence(
        self,
        *,
        demand: CustomerDemand,
        category_decision: DemandCategoryDecision,
        product: ProductCandidate,
        product_text: str,
    ) -> list[ScoreReason]:
        reasons: list[ScoreReason] = []

        if demand.requires_fixed_ip and contains_any(product_text, FIXED_IP_EVIDENCE_KEYWORDS):
            reasons.append(
                ScoreReason(
                    code="fixed_ip_evidence_match",
                    score_delta=10.0,
                    message="客户要求固定公网 IP，候选包含固定IP/公网IP/IP地址/BGP/IPMAN 等证据。",
                    evidence=short_evidence(product_text, FIXED_IP_EVIDENCE_KEYWORDS),
                )
            )

        if category_decision.primary_category_id == "4" and contains_any(product_text, OVERSEAS_EVIDENCE_KEYWORDS):
            reasons.append(
                ScoreReason(
                    code="overseas_evidence_match",
                    score_delta=10.0,
                    message="客户需求为海外访问/跨境加速，候选包含海外、国际、BGP、IPMAN、精品专线或智能专线等证据。",
                    evidence=short_evidence(product_text, OVERSEAS_EVIDENCE_KEYWORDS),
                )
            )

        if category_decision.primary_category_id == "7" and contains_any(product_text, MOBILE_EVIDENCE_KEYWORDS):
            reasons.append(
                ScoreReason(
                    code="mobile_evidence_match",
                    score_delta=10.0,
                    message="客户需求为移动通信/流量，候选包含移动、5G、流量、手机卡等证据。",
                    evidence=short_evidence(product_text, MOBILE_EVIDENCE_KEYWORDS),
                )
            )

        if category_decision.recommendation_mode == "service_process":
            identity_text = build_product_identity_text(product)
            process_hits = matched_keywords(identity_text, PROCESS_IDENTITY_KEYWORDS)
            if process_hits:
                reasons.append(
                    ScoreReason(
                        code="service_process_identity_match",
                        score_delta=16.0 + min(len(process_hits), 4),
                        message="当前是办理/变更/续约/拆机类需求，候选标题或路径命中流程类证据。",
                        evidence=", ".join(process_hits[:6]),
                    )
                )

        if demand.budget is not None and has_price_evidence(product):
            reasons.append(
                ScoreReason(
                    code="price_evidence_for_budget",
                    score_delta=6.0,
                    message="客户提供了预算，候选包含价格或费用规则证据。",
                )
            )

        if demand.user_count and has_capacity_or_bandwidth_evidence(product_text):
            reasons.append(
                ScoreReason(
                    code="capacity_or_bandwidth_evidence",
                    score_delta=4.0,
                    message="客户提供了人数/终端规模，候选包含带宽、线数、终端数或套餐档位证据。",
                )
            )

        return reasons

    def _score_filter_risks(self, filter_reasons: list[FilterReason]) -> list[ScoreReason]:
        penalties: list[ScoreReason] = []
        for reason in filter_reasons:
            penalty = RISK_PENALTY_BY_CODE.get(reason.code)
            if penalty is None:
                continue
            penalties.append(
                ScoreReason(
                    code=f"risk_penalty_{reason.code}",
                    score_delta=penalty,
                    message=f"Rule Filter 风险扣分：{reason.message}",
                    evidence=reason.evidence,
                )
            )
        return penalties


def matched_keywords(text: str, keywords: Iterable[str]) -> list[str]:
    """返回命中的关键词，保持原始顺序并去重。"""

    normalized = text.lower()
    return dedupe(keyword for keyword in keywords if keyword and keyword.lower() in normalized)


def has_capacity_or_bandwidth_evidence(text: str) -> bool:
    """判断候选是否包含容量、带宽、线数或终端规模证据。"""

    return contains_any(
        text,
        (
            "M",
            "Mbps",
            "G",
            "Gbps",
            "带宽",
            "速率",
            "线",
            "坐席",
            "终端",
            "台",
            "用户",
            "人数",
            "主卡",
            "副卡",
        ),
    )


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
