import json
import re
from typing import Any

from langchain.tools import tool

from tools.sales_tools.intl_route_tool import build_intl_route_response
from tools.sales_tools.isp_tool import build_isp_quote_response
from tools.sales_tools.product_tool import display_text
from tools.sales_tools.requirement_parser import parse_requirement_payload
from tools.sales_tools.rule_engine import classify_requirement_payload
from tools.sales_tools.sd_wan_tool import build_sd_wan_quote_response


SUPPORTED_MODULES = {
    "sd_wan": "SD-WAN",
    "intl_route_optimization": "国际路由优化",
    "isp_private_line": "ISP 专线",
}
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


def _looks_like_derived_tool_output(query: str) -> bool:
    if not query:
        return False
    has_marker = any(marker in query for marker in DERIVED_QUERY_MARKERS)
    has_product_id = bool(re.search(r"(intl_route_optimization|sd_wan|isp)-\d+", query))
    return has_marker and has_product_id


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


def _discount_rule_label(rule: dict[str, Any] | None) -> str:
    if not rule:
        return ""
    parts = []
    if rule.get("rule_code"):
        parts.append(str(rule["rule_code"]))
    if rule.get("label"):
        parts.append(display_text(rule["label"]))
    if rule.get("discount_rate") is not None:
        rate = _number(rule.get("discount_rate"), 1)
        parts.append(f"{rate * 10:g} 折")
    if rule.get("base_field"):
        parts.append(f"基于 {display_text(rule['base_field'])}")
    return "，".join(parts)


def _normalize_label(value: Any) -> str:
    return display_text(value, "").strip().lower().replace(" ", "")


def _budget_upper(parsed: dict[str, Any] | None) -> float | None:
    budget = (parsed or {}).get("budget") or {}
    upper = budget.get("upper_cny")
    return _number(upper) if upper is not None else None


def _is_annual_billing(value: Any) -> bool:
    return display_text(value, "") == "年付"


def _is_monthly_billing(value: Any) -> bool:
    return display_text(value, "") == "月付"


def _pick_discount_rule(pricing_input: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    module_key = pricing_input.get("module_key")
    billing_cycle = pricing_input.get("billing_cycle")
    selected_rule = pricing_input.get("discount_rule")
    discount_options = pricing_input.get("discount_options") or []

    if module_key == "sd_wan":
        if selected_rule:
            label = _normalize_label(selected_rule.get("label"))
            if "ict" in label:
                return selected_rule, "SD-WAN 命中 ICT 签约授权折扣，按 8 折计算。"
            if "直签" in label:
                return selected_rule, "SD-WAN 命中直签授权折扣，按 7 折计算。"
            return selected_rule, "SD-WAN 使用核算模块传入的授权折扣规则。"
        return None, "SD-WAN 未指定 ICT 签约或直签，本次不自动套用授权折扣。"

    if module_key == "intl_route_optimization":
        if not _is_annual_billing(billing_cycle):
            return None, "国际路由优化当前为月付套餐，年付 95 折不适用。"
        annual_rule = next(
            (
                option
                for option in discount_options
                if option.get("rule_code") == "annual_authorized_discount"
                or "年付" in display_text(option.get("label"), "")
            ),
            None,
        )
        if annual_rule:
            return annual_rule, "国际路由优化年付套餐命中年付授权折扣，按 95 折计算。"
        return None, "国际路由优化为年付套餐，但产品数据未提供年付授权折扣规则。"

    if module_key == "isp_private_line":
        return None, "ISP 专线当前未配置折扣规则，按套餐标价和初装费计算。"

    return None, "未知产品模块，未套用折扣。"


def _validated_discount_rate(module_key: str, rule: dict[str, Any] | None) -> float:
    if not rule:
        return 1.0

    label = _normalize_label(rule.get("label"))
    raw_rate = _number(rule.get("discount_rate"), 1)

    if module_key == "sd_wan":
        if "ict" in label:
            return 0.8
        if "直签" in label:
            return 0.7
        return raw_rate or 1.0

    if module_key == "intl_route_optimization":
        return 0.95

    return raw_rate or 1.0


def calculate_pricing_input(
    pricing_input: dict[str, Any],
    parsed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    module_key = pricing_input.get("module_key")
    quantity = max(1, int(_number(pricing_input.get("quantity"), 1)))
    unit_price = _number(
        pricing_input.get("unit_price")
        if pricing_input.get("unit_price") is not None
        else pricing_input.get("base_price")
    )
    setup_fee = _number(pricing_input.get("setup_fee"))
    discount_rule, discount_reason = _pick_discount_rule(pricing_input)
    discount_rate = _validated_discount_rate(module_key, discount_rule)

    original_subtotal = round(unit_price * quantity, 2)
    discounted_unit_price = round(unit_price * discount_rate, 2)
    discounted_subtotal = round(discounted_unit_price * quantity, 2)
    setup_total = round(setup_fee * quantity, 2)
    payable_total = round(discounted_subtotal + setup_total, 2)
    budget_upper = _budget_upper(parsed)

    return {
        "module_key": module_key,
        "module_name": SUPPORTED_MODULES.get(module_key, display_text(module_key)),
        "product_id": pricing_input.get("product_id"),
        "product_name": pricing_input.get("product_name"),
        "billing_cycle": pricing_input.get("billing_cycle"),
        "currency": pricing_input.get("currency", "CNY"),
        "quantity": quantity,
        "unit": pricing_input.get("unit"),
        "unit_price": unit_price,
        "price_source": pricing_input.get("price_source"),
        "original_subtotal": original_subtotal,
        "discount_rule": discount_rule,
        "discount_rate": discount_rate,
        "discount_reason": discount_reason,
        "discount_amount": round(original_subtotal - discounted_subtotal, 2),
        "discounted_unit_price": discounted_unit_price,
        "discounted_subtotal": discounted_subtotal,
        "setup_fee": setup_fee,
        "setup_fee_source": pricing_input.get("setup_fee_source"),
        "setup_total": setup_total,
        "payable_total": payable_total,
        "budget_upper": budget_upper,
        "budget_fit": budget_upper is None or payable_total <= budget_upper,
        "budget_delta": None if budget_upper is None else round(budget_upper - payable_total, 2),
    }


def _pricing_inputs_from_module_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    inputs = []
    seen: set[str] = set()

    candidates = response.get("candidates") or response.get("compare_items") or []
    recommendation = response.get("recommendation")
    if recommendation:
        candidates = [recommendation, *candidates]

    for item in candidates:
        pricing_input = item.get("pricing_input") if isinstance(item, dict) else None
        if not pricing_input:
            continue
        key = display_text(pricing_input.get("product_id")) + "|" + display_text(pricing_input.get("billing_cycle"))
        if key in seen:
            continue
        seen.add(key)
        inputs.append(pricing_input)

    recommended_input = response.get("summary", {}).get("recommended_pricing_input")
    if recommended_input:
        key = display_text(recommended_input.get("product_id")) + "|" + display_text(recommended_input.get("billing_cycle"))
        if key not in seen:
            inputs.insert(0, recommended_input)

    return inputs


def _build_module_response(query: str, parsed: dict[str, Any], module_key: str) -> dict[str, Any]:
    if module_key == "sd_wan":
        return build_sd_wan_quote_response(query)
    if module_key == "intl_route_optimization":
        return build_intl_route_response(query)
    if module_key == "isp_private_line":
        return build_isp_quote_response(query)
    return {
        "raw_query": query,
        "parsed_requirements": parsed,
        "message": f"calculator 暂不支持模块：{module_key}",
    }


def _resolve_module_key(parsed: dict[str, Any]) -> str | None:
    rule_result = classify_requirement_payload(parsed)
    return rule_result.get("primary_module")


def build_calculator_response(query: str) -> dict[str, Any]:
    parsed = parse_requirement_payload(query)
    module_key = _resolve_module_key(parsed)
    if not module_key:
        return {
            "raw_query": query,
            "parsed_requirements": parsed,
            "module_key": None,
            "message": "calculator 未能识别要计价的产品模块，请先通过 Rule Engine 或产品核算模块明确候选方案。",
            "pricing_results": [],
        }

    module_response = _build_module_response(query, parsed, module_key)
    pricing_inputs = _pricing_inputs_from_module_response(module_response)
    pricing_results = [
        calculate_pricing_input(pricing_input, parsed)
        for pricing_input in pricing_inputs
    ]

    return {
        "raw_query": query,
        "parsed_requirements": parsed,
        "module_key": module_key,
        "module_name": SUPPORTED_MODULES.get(module_key, module_key),
        "module_response_summary": module_response.get("summary"),
        "pricing_inputs": pricing_inputs,
        "pricing_results": pricing_results,
        "message": "已完成折扣与总价计算。" if pricing_results else "未找到可计算的 pricing_input。",
    }


def render_calculator_response(response: dict[str, Any]) -> str:
    lines = ["Calculator 计价结果", response["message"]]
    if response.get("module_name"):
        lines.append(f"产品模块：{response['module_name']}")

    results = response.get("pricing_results", [])
    if not results:
        return "\n".join(lines)

    lines.append("")
    for index, item in enumerate(results, start=1):
        budget_text = ""
        if item.get("budget_upper") is not None:
            if item["budget_fit"]:
                budget_text = f"，预算内，余量 {_format_money(item.get('budget_delta'))}"
            else:
                budget_text = f"，超预算 {_format_money(abs(item.get('budget_delta') or 0))}"

        lines.append(
            f"{index}. {display_text(item.get('product_name'), display_text(item.get('product_id')))}（{item.get('product_id')}）"
        )
        lines.append(
            f"   计费：{item.get('billing_cycle')}，数量 {item.get('quantity')}，"
            f"单价 {_format_money(item.get('unit_price'))}/{display_text(item.get('unit'))}"
        )
        lines.append(
            f"   标价小计 {_format_money(item.get('original_subtotal'))}；"
            f"折扣 {_format_money(item.get('discount_amount'))}；"
            f"折后小计 {_format_money(item.get('discounted_subtotal'))}；"
            f"初装/调试费 {_format_money(item.get('setup_total'))}；"
            f"应付合计 {_format_money(item.get('payable_total'))}{budget_text}"
        )
        lines.append(f"   规则说明：{item.get('discount_reason')}")
        price_source = _source_label(item.get("price_source"))
        setup_source = _source_label(item.get("setup_fee_source"))
        source_lines = []
        if price_source:
            source_lines.append(f"套餐价来自产品表 {price_source}")
        if setup_source:
            source_lines.append(f"初装/调试费来自产品表 {setup_source}")
        elif item.get("setup_total"):
            source_lines.append("初装/调试费未携带来源，请回查产品模块输出")
        discount_source = _discount_rule_label(item.get("discount_rule"))
        if discount_source:
            source_lines.append(f"折扣规则来自产品数据 {discount_source}")
        if source_lines:
            lines.append("   费用来源：" + "；".join(source_lines))

    return "\n".join(lines)


@tool
def calculator(query: str) -> str:
    """
    Calculator：用于对 SD-WAN、国际路由优化、ISP 专线候选方案统一计算折扣与总价。
    规则：
    - SD-WAN：ICT 签约 8 折，直签 7 折；未指定签约方式时不自动套折扣。
    - 国际路由优化：只有年付套餐享受年付 95 折，月付不享受。
    - ISP 专线：按套餐费 + 初装费核算，当前不套折扣。
    输入必须是客户原始需求，不要传入其他工具输出、候选方案摘要或改写后的产品清单。
    """
    if _looks_like_derived_tool_output(query):
        return (
            "Calculator 输入错误：请传入客户原始需求，不要传入候选方案摘要或其他工具输出。"
            "例如传入“客户北京上海广州三个办公室要组网，大概30个人用，想年付，ICT签约，预算10万以内”。"
        )
    response = build_calculator_response(query)
    return render_calculator_response(response)

