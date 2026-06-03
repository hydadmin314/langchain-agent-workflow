from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.sales_recommendation_agent.intent_parser import SalesRecommendationSettings
from agent.sales_recommendation_agent.intent_parser.models import CustomerDemand, RegionScope, RouteDecision, ScenarioType
from agent.sales_recommendation_agent.product_repository.models import ProductCandidate
from agent.sales_recommendation_agent.recommender import CandidateRetriever


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
        )
        decision = RouteDecision(
            route="flow_overseas_access",
            action="进入海外访问类产品推荐分支",
            priority=80,
            reason="目标为海外。",
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
            decision=decision,
            products=products,
            raw_text="客户上海办公室10人访问美国 SaaS 很慢",
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
        )
        decision = RouteDecision(
            route="flow_dedicated_ip_bandwidth",
            action="进入固定 IP / 高带宽 / 专线类产品推荐分支",
            priority=90,
            reason="需要公网 IP。",
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

        result = CandidateRetriever().retrieve(demand=demand, decision=decision, products=products)

        self.assertEqual(result.demand_categories[0].category_id, "2")
        self.assertEqual(result.matched_count, 1)
        self.assertIn("公网IP", result.candidates[0].matched_keywords)

    def test_clarify_route_returns_questions_without_candidates(self) -> None:
        demand = CustomerDemand(missing_fields=["user_count", "budget"])
        decision = RouteDecision(
            route="flow_clarify_requirements",
            action="进入需求澄清分支",
            priority=30,
            reason="缺少字段。",
        )

        result = CandidateRetriever().retrieve(demand=demand, decision=decision, products=[])

        self.assertEqual(result.matched_count, 0)
        self.assertTrue(result.clarify_questions)


if __name__ == "__main__":
    unittest.main()
