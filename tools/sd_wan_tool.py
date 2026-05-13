import json
import re
from typing import Any

from langchain.tools import tool

from tools.product_tool import display_text, load_flat_products, normalize_text
from tools.requirement_parser import parse_requirement_payload


SD_WAN_CATEGORY = "SD-WAN"
DEFAULT_COMPARE_LIMIT = 3


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


def _extract_bandwidth_mbps(product: dict[str, Any]) -> int | None:
    source = " ".join(
        display_text(product.get(field), "")
        for field in ("spec_detail", "spec_text", "product_name")
    )
    match = re.search(r"(\d+(?:\.\d+)?)\s*M", source, re.IGNORECASE)
    if not match:
        return None
    bandwidth = float(match.group(1))
    return int(bandwidth) if bandwidth.is_integer() else round(bandwidth)


def _resolve_required_bandwidth(parsed: dict[str, Any]) -> tuple[int | None, str | None]:
    bandwidth_values = parsed.get("spec_requirements", {}).get("bandwidth_mbps") or []
    if bandwidth_values:
        return int(max(bandwidth_values)), "客户明确提出带宽规格"

    estimates: list[tuple[int, str]] = []
    site_count = parsed.get("spec_requirements", {}).get("site_count")
    branch_count = parsed.get("spec_requirements", {}).get("branch_count")
    user_count = parsed.get("spec_requirements", {}).get("user_count")
    inferred_nodes, inferred_reason = _resolve_node_count(parsed)

    if site_count or branch_count or inferred_nodes > 1:
        nodes = site_count or (branch_count + 1 if branch_count else inferred_nodes)
        if nodes <= 3:
            estimates.append((10, f"按小规模多点组网预估 10M（{inferred_reason}）"))
        elif nodes <= 6:
            estimates.append((20, f"按中等规模多点组网预估 20M（{inferred_reason}）"))
        else:
            estimates.append((30, f"按较多站点组网预估 30M（{inferred_reason}）"))

    if user_count:
        if user_count <= 20:
            estimates.append((10, "按用户规模预估 10M"))
        elif user_count <= 50:
            estimates.append((20, "按用户规模预估 20M"))
        else:
            estimates.append((30, "按用户规模预估 30M"))

    if estimates:
        bandwidth, reason = max(estimates, key=lambda item: item[0])
        return bandwidth, "未给明确带宽，" + reason

    return None, None


def _parse_site_count_from_query(query: str) -> int | None:
    query_norm = normalize_text(query)
    numerals = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    match = re.search(r"(\d+|[一二两三四五六七八九十])个?(?:办公室|办公点|站点|网点|门店|分公司|城市)", query_norm)
    if not match:
        return None
    value = match.group(1)
    return int(value) if value.isdigit() else numerals.get(value)


def _resolve_node_count(parsed: dict[str, Any]) -> tuple[int, str]:
    specs = parsed.get("spec_requirements", {})
    site_count = specs.get("site_count")
    branch_count = specs.get("branch_count")
    if site_count:
        return max(1, int(site_count)), "按客户明确站点数核算"
    if branch_count:
        return max(1, int(branch_count) + 1), "按分支数 + 总部估算节点数"
    parsed_site_count = _parse_site_count_from_query(parsed.get("raw_query", ""))
    if parsed_site_count:
        return max(1, parsed_site_count), "按需求文本中的办公点/站点数量核算"
    locations = parsed.get("locations") or []
    location_count = len({item.get("label") for item in locations if item.get("label")})
    query_norm = normalize_text(parsed.get("raw_query"))
    if location_count >= 2 and any(term in query_norm for term in ("组网", "互联", "办公室", "办公点", "站点")):
        return location_count, "按需求中出现的多个办公地点估算节点数"
    query_norm = normalize_text(parsed.get("raw_query"))
    if any(term in query_norm for term in ("总部", "总公司")) and any(
        term in query_norm for term in ("分公司", "分支", "门店", "办公室")
    ):
        return 2, "识别到总部与分支/办公室互联，按 2 个节点估算"
    return 1, "未给站点数，按单节点套餐核算"


def _preferred_contract_modes(parsed: dict[str, Any]) -> list[str]:
    modes = parsed.get("contract_modes") or []
    if modes:
        return modes
    query_norm = normalize_text(parsed.get("raw_query"))
    if "ict" in query_norm:
        return ["ICT签约"]
    if "直签" in query_norm:
        return ["直签"]
    return []


def _discount_rule(option: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_code": option.get("rule_code"),
        "label": option.get("label"),
        "description": option.get("description"),
        "discount_rate": option.get("discount_rate"),
        "base_field": option.get("base_field"),
    }


def _build_pricing_input(
    product: dict[str, Any],
    selected_discount_rule: dict[str, Any] | None,
    node_count: int,
) -> dict[str, Any]:
    base_price = product.get("annual_price") or product.get("sale_price")
    return {
        "pricing_stage": "module_pricing_input",
        "calculator_required": True,
        "module_key": "sd_wan",
        "product_id": product.get("product_id"),
        "product_name": product.get("product_name"),
        "billing_cycle": product.get("billing_cycle"),
        "currency": product.get("price_currency", "CNY"),
        "quantity": node_count,
        "unit": product.get("unit"),
        "unit_price": base_price,
        "price_source": {
            "product_id": product.get("product_id"),
            "source_product_no": product.get("source_product_no"),
            "product_name": product.get("product_name"),
            "spec_text": product.get("spec_text"),
            "billing_cycle": product.get("billing_cycle"),
            "sale_price": base_price,
            "currency": product.get("price_currency", "CNY"),
        },
        "setup_fee": product.get("setup_fee") or 0,
        "discount_rule": selected_discount_rule,
        "discount_options": [_discount_rule(option) for option in product.get("discount_options", [])],
        "note": "SD-WAN 模块只提供套餐、数量和授权规则，最终折扣与总价由 calculator 确认。",
    }


def _build_package_quote(
    product: dict[str, Any],
    parsed: dict[str, Any],
    required_bandwidth: int | None,
    node_count: int,
) -> dict[str, Any]:
    bandwidth = _extract_bandwidth_mbps(product)
    discount_options = [_discount_rule(option) for option in product.get("discount_options", [])]

    preferred_modes = _preferred_contract_modes(parsed)
    selected_discount_rule = None
    for mode in preferred_modes:
        selected_discount_rule = next(
            (
                item
                for item in discount_options
                if normalize_text(mode) in normalize_text(item.get("label"))
            ),
            None,
        )
        if selected_discount_rule:
            break

    pricing_input = _build_pricing_input(product, selected_discount_rule, node_count)
    budget = parsed.get("budget") or {}
    budget_upper = budget.get("upper_cny")
    unit_price = product.get("annual_price") or product.get("sale_price")
    list_total = _number(unit_price) * node_count if unit_price is not None else None
    budget_fit = budget_upper is None or list_total is None or list_total <= budget_upper
    meets_bandwidth = required_bandwidth is None or (bandwidth is not None and bandwidth >= required_bandwidth)

    score = 0
    if meets_bandwidth:
        score += 60
    elif bandwidth is not None and required_bandwidth is not None:
        score -= (required_bandwidth - bandwidth) * 3
    if budget_fit:
        score += 25
    else:
        score -= 30
    if selected_discount_rule and selected_discount_rule.get("label") == "直签":
        score += 8
    if bandwidth is not None and required_bandwidth is not None and bandwidth == required_bandwidth:
        score += 15
    if unit_price is not None:
        score -= int(_number(unit_price) // 10000)

    return {
        "product_id": product.get("product_id"),
        "product_name": product.get("product_name"),
        "spec_text": product.get("spec_text"),
        "bandwidth_mbps": bandwidth,
        "billing_cycle": product.get("billing_cycle"),
        "unit": product.get("unit"),
        "annual_price": product.get("annual_price") or product.get("sale_price"),
        "list_total": list_total,
        "node_count": node_count,
        "discount_options": discount_options,
        "selected_discount": selected_discount_rule,
        "pricing_input": pricing_input,
        "meets_bandwidth": meets_bandwidth,
        "budget_fit_on_list_price": budget_fit,
        "budget_fit": budget_fit,
        "score": score,
        "advantages": _build_advantages(bandwidth, required_bandwidth, budget_fit, selected_discount_rule),
        "tradeoffs": _build_tradeoffs(bandwidth, required_bandwidth, budget_fit),
    }


def _build_advantages(
    bandwidth: int | None,
    required_bandwidth: int | None,
    budget_fit: bool,
    selected_discount: dict[str, Any] | None,
) -> list[str]:
    advantages = []
    if required_bandwidth is None:
        advantages.append("可作为 SD-WAN 年付基础候选")
    elif bandwidth is not None and bandwidth >= required_bandwidth:
        advantages.append(f"带宽不低于需求 {required_bandwidth}M")
    if budget_fit:
        advantages.append("按节点数计算的标价总额在预算内，最终总价待 calculator 确认")
    if selected_discount:
        advantages.append(f"可向 calculator 传入{selected_discount.get('label')}授权规则")
    return advantages


def _build_tradeoffs(
    bandwidth: int | None,
    required_bandwidth: int | None,
    budget_fit: bool,
) -> list[str]:
    tradeoffs = []
    if required_bandwidth is not None and bandwidth is not None and bandwidth < required_bandwidth:
        tradeoffs.append(f"带宽低于需求 {required_bandwidth}M")
    if not budget_fit:
        tradeoffs.append("按节点数计算的标价总额超出当前预算，最终折后总价待 calculator 确认")
    if required_bandwidth is not None and bandwidth is not None and bandwidth > required_bandwidth:
        tradeoffs.append("配置高于当前需求，成本更高但预留增长空间")
    return tradeoffs


def _load_sd_wan_products() -> list[dict[str, Any]]:
    products = [
        product
        for product in load_flat_products()
        if product.get("category") == SD_WAN_CATEGORY
    ]
    return sorted(products, key=lambda item: _extract_bandwidth_mbps(item) or 0)


def build_sd_wan_quote_response(query: str) -> dict[str, Any]:
    parsed = parse_requirement_payload(query)
    required_bandwidth, bandwidth_reason = _resolve_required_bandwidth(parsed)
    node_count, node_count_reason = _resolve_node_count(parsed)
    products = _load_sd_wan_products()
    quotes = [
        _build_package_quote(product, parsed, required_bandwidth, node_count)
        for product in products
    ]
    quotes.sort(
        key=lambda item: (
            not item["meets_bandwidth"],
            not item["budget_fit"],
            -item["score"],
            _number(item["annual_price"], float("inf")),
            item["bandwidth_mbps"] or 0,
        )
    )
    recommendation = quotes[0] if quotes else None
    compare_items = _select_compare_items(quotes, recommendation)

    return {
        "raw_query": query,
        "parsed_requirements": parsed,
        "module": {
            "module_key": "sd_wan",
            "module_name": "SD-WAN 核算模块",
            "category": SD_WAN_CATEGORY,
        },
        "assumptions": {
            "required_bandwidth_mbps": required_bandwidth,
            "bandwidth_reason": bandwidth_reason,
            "node_count": node_count,
            "node_count_reason": node_count_reason,
            "contract_modes": _preferred_contract_modes(parsed),
            "contract_mode_reason": (
                "按客户指定签约方式核算"
                if _preferred_contract_modes(parsed)
                else "未指定签约方式，暂不选择授权规则，待销售确认后交给 calculator"
            ),
        },
        "summary": {
            "candidate_count": len(quotes),
            "recommended_product_id": recommendation.get("product_id") if recommendation else None,
            "recommended_discount": (recommendation.get("selected_discount") or {}).get("label") if recommendation else None,
            "recommended_pricing_input": recommendation.get("pricing_input") if recommendation else None,
        },
        "recommendation": recommendation,
        "compare_items": compare_items,
        "all_quotes": quotes,
    }


def _select_compare_items(
    quotes: list[dict[str, Any]],
    recommendation: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not quotes:
        return []
    selected = []
    seen_ids = set()

    lower_option = None
    if recommendation:
        lower_candidates = [
            item
            for item in quotes
            if (item.get("bandwidth_mbps") or 0) < (recommendation.get("bandwidth_mbps") or 0)
        ]
        lower_option = max(
            lower_candidates,
            key=lambda item: item.get("bandwidth_mbps") or 0,
            default=None,
        )

    higher_option = None
    if recommendation:
        higher_candidates = [
            item
            for item in quotes
            if (item.get("bandwidth_mbps") or 0) > (recommendation.get("bandwidth_mbps") or 0)
        ]
        higher_option = min(
            higher_candidates,
            key=lambda item: item.get("bandwidth_mbps") or float("inf"),
            default=None,
        )

    cheapest_budget_fit = min(
        (item for item in quotes if item.get("budget_fit")),
        key=lambda item: _number(item.get("annual_price"), float("inf")),
        default=None,
    )

    for item in [recommendation, lower_option, higher_option, cheapest_budget_fit, *quotes]:
        if not item or item["product_id"] in seen_ids:
            continue
        selected.append(item)
        seen_ids.add(item["product_id"])
        if len(selected) >= DEFAULT_COMPARE_LIMIT:
            break
    return selected


def _sd_wan_follow_up_questions(parsed: dict[str, Any]) -> list[str]:
    questions = []
    source_questions = parsed.get("human_follow_up_questions") or parsed.get("follow_up_questions", [])
    for question in source_questions:
        if "月付" in question and "年付" in question:
            continue
        questions.append(question)
    return questions


def _render_discount_options(quote: dict[str, Any]) -> str:
    parts = []
    for option in quote.get("discount_options", []):
        discount_rate = option.get("discount_rate")
        discount_text = f"{discount_rate * 10:g} 折" if isinstance(discount_rate, (int, float)) else display_text(discount_rate)
        parts.append(
            f"{option.get('label')}：授权规则 {discount_text}"
        )
    return "；".join(parts) if parts else "暂无授权折扣"


def render_sd_wan_quote_response(response: dict[str, Any]) -> str:
    assumptions = response["assumptions"]
    recommendation = response.get("recommendation")
    lines = ["SD-WAN 核算模块"]
    lines.append("说明：本模块只输出套餐推荐和 pricing_input；折扣金额与总价由 calculator 统一确认。")

    requirement_parts = []
    if assumptions.get("required_bandwidth_mbps"):
        requirement_parts.append(f"带宽 {assumptions['required_bandwidth_mbps']}M")
    requirement_parts.append(f"节点数 {assumptions['node_count']}")
    if assumptions.get("contract_modes"):
        requirement_parts.append("签约方式 " + "、".join(assumptions["contract_modes"]))
    if requirement_parts:
        lines.append("核算口径：" + "，".join(requirement_parts))

    notes = [
        item
        for item in (assumptions.get("bandwidth_reason"), assumptions.get("node_count_reason"))
        if item
    ]
    if notes:
        lines.append("口径说明：" + "；".join(notes))
    lines.append(f"签约说明：{assumptions['contract_mode_reason']}。")

    if not recommendation:
        lines.append("当前没有找到 SD-WAN 套餐，请确认产品目录是否已导入。")
        return "\n".join(lines)

    selected_discount = recommendation.get("selected_discount") or {}
    selected_discount_label = display_text(selected_discount.get("label"), "暂未指定")
    lines.append(
        f"推荐套餐：{display_text(recommendation.get('product_name'))} "
        f"{display_text(recommendation.get('spec_text'))}（{recommendation.get('product_id')}），"
        f"授权规则：{selected_discount_label}，"
        f"已生成 pricing_input，折扣与总价待 calculator 确认。"
    )
    if not recommendation.get("budget_fit"):
        lines.append("预算提示：按节点数计算的标价总额已超预算，折扣后价格以 calculator 确认结果为准。")

    lines.append("")
    lines.append("套餐对比：")
    for quote in response.get("compare_items", []):
        selected = quote.get("selected_discount") or {}
        selected_label = display_text(selected.get("label"), "暂未指定")
        lines.append(
            f"- {display_text(quote.get('spec_text'))}：标价 {_format_money(quote.get('annual_price'))}/节点/年；"
            f"{_render_discount_options(quote)}；推荐授权规则 {selected_label}，"
            f"pricing_input 待 calculator 确认。"
        )
        if quote.get("advantages"):
            lines.append("  优势：" + "；".join(quote["advantages"]))
        if quote.get("tradeoffs"):
            lines.append("  取舍：" + "；".join(quote["tradeoffs"]))

    follow_up_questions = _sd_wan_follow_up_questions(response.get("parsed_requirements", {}))
    if follow_up_questions:
        lines.append("")
        lines.append("我建议再和客户确认这几件事：")
        for question in follow_up_questions:
            lines.append(f"- {question}")

    return "\n".join(lines)


@tool
def quote_sd_wan_packages(query: str) -> str:
    """
    SD-WAN 核算模块：用于对 SD-WAN 不同带宽套餐做优劣对比，
    计算 ICT 签约、直签等授权折扣，并给出推荐套餐。
    """
    return render_sd_wan_quote_response(build_sd_wan_quote_response(query))


@tool
def quote_sd_wan_packages_json(query: str) -> str:
    """
    返回 SD-WAN 核算模块的结构化 JSON，便于调试套餐、折扣和推荐分数。
    """
    return json.dumps(build_sd_wan_quote_response(query), ensure_ascii=False, indent=2)
