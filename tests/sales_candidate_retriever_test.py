from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.sales_recommendation_agent.intent_parser.models import (
    CustomerDemand,
    DemandCategoryDecision,
    DemandCategoryMatch,
    RegionScope,
    ScenarioType,
)
from agent.sales_recommendation_agent.product_repository.models import (
    ConstraintCandidate,
    OptionalPackageCandidate,
    PackageCandidate,
    PriceRuleCandidate,
    ProductCandidate,
)
from agent.sales_recommendation_agent.recommender import (
    CandidateComparator,
    CandidateRetriever,
    CandidateRuleFilter,
    CandidateScorer,
    RecommendationExplainer,
)
from agent.sales_recommendation_agent.recommender.models import (
    CandidateFilterResult,
    CandidateRetrievalResult,
    FilterReason,
    FilteredCandidate,
    RecommendationExplanationResult,
    RetrievedCandidate,
)


class SalesCandidateRetrieverTest(unittest.TestCase):
    def test_overseas_access_retrieves_classified_products(self) -> None:
        demand = CustomerDemand(
            access_source="上海办公室",
            source_scope=RegionScope.domestic,
            target_region="美国 SaaS",
            target_scope=RegionScope.overseas,
            user_count=10,
            bandwidth_est_mbps=10,
            scenario_type=ScenarioType.overseas_access,
            raw_keywords=["美国", "海外 SaaS"],
            category_candidate_keywords=["美国", "海外 SaaS"],
        )
        category_decision = DemandCategoryDecision(
            primary_category_id="4",
            primary_category_name="海外访问与跨境加速",
            recommendation_mode="new_sale",
            category_matches=[
                DemandCategoryMatch(
                    category_id="4",
                    category_name="海外访问与跨境加速",
                    score=20,
                    matched_keywords=["美国"],
                )
            ],
        )
        products = [
            ProductCandidate(
                document_id="doc_jp",
                product_name="精品专线",
                product_family="互联网专线",
                category_path="电信政企/1.2精品专线",
                keywords=["精品专线", "互联网专线"],
            ),
            ProductCandidate(
                document_id="doc_voice",
                product_name="商云通",
                product_family="语音",
                category_path="电信号百/商云通",
                keywords=["商云通", "语音"],
            ),
        ]

        result = CandidateRetriever().retrieve(
            demand=demand,
            category_decision=category_decision,
            products=products,
        )

        self.assertEqual(result.demand_categories[0].category_id, "4")
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(result.candidates[0].product.document_id, "doc_jp")

    def test_fixed_ip_retrieves_ip_related_products(self) -> None:
        demand = CustomerDemand(
            target_region="企业官网",
            target_scope=RegionScope.domestic,
            user_count=20,
            bandwidth_est_mbps=20,
            requires_fixed_ip=True,
            scenario_type=ScenarioType.dedicated_ip_or_high_bandwidth,
            raw_keywords=["固定公网IP", "备案"],
            category_candidate_keywords=["固定公网IP", "备案"],
        )
        category_decision = DemandCategoryDecision(
            primary_category_id="2",
            primary_category_name="固定IP_高带宽_互联网专线",
            recommendation_mode="new_sale",
            category_matches=[
                DemandCategoryMatch(
                    category_id="2",
                    category_name="固定IP_高带宽_互联网专线",
                    score=30,
                    matched_keywords=["固定公网IP", "备案"],
                )
            ],
        )
        products = [
            ProductCandidate(
                document_id="doc_ipman",
                product_name="IPMAN",
                product_family="互联网专线",
                category_path="电信政企/IPMAN",
                keywords=["IPMAN", "公网IP", "IP地址"],
            )
        ]

        result = CandidateRetriever().retrieve(demand=demand, category_decision=category_decision, products=products)

        self.assertEqual(result.demand_categories[0].category_id, "2")
        self.assertEqual(result.matched_count, 1)
        self.assertIn("公网IP", result.candidates[0].matched_keywords)

    def test_no_category_returns_clarify_questions_without_candidates(self) -> None:
        demand = CustomerDemand(missing_fields=["user_count", "budget"])
        category_decision = DemandCategoryDecision(
            recommendation_mode="clarify",
            clarify_questions=["预计多少人、多少终端、多少号码或多少坐席使用？"],
        )

        result = CandidateRetriever().retrieve(demand=demand, category_decision=category_decision, products=[])

        self.assertEqual(result.matched_count, 0)
        self.assertTrue(result.clarify_questions)

    def test_rule_filter_removes_inactive_product(self) -> None:
        demand = CustomerDemand()
        category_decision = DemandCategoryDecision(primary_category_id="1", recommendation_mode="new_sale")
        retrieval_result = CandidateRetrievalResult(
            total_products=1,
            matched_count=1,
            candidates=[
                RetrievedCandidate(
                    product=ProductCandidate(document_id="doc_old", document_status="inactive"),
                    retrieval_score=20,
                )
            ],
        )

        result = CandidateRuleFilter().apply(
            demand=demand,
            category_decision=category_decision,
            retrieval_result=retrieval_result,
        )

        self.assertEqual(result.kept_count, 0)
        self.assertEqual(result.removed_count, 1)
        self.assertEqual(result.removed_candidates[0].filter_reasons[0].code, "inactive_product")

    def test_rule_filter_marks_fixed_ip_risk_without_removing(self) -> None:
        demand = CustomerDemand(requires_fixed_ip=True)
        category_decision = DemandCategoryDecision(primary_category_id="2", recommendation_mode="new_sale")
        retrieval_result = CandidateRetrievalResult(
            total_products=1,
            matched_count=1,
            candidates=[
                RetrievedCandidate(
                    product=ProductCandidate(
                        document_id="doc_normal_broadband",
                        product_name="普通企业宽带",
                        document_status="active",
                    ),
                    retrieval_score=20,
                )
            ],
        )

        result = CandidateRuleFilter().apply(
            demand=demand,
            category_decision=category_decision,
            retrieval_result=retrieval_result,
        )

        self.assertEqual(result.kept_count, 1)
        self.assertEqual(result.kept_candidates[0].filter_reasons[0].code, "missing_fixed_ip_evidence")
        self.assertIn("fixed_ip_needs_confirmation", result.kept_candidates[0].risk_tags)

    def test_rule_filter_removes_mobile_candidate_without_mobile_evidence(self) -> None:
        demand = CustomerDemand(category_candidate_keywords=["5G", "大流量"])
        category_decision = DemandCategoryDecision(primary_category_id="7", recommendation_mode="new_sale")
        retrieval_result = CandidateRetrievalResult(
            total_products=1,
            matched_count=1,
            candidates=[
                RetrievedCandidate(
                    product=ProductCandidate(
                        document_id="doc_broadband",
                        product_name="精品专线",
                        document_status="active",
                        keywords=["精品专线", "互联网专线"],
                    ),
                    retrieval_score=20,
                )
            ],
        )

        result = CandidateRuleFilter().apply(
            demand=demand,
            category_decision=category_decision,
            retrieval_result=retrieval_result,
        )

        self.assertEqual(result.kept_count, 0)
        self.assertEqual(result.removed_count, 1)
        self.assertEqual(
            result.removed_candidates[0].filter_reasons[0].code,
            "mobile_category_without_mobile_evidence",
        )

    def test_rule_filter_warns_when_budget_has_no_price_evidence(self) -> None:
        demand = CustomerDemand(budget=5000)
        category_decision = DemandCategoryDecision(primary_category_id="4", recommendation_mode="new_sale")
        retrieval_result = CandidateRetrievalResult(
            total_products=1,
            matched_count=1,
            candidates=[
                RetrievedCandidate(
                    product=ProductCandidate(
                        document_id="doc_overseas",
                        product_name="智能专线",
                        document_status="active",
                        keywords=["智能专线"],
                    ),
                    retrieval_score=20,
                )
            ],
        )

        result = CandidateRuleFilter().apply(
            demand=demand,
            category_decision=category_decision,
            retrieval_result=retrieval_result,
        )

        reason_codes = [reason.code for reason in result.kept_candidates[0].filter_reasons]
        self.assertIn("budget_without_price_evidence", reason_codes)
        self.assertIn("price_missing", result.kept_candidates[0].risk_tags)

    def test_rule_filter_removes_normal_product_for_service_process(self) -> None:
        demand = CustomerDemand()
        category_decision = DemandCategoryDecision(primary_category_id="13", recommendation_mode="service_process")
        retrieval_result = CandidateRetrievalResult(
            total_products=1,
            matched_count=1,
            candidates=[
                RetrievedCandidate(
                    product=ProductCandidate(
                        document_id="doc_package_form",
                        product_name="精品专线",
                        document_status="active",
                        filename="精品专线套餐申请登记表.docx",
                        category_path="电信政企/精品专线",
                    ),
                    retrieval_score=20,
                )
            ],
        )

        result = CandidateRuleFilter().apply(
            demand=demand,
            category_decision=category_decision,
            retrieval_result=retrieval_result,
        )

        self.assertEqual(result.kept_count, 0)
        self.assertEqual(result.removed_count, 1)
        self.assertEqual(result.removed_candidates[0].filter_reasons[0].code, "service_process_mismatch")

    def test_rule_filter_keeps_service_process_candidate_when_retrieval_matched_process_keyword(self) -> None:
        demand = CustomerDemand()
        category_decision = DemandCategoryDecision(primary_category_id="13", recommendation_mode="service_process")
        retrieval_result = CandidateRetrievalResult(
            total_products=1,
            matched_count=1,
            candidates=[
                RetrievedCandidate(
                    product=ProductCandidate(
                        document_id="doc_change_process",
                        product_name="套餐申请登记表",
                        document_status="active",
                        filename="套餐申请登记表.docx",
                        category_path="电信政企/精品专线",
                    ),
                    retrieval_score=20,
                    matched_keywords=["拆机", "变更"],
                )
            ],
        )

        result = CandidateRuleFilter().apply(
            demand=demand,
            category_decision=category_decision,
            retrieval_result=retrieval_result,
        )

        self.assertEqual(result.kept_count, 1)
        self.assertEqual(result.removed_count, 0)

    def test_scorer_ranks_stronger_candidate_before_risky_candidate(self) -> None:
        demand = CustomerDemand(requires_fixed_ip=True, budget=5000)
        category_decision = DemandCategoryDecision(primary_category_id="2", recommendation_mode="new_sale")
        good = FilteredCandidate(
            decision="keep",
            candidate=RetrievedCandidate(
                retrieval_score=20,
                product=ProductCandidate(
                    document_id="doc_ipman",
                    product_name="IPMAN",
                    document_status="active",
                    keywords=["IPMAN", "公网IP", "IP地址"],
                    packages=[PackageCandidate(package_name="IPMAN 100M", price=3000, source_evidence="100M 月费3000元")],
                ),
            ),
        )
        risky = FilteredCandidate(
            decision="keep",
            filter_reasons=[
                FilterReason(
                    code="missing_fixed_ip_evidence",
                    message="客户要求固定 IP，但候选缺少证据。",
                )
            ],
            candidate=RetrievedCandidate(
                retrieval_score=22,
                product=ProductCandidate(
                    document_id="doc_broadband",
                    product_name="普通企业宽带",
                    document_status="active",
                    keywords=["企业宽带"],
                ),
            ),
        )
        filter_result = CandidateFilterResult(
            primary_category_id="2",
            recommendation_mode="new_sale",
            total_candidates=2,
            kept_count=2,
            kept_candidates=[risky, good],
        )

        result = CandidateScorer().score(
            demand=demand,
            category_decision=category_decision,
            filter_result=filter_result,
        )

        self.assertEqual(result.scored_candidates[0].filtered_candidate.candidate.product.document_id, "doc_ipman")
        self.assertGreater(
            result.scored_candidates[0].final_score,
            result.scored_candidates[1].final_score,
        )

    def test_scorer_applies_mobile_bundle_penalty(self) -> None:
        demand = CustomerDemand(category_candidate_keywords=["5G", "大流量"])
        category_decision = DemandCategoryDecision(primary_category_id="7", recommendation_mode="new_sale")
        mobile = FilteredCandidate(
            decision="keep",
            candidate=RetrievedCandidate(
                retrieval_score=20,
                product=ProductCandidate(
                    document_id="doc_5g",
                    product_name="5G畅享套餐",
                    document_status="active",
                    keywords=["5G", "流量", "手机卡"],
                    packages=[PackageCandidate(package_name="5G 99元套餐", price=99, source_evidence="99元 国内流量20GB")],
                ),
            ),
        )
        bundle = FilteredCandidate(
            decision="keep",
            filter_reasons=[
                FilterReason(
                    code="mobile_candidate_may_be_broadband_bundle",
                    message="候选主体可能是宽带/专线套餐。",
                )
            ],
            candidate=RetrievedCandidate(
                retrieval_score=20,
                product=ProductCandidate(
                    document_id="doc_bundle",
                    product_name="精品专线",
                    document_status="active",
                    keywords=["精品专线", "5G畅享套餐", "移动业务"],
                ),
            ),
        )
        filter_result = CandidateFilterResult(
            primary_category_id="7",
            recommendation_mode="new_sale",
            total_candidates=2,
            kept_count=2,
            kept_candidates=[bundle, mobile],
        )

        result = CandidateScorer().score(
            demand=demand,
            category_decision=category_decision,
            filter_result=filter_result,
        )

        self.assertEqual(result.scored_candidates[0].filtered_candidate.candidate.product.document_id, "doc_5g")
        penalty_codes = [item.code for item in result.scored_candidates[1].risk_penalties]
        self.assertIn("risk_penalty_mobile_candidate_may_be_broadband_bundle", penalty_codes)

    def test_scorer_prefers_identity_match_for_explicit_demand_keyword(self) -> None:
        demand = CustomerDemand(category_candidate_keywords=["云中继"])
        category_decision = DemandCategoryDecision(
            primary_category_id="6",
            recommendation_mode="new_sale",
            category_matches=[
                DemandCategoryMatch(
                    category_id="6",
                    category_name="固定电话_语音中继_呼叫业务",
                    score=20,
                    matched_keywords=["云中继"],
                )
            ],
        )
        direct_product = FilteredCandidate(
            decision="keep",
            candidate=RetrievedCandidate(
                retrieval_score=25,
                product=ProductCandidate(
                    document_id="doc_cloud_trunk",
                    product_name="云中继业务受理表",
                    document_status="active",
                    category_path="电信号百/云中继",
                    keywords=["云中继"],
                ),
            ),
        )
        bundle_product = FilteredCandidate(
            decision="keep",
            candidate=RetrievedCandidate(
                retrieval_score=35,
                product=ProductCandidate(
                    document_id="doc_bundle_voice",
                    product_name="精品专线",
                    document_status="active",
                    keywords=["精品专线", "商云通", "语音"],
                    packages=[PackageCandidate(package_name="精品专线套餐", source_evidence="基础套餐")],
                ),
            ),
        )
        filter_result = CandidateFilterResult(
            primary_category_id="6",
            recommendation_mode="new_sale",
            total_candidates=2,
            kept_count=2,
            kept_candidates=[bundle_product, direct_product],
        )

        result = CandidateScorer().score(
            demand=demand,
            category_decision=category_decision,
            filter_result=filter_result,
        )

        self.assertEqual(
            result.scored_candidates[0].filtered_candidate.candidate.product.document_id,
            "doc_cloud_trunk",
        )

    def test_comparator_builds_structured_product_summary(self) -> None:
        demand = CustomerDemand(budget=5000)
        category_decision = DemandCategoryDecision(primary_category_id="4", recommendation_mode="new_sale")
        product = ProductCandidate(
            document_id="doc_smart_line",
            product_name="智能专线",
            product_family="互联网专线",
            carrier="中国电信",
            region="上海",
            document_type="申请表",
            document_status="active",
            category_path="电信政企/智能专线",
            packages=[
                PackageCandidate(
                    package_name="智能专线100M",
                    speed="100M",
                    price=688,
                    billing_period="月",
                    contract_period="1年",
                    source_evidence="智能专线100M 688元/月",
                )
            ],
            fee_rules=[
                PriceRuleCandidate(
                    rule_type="installation_fee",
                    name="安装调测费",
                    amount=300,
                    source_evidence="安装调测费300元",
                )
            ],
            optional_packages=[
                OptionalPackageCandidate(
                    name="上行升速包",
                    fee_summary="100元/月",
                    price_items=[{"price": 100, "billing_period": "月"}],
                )
            ],
            constraints=[
                ConstraintCandidate(
                    name="欠费限制",
                    description="欠费客户不得办理优惠",
                    source_evidence="欠费客户不得办理优惠",
                )
            ],
        )
        scored = CandidateScorer().score(
            demand=demand,
            category_decision=category_decision,
            filter_result=CandidateFilterResult(
                primary_category_id="4",
                primary_category_name="海外访问与跨境加速",
                recommendation_mode="new_sale",
                total_candidates=1,
                kept_count=1,
                kept_candidates=[
                    FilteredCandidate(
                        decision="keep",
                        candidate=RetrievedCandidate(product=product, retrieval_score=30),
                    )
                ],
            ),
        )

        result = CandidateComparator().compare(
            demand=demand,
            category_decision=category_decision,
            score_result=scored,
            top_n=1,
        )

        self.assertEqual(result.compared_count, 1)
        compared = result.products[0]
        self.assertEqual(compared.product_name, "智能专线")
        self.assertEqual(compared.package_summary.package_count, 1)
        self.assertEqual(compared.package_summary.price_range, "688")
        self.assertEqual(compared.fee_summary.fee_rule_count, 1)
        self.assertTrue(compared.fee_summary.has_installation_fee)
        self.assertEqual(compared.optional_package_summary.optional_package_count, 1)
        self.assertIn("application_materials 尚未进入 ProductCandidate", compared.missing_info[0])
        self.assertTrue(result.comparison_dimensions)

    def test_recommendation_explainer_parses_llm_json(self) -> None:
        demand = CustomerDemand(
            access_source="上海办公室",
            target_region="美国 SaaS",
            user_count=10,
            budget=5000,
            scenario_type=ScenarioType.overseas_access,
        )
        category_decision = DemandCategoryDecision(
            primary_category_id="4",
            primary_category_name="海外访问与跨境加速",
            recommendation_mode="new_sale",
        )
        product = ProductCandidate(
            document_id="doc_smart_line",
            product_name="智能专线",
            document_status="active",
            packages=[PackageCandidate(package_name="智能专线100M", speed="100M", price=3000)],
        )
        scored = CandidateScorer().score(
            demand=demand,
            category_decision=category_decision,
            filter_result=CandidateFilterResult(
                primary_category_id="4",
                primary_category_name="海外访问与跨境加速",
                recommendation_mode="new_sale",
                total_candidates=1,
                kept_count=1,
                kept_candidates=[
                    FilteredCandidate(
                        decision="keep",
                        candidate=RetrievedCandidate(product=product, retrieval_score=30),
                    )
                ],
            ),
        )
        comparison = CandidateComparator().compare(
            demand=demand,
            category_decision=category_decision,
            score_result=scored,
            top_n=1,
        )
        explainer = RecommendationExplainer(
            client=FakeOpenAIClient(
                {
                    "summary": "智能专线能够满足当前访问美国 SaaS 的需求。",
                    "recommended_product": {
                        "document_id": "doc_smart_line",
                        "product_name": "智能专线",
                        "reason": "该候选排序第一，并有套餐价格信息，可以满足客户需求。",
                    },
                    "alternative_products": [],
                    "comparison_summary": ["智能专线适合当前海外 SaaS 访问方向。"],
                    "risk_reminders": [],
                    "clarifying_questions": ["确认客户访问的 SaaS 名称和并发人数。"],
                    "sales_talk": "可以先按智能专线方向沟通，再确认访问目标和预算。",
                    "evidence_notes": ["依据 Comparator Top1。"],
                }
            )
        )

        result = explainer.explain(
            query="上海办公室10人访问美国SaaS很慢，预算5000左右",
            demand=demand,
            category_decision=category_decision,
            comparison_result=comparison,
        )

        self.assertIsInstance(result, RecommendationExplanationResult)
        self.assertEqual(result.recommended_product.document_id, "doc_smart_line")
        self.assertIn("智能专线", result.summary)
        self.assertNotIn("能够满足", result.summary)
        self.assertNotIn("可以满足", result.recommended_product.reason)
        self.assertTrue(any("不能直接承诺访问效果" in item for item in result.risk_reminders))


class FakeMessage:
    def __init__(self, content: str):
        self.content = content


class FakeChoice:
    def __init__(self, content: str):
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content: str):
        self.choices = [FakeChoice(content)]


class FakeCompletions:
    def __init__(self, payload: dict):
        self.payload = payload
        self.last_request = None

    def create(self, **kwargs):
        self.last_request = kwargs
        return FakeResponse(json_dumps(self.payload))


class FakeChat:
    def __init__(self, payload: dict):
        self.completions = FakeCompletions(payload)


class FakeOpenAIClient:
    def __init__(self, payload: dict):
        self.chat = FakeChat(payload)


def json_dumps(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
