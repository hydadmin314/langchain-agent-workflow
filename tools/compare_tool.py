import json
from typing import Any

from langchain.tools import tool

from tools.product_tool import display_text
from tools.recommend_tool import choose_package_tiers, rank_candidates
from tools.requirement_parser import parse_requirement_payload


MAX_COMPARE_ITEMS = 3


def build_comparison_item(candidate: dict[str, Any], tier_name: str | None = None) -> dict[str, Any]:
    effective_pricing = candidate.get("effective_pricing", {})
    selected_discount = effective_pricing.get("selected_discount")

    return {
        "tier_name": tier_name,
        "product_id": candidate.get("product_id"),
        "category": candidate.get("category"),
        "product_name": candidate.get("product_name"),
        "brand": candidate.get("brand"),
        "model": candidate.get("model"),
        "spec_text": candidate.get("spec_text"),
        "billing_cycle": candidate.get("billing_cycle"),
        "unit": candidate.get("unit"),
        "sale_price": candidate.get("sale_price"),
        "min_sale_price": candidate.get("min_sale_price"),
        "setup_fee": candidate.get("setup_fee"),
        "effective_price": effective_pricing.get("display_price"),
        "effective_price_basis": effective_pricing.get("price_basis"),
        "initial_total": effective_pricing.get("initial_total"),
        "budget_fit": candidate.get("budget_fit"),
        "score": candidate.get("score"),
        "description": candidate.get("description"),
        "match_reasons": candidate.get("match_reasons", []),
        "pricing_notes": effective_pricing.get("notes", []),
        "selected_discount": selected_discount,
    }


def select_comparison_items(parsed: dict[str, Any], candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    if not candidates:
        return [], "no_candidates"

    explicit_compare = (
        len(parsed.get("model_keywords", [])) >= 2
        or len(parsed.get("product_filters", {}).get("spec_terms", [])) > 0
        or parsed.get("intent") == "comparison"
    )

    if explicit_compare and len(candidates) <= MAX_COMPARE_ITEMS:
        items = [build_comparison_item(candidate) for candidate in candidates[:MAX_COMPARE_ITEMS]]
        return items, "direct_candidates"

    tiered_recommendations = choose_package_tiers(candidates)
    if tiered_recommendations:
        items = [
            build_comparison_item(recommendation["product"], recommendation.get("tier_name"))
            for recommendation in tiered_recommendations[:MAX_COMPARE_ITEMS]
        ]
        return items, "recommended_tiers"

    return [build_comparison_item(candidate) for candidate in candidates[:MAX_COMPARE_ITEMS]], "fallback_top_candidates"


def format_discount_advantage(item: dict[str, Any]) -> str | None:
    discount = item.get("selected_discount")
    if not discount:
        return None
    return f"{discount.get('label')} -> {discount.get('discounted_price')} 元"


def safe_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def choose_winner(items: list[dict[str, Any]], key_name: str, prefer: str = "min") -> dict[str, Any] | None:
    comparable = [item for item in items if safe_number(item.get(key_name)) is not None]
    if not comparable:
        return None

    if prefer == "max":
        return max(comparable, key=lambda item: safe_number(item.get(key_name)) or float("-inf"))
    return min(comparable, key=lambda item: safe_number(item.get(key_name)) or float("inf"))


def build_comparison_dimensions(items: list[dict[str, Any]]) -> dict[str, Any]:
    best_price = choose_winner(items, "effective_price", "min")
    best_initial_total = choose_winner(items, "initial_total", "min")
    best_min_sale_price = choose_winner(items, "min_sale_price", "min")
    top_match = choose_winner(items, "score", "max")

    return {
        "lowest_effective_price": best_price.get("product_id") if best_price else None,
        "lowest_initial_total": best_initial_total.get("product_id") if best_initial_total else None,
        "lowest_min_sale_price": best_min_sale_price.get("product_id") if best_min_sale_price else None,
        "highest_match_score": top_match.get("product_id") if top_match else None,
    }


def build_observations(items: list[dict[str, Any]], parsed: dict[str, Any]) -> list[str]:
    observations = []
    if len(items) < 2:
        return observations

    best_initial_total = choose_winner(items, "initial_total", "min")
    if best_initial_total is not None:
        observations.append(
            f"首期投入最低的是 {best_initial_total['product_name']}（{best_initial_total['product_id']}），"
            f"总计 {best_initial_total['initial_total']} 元。"
        )

    budget_fit_items = [item for item in items if item.get("budget_fit")]
    if parsed.get("budget") is not None:
        if budget_fit_items:
            observations.append(
                f"共有 {len(budget_fit_items)} 个候选满足当前预算。"
            )
        else:
            observations.append("当前参与比对的候选都超出预算。")

    discounted_items = [item for item in items if item.get("selected_discount")]
    if discounted_items:
        discount_text = "；".join(
            f"{item['product_id']} 可用 {format_discount_advantage(item)}"
            for item in discounted_items
            if format_discount_advantage(item)
        )
        if discount_text:
            observations.append(f"折扣差异：{discount_text}")

    billing_cycles = {display_text(item.get("billing_cycle")) for item in items}
    if len(billing_cycles) > 1:
        observations.append(f"当前候选包含不同计费周期：{'、'.join(sorted(billing_cycles))}。")

    specs = {display_text(item.get("spec_text")) for item in items}
    if len(specs) > 1:
        observations.append("候选规格不同，适合直接做场景与预算并行对比。")

    return observations


def build_recommended_choice(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not items:
        return None

    sorted_items = sorted(
        items,
        key=lambda item: (
            not item.get("budget_fit", False),
            -(item.get("score") or 0),
            safe_number(item.get("initial_total")) if safe_number(item.get("initial_total")) is not None else float("inf"),
            display_text(item.get("product_id")),
        ),
    )
    winner = sorted_items[0]
    return {
        "product_id": winner.get("product_id"),
        "tier_name": winner.get("tier_name"),
        "reason": winner.get("match_reasons", []),
    }


def build_comparison_response(query: str) -> dict[str, Any]:
    parsed = parse_requirement_payload(query)
    candidates = rank_candidates(parsed)
    comparison_items, comparison_basis = select_comparison_items(parsed, candidates)

    response = {
        "raw_query": query,
        "parsed_requirements": parsed,
        "comparison_basis": comparison_basis,
        "summary": {
            "candidate_count": len(candidates),
            "compared_count": len(comparison_items),
            "ready_for_comparison": len(comparison_items) >= 2,
        },
        "follow_up_questions": parsed.get("follow_up_questions", []),
        "comparison_dimensions": build_comparison_dimensions(comparison_items),
        "observations": build_observations(comparison_items, parsed),
        "recommended_choice": build_recommended_choice(comparison_items),
        "compared_items": comparison_items,
    }

    if not comparison_items:
        response["message"] = "当前没有找到可比对的候选，请补充型号、规格、分类或预算。"
    elif len(comparison_items) == 1:
        response["message"] = "当前只找到 1 个候选，建议再补一个型号、规格或品牌后再做对比。"
    elif parsed.get("follow_up_questions"):
        response["message"] = "已生成初步比对结果，补充缺失信息后可以得到更稳定的对比结论。"
    else:
        response["message"] = "已生成可直接用于销售沟通的方案比对结果。"

    return response


def format_money(value: Any) -> str:
    if value is None:
        return "-"
    return f"{value} 元"


def render_comparison_item(item: dict[str, Any]) -> list[str]:
    selected_discount = item.get("selected_discount")
    lines = [
        f"{item['product_name']}（{item['product_id']}）",
        f"  品牌/型号：{display_text(item.get('brand'))} / {display_text(item.get('model'))}",
        f"  规格：{display_text(item.get('spec_text'))}",
        f"  计费：{display_text(item.get('billing_cycle'))}，单位 {display_text(item.get('unit'))}",
        f"  标价：{format_money(item.get('sale_price'))}",
        f"  首期合计：{format_money(item.get('initial_total'))}",
    ]

    if item.get("min_sale_price") is not None:
        lines.append(f"  最低销售价：{format_money(item.get('min_sale_price'))}")
    if item.get("setup_fee") is not None:
        lines.append(f"  调试费：{format_money(item.get('setup_fee'))}")
    if item.get("effective_price") is not None:
        lines.append(f"  本次对比计价：{format_money(item.get('effective_price'))}")
    if selected_discount:
        lines.append(
            f"  折扣说明：{selected_discount.get('label')}，折后 {format_money(selected_discount.get('discounted_price'))}"
        )
    if item.get("description"):
        lines.append(f"  场景说明：{item.get('description')}")
    if item.get("match_reasons"):
        lines.append(f"  匹配理由：{'；'.join(item['match_reasons'])}")
    if item.get("pricing_notes"):
        lines.append(f"  备注：{'；'.join(item['pricing_notes'])}")

    return lines


def render_comparison_response(response: dict[str, Any]) -> str:
    parsed = response["parsed_requirements"]
    items = response["compared_items"]

    lines = ["方案对比"]
    requirement_parts = []
    if parsed.get("target_categories"):
        requirement_parts.append("分类：" + "、".join(parsed["target_categories"]))
    if parsed.get("model_keywords"):
        requirement_parts.append("型号：" + "、".join(parsed["model_keywords"]))
    if parsed.get("product_filters", {}).get("spec_terms"):
        requirement_parts.append("规格：" + "、".join(parsed["product_filters"]["spec_terms"]))
    if parsed.get("billing_cycles"):
        requirement_parts.append("计费周期：" + "、".join(parsed["billing_cycles"]))

    if requirement_parts:
        lines.append("对比范围：" + " | ".join(requirement_parts))

    lines.append(response["message"])

    recommended_choice = response.get("recommended_choice")
    if recommended_choice:
        lines.append(
            f"优先建议：{recommended_choice['product_id']}"
            + (f"（{recommended_choice['tier_name']}）" if recommended_choice.get("tier_name") else "")
        )

    if response.get("observations"):
        lines.append("核心结论：")
        for observation in response["observations"]:
            lines.append(f"- {observation}")

    if items:
        lines.append("")
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
    用于对多个候选产品或套餐方案做结构化比对。
    适用于“HX404G 和 SMG1000-D4S 对比”“4口语音网关对比一下”“Zoom 300方两个方案区别”等场景。

    Args:
        query: 用户的自然语言比对需求。
    """
    response = build_comparison_response(query)
    return render_comparison_response(response)
