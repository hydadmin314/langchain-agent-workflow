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

    def test_office_dynamic_ip_category(self) -> None:
        result = self.workflow.analyze("客户上海办公室大概30个人办公上网，不需要固定IP，预算8000左右。")

        self.assertEqual(result.structured_data.primary_domain, "上网")
        self.assertEqual(result.structured_data.fixed_ip_required, False)
        self.assertIn("不需要固定IP", result.structured_data.negative_signals)
        self.assertEqual(result.structured_data.user_count, "30人")
        self.assertEqual(result.structured_data.user_count_value, 30)
        self.assertEqual(result.structured_data.bandwidth_est_mbps, 30)
        self.assertEqual(result.structured_data.budget_amount, 8000)
        self.assertEqual(result.category_decision.primary_category_id, "internet_office_dynamic_ip")

    def test_point_to_point_network_category(self) -> None:
        result = self.workflow.analyze("上海总部和杭州分公司要内网互通，两个点之间要稳定专线，约80人使用。")

        self.assertEqual(result.structured_data.bandwidth_est_mbps, 80)
        self.assertEqual(result.structured_data.scenario_type, ScenarioType.domestic_networking)
        self.assertEqual(result.structured_data.primary_domain, "组网")
        self.assertEqual(result.category_decision.primary_category_id, "network_point_to_point")

    def test_headquarter_access_single_subsidiary_is_point_to_point(self) -> None:
        result = self.workflow.analyze("上海总部访问新疆子公司业务慢，需要提高访问速率并保障稳定。")

        self.assertEqual(result.structured_data.primary_domain, "组网")
        self.assertEqual(result.category_decision.primary_category_id, "network_point_to_point")

    def test_fixed_ip_category(self) -> None:
        result = self.workflow.analyze("客户有企业官网和服务器要对外访问，需要固定公网IP和备案，预计20人办公。")

        self.assertTrue(result.structured_data.requires_fixed_ip)
        self.assertEqual(result.structured_data.primary_domain, "上网")
        self.assertEqual(result.category_decision.primary_category_id, "internet_fixed_ip")

    def test_street_store_category(self) -> None:
        result = self.workflow.analyze("沿街店铺5人以内，需要便宜宽带给收银和WiFi用，预算3000元以内。")

        self.assertEqual(result.structured_data.primary_domain, "上网")
        self.assertEqual(result.category_decision.primary_category_id, "internet_store_street")
        self.assertEqual(result.category_decision.recommendation_mode, "new_sale")

    def test_point_to_multipoint_category(self) -> None:
        result = self.workflow.analyze("总部要连接5个门店，做点对多组网，希望成本低一些。")

        self.assertEqual(result.structured_data.primary_domain, "组网")
        self.assertEqual(result.category_decision.primary_category_id, "network_point_to_multipoint")

    def test_smart_network_category(self) -> None:
        result = self.workflow.analyze("多个分支已有宽带，想通过SD-WAN设备快速做智能组网。")

        self.assertEqual(result.structured_data.primary_domain, "组网")
        self.assertEqual(result.category_decision.primary_category_id, "network_smart")

    def test_process_keywords_no_longer_route_to_product_category(self) -> None:
        result = self.workflow.analyze("外地公司在上海办理业务，需要哪些材料和担保要求？")

        self.assertEqual(result.category_decision.primary_category_id, "")
        self.assertEqual(result.category_decision.recommendation_mode, "clarify")

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
