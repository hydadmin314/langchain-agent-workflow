from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# 支持直接运行本文件：
#   python tests\sales_recommendation_workflow_test.py
# 直接按文件路径运行时，Python 默认不会把项目根目录加入 import 搜索路径，
# 所以这里手动把项目根目录放到 sys.path 最前面。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.sales_recommendation_agent import SalesRecommendationWorkflow


DEFAULT_SESSION_ID = "manual-sales-demo"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="销售推荐 Agent 多轮对话手动测试脚本。输入 q/quit/exit 结束。"
    )
    parser.add_argument(
        "--session-id",
        default=DEFAULT_SESSION_ID,
        help="会话 ID；同一个 session-id 下会保留多轮上下文。",
    )
    parser.add_argument(
        "--published-root",
        default="data/product_doc_agent/published",
        help="已审核发布的产品 JSON 目录。",
    )
    parser.add_argument("--top-k", type=int, default=3, help="候选产品召回和对比数量。")
    parser.add_argument(
        "--skip-explainer",
        action="store_true",
        help="跳过真实 LLM 推荐说明，只测试需求解析、记忆、召回、过滤、打分和对比。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="每轮输出完整 JSON，方便检查字段。",
    )
    args = parser.parse_args()

    workflow = SalesRecommendationWorkflow(
        published_root=args.published_root,
        top_k=args.top_k,
        skip_explainer=args.skip_explainer,
    )

    print("销售推荐 Agent 多轮对话测试")
    print(f"session_id: {args.session_id}")
    print("输入 q / quit / exit 结束；输入 reset 清空当前会话后继续。")
    print("示例：客户想办个网络")
    print()

    while True:
        user_text = input("你：").strip()
        if not user_text:
            continue
        if user_text.lower() in {"q", "quit", "exit"}:
            print("已结束。")
            return
        if user_text.lower() == "reset":
            workflow.memory_store.clear(args.session_id)
            print("已清空当前会话上下文。")
            continue

        try:
            result = workflow.run(session_id=args.session_id, user_text=user_text)
        except Exception as exc:
            print(f"运行失败：{exc}")
            continue

        if args.json:
            print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
            continue

        print()
        print_result_summary(result)
        print()


def print_result_summary(result) -> None:
    """打印适合人工手动测试阅读的摘要。"""

    need = result.customer_need
    print(f"状态：{result.status}")
    print(f"回复：{result.assistant_message}")
    print()
    print("当前需求表：")
    print(f"- 主分类：{need.primary_category}")
    print(f"- 辅助分类：{need.secondary_categories}")
    print(f"- 目标：{need.primary_goal}")
    print(f"- 场景：{need.usage_scene}")
    print(f"- 办理动作：{need.business_action}")
    print(f"- 地点数量：{need.site_count}")
    print(f"- 使用规模：{need.user_count}")
    print(f"- 带宽需求：{need.bandwidth_need}")
    print(f"- 固定IP：{need.fixed_ip_required}")
    print(f"- 固定IP数量：{need.fixed_ip_count}")
    print(f"- 语音需求：{need.voice_required}")
    print(f"- 并发/通道：{need.concurrent_calls}")
    print(f"- 海外访问：{need.overseas_access}")
    print(f"- 海外目标：{need.overseas_target}")
    print(f"- 云/IDC：{need.server_or_idc_required}")
    print(f"- 云办公：{need.cloud_office_required}")
    print(f"- 安全需求：{need.security_required}")
    print(f"- 行业场景：{need.industry_scene}")
    print(f"- 营销触达：{need.marketing_touch_required}")
    print(f"- 预算：{need.budget}")
    print(f"- 稳定性：{need.reliability_level}")
    print(f"- 运营商偏好：{need.carrier_preference}")
    print(f"- 区域：{need.region}")
    print(f"- 客户类型：{need.customer_type}")

    if result.readiness:
        print()
        print("推荐准备度：")
        print(f"- decision：{result.readiness.decision}")
        print(f"- reason：{result.readiness.reason}")
        if result.readiness.missing_conditions:
            print(f"- 缺失条件：{result.readiness.missing_conditions}")
        if result.readiness.assumptions:
            print(f"- 当前假设：{result.readiness.assumptions}")

    if result.clarification and result.clarification.questions:
        print()
        print("追问字段：")
        print(f"- fields：{result.clarification.fields}")
        for question in result.clarification.questions:
            print(f"- {question}")

    if result.retrieval:
        print()
        print("候选召回：")
        print(f"- candidate_count：{len(result.retrieval.candidates)}")
        for index, candidate in enumerate(result.retrieval.candidates[:3], start=1):
            product = candidate.product
            name = product.product_name or product.title or product.filename or product.document_id
            print(f"  [{index}] {name} score={candidate.retrieval_score}")

    if result.comparison:
        print()
        print("Top 对比：")
        print(f"- compared_count：{result.comparison.compared_count}")
        for item in result.comparison.products[:3]:
            print(f"  [{item.rank}] {item.product_name} final_score={item.final_score}")

    if result.explanation:
        print()
        print("LLM 推荐说明：")
        print(f"- summary：{result.explanation.summary}")
        if result.explanation.recommended_product.product_name:
            print(
                "- recommended："
                f"{result.explanation.recommended_product.product_name} "
                f"({result.explanation.recommended_product.document_id})"
            )
        if result.explanation.risk_reminders:
            print(f"- risks：{result.explanation.risk_reminders[:3]}")

    if result.errors:
        print()
        print("流程错误：")
        for error in result.errors:
            print(f"- {error}")


if __name__ == "__main__":
    main()
