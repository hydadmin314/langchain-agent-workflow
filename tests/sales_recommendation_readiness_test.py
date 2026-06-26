from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.sales_recommendation_agent.intent_parser import CustomerDemand, DemandCategoryDecision
from agent.sales_recommendation_agent.recommender import (
    ClarificationQuestionGenerator,
    RecommendationReadinessEvaluator,
)


class SalesRecommendationReadinessTest(unittest.TestCase):
    """验证推荐前最低条件判断。

    这些测试不调用真实 LLM，只验证程序规则是否能稳定判断：
    - 信息足够时进入推荐
    - 信息不足时先追问
    - 可假设字段不会直接写回 customer_need
    """

    def test_fixed_ip_need_can_recommend_with_assumptions(self) -> None:
        demand = CustomerDemand(
            primary_domain="上网",
            primary_category="上网-固定IP上网",
            primary_goal="服务器对外访问",
            fixed_ip_required=True,
            budget="5000左右",
            region="上海",
        )
        category = DemandCategoryDecision(
            primary_domain="上网",
            primary_category_id="internet_fixed_ip",
            primary_category_name="上网-固定IP上网",
            recommendation_mode="new_sale",
        )

        result = RecommendationReadinessEvaluator().evaluate(
            demand=demand,
            category_decision=category,
        )

        self.assertEqual(result.decision, "ready_with_assumptions")
        self.assertFalse(result.missing_conditions)
        self.assertIn("IPMAN", result.product_lock_hints)
        self.assertEqual(demand.fixed_ip_required, True)

    def test_point_to_point_network_can_recommend_with_bandwidth_assumption(self) -> None:
        demand = CustomerDemand(
            primary_domain="组网",
            primary_category="组网-点对点",
            primary_goal="上海总部访问新疆子公司业务",
            usage_scene="总部与一个分支互联",
            site_count="总部+1个分支",
            region="上海+新疆",
        )
        category = DemandCategoryDecision(
            primary_domain="组网",
            primary_category_id="network_point_to_point",
            primary_category_name="组网-点对点",
            recommendation_mode="new_sale",
        )

        result = RecommendationReadinessEvaluator().evaluate(
            demand=demand,
            category_decision=category,
        )

        self.assertEqual(result.decision, "ready_with_assumptions")
        self.assertFalse(result.missing_conditions)
        self.assertIn("IPRAN", result.product_lock_hints)

    def test_unclear_need_should_ask_clarification(self) -> None:
        demand = CustomerDemand(primary_category="", primary_goal="", usage_scene="")
        category = DemandCategoryDecision(primary_category_id="", primary_category_name="")

        result = RecommendationReadinessEvaluator().evaluate(
            demand=demand,
            category_decision=category,
        )

        self.assertEqual(result.decision, "ask_clarification")
        self.assertIn("primary_category", result.missing_conditions)
        self.assertTrue(result.clarification_plan.intents)

    def test_fallback_clarification_is_available_without_llm(self) -> None:
        demand = CustomerDemand(primary_category="", primary_goal="", usage_scene="")
        category = DemandCategoryDecision(primary_category_id="", primary_category_name="")
        readiness = RecommendationReadinessEvaluator().evaluate(
            demand=demand,
            category_decision=category,
        )

        result = ClarificationQuestionGenerator().generate(
            query="客户想办个网络",
            demand=demand,
            category_decision=category,
            readiness_result=readiness,
        )

        self.assertTrue(result.message)
        self.assertTrue(result.questions)


if __name__ == "__main__":
    unittest.main()
