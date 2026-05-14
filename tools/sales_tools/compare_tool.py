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


MAX_COMPARE_ITEMS = 4
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


def _module_response(query: str, module_key: str) -> dict[str, Any]:
    if module_key == "sd_wan":
        return build_sd_wan_quote_response(query)
    if module_key == "intl_route_optimization":
        return build_intl_route_response(query)
    if module_key == "isp_private_line":
        return build_isp_quote_response(query)
    return {
        "raw_query": query,
        "module": {
            "module_key": module_key,
            "module_name": module_key,
        },
        "candidates": [],
        "compare_items": [],
        "recommendation": None,
    }


def _candidate_pool(response: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    seen_ids: set[str] = set()

    recommendation = response.get("recommendation")
    if recommendation:
        items.append(("recommended", "推荐方案", recommendation))

    for item in response.get("candidates", []):
        role_code = item.get("role_code") or "candidate"
        role_name = item.get("role_name") or "候选方案"
        items.append((role_code, role_name, item))

    for item in response.get("compare_items", []):
        items.append(("candidate", "候选方案", item))

    normalized = []
    module = response.get("module") or {}
    for role_code, role_name, item in items:
        if not isinstance(item, dict):
            continue
        product_id = display_text(item.get("product_id"), "")
        if not product_id or product_id in seen_ids:
            continue
        seen_ids.add(product_id)
        normalized.append(_normalize_candidate(module, role_code, role_name, item))
        if len(normalized) >= MAX_COMPARE_ITEMS:
            break
    return normalized


def _pricing_reference(item: dict[str, Any]) -> float | None:
    pricing_input = item.get("pricing_input") or {}
    for key in ("unit_price", "base_price"):
        if pricing_input.get(key) is not None:
            return _number(pricing_input.get(key))
    for key in ("sale_price", "annual_price"):
        if item.get(key) is not None:
            return _number(item.get(key))
    return None


def _bandwidth_text(item: dict[str, Any]) -> str:
    if item.get("bandwidth_mbps") is not None:
        return f"{item['bandwidth_mbps']}M"
    down = item.get("down_mbps")
    up = item.get("up_mbps")
    if down is not None and up is not None:
        return f"{down}M/{up}M"
    if item.get("max_bandwidth_mbps") is not None:
        return f"{item['max_bandwidth_mbps']}M"
    return display_text(item.get("spec_text"))


def _normalize_candidate(
    module: dict[str, Any],
    role_code: str,
    role_name: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    pricing_input = item.get("pricing_input") or {}
    pricing_reference = _pricing_reference(item)
    quantity = int(_number(pricing_input.get("quantity"), 1)) if pricing_input else 1
    setup_fee = _number(pricing_input.get("setup_fee"), _number(item.get("setup_fee")))
    estimated_list_total = None
    if pricing_reference is not None:
        estimated_list_total = round(pricing_reference * quantity + setup_fee * quantity, 2)

    return {
        "module_key": module.get("module_key"),
        "module_name": module.get("module_name"),
        "category": module.get("category"),
        "role_code": role_code,
        "role_name": role_name,
        "product_id": item.get("product_id"),
        "product_name": item.get("product_name"),
        "spec_text": item.get("spec_text"),
        "bandwidth_text": _bandwidth_text(item),
        "billing_cycle": item.get("billing_cycle") or pricing_input.get("billing_cycle"),
        "unit": pricing_input.get("unit") or item.get("unit"),
        "quantity": quantity,
        "pricing_reference": pricing_reference,
        "setup_fee": setup_fee,
        "estimated_list_total": estimated_list_total,
        "budget_fit_on_list_price": item.get("budget_fit_on_list_price", item.get("budget_fit")),
        "score": item.get("score"),
        "advantages": item.get("advantages") or item.get("match_reasons") or [],
        "tradeoffs": item.get("tradeoffs") or [],
        "description": item.get("description"),
        "pricing_input": pricing_input,
    }


def _choose_recommended(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not items:
        return None
    recommended = next((item for item in items if item.get("role_code") == "recommended"), None)
    if recommended:
        return recommended
    return max(
        items,
        key=lambda item: (
            bool(item.get("budget_fit_on_list_price")),
            _number(item.get("score")),
            -_number(item.get("estimated_list_total"), 10**12),
        ),
    )


def _build_observations(items: list[dict[str, Any]], recommended: dict[str, Any] | None) -> list[str]:
    observations = []
    if not items:
        return observations

    budget_fit_count = sum(1 for item in items if item.get("budget_fit_on_list_price"))
    observations.append(f"共有 {len(items)} 个候选进入对比，其中 {budget_fit_count} 个按标价口径在预算内。")

    cheapest = min(
        (item for item in items if item.get("estimated_list_total") is not None),
        key=lambda item: item["estimated_list_total"],
        default=None,
    )
    if cheapest:
        observations.append(
            f"标价口径最低的是 {display_text(cheapest.get('product_name'))}"
            f"（{cheapest.get('product_id')}），约 {_format_money(cheapest.get('estimated_list_total'))}。"
        )

    if recommended:
        observations.append(
            f"当前优先建议 {display_text(recommended.get('product_name'))}"
            f"（{recommended.get('product_id')}），因为它在规格、预算和场景匹配之间更均衡。"
        )

    if any(item.get("pricing_input") for item in items):
        observations.append("本层只做方案对比，折扣、初装费合计和最终应付金额交给 calculator 统一确认。")
    return observations


def build_comparison_response(query: str) -> dict[str, Any]:
    parsed = parse_requirement_payload(query)
    rule_result = classify_requirement_payload(parsed)
    module_key = rule_result.get("primary_module")
    if not module_key:
        return {
            "raw_query": query,
            "parsed_requirements": parsed,
            "rule_engine": rule_result,
            "module_key": None,
            "summary": {
                "candidate_count": 0,
                "compared_count": 0,
                "ready_for_comparison": False,
            },
            "compared_items": [],
            "recommended_choice": None,
            "observations": [],
            "follow_up_questions": parsed.get("follow_up_questions", []),
            "message": "未识别到可对比的产品模块，请先补充客户场景或产品线。",
        }

    module_response = _module_response(query, module_key)
    compared_items = _candidate_pool(module_response)
    recommended = _choose_recommended(compared_items)
    observations = _build_observations(compared_items, recommended)

    return {
        "raw_query": query,
        "parsed_requirements": parsed,
        "rule_engine": rule_result,
        "module_key": module_key,
        "module_response_summary": module_response.get("summary"),
        "summary": {
            "candidate_count": len(compared_items),
            "compared_count": len(compared_items),
            "ready_for_comparison": len(compared_items) >= 2,
        },
        "compared_items": compared_items,
        "recommended_choice": recommended,
        "observations": observations,
        "follow_up_questions": module_response.get("follow_up_questions") or parsed.get("follow_up_questions", []),
        "message": "已生成候选方案对比，可继续进入 calculator 计算折扣与总价。"
        if compared_items
        else "已命中产品模块，但没有拿到可对比候选方案。",
    }


def render_comparison_item(item: dict[str, Any]) -> list[str]:
    budget_text = "预算内" if item.get("budget_fit_on_list_price") else "超预算/待确认"
    lines = [
        f"{item['role_name']}：{display_text(item.get('product_name'))}（{item.get('product_id')}）",
        f"  规格：{display_text(item.get('spec_text'))}；带宽：{display_text(item.get('bandwidth_text'))}",
        f"  计费：{display_text(item.get('billing_cycle'))}；数量：{item.get('quantity')}；"
        f"标价口径：{_format_money(item.get('estimated_list_total'))}（{budget_text}）",
    ]
    if item.get("advantages"):
        lines.append("  优势：" + "；".join(display_text(value) for value in item["advantages"]))
    if item.get("tradeoffs"):
        lines.append("  取舍：" + "；".join(display_text(value) for value in item["tradeoffs"]))
    if item.get("pricing_input"):
        lines.append("  后续：已生成 pricing_input，可交给 calculator 计算最终金额。")
    return lines


def render_comparison_response(response: dict[str, Any]) -> str:
    parsed = response["parsed_requirements"]
    items = response["compared_items"]
    lines = ["方案对比", response["message"]]

    requirement_parts = []
    if parsed.get("target_categories"):
        requirement_parts.append("分类：" + "、".join(parsed["target_categories"]))
    if parsed.get("scenarios"):
        requirement_parts.append("场景：" + "、".join(parsed["scenarios"]))
    if parsed.get("billing_cycles"):
        requirement_parts.append("计费周期：" + "、".join(parsed["billing_cycles"]))
    budget = parsed.get("budget") or {}
    if budget.get("upper_cny") is not None:
        requirement_parts.append("预算：" + _format_money(budget["upper_cny"]) + "以内")
    if requirement_parts:
        lines.append("对比范围：" + " | ".join(requirement_parts))

    recommended = response.get("recommended_choice")
    if recommended:
        lines.append(f"优先建议：{recommended.get('product_id')}（{display_text(recommended.get('role_name'))}）")

    if response.get("observations"):
        lines.append("核心结论：")
        for observation in response["observations"]:
            lines.append(f"- {observation}")

    if items:
        lines.append("")
        lines.append("候选明细：")
        for item in items:
            lines.extend(render_comparison_item(item))
            lines.append("")

    if response.get("follow_up_questions"):
        lines.append("建议补充：")
        for question in response["follow_up_questions"]:
            lines.append(f"- {question}")

    return "\n".join(line for line in lines if line is not None).rstrip()


@tool
def compare_packages(query: str) -> str:
    """
    Compare：用于对 Rule Engine 命中的产品核算模块候选方案做横向对比。
    本工具只比较规格、预算匹配、优势和取舍，不计算最终折扣与总价；
    最终价格交给 calculator 统一确认。
    输入必须是客户原始需求，不要传入其他工具输出、候选方案摘要或改写后的产品清单。
    """
    if _looks_like_derived_tool_output(query):
        return (
            "Compare 输入错误：请传入客户原始需求，不要传入候选方案摘要或其他工具输出。"
            "例如传入“有个做游戏的客户，服务器在美国，上海办公室访问很慢，大概10个人用，想先试一个月，预算1万元以内”。"
        )
    response = build_comparison_response(query)
    return render_comparison_response(response)


@tool
def compare_packages_json(query: str) -> str:
    """
    返回 Compare 层结构化 JSON，便于调试候选方案、pricing_input 和推荐选择。
    """
    return json.dumps(build_comparison_response(query), ensure_ascii=False, indent=2)

