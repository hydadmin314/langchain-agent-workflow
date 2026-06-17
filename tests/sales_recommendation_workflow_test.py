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
            print(f"  [{index}] {format_product_label(name=name, category_path=product.category_path)} score={candidate.retrieval_score}")

    if result.comparison:
        print()
        print("Top 对比：")
        print(f"- compared_count：{result.comparison.compared_count}")
        if result.comparison.comparison_dimensions:
            print("- 维度摘要：")
            for dimension in result.comparison.comparison_dimensions:
                print(f"  - {dimension.dimension}：{dimension.summary}")
        for item in result.comparison.products[:3]:
            print(f"  [{item.rank}] {format_product_label(name=item.product_name, category_path=item.category_path)}")
            print(f"      final_score={item.final_score} retrieval_score={item.retrieval_score}")
            print(f"      文档类型：{item.document_type or '未提取'}；运营商：{item.carrier or '未提取'}；区域：{item.region or '未提取'}")
            print(f"      套餐：{format_package_summary(item)}")
            print(f"      费用：{format_fee_summary(item)}")
            print(f"      可选包：{format_optional_summary(item)}")
            print(f"      限制/风险：{format_risk_summary(item)}")
            if item.missing_info:
                print(f"      信息缺口：{'；'.join(item.missing_info[:3])}")
            if item.matched_strengths:
                print(f"      匹配依据：{'；'.join(item.matched_strengths[:2])}")

    if result.explanation:
        print()
        print("LLM 推荐说明：")
        print(f"- summary：{result.explanation.summary}")
        if result.explanation.recommended_product.product_name:
            recommended_label = format_recommended_explanation_label(result)
            print(
                "- recommended："
                f"{recommended_label} "
                f"({result.explanation.recommended_product.document_id})"
            )
        if result.explanation.comparison_summary:
            print(f"- comparison：{result.explanation.comparison_summary[:3]}")
        if result.explanation.clarifying_questions:
            print(f"- questions：{result.explanation.clarifying_questions[:3]}")
        if result.explanation.risk_reminders:
            print(f"- risks：{result.explanation.risk_reminders[:3]}")

    if result.errors:
        print()
        print("流程错误：")
        for error in result.errors:
            print(f"- {error}")


def format_product_label(*, name: str, category_path: str) -> str:
    """把产品名和产品目录定位放在一起，方便人工测试时识别“这是哪个产品”。"""

    return f"{name} [{category_path}]" if category_path else name


def format_recommended_explanation_label(result) -> str:
    """LLM 推荐说明只返回 document_id/product_name，这里回查 Comparator 补上目录定位。"""

    explanation = result.explanation
    if not explanation or not result.comparison:
        return ""
    document_id = explanation.recommended_product.document_id
    product_name = explanation.recommended_product.product_name
    for item in result.comparison.products:
        if item.document_id == document_id:
            return format_product_label(name=product_name or item.product_name, category_path=item.category_path)
    return product_name


def format_package_summary(item) -> str:
    """格式化套餐能力摘要，避免 Top 对比只剩分数。"""

    summary = item.package_summary
    parts = []
    if summary.package_names:
        parts.append("套餐=" + "、".join(summary.package_names[:3]))
    if summary.speeds:
        parts.append("速率=" + "、".join(summary.speeds[:3]))
    if summary.price_range:
        parts.append("价格=" + summary.price_range)
    if summary.contract_periods:
        parts.append("协议期=" + "、".join(summary.contract_periods[:3]))
    return "；".join(parts) if parts else "暂无套餐摘要"


def format_fee_summary(item) -> str:
    """格式化费用摘要。"""

    summary = item.fee_summary
    parts = []
    if summary.fee_rule_names:
        parts.append("规则=" + "、".join(summary.fee_rule_names[:4]))
    fee_flags = []
    if summary.has_monthly_fee:
        fee_flags.append("月费")
    if summary.has_one_time_fee:
        fee_flags.append("一次性费用")
    if summary.has_installation_fee:
        fee_flags.append("安装/调测费")
    if summary.has_deposit:
        fee_flags.append("押金")
    if summary.has_penalty_rule:
        fee_flags.append("违约/退出规则")
    if fee_flags:
        parts.append("费用类型=" + "、".join(fee_flags))
    if summary.amount_values:
        parts.append("金额样例=" + "、".join(str(value) for value in summary.amount_values[:4]))
    return "；".join(parts) if parts else "暂无费用摘要"


def format_optional_summary(item) -> str:
    """格式化可选包摘要。"""

    summary = item.optional_package_summary
    if not summary.optional_package_count:
        return "暂无可选包摘要"
    names = summary.names[:4] or summary.paid_package_names[:4] or summary.free_package_names[:4]
    return f"{summary.optional_package_count}个：" + "、".join(names)


def format_risk_summary(item) -> str:
    """格式化限制和风险摘要。"""

    risks = list(item.risk_warnings[:2])
    risks.extend(item.constraint_summary.important_risks[:2])
    if item.constraint_summary.blocks_recommendation:
        risks.insert(0, "存在阻断推荐限制")
    return "；".join(risks) if risks else "暂无明确风险提醒"


if __name__ == "__main__":
    main()
