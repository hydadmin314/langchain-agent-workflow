from __future__ import annotations

import unittest

from agent.sales_recommendation_agent.intent_parser.models import (
    CustomerDemand,
    DemandCategoryDecision,
    DemandCategoryMatch,
    RequirementAnalysisResult,
)
from agent.sales_recommendation_agent.workflow import SalesRecommendationWorkflow
from agent.sales_recommendation_agent.product_repository.models import ProductLoadResult
from memory.sales_recommendation_agent_memory import InMemoryConversationStore


class FakeRequirementWorkflow:
    """测试用需求解析器，避免单元测试依赖真实大模型。"""

    def __init__(self) -> None:
        self.classifier = FakeClassifier()

    def analyze(self, text: str) -> RequirementAnalysisResult:
        if "上海办公室" in text:
            demand = CustomerDemand(
                primary_goal="办公上网",
                usage_scene="上海办公室",
                region="上海",
                user_count="20人",
                confidence=0.9,
            )
        elif "固定IP" in text or "固定 IP" in text:
            demand = CustomerDemand(
                fixed_ip_required=False,
                budget="3000以内",
                confidence=0.9,
            )
        else:
            demand = CustomerDemand(primary_goal="办网络", confidence=0.6)
        return RequirementAnalysisResult(
            structured_data=demand,
            category_decision=self.classifier.classify(demand=demand, raw_text=text),
        )


class FakeClassifier:
    """测试用 13 类分类器。"""

    def classify(self, *, demand: CustomerDemand, raw_text: str) -> DemandCategoryDecision:
        return DemandCategoryDecision(
            primary_category_id="1",
            primary_category_name="企业上网与办公宽带",
            category_matches=[
                DemandCategoryMatch(
                    category_id="1",
                    category_name="企业上网与办公宽带",
                    score=20,
                    matched_keywords=["办公", "上网"],
                )
            ],
            recommendation_mode="recommend",
            confidence=0.8,
            reason="测试分类结果",
        )


class EmptyProductRepository:
    """测试用产品仓库，返回空产品库以验证流程不会报错。"""

    def load_result(self) -> ProductLoadResult:
        return ProductLoadResult(products=[], errors=[])


class SalesRecommendationWorkflowTest(unittest.TestCase):
    def test_multi_turn_memory_merges_customer_need(self) -> None:
        store = InMemoryConversationStore()
        workflow = SalesRecommendationWorkflow(
            requirement_workflow=FakeRequirementWorkflow(),
            memory_store=store,
            product_repository=EmptyProductRepository(),
            skip_explainer=True,
        )

        first = workflow.run(session_id="demo-session", user_text="客户想办个网络")
        self.assertEqual(first.status, "ask_clarification")
        self.assertEqual(first.customer_need.primary_goal, "办网络")

        second = workflow.run(session_id="demo-session", user_text="上海办公室，20个人办公用")
        self.assertIn(second.status, {"recommended", "no_candidate", "ask_clarification"})
        self.assertEqual(second.customer_need.usage_scene, "上海办公室")
        self.assertEqual(second.customer_need.user_count, "20人")
        self.assertEqual(store.get("demo-session").turn_count, 2)

        third = workflow.run(session_id="demo-session", user_text="不需要固定IP，预算3000以内")
        self.assertFalse(third.customer_need.fixed_ip_required)
        self.assertEqual(third.customer_need.budget, "3000以内")
        self.assertEqual(store.get("demo-session").turn_count, 3)


if __name__ == "__main__":
    unittest.main()
