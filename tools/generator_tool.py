import json
import re
from typing import Any

from langchain.tools import tool

from tools.calc_tool import build_calculator_response
from tools.compare_tool import build_comparison_response
from tools.product_tool import display_text
from tools.rag_tool import build_rag_context_response
from tools.requirement_parser import parse_requirement_payload
from tools.rule_engine import classify_requirement_payload


DERIVED_QUERY_MARKERS = (
    "候选方案",
    "推荐核算方案",
    "低带宽备选方案",
    "扩容备选方案",
    "候选明细",
    "标价口径",
    "pricing_input",
)


def _number(value: Any, default: float = 0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_money(value: Any) -> str:
    if value is None:
        return "-"
    amount = _number(value)
    if amount.is_integer():
        return f"{int(amount)} 元"
    return f"{amount:.2f} 元"


def _join(values: list[Any] | None, default: str = "-") -> str:
    cleaned = [display_text(value, "").strip() for value in values or [] if display_text(value, "").strip()]
    return "、".join(cleaned) if cleaned else default


def _source_label(source: dict[str, Any] | None) -> str:
    if not source:
        return ""
    parts = []
    if source.get("product_id"):
        parts.append(str(source["product_id"]))
    if source.get("source_product_no") is not None:
        parts.append(f"source_product_no {source['source_product_no']}")
    if source.get("spec_text"):
        parts.append(display_text(source["spec_text"]))
    if source.get("sale_price") is not None:
        parts.append(_format_money(source["sale_price"]))
    return "，".join(parts)


def _looks_like_derived_tool_output(query: str) -> bool:
    if not query:
        return False
    has_marker = any(marker in query for marker in DERIVED_QUERY_MARKERS)
    has_product_id = bool(re.search(r"(intl_route_optimization|sd_wan|isp)-\d+", query))
    return has_marker and has_product_id


def _budget_line(item: dict[str, Any]) -> str:
    if item.get("budget_upper") is None:
        return "未提供预算上限"
    if item.get("budget_fit"):
        return f"预算内，余量 {_format_money(item.get('budget_delta'))}"
    return f"超预算 {_format_money(abs(item.get('budget_delta') or 0))}"


def _requirement_summary(parsed: dict[str, Any], module_name: str | None) -> list[str]:
    specs = parsed.get("spec_requirements") or {}
    budget = parsed.get("budget") or {}
    parts = [
        f"- 场景：{_join(parsed.get('scenarios'))}",
        f"- 产品模块：{display_text(module_name, '-')}",
        f"- 计费周期：{_join(parsed.get('billing_cycles'))}",
        f"- 签约方式：{_join(parsed.get('contract_modes'))}",
    ]
    if specs.get("user_count"):
        parts.append(f"- 使用人数：{specs['user_count']} 人")
    if specs.get("bandwidth_mbps"):
        parts.append(f"- 明确带宽：{max(specs['bandwidth_mbps'])}M")
    if budget.get("upper_cny") is not None:
        parts.append(f"- 预算：{_format_money(budget['upper_cny'])}以内")
    return parts


def _compare_item_by_id(compare_response: dict[str, Any], product_id: str | None) -> dict[str, Any] | None:
    if not product_id:
        return None
    return next(
        (item for item in compare_response.get("compared_items", []) if item.get("product_id") == product_id),
        None,
    )


def build_quote_proposal_response(query: str) -> dict[str, Any]:
    parsed = parse_requirement_payload(query)
    rule_result = classify_requirement_payload(parsed)
    compare_response = build_comparison_response(query)
    calculator_response = build_calculator_response(query)
    rag_response = build_rag_context_response(query)

    pricing_results = calculator_response.get("pricing_results", [])
    recommended_pricing = pricing_results[0] if pricing_results else None
    recommended_compare = _compare_item_by_id(
        compare_response,
        recommended_pricing.get("product_id") if recommended_pricing else None,
    )

    return {
        "raw_query": query,
        "parsed_requirements": parsed,
        "rule_engine": rule_result,
        "compare": compare_response,
        "calculator": calculator_response,
        "rag": rag_response,
        "recommended_pricing": recommended_pricing,
        "recommended_compare": recommended_compare,
        "message": "已生成方案书/报价说明。" if recommended_pricing else "未找到可生成报价说明的计价结果。",
    }


def render_quote_proposal_response(response: dict[str, Any]) -> str:
    parsed = response["parsed_requirements"]
    calculator = response["calculator"]
    compare = response["compare"]
    rag = response["rag"]
    recommended = response.get("recommended_pricing")
    recommended_compare = response.get("recommended_compare") or {}

    lines = ["方案书 / 报价说明", response["message"]]
    lines.append("")
    lines.append("一、客户需求摘要")
    lines.extend(_requirement_summary(parsed, calculator.get("module_name")))

    if not recommended:
        lines.append("")
        lines.append("当前未生成可报价方案，请补充客户场景、产品线或预算信息后重试。")
        return "\n".join(lines)

    lines.append("")
    lines.append("二、推荐结论")
    lines.append(
        f"推荐方案：{display_text(recommended.get('product_name'), display_text(recommended.get('product_id')))}"
        f"（{recommended.get('product_id')}）"
    )
    if recommended_compare.get("spec_text"):
        lines.append(f"- 规格：{display_text(recommended_compare.get('spec_text'))}")
    lines.append(
        f"- 计费：{display_text(recommended.get('billing_cycle'))}，数量 {recommended.get('quantity')}，"
        f"单价 {_format_money(recommended.get('unit_price'))}/{display_text(recommended.get('unit'))}"
    )
    lines.append(
        f"- 应付合计：{_format_money(recommended.get('payable_total'))}"
        f"（{_budget_line(recommended)}）"
    )
    lines.append(f"- 计价规则：{recommended.get('discount_reason')}")
    if recommended_compare.get("advantages"):
        lines.append("- 推荐依据：" + "；".join(display_text(value) for value in recommended_compare["advantages"]))
    if recommended_compare.get("tradeoffs"):
        lines.append("- 主要取舍：" + "；".join(display_text(value) for value in recommended_compare["tradeoffs"]))

    lines.append("")
    lines.append("三、候选方案报价对比")
    for index, item in enumerate(calculator.get("pricing_results", []), start=1):
        compare_item = _compare_item_by_id(compare, item.get("product_id")) or {}
        spec = display_text(compare_item.get("spec_text"), "-")
        lines.append(
            f"{index}. {display_text(item.get('product_name'), display_text(item.get('product_id')))}"
            f"（{item.get('product_id')}）：{spec}，"
            f"{display_text(item.get('billing_cycle'))}，数量 {item.get('quantity')}，"
            f"标价小计 {_format_money(item.get('original_subtotal'))}，"
            f"折扣 {_format_money(item.get('discount_amount'))}，"
            f"初装/调试费 {_format_money(item.get('setup_total'))}，"
            f"应付 {_format_money(item.get('payable_total'))}，{_budget_line(item)}。"
        )

    lines.append("")
    lines.append("四、费用来源与边界")
    price_source = _source_label(recommended.get("price_source"))
    setup_source = _source_label(recommended.get("setup_fee_source"))
    if price_source:
        lines.append(f"- 套餐价来源：产品表 {price_source}")
    if setup_source:
        lines.append(f"- 初装/调试费来源：产品表 {setup_source}")
    elif recommended.get("setup_total"):
        lines.append("- 初装/调试费来源：未携带来源，请回查产品模块输出。")
    lines.append(f"- 折扣/价格规则：{recommended.get('discount_reason')}")
    lines.append("- 技术参数、服务权益和销售话术依据：RAG 层当前未配置知识库，不能补充产品数据和工具结果之外的承诺。")

    lines.append("")
    lines.append("五、需销售确认")
    follow_up = compare.get("follow_up_questions") or parsed.get("follow_up_questions") or []
    for question in follow_up:
        lines.append(f"- {question}")
    unsupported = rag.get("unsupported_claims") or []
    if unsupported:
        lines.append("- SLA、开通周期、免费测试、巡检、赠品、IP 赠送、调试费减免、锁价等：产品数据未配置，需销售确认。")

    return "\n".join(lines)


@tool
def generate_quote_proposal(query: str) -> str:
    """
    Generator：生成方案书 / 报价说明。
    输入必须是客户原始需求。本工具会按 Intent Parser -> Rule Engine -> 产品核算模块 -> Compare -> Calculator -> RAG -> Generator
    的顺序编排，并只使用前面各层已有的结构化结果生成最终说明。
    """
    if _looks_like_derived_tool_output(query):
        return (
            "Generator 输入错误：请传入客户原始需求，不要传入候选方案摘要或其他工具输出。"
            "例如传入“客户需要固定IP和100M大带宽，上海办公，预算2万以内”。"
        )
    response = build_quote_proposal_response(query)
    return render_quote_proposal_response(response)


@tool
def generate_quote_proposal_json(query: str) -> str:
    """
    返回 Generator 层结构化 JSON，便于调试完整销售流程结果。
    """
    response = build_quote_proposal_response(query)
    return json.dumps(response, ensure_ascii=False, indent=2)
