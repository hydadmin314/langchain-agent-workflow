from __future__ import annotations

from typing import Iterable

from agent.sales_recommendation_agent.intent_parser.models import CustomerDemand, DemandCategoryDecision, DemandCategoryMatch
from agent.sales_recommendation_agent.intent_parser.taxonomy import DEMAND_CATEGORY_RULES_BY_ID
from agent.sales_recommendation_agent.product_repository.models import ProductCandidate
from agent.sales_recommendation_agent.recommender.models import CandidateRetrievalResult, RetrievedCandidate


class CandidateRetriever:
    """按 13 类产品需求分类体系召回候选产品。

    本层只负责“召回可能相关的产品”，不做最终推荐结论，也不做报价核算。
    """

    def __init__(
        self,
        *,
        min_score: float = 10.0,
        default_top_k: int = 20,
    ):
        self.min_score = min_score
        self.default_top_k = default_top_k

    def retrieve(
        self,
        *,
        demand: CustomerDemand,
        category_decision: DemandCategoryDecision,
        products: list[ProductCandidate],
        top_k: int | None = None,
    ) -> CandidateRetrievalResult:
        if not category_decision.category_matches:
            return CandidateRetrievalResult(
                primary_category_id="",
                primary_category_name="",
                recommendation_mode="clarify",
                total_products=len(products),
                matched_count=0,
                demand_categories=[],
                candidates=[],
                clarify_questions=category_decision.clarify_questions,
            )

        retrieved: list[RetrievedCandidate] = []
        for product in products:
            candidate = self._score_product(
                product=product,
                demand=demand,
                category_decision=category_decision,
            )
            if candidate and candidate.retrieval_score >= self.min_score:
                retrieved.append(candidate)

        retrieved.sort(key=lambda item: item.retrieval_score, reverse=True)
        limited = retrieved[: top_k or self.default_top_k]
        return CandidateRetrievalResult(
            primary_category_id=category_decision.primary_category_id,
            primary_category_name=category_decision.primary_category_name,
            recommendation_mode=category_decision.recommendation_mode,
            total_products=len(products),
            matched_count=len(limited),
            demand_categories=category_decision.category_matches,
            candidates=limited,
            clarify_questions=category_decision.clarify_questions,
        )

    def _score_product(
        self,
        *,
        product: ProductCandidate,
        demand: CustomerDemand,
        category_decision: DemandCategoryDecision,
    ) -> RetrievedCandidate | None:
        if not product.is_active:
            return None

        search_text = self._build_product_search_text(product)
        score = 0.0
        matched_reasons: list[str] = []
        matched_keywords: list[str] = []
        warnings: list[str] = []

        scoring_categories = [
            item
            for item in category_decision.category_matches
            if item.category_id == category_decision.primary_category_id
        ] or category_decision.category_matches[:1]

        for category_match in scoring_categories:
            rule = DEMAND_CATEGORY_RULES_BY_ID.get(category_match.category_id)
            if not rule:
                continue

            product_keywords = _matched_keywords(search_text, rule.product_keywords)
            path_keywords = _matched_keywords(search_text, rule.product_path_keywords)
            if product_keywords:
                score += 10 + 2 * len(product_keywords)
                matched_keywords.extend(product_keywords)
                matched_reasons.append(
                    f"命中需求分类「{rule.category_name}」对应产品关键词：{', '.join(product_keywords[:5])}"
                )
            if path_keywords:
                score += 8 + 2 * len(path_keywords)
                matched_keywords.extend(path_keywords)
                matched_reasons.append(
                    f"命中需求分类「{rule.category_name}」对应产品目录/产品族：{', '.join(path_keywords[:5])}"
                )
            if product_keywords or path_keywords:
                score += min(category_match.score, 12)

            for note in rule.notes:
                if note not in warnings and (product_keywords or path_keywords):
                    warnings.append(note)

        if demand.requires_fixed_ip:
            ip_keywords = _matched_keywords(search_text, ("固定IP", "公网IP", "IP地址", "IPMAN", "BGP", "备案"))
            if ip_keywords:
                score += 8
                matched_keywords.extend(ip_keywords)
                matched_reasons.append(f"客户需要固定公网 IP，产品文本命中：{', '.join(ip_keywords[:5])}")

        target_hits = _matched_keywords(search_text, _target_keywords(demand))
        if target_hits:
            score += 5
            matched_keywords.extend(target_hits)
            matched_reasons.append(f"命中客户目标区域/场景关键词：{', '.join(target_hits[:5])}")

        bandwidth_hits = self._match_bandwidth(product, demand)
        if bandwidth_hits:
            score += 4
            matched_keywords.extend(bandwidth_hits)
            matched_reasons.append(f"命中客户带宽需求：{', '.join(bandwidth_hits[:5])}")

        if not product.packages and not product.optional_packages and not product.fee_rules:
            warnings.append("该产品 JSON 的套餐/可选包/费用规则较少，后续推荐需要人工复核数据完整性。")
            score -= 2

        if score <= 0:
            return None

        return RetrievedCandidate(
            product=product,
            retrieval_score=round(score, 2),
            demand_categories=scoring_categories,
            matched_reasons=_dedupe(matched_reasons),
            matched_keywords=_dedupe(matched_keywords),
            warnings=_dedupe(warnings),
        )

    def _build_product_search_text(self, product: ProductCandidate) -> str:
        values: list[str] = [
            product.title,
            product.product_name,
            product.product_family,
            product.carrier,
            product.region,
            product.document_type,
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

        return " ".join(value for value in values if value)

    def _match_bandwidth(self, product: ProductCandidate, demand: CustomerDemand) -> list[str]:
        if not demand.bandwidth_est_mbps:
            return []
        expected = f"{demand.bandwidth_est_mbps}M"
        search_text = self._build_product_search_text(product)
        if expected in search_text:
            return [expected]
        if demand.bandwidth_est_mbps >= 1000 and ("1G" in search_text or "千兆" in search_text):
            return ["1G/千兆"]
        return []


def _matched_keywords(text: str, keywords: Iterable[str]) -> list[str]:
    normalized = text.lower()
    matched: list[str] = []
    for keyword in keywords:
        if keyword and keyword.lower() in normalized:
            matched.append(keyword)
    return _dedupe(matched)


def _target_keywords(demand: CustomerDemand) -> tuple[str, ...]:
    values: list[str] = []
    if demand.target_region:
        values.append(demand.target_region)
    values.extend(demand.raw_keywords)
    values.extend(demand.category_candidate_keywords)
    if demand.target_scope.value == "overseas":
        values.extend(["海外", "国外", "国际", "跨境"])
    return tuple(value for value in values if value)


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
