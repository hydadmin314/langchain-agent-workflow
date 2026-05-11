import json
from typing import Any

from langchain.tools import tool

from tools.product_tool import display_text, load_flat_products, normalize_text
from tools.requirement_parser import parse_requirement_payload


MAX_RECOMMENDATIONS = 3


def resolve_discount_option(product: dict[str, Any], parsed: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    notes: list[str] = []
    discount_options = product.get("discount_options", [])
    if not discount_options:
        return None, notes

    contract_modes = parsed.get("contract_modes", [])
    if contract_modes:
        contract_mode_map = {
            "ICT签约": "ict签约",
            "直签": "直签",
        }
        for contract_mode in contract_modes:
            expected_label = contract_mode_map.get(contract_mode, contract_mode).lower()
            matched = next(
                (
                    option
                    for option in discount_options
                    if expected_label in normalize_text(option.get("label"))
                ),
                None,
            )
            if matched:
                return matched, notes
        available_labels = "、".join(display_text(option.get("label")) for option in discount_options)
        notes.append(f"当前产品未配置“{'、'.join(contract_modes)}”专属规则，可用折扣为：{available_labels}")

    if parsed.get("needs_discount_price"):
        best_option = min(
            discount_options,
            key=lambda option: float(option.get("discounted_price") or option.get("base_price") or float("inf")),
        )
        return best_option, notes

    return None, notes


def compute_effective_pricing(product: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    notes: list[str] = []
    selected_discount, discount_notes = resolve_discount_option(product, parsed)
    notes.extend(discount_notes)

    display_price = product.get("sale_price")
    price_basis = product.get("price_basis")
    billing_cycle = product.get("billing_cycle")

    if selected_discount is not None:
        display_price = selected_discount.get("discounted_price")
        price_basis = "discounted_" + display_text(selected_discount.get("base_field"), "annual_price")

    setup_fee = product.get("setup_fee") or 0
    initial_total = None
    if display_price is not None:
        initial_total = display_price + setup_fee

    return {
        "display_price": display_price,
        "price_basis": price_basis,
        "billing_cycle": billing_cycle,
        "setup_fee": product.get("setup_fee"),
        "initial_total": initial_total,
        "selected_discount": selected_discount,
        "notes": notes,
    }


def matches_budget(effective_pricing: dict[str, Any], budget: dict[str, Any] | None) -> bool:
    if budget is None:
        return True

    initial_total = effective_pricing.get("initial_total")
    if initial_total is None:
        return True

    upper = budget.get("upper_cny")
    lower = budget.get("lower_cny")
    if upper is not None and initial_total > upper:
        return False
    if lower is not None and initial_total < lower:
        return False
    return True


def product_matches_filters(product: dict[str, Any], parsed: dict[str, Any]) -> bool:
    filters = parsed.get("product_filters", {})

    categories = filters.get("categories") or []
    if categories and product.get("category") not in categories:
        return False

    brands = filters.get("brands") or []
    if brands and display_text(product.get("brand")) not in brands:
        return False

    billing_cycles = filters.get("billing_cycles") or []
    if billing_cycles and product.get("billing_cycle") not in billing_cycles:
        return False

    model_keywords = filters.get("model_keywords") or []
    if model_keywords and not any(model.lower() in normalize_text(product.get("model")) for model in model_keywords):
        return False

    spec_terms = filters.get("spec_terms") or []
    if spec_terms:
        spec_blob = " ".join(
            [
                display_text(product.get("spec_text"), ""),
                display_text(product.get("description"), ""),
                display_text(product.get("product_name"), ""),
            ]
        ).lower()
        for spec_term in spec_terms:
            if spec_term.lower() not in spec_blob:
                return False

    feature_keywords = filters.get("feature_keywords") or []
    if feature_keywords:
        feature_blob = " ".join(
            [
                display_text(product.get("description"), ""),
                display_text(product.get("notes"), ""),
                display_text(product.get("product_name"), ""),
            ]
        ).lower()
        for feature in feature_keywords:
            if feature.lower() not in feature_blob:
                return False

    return True


def build_match_reasons(product: dict[str, Any], parsed: dict[str, Any], effective_pricing: dict[str, Any]) -> list[str]:
    reasons = []

    if parsed.get("target_categories"):
        reasons.append(f"匹配分类：{product.get('category')}")
    if product.get("model"):
        reasons.append(f"型号：{product.get('model')}")
    if product.get("spec_text"):
        reasons.append(f"规格：{product.get('spec_text')}")
    if parsed.get("feature_keywords") and product.get("description"):
        reasons.append(f"功能描述覆盖：{product.get('description')}")
    if effective_pricing.get("selected_discount"):
        reasons.append(
            f"可用折扣：{effective_pricing['selected_discount'].get('label')}，折后 {effective_pricing['selected_discount'].get('discounted_price')} 元"
        )
    elif effective_pricing.get("display_price") is not None:
        reasons.append(f"当前价格：{effective_pricing['display_price']} 元")

    return reasons[:4]


def score_product_for_recommendation(product: dict[str, Any], parsed: dict[str, Any], effective_pricing: dict[str, Any]) -> int:
    score = 0

    if parsed.get("target_categories") and product.get("category") in parsed["target_categories"]:
        score += 60
    if parsed.get("brand_preferences") and display_text(product.get("brand")) in parsed["brand_preferences"]:
        score += 20
    if parsed.get("billing_cycles") and product.get("billing_cycle") in parsed["billing_cycles"]:
        score += 18
    if parsed.get("model_keywords") and any(
        model.lower() in normalize_text(product.get("model")) for model in parsed["model_keywords"]
    ):
        score += 30

    spec_requirements = parsed.get("spec_requirements", {})
    spec_text_norm = normalize_text(product.get("spec_text"))
    if spec_requirements.get("port_count") is not None and f"{spec_requirements['port_count']}口" in spec_text_norm + normalize_text(product.get("description")):
        score += 26
    if spec_requirements.get("participant_count") is not None and f"{spec_requirements['participant_count']}方" in spec_text_norm:
        score += 26
    for bandwidth_term in spec_requirements.get("bandwidth_terms", []):
        if bandwidth_term.lower() in spec_text_norm:
            score += 24

    feature_keywords = parsed.get("feature_keywords", [])
    if feature_keywords:
        feature_blob = " ".join(
            [
                normalize_text(product.get("description")),
                normalize_text(product.get("notes")),
                normalize_text(product.get("product_name")),
            ]
        )
        for feature in feature_keywords:
            if feature.lower() in feature_blob:
                score += 12

    if effective_pricing.get("selected_discount") is not None:
        score += 12
    if matches_budget(effective_pricing, parsed.get("budget")):
        score += 15
    elif parsed.get("budget") is not None:
        score -= 35

    price_preference = parsed.get("price_preference")
    initial_total = effective_pricing.get("initial_total")
    if initial_total is not None:
        if price_preference == "lowest_price_first":
            score += max(0, 100000 - int(initial_total)) // 2000
        elif price_preference == "quality_first":
            score += min(20, int(initial_total) // 5000)
        elif price_preference == "value_first":
            score += 10

    return score


def build_candidate(product: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    effective_pricing = compute_effective_pricing(product, parsed)
    score = score_product_for_recommendation(product, parsed, effective_pricing)
    budget_fit = matches_budget(effective_pricing, parsed.get("budget"))

    return {
        "product_id": product.get("product_id"),
        "category": product.get("category"),
        "product_name": product.get("product_name"),
        "brand": product.get("brand"),
        "model": product.get("model"),
        "spec_text": product.get("spec_text"),
        "description": product.get("description"),
        "billing_cycle": product.get("billing_cycle"),
        "unit": product.get("unit"),
        "sale_price": product.get("sale_price"),
        "min_sale_price": product.get("min_sale_price"),
        "setup_fee": product.get("setup_fee"),
        "effective_pricing": effective_pricing,
        "budget_fit": budget_fit,
        "score": score,
        "match_reasons": build_match_reasons(product, parsed, effective_pricing),
    }


def rank_candidates(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for product in load_flat_products():
        if not product_matches_filters(product, parsed):
            continue
        candidates.append(build_candidate(product, parsed))

    candidates.sort(
        key=lambda item: (
            not item["budget_fit"],
            -item["score"],
            float(item["effective_pricing"].get("initial_total") or float("inf")),
            display_text(item.get("product_id")),
        )
    )
    return candidates


def choose_package_tiers(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidates:
        return []

    budget_fit_candidates = [item for item in candidates if item["budget_fit"]]
    pool = budget_fit_candidates or candidates

    sorted_by_price = sorted(
        pool,
        key=lambda item: (
            float(item["effective_pricing"].get("initial_total") or float("inf")),
            display_text(item.get("product_id")),
        ),
    )
    sorted_by_score = sorted(
        pool,
        key=lambda item: (
            -item["score"],
            float(item["effective_pricing"].get("initial_total") or float("inf")),
            display_text(item.get("product_id")),
        ),
    )
    sorted_by_premium = sorted(
        pool,
        key=lambda item: (
            -float(item["effective_pricing"].get("initial_total") or 0),
            -item["score"],
            display_text(item.get("product_id")),
        ),
    )

    slots = [
        ("economy", "低预算方案", sorted_by_price),
        ("balanced", "推荐方案", sorted_by_score),
        ("premium", "高配置方案", sorted_by_premium),
    ]

    selected_ids: set[str] = set()
    recommendations = []
    for tier_code, tier_name, source in slots:
        chosen = next((item for item in source if item["product_id"] not in selected_ids), None)
        if chosen is None:
            continue
        selected_ids.add(chosen["product_id"])
        recommendations.append(
            {
                "tier_code": tier_code,
                "tier_name": tier_name,
                "package_type": "single_product",
                "product": chosen,
            }
        )
        if len(recommendations) >= MAX_RECOMMENDATIONS:
            break

    return recommendations


def build_summary(parsed: dict[str, Any], candidates: list[dict[str, Any]], recommendations: list[dict[str, Any]]) -> dict[str, Any]:
    budget_fit_count = sum(1 for item in candidates if item["budget_fit"])
    return {
        "candidate_count": len(candidates),
        "budget_fit_count": budget_fit_count,
        "has_budget_fit": budget_fit_count > 0,
        "recommended_count": len(recommendations),
        "primary_categories": parsed.get("target_categories", []),
        "ready_for_recommendation": parsed.get("ready_for_recommendation", False),
    }


def build_recommendation_response(query: str) -> dict[str, Any]:
    parsed = parse_requirement_payload(query)
    candidates = rank_candidates(parsed)
    recommendations = choose_package_tiers(candidates)

    response = {
        "raw_query": query,
        "parsed_requirements": parsed,
        "summary": build_summary(parsed, candidates, recommendations),
        "follow_up_questions": parsed.get("follow_up_questions", []),
        "recommendations": recommendations,
    }

    if not recommendations:
        response["message"] = "当前没有找到可推荐的产品，请调整条件或补充需求。"
    elif parsed.get("budget") is not None and not response["summary"]["has_budget_fit"]:
        response["message"] = "已找到匹配候选，但当前候选都超出预算，可考虑放宽预算或调整规格。"
    elif not parsed.get("ready_for_recommendation", False):
        response["message"] = "当前推荐为预选结果，补充缺失信息后可以得到更稳定的推荐。"
    else:
        response["message"] = "已基于当前需求生成可执行的推荐结果。"

    return response


def format_money(value: Any) -> str:
    if value is None:
        return "-"
    return f"{value} 元"


def format_budget_text(budget: dict[str, Any] | None) -> str | None:
    if not budget:
        return None
    upper = budget.get("upper_cny")
    lower = budget.get("lower_cny")
    if upper is not None:
        return f"预算上限 {upper} 元"
    if lower is not None:
        return f"预算下限 {lower} 元"
    return None


def render_recommendation_entry(recommendation: dict[str, Any]) -> list[str]:
    product = recommendation["product"]
    effective_pricing = product.get("effective_pricing", {})
    selected_discount = effective_pricing.get("selected_discount")

    lines = [
        f"{recommendation['tier_name']}：{product['product_name']}（{product['product_id']}）",
        f"  品牌/型号：{display_text(product.get('brand'))} / {display_text(product.get('model'))}",
        f"  规格：{display_text(product.get('spec_text'))}",
        f"  计费：{display_text(product.get('billing_cycle'))}，单位 {display_text(product.get('unit'))}",
        f"  标价：{format_money(product.get('sale_price'))}",
    ]

    if product.get("min_sale_price") is not None:
        lines.append(f"  最低销售价：{format_money(product.get('min_sale_price'))}")
    if product.get("setup_fee") is not None:
        lines.append(f"  调试费：{format_money(product.get('setup_fee'))}")
    if effective_pricing.get("display_price") is not None:
        lines.append(f"  本次推荐计价：{format_money(effective_pricing.get('display_price'))}")
    if effective_pricing.get("initial_total") is not None:
        lines.append(f"  首期合计：{format_money(effective_pricing.get('initial_total'))}")
    if selected_discount:
        lines.append(
            f"  折扣说明：{selected_discount.get('label')}，折后 {format_money(selected_discount.get('discounted_price'))}"
        )
    if product.get("description"):
        lines.append(f"  适配说明：{product.get('description')}")
    if product.get("match_reasons"):
        lines.append(f"  推荐理由：{'；'.join(product['match_reasons'])}")
    if effective_pricing.get("notes"):
        lines.append(f"  备注：{'；'.join(effective_pricing['notes'])}")

    return lines


def render_recommendation_response(response: dict[str, Any]) -> str:
    parsed = response["parsed_requirements"]
    summary = response["summary"]
    recommendations = response["recommendations"]

    lines = ["推荐结果"]
    requirement_parts = []
    if parsed.get("target_categories"):
        requirement_parts.append("分类：" + "、".join(parsed["target_categories"]))
    if parsed.get("billing_cycles"):
        requirement_parts.append("计费周期：" + "、".join(parsed["billing_cycles"]))
    if parsed.get("contract_modes"):
        requirement_parts.append("签约方式：" + "、".join(parsed["contract_modes"]))
    budget_text = format_budget_text(parsed.get("budget"))
    if budget_text:
        requirement_parts.append(budget_text)
    if parsed.get("product_filters", {}).get("spec_terms"):
        requirement_parts.append("规格：" + "、".join(parsed["product_filters"]["spec_terms"]))
    if parsed.get("feature_keywords"):
        requirement_parts.append("需求点：" + "、".join(parsed["feature_keywords"]))

    if requirement_parts:
        lines.append("需求摘要：" + " | ".join(requirement_parts))

    lines.append(response["message"])

    if recommendations:
        lines.append("")
        for recommendation in recommendations:
            lines.extend(render_recommendation_entry(recommendation))
            lines.append("")

    if summary.get("candidate_count") and not summary.get("recommended_count"):
        lines.append(f"候选数量：{summary['candidate_count']}，但暂未形成推荐结果。")

    if response.get("follow_up_questions"):
        lines.append("建议补充：")
        for question in response["follow_up_questions"]:
            lines.append(f"- {question}")

    return "\n".join(line for line in lines if line is not None).rstrip()


@tool
def recommend_packages(query: str) -> str:
    """
    用于根据客户需求自动推荐产品方案或套餐候选项。
    适用于“帮我推荐一个门店语音网关”“给我一个年付 10M 的国际优化方案”“Zoom 300方怎么选”等场景。

    Args:
        query: 用户的自然语言需求描述。
    """
    response = build_recommendation_response(query)
    return render_recommendation_response(response)
