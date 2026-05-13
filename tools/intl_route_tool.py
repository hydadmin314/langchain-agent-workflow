import json
import re
from typing import Any

from langchain.tools import tool

from tools.product_tool import display_text, load_flat_products, normalize_text
from tools.requirement_parser import parse_requirement_payload
from tools.rule_engine import classify_requirement_payload


MODULE_KEY = "intl_route_optimization"
MODULE_NAME = "国际路由优化核算模块"
CATEGORY_NAME = "国际路由优化"
DEFAULT_BILLING_CYCLE = "月付"
MAX_CANDIDATES = 3


def format_money(value: Any) -> str:
    if value is None:
        return "-"
    return f"{value} 元"


def safe_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_product_bandwidth_mbps(product: dict[str, Any]) -> int | None:
    spec_blob = " ".join(
        display_text(product.get(field), "")
        for field in ("spec_detail", "spec_text", "description", "product_name")
    )
    match = re.search(r"(\d+(?:\.\d+)?)\s*([MG])", spec_blob, re.IGNORECASE)
    if not match:
        return None

    value = float(match.group(1))
    unit = match.group(2).upper()
    if unit == "G":
        value *= 1000
    return int(value) if value.is_integer() else round(value)


def load_intl_route_products(billing_cycle: str | None = None) -> list[dict[str, Any]]:
    products = []
    for product in load_flat_products():
        if product.get("category") != CATEGORY_NAME:
            continue
        if billing_cycle and product.get("billing_cycle") != billing_cycle:
            continue
        bandwidth_mbps = extract_product_bandwidth_mbps(product)
        if bandwidth_mbps is None:
            continue
        item = dict(product)
        item["bandwidth_mbps"] = bandwidth_mbps
        products.append(item)

    products.sort(
        key=lambda item: (
            item["bandwidth_mbps"],
            safe_number(item.get("sale_price")) or float("inf"),
            display_text(item.get("product_id")),
        )
    )
    return products


def resolve_billing_cycle(parsed: dict[str, Any]) -> dict[str, Any]:
    cycles = parsed.get("billing_cycles") or []
    service_period = parsed.get("service_period") or {}
    service_months = service_period.get("months")

    if "年付" in cycles:
        return {
            "billing_cycle": "年付",
            "source": "explicit",
            "reason": "客户明确提到年付或包年。",
        }
    if "月付" in cycles:
        return {
            "billing_cycle": "月付",
            "source": "explicit",
            "reason": "客户明确提到月付、试用或一个月周期。",
        }
    if service_months is not None and service_months >= 12:
        return {
            "billing_cycle": "年付",
            "source": "inferred",
            "reason": "客户提出一年及以上使用周期，国际路由优化优先按年付套餐核算。",
        }
    if service_period.get("trial_requested") or service_months == 1:
        return {
            "billing_cycle": "月付",
            "source": "inferred",
            "reason": "客户有试用或一个月周期诉求，默认按月付核算。",
        }
    return {
        "billing_cycle": DEFAULT_BILLING_CYCLE,
        "source": "default",
        "reason": "客户未明确计费周期，国际路由优化先按月付试算。",
    }


def snap_up_bandwidth(value_mbps: int, available_bandwidths: list[int]) -> int:
    for bandwidth in available_bandwidths:
        if bandwidth >= value_mbps:
            return bandwidth
    return available_bandwidths[-1]


def estimate_bandwidth(parsed: dict[str, Any], available_bandwidths: list[int]) -> dict[str, Any]:
    specs = parsed.get("spec_requirements", {})
    explicit_bandwidths = specs.get("bandwidth_mbps") or []
    if explicit_bandwidths:
        requested = int(max(explicit_bandwidths))
        return {
            "bandwidth_mbps": snap_up_bandwidth(requested, available_bandwidths),
            "requested_mbps": requested,
            "source": "explicit",
            "confidence": "high",
            "reasons": [f"客户已明确带宽需求：{requested}M。"],
        }

    user_count = specs.get("user_count") or specs.get("account_count")
    industries = set(parsed.get("industry_hints") or [])
    pain_points = set(parsed.get("pain_points") or [])
    network = parsed.get("network_requirements") or {}
    reasons = []

    if user_count:
        if user_count <= 3:
            estimated = 2
        elif user_count <= 5:
            estimated = 4
        elif user_count <= 10:
            estimated = 10
        elif user_count <= 20:
            estimated = 20
        elif user_count <= 30:
            estimated = 30
        elif user_count <= 50:
            estimated = 50
        elif user_count <= 100:
            estimated = 100
        elif user_count <= 200:
            estimated = 200
        else:
            estimated = 500
        reasons.append(f"按 {user_count} 人访问海外应用估算带宽。")
    else:
        estimated = 10
        reasons.append("客户未给出人数或带宽，先按 10M 做国际访问优化试算。")

    # 后续可以用这个做行业画像规则，比如下面的游戏
    # if "游戏" in industries and estimated < 10:
    #     estimated = 10
    #     reasons.append("游戏类业务对访问稳定性更敏感，最低按 10M 起估。")
        
    if network.get("has_latency_or_speed_issue") or pain_points:
        reasons.append("客户反馈访问慢、延迟或链路不稳定，优先选择不低于估算值的规格。")

    return {
        "bandwidth_mbps": snap_up_bandwidth(estimated, available_bandwidths),
        "requested_mbps": None,
        "source": "estimated",
        "confidence": "medium" if user_count else "low",
        "reasons": reasons,
    }


def get_budget_upper(parsed: dict[str, Any]) -> int | None:
    budget = parsed.get("budget") or {}
    upper = budget.get("upper_cny")
    return int(upper) if upper is not None else None


def find_bandwidth_neighbors(products: list[dict[str, Any]], target_bandwidth: int) -> dict[str, dict[str, Any] | None]:
    by_bandwidth = {product["bandwidth_mbps"]: product for product in products}
    bandwidths = sorted(by_bandwidth)
    recommended_bandwidth = snap_up_bandwidth(target_bandwidth, bandwidths)
    recommended_index = bandwidths.index(recommended_bandwidth)

    lower = by_bandwidth[bandwidths[recommended_index - 1]] if recommended_index > 0 else None
    recommended = by_bandwidth[recommended_bandwidth]
    higher = by_bandwidth[bandwidths[recommended_index + 1]] if recommended_index + 1 < len(bandwidths) else None
    return {
        "lower": lower,
        "recommended": recommended,
        "higher": higher,
    }


def find_budget_fit_product(products: list[dict[str, Any]], budget_upper: int | None) -> dict[str, Any] | None:
    if budget_upper is None:
        return None
    fit_products = [
        product
        for product in products
        if safe_number(product.get("sale_price")) is not None
        and (safe_number(product.get("sale_price")) or 0) <= budget_upper
    ]
    if not fit_products:
        return None
    return max(
        fit_products,
        key=lambda item: (
            item["bandwidth_mbps"],
            safe_number(item.get("sale_price")) or 0,
        ),
    )


def add_unique_candidate(
    selected: list[tuple[str, str, dict[str, Any]]],
    role_code: str,
    role_name: str,
    product: dict[str, Any] | None,
) -> None:
    if product is None:
        return
    if any(existing.get("product_id") == product.get("product_id") for _, _, existing in selected):
        return
    selected.append((role_code, role_name, product))


def select_candidate_products(
    products: list[dict[str, Any]],
    bandwidth_plan: dict[str, Any],
    budget_upper: int | None,
) -> list[tuple[str, str, dict[str, Any]]]:
    neighbors = find_bandwidth_neighbors(products, bandwidth_plan["bandwidth_mbps"])
    budget_fit_product = find_budget_fit_product(products, budget_upper)
    recommended = neighbors["recommended"]
    recommended_price = safe_number(recommended.get("sale_price")) if recommended else None
    recommended_over_budget = (
        budget_upper is not None
        and recommended_price is not None
        and recommended_price > budget_upper
    )

    selected: list[tuple[str, str, dict[str, Any]]] = []
    if recommended_over_budget:
        add_unique_candidate(selected, "budget_fit", "预算内可试方案", budget_fit_product)

    add_unique_candidate(selected, "lower", "低带宽备选方案", neighbors["lower"])
    add_unique_candidate(selected, "recommended", "推荐核算方案", neighbors["recommended"])
    add_unique_candidate(selected, "higher", "扩容备选方案", neighbors["higher"])

    if len(selected) < MAX_CANDIDATES:
        add_unique_candidate(selected, "budget_fit", "预算内可试方案", budget_fit_product)

    return selected[:MAX_CANDIDATES]


def build_candidate(
    role_code: str,
    role_name: str,
    product: dict[str, Any],
    parsed: dict[str, Any],
    bandwidth_plan: dict[str, Any],
) -> dict[str, Any]:
    budget_upper = get_budget_upper(parsed)
    sale_price = product.get("sale_price")
    sale_price_number = safe_number(sale_price)
    budget_fit = True
    budget_delta = None
    if budget_upper is not None and sale_price_number is not None:
        budget_fit = sale_price_number <= budget_upper
        budget_delta = round(budget_upper - sale_price_number, 2)

    reasons = [
        f"匹配国际路由优化产品线，规格 {product['bandwidth_mbps']}M。",
        f"计费周期按 {product.get('billing_cycle')} 核算。",
    ]
    if role_code == "recommended":
        reasons.append(f"该规格最接近当前带宽判断：{bandwidth_plan['bandwidth_mbps']}M。")
    if role_code == "lower":
        reasons.append("低于推荐规格，适合先小范围试用或压预算。")
    if role_code == "higher":
        reasons.append("高于推荐规格，适合作为体验优先或后续扩容备选。")
    if role_code == "budget_fit":
        reasons.append("该规格在当前预算上限内，可作为预算优先方案。")

    return {
        "role_code": role_code,
        "role_name": role_name,
        "product_id": product.get("product_id"),
        "category": product.get("category"),
        "product_name": product.get("product_name"),
        "brand": product.get("brand"),
        "model": product.get("model"),
        "spec_text": product.get("spec_text"),
        "bandwidth_mbps": product.get("bandwidth_mbps"),
        "billing_cycle": product.get("billing_cycle"),
        "unit": product.get("unit"),
        "sale_price": sale_price,
        "monthly_price": product.get("monthly_price"),
        "annual_price": product.get("annual_price"),
        "price_basis": product.get("price_basis"),
        "discount_options": product.get("discount_options", []),
        "setup_fee": product.get("setup_fee"),
        "budget_fit_on_list_price": budget_fit,
        "budget_delta_on_list_price": budget_delta,
        "description": product.get("description"),
        "match_reasons": reasons,
        "pricing_input": {
            "module_key": MODULE_KEY,
            "product_id": product.get("product_id"),
            "product_name": product.get("product_name"),
            "base_price": sale_price,
            "price_source": {
                "product_id": product.get("product_id"),
                "source_product_no": product.get("source_product_no"),
                "product_name": product.get("product_name"),
                "spec_text": product.get("spec_text"),
                "billing_cycle": product.get("billing_cycle"),
                "sale_price": sale_price,
                "currency": product.get("price_currency", "CNY"),
            },
            "price_basis": product.get("price_basis"),
            "billing_cycle": product.get("billing_cycle"),
            "unit": product.get("unit"),
            "setup_fee": product.get("setup_fee"),
            "discount_options": product.get("discount_options", []),
        },
    }


def build_context_summary(parsed: dict[str, Any]) -> dict[str, Any]:
    specs = parsed.get("spec_requirements", {})
    network = parsed.get("network_requirements", {})
    return {
        "source_locations": network.get("source_locations", []),
        "destination_locations": network.get("destination_locations", []),
        "cross_border_access": network.get("cross_border_access", False),
        "pain_points": parsed.get("pain_points", []),
        "industry_hints": parsed.get("industry_hints", []),
        "user_count": specs.get("user_count"),
        "service_period": parsed.get("service_period"),
        "budget": parsed.get("budget"),
    }


def build_follow_up_questions(parsed: dict[str, Any], bandwidth_plan: dict[str, Any]) -> list[str]:
    questions: list[str] = []
    network = parsed.get("network_requirements") or {}
    specs = parsed.get("spec_requirements") or {}
    if not network.get("destination_locations"):
        questions.append("建议补充主要访问的海外地区或服务器所在地。")
    if not specs.get("bandwidth_mbps"):
        questions.append("当前带宽为系统预估，建议销售继续确认客户期望带宽或并发使用人数。")
    if not parsed.get("billing_cycles"):
        questions.append("当前默认按月付核算，若客户可接受年付，后续可进入 calculator 计算年付授权折扣。")
    if bandwidth_plan.get("confidence") == "low":
        questions.append("当前缺少人数、业务量或明确带宽，方案仅适合作初步沟通。")
    return questions


def build_intl_route_response(query: str) -> dict[str, Any]:
    parsed = parse_requirement_payload(query)
    rule_result = classify_requirement_payload(parsed)
    billing = resolve_billing_cycle(parsed)
    products = load_intl_route_products(billing["billing_cycle"])
    if not products:
        return {
            "raw_query": query,
            "parsed_requirements": parsed,
            "rule_engine": rule_result,
            "module": {
                "module_key": MODULE_KEY,
                "module_name": MODULE_NAME,
                "category": CATEGORY_NAME,
            },
            "message": "当前没有找到国际路由优化产品数据。",
            "candidates": [],
            "ready_for_comparison": False,
        }

    available_bandwidths = sorted({product["bandwidth_mbps"] for product in products})
    bandwidth_plan = estimate_bandwidth(parsed, available_bandwidths)
    budget_upper = get_budget_upper(parsed)
    selected_products = select_candidate_products(products, bandwidth_plan, budget_upper)
    candidates = [
        build_candidate(role_code, role_name, product, parsed, bandwidth_plan)
        for role_code, role_name, product in selected_products
    ]
    recommendation = next(
        (candidate for candidate in candidates if candidate.get("role_code") == "recommended"),
        candidates[0] if candidates else None,
    )

    follow_up_questions = build_follow_up_questions(parsed, bandwidth_plan)
    module_hit = any(item.get("module_key") == MODULE_KEY for item in rule_result.get("routed_modules", []))

    if not module_hit:
        message = "Rule Engine 未将该需求优先路由到国际路由优化，本结果仅作为手动核算参考。"
    elif candidates:
        message = "已完成国际路由优化候选方案核算，可进入方案对比模块。"
    else:
        message = "已命中国际路由优化场景，但当前没有筛出候选方案。"

    return {
        "raw_query": query,
        "parsed_requirements": parsed,
        "rule_engine": rule_result,
        "module": {
            "module_key": MODULE_KEY,
            "module_name": MODULE_NAME,
            "category": CATEGORY_NAME,
            "billing_cycle": billing["billing_cycle"],
            "billing_cycle_source": billing["source"],
            "billing_cycle_reason": billing["reason"],
        },
        "scenario_context": build_context_summary(parsed),
        "bandwidth_plan": bandwidth_plan,
        "available_bandwidths": available_bandwidths,
        "summary": {
            "candidate_count": len(candidates),
            "recommended_product_id": recommendation.get("product_id") if recommendation else None,
            "recommended_pricing_input": recommendation.get("pricing_input") if recommendation else None,
        },
        "recommendation": recommendation,
        "candidates": candidates,
        "follow_up_questions": follow_up_questions,
        "ready_for_comparison": len(candidates) >= 2,
        "ready_for_calculator": len(candidates) > 0,
        "message": message,
    }


def render_candidate(candidate: dict[str, Any]) -> list[str]:
    budget_text = "预算内" if candidate.get("budget_fit_on_list_price") else "超出预算"
    discount_note = ""
    if candidate.get("discount_options"):
        discount_note = "；含可用折扣规则，后续由 calculator 统一计算"

    lines = [
        f"{candidate['role_name']}：{candidate['product_name']}（{candidate['product_id']}）",
        f"  规格：{candidate['bandwidth_mbps']}M，计费：{candidate['billing_cycle']}，标价：{format_money(candidate.get('sale_price'))}/{display_text(candidate.get('unit'))}（{budget_text}）{discount_note}",
    ]
    if candidate.get("budget_delta_on_list_price") is not None:
        delta = candidate["budget_delta_on_list_price"]
        if delta >= 0:
            lines.append(f"  预算余量：{format_money(delta)}")
        else:
            lines.append(f"  超预算：{format_money(abs(delta))}")
    if candidate.get("match_reasons"):
        lines.append(f"  核算依据：{'；'.join(candidate['match_reasons'])}")
    if candidate.get("description"):
        lines.append(f"  产品说明：{candidate['description']}")
    return lines


def render_intl_route_response(response: dict[str, Any]) -> str:
    lines = ["国际路由优化核算结果", response["message"]]
    module = response.get("module", {})
    context = response.get("scenario_context", {})
    bandwidth_plan = response.get("bandwidth_plan", {})

    source_locations = "、".join(context.get("source_locations") or []) or "-"
    destination_locations = "、".join(context.get("destination_locations") or []) or "-"
    pain_points = "、".join(context.get("pain_points") or []) or "-"
    budget = context.get("budget") or {}
    budget_text = format_money(budget.get("upper_cny")) + "以内" if budget.get("upper_cny") is not None else "-"

    lines.append(
        f"场景摘要：来源 {source_locations} -> 目标 {destination_locations} | 痛点 {pain_points} | "
        f"人数 {display_text(context.get('user_count'))} | 预算 {budget_text}"
    )
    lines.append(
        f"计费周期：{module.get('billing_cycle')}（{module.get('billing_cycle_reason')}）"
    )
    if bandwidth_plan:
        lines.append(
            "带宽判断："
            f"{bandwidth_plan.get('bandwidth_mbps')}M，来源 {bandwidth_plan.get('source')}，"
            f"置信度 {bandwidth_plan.get('confidence')}。"
        )
        if bandwidth_plan.get("reasons"):
            lines.append("判断依据：" + "；".join(bandwidth_plan["reasons"]))

    candidates = response.get("candidates", [])
    if candidates:
        lines.append("")
        lines.append("候选方案：")
        for candidate in candidates:
            lines.extend(render_candidate(candidate))
            lines.append("")

    if response.get("follow_up_questions"):
        lines.append("待确认信息：")
        for question in response["follow_up_questions"]:
            lines.append(f"- {question}")

    if candidates:
        lines.append("后续流转：候选方案进入 Compare 做方案对比，折扣与总价交给 calculator 统一计算。")

    return "\n".join(lines).rstrip()


@tool
def estimate_intl_route_optimization(query: str) -> str:
    """
    国际路由优化核算模块。
    用于在 Rule Engine 命中“访问海外应用 / 国际访问优化”后，根据客户需求估算带宽、
    筛选国际路由优化候选方案，并输出可交给 Compare 和 calculator 的候选项。
    """
    response = build_intl_route_response(query)
    return render_intl_route_response(response)
