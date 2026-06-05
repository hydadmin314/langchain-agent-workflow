from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.sales_recommendation_agent.intent_parser import (
    HeuristicDemandParser,
    RegionScope,
    SalesRecommendationSettings,
    SalesRequirementWorkflow,
    ScenarioType,
    parse_customer_demand_json,
)


class SalesRecommendationRequirementTest(unittest.TestCase):
    def setUp(self) -> None:
        # 测试里不使用真实 LLM，保证本地和 CI 都能稳定运行。
        self.settings = SalesRecommendationSettings(
            llm_api_key=None,
            llm_model="test",
            llm_base_url=None,
            llm_temperature=0,
            llm_max_tokens=500,
            llm_timeout_seconds=60,
            llm_enable_thinking=False,
            llm_enable_search=False,
            per_user_bandwidth_mbps=1,
            high_bandwidth_threshold_mbps=100,
        )
        self.workflow = SalesRequirementWorkflow(
            settings=self.settings,
            parser=HeuristicDemandParser(self.settings),
        )

    def test_overseas_access_demand(self) -> None:
        result = self.workflow.analyze(
            "客户上海办公室大概10个人，想先试一个月访问美国 SaaS，预算5000左右。"
        )

        self.assertEqual(result.structured_data.user_count, "10人")
        self.assertEqual(result.structured_data.user_count_value, 10)
        self.assertEqual(result.structured_data.bandwidth_est_mbps, 10)
        self.assertEqual(result.structured_data.target_scope, RegionScope.overseas)
        self.assertEqual(result.structured_data.budget_amount, 5000)
        self.assertEqual(result.category_decision.primary_category_id, "4")

    def test_domestic_networking_category(self) -> None:
        result = self.workflow.analyze("上海总部和杭州分公司要内网互通，两个点之间要稳定专线，约80人使用。")

        self.assertEqual(result.structured_data.bandwidth_est_mbps, 80)
        self.assertEqual(result.structured_data.scenario_type, ScenarioType.domestic_networking)
        self.assertEqual(result.category_decision.primary_category_id, "3")

    def test_fixed_ip_category(self) -> None:
        result = self.workflow.analyze("客户有企业官网和服务器要对外访问，需要固定公网IP和备案，预计20人办公。")

        self.assertTrue(result.structured_data.requires_fixed_ip)
        self.assertEqual(result.category_decision.primary_category_id, "2")

    def test_voice_category_does_not_fall_into_clarify_only(self) -> None:
        result = self.workflow.analyze("客户要企业固定电话和呼叫中心坐席，想了解30B+D和云中继方案。")

        self.assertEqual(result.category_decision.primary_category_id, "6")
        self.assertEqual(result.category_decision.recommendation_mode, "new_sale")

    def test_mobile_5g_is_not_bandwidth(self) -> None:
        result = self.workflow.analyze("公司要给员工办手机卡和5G大流量套餐，最好宽带和手机一起办。")

        self.assertEqual(result.structured_data.bandwidth_est_mbps, 0)
        self.assertEqual(result.category_decision.primary_category_id, "7")

    def test_process_keywords_route_to_service_process(self) -> None:
        result = self.workflow.analyze("客户已有宽带，现在想拆机，需要知道办理流程和材料。")

        self.assertEqual(result.category_decision.primary_category_id, "13")
        self.assertEqual(result.category_decision.recommendation_mode, "service_process")

    def test_material_and_guarantee_route_to_service_process(self) -> None:
        result = self.workflow.analyze("外地公司在上海办理业务，需要哪些材料和担保要求？")

        self.assertEqual(result.category_decision.primary_category_id, "13")
        self.assertEqual(result.category_decision.recommendation_mode, "service_process")

    def test_parse_json_from_markdown_fence(self) -> None:
        demand = parse_customer_demand_json(
            """```json
            {
              "access_source": "上海办公室",
              "source_scope": "domestic",
              "target_region": "美国 SaaS",
              "target_scope": "overseas",
              "user_count": 10,
              "bandwidth_est_mbps": 10,
              "duration": "试用1个月",
              "budget": 5000,
              "requires_fixed_ip": false,
              "scenario_type": "overseas_access",
              "raw_keywords": ["上海", "10人"],
              "category_candidate_keywords": ["美国", "海外 SaaS"],
              "confidence": 0.9,
              "missing_fields": []
            }
            ```"""
        )

        self.assertEqual(demand.user_count, "10人")
        self.assertEqual(demand.user_count_value, 10)
        self.assertEqual(demand.target_scope, RegionScope.overseas)
        self.assertIn("美国 SaaS", demand.category_candidate_keywords)


if __name__ == "__main__":
    unittest.main()
