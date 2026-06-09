import json
import unittest
from pathlib import Path

from web.sales_demo.server import (
    SalesDemoService,
    parse_single_call_output,
    repair_common_json_errors,
    validate_customer_need_patch,
)


class SalesDemoServerJsonTest(unittest.TestCase):
    def test_repairs_missing_comma_between_properties(self) -> None:
        content = (
            '{"customer_need_patch":{"primary_goal":"提升访问速度"}'
            '\n"action":"clarify","message":"请补充带宽",'
            '"questions":["期望带宽是多少？"],"fields":["bandwidth_need"],'
            '"explanation":{}}'
        )

        output = parse_single_call_output(content)

        self.assertEqual(output.action, "clarify")
        self.assertEqual(
            output.customer_need_patch["primary_goal"],
            "提升访问速度",
        )

    def test_removes_trailing_comma(self) -> None:
        content = '{"customer_need_patch":{},"action":"no_candidate",}'

        repaired = repair_common_json_errors(content)

        self.assertEqual(json.loads(repaired)["action"], "no_candidate")

    def test_normalizes_model_patch_types(self) -> None:
        demand = validate_customer_need_patch(
            {
                "site_count": 4,
                "user_count": 80,
                "fixed_ip_required": "不需要",
                "raw_keywords": "多点互联",
                "confidence": "0.92",
            }
        )

        self.assertEqual(demand.site_count, "4")
        self.assertEqual(demand.user_count, "80")
        self.assertFalse(demand.fixed_ip_required)
        self.assertEqual(demand.raw_keywords, ["多点互联"])
        self.assertEqual(demand.confidence, 0.92)


class SalesDemoTurnPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        project_root = Path(__file__).resolve().parents[1]
        cls.service = SalesDemoService(
            published_root=project_root / "data" / "published",
            use_llm=False,
        )

    def test_complete_need_can_recommend_before_six_turns(self) -> None:
        result, _, _, policy = self.service.run_turn(
            session_id="complete-need",
            user_text="上海总部和新疆分公司需要稳定互联，共2个站点，计划使用100M专线。",
        )

        self.assertTrue(policy.terminal)
        self.assertIn(result.status, {"recommended", "no_candidate"})
        self.assertLessEqual(result.state.turn_count, 6)

    def test_vague_need_must_finish_by_sixth_turn(self) -> None:
        session_id = "vague-six-turns"
        replies = ["客户想了解网络方案", "不清楚", "暂时不知道", "没有", "不确定", "先按现有信息"]
        final_result = None
        final_policy = None

        for reply in replies:
            final_result, _, _, final_policy = self.service.run_turn(
                session_id=session_id,
                user_text=reply,
            )
            if final_policy.terminal:
                break

        self.assertIsNotNone(final_result)
        self.assertIsNotNone(final_policy)
        self.assertTrue(final_policy.terminal)
        self.assertIn(final_result.status, {"recommended", "no_candidate"})
        self.assertLessEqual(final_result.state.turn_count, 6)


if __name__ == "__main__":
    unittest.main()
