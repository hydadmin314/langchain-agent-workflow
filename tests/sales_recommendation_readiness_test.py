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

    def test_overseas_need_can_recommend_with_assumptions(self) -> None:
        demand = CustomerDemand(
            primary_category="海外访问与跨境加速",
            primary_goal="海外访问",
            usage_scene="上海办公室",
            user_count="10人",
            overseas_access=True,
            overseas_target="美国SaaS",
            budget="5000左右",
            region="上海",
        )
        category = DemandCategoryDecision(
            primary_category_id="4",
            primary_category_name="海外访问与跨境加速",
            recommendation_mode="new_sale",
        )

        result = RecommendationReadinessEvaluator().evaluate(
            demand=demand,
            category_decision=category,
        )

        self.assertEqual(result.decision, "ready_with_assumptions")
        self.assertFalse(result.missing_conditions)
        self.assertIn("精品专线", result.product_lock_hints)
        self.assertIsNone(demand.fixed_ip_required, "假设不能写回真实需求表")

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

    def test_service_process_ready_when_action_and_existing_product_are_clear(self) -> None:
        demand = CustomerDemand(
            primary_category="办理变更_续约_拆机_撤单",
            primary_goal="已有宽带",
            business_action="拆机",
        )
        category = DemandCategoryDecision(
            primary_category_id="13",
            primary_category_name="办理变更_续约_拆机_撤单",
            recommendation_mode="service_process",
        )

        result = RecommendationReadinessEvaluator().evaluate(
            demand=demand,
            category_decision=category,
        )

        self.assertEqual(result.decision, "ready")
        self.assertIn("办理流程模式", result.product_lock_hints)

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
