import json
import re
from typing import Any

from langchain.tools import tool

from tools.sales_tools.product_tool import display_text, load_flat_products, normalize_text
from tools.sales_tools.requirement_parser import parse_requirement_payload


ISP_CATEGORY = "ISP"
DEFAULT_COMPARE_LIMIT = 4


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


def _bandwidth_to_mbps(value: str, unit: str) -> int:
    number = float(value)
    if unit.lower() == "g":
        number *= 1000
    return int(number) if number.is_integer() else round(number)


def _extract_bandwidth_pair(product: dict[str, Any]) -> tuple[int | None, int | None]:
    source = " ".join(
        display_text(product.get(field), "")
        for field in ("spec_detail", "spec_text", "product_name")
    )
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*([MG])\s*/\s*(\d+(?:\.\d+)?)\s*([MG])",
        source,
        re.IGNORECASE,
    )
    if not match:
        return None, None
    first = _bandwidth_to_mbps(match.group(1), match.group(2))
    second = _bandwidth_to_mbps(match.group(3), match.group(4))
    return first, second


def _is_recurring_package(product: dict[str, Any]) -> bool:
    if product.get("category") != ISP_CATEGORY:
        return False
    if product.get("billing_cycle") not in {"月付", "年付"}:
        return False
    spec = normalize_text(product.get("spec_detail") or product.get("spec_text"))
    name = normalize_text(product.get("product_name"))
    if "初装费" in spec or "ipv4" in spec:
        return False
    return bool(re.search(r"\d+(?:\.\d+)?\s*[mg]\s*/\s*\d+(?:\.\d+)?\s*[mg]", spec + " " + name, re.IGNORECASE))


def _load_isp_products() -> list[dict[str, Any]]:
    products = [product for product in load_flat_products() if _is_recurring_package(product)]
    return sorted(
        products,
        key=lambda item: (
            display_text(item.get("product_name")),
            _extract_bandwidth_pair(item)[0] or 0,
            display_text(item.get("billing_cycle")),
            _number(item.get("sale_price"), float("inf")),
        ),
    )


def _build_setup_fee_lookup() -> dict[tuple[str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for product in load_flat_products():
        if product.get("category") != ISP_CATEGORY:
            continue
        spec = normalize_text(product.get("spec_detail") or product.get("spec_text"))
        if "初装费" not in spec:
            continue
        billing_cycle = display_text(product.get("billing_cycle"), "")
        if not billing_cycle:
            continue
        lookup[(display_text(product.get("product_name")), billing_cycle)] = {
            "amount": _number(product.get("sale_price")),
            "product_id": product.get("product_id"),
            "source_product_no": product.get("source_product_no"),
            "product_name": product.get("product_name"),
            "spec_text": product.get("spec_text"),
            "spec_detail": product.get("spec_detail"),
            "billing_cycle": product.get("billing_cycle"),
            "sale_price": product.get("sale_price"),
            "currency": product.get("price_currency", "CNY"),
        }
    return lookup


def _resolve_required_bandwidth(parsed: dict[str, Any]) -> tuple[int | None, str | None]:
    values = parsed.get("spec_requirements", {}).get("bandwidth_mbps") or []
    if values:
        return int(max(values)), "客户明确提出带宽规格"

    network = parsed.get("network_requirements", {})
    user_count = parsed.get("spec_requirements", {}).get("user_count")
    if network.get("requires_large_bandwidth"):
        return 100, "客户提出大带宽但未给规格，按 100M 起步预估"
    if user_count:
        if user_count <= 20:
            return 20, "未给明确带宽，按用户规模预估 20M"
        if user_count <= 50:
            return 50, "未给明确带宽，按用户规模预估 50M"
        return 100, "未给明确带宽，按用户规模预估 100M"
    return None, None


def _resolve_line_preference(parsed: dict[str, Any]) -> tuple[str | None, str | None]:
    query_norm = normalize_text(parsed.get("raw_query"))
    if any(term in query_norm for term in ("国际精品", "国际互联网", "国际专线", "跨境", "海外")):
        return "international_premium", "识别到国际精品/海外访问相关诉求"
    if any(term in query_norm for term in ("国内精品", "精品互联网", "公网ip", "固定ip", "静态ip")):
        return "domestic_premium", "识别到精品互联网或固定公网 IP 诉求"
    if any(term in query_norm for term in ("经济", "低价", "便宜", "省钱")):
        return "economy", "识别到经济型/低预算诉求"
    if any(term in query_norm for term in ("尊享", "高稳定", "稳定", "质量")):
        return "premium_access", "识别到尊享或稳定性优先诉求"
    if parsed.get("network_requirements", {}).get("requires_public_ip") or parsed.get("network_requirements", {}).get("requires_fixed_ip"):
        return "domestic_premium", "固定公网 IP 诉求优先匹配含 IP 的精品互联网"
    return None, None


def _line_kind(product: dict[str, Any]) -> str:
    name = display_text(product.get("product_name"), "")
    if "国际精品" in name:
        return "international_premium"
    if "国内精品" in name or product.get("product_name") == "互联网专线":
        return "domestic_premium"
    if "尊享" in name:
        return "premium_access"
    if "经济" in name or "公寓" in name:
        return "economy"
    return "standard"


def _ip_capacity(product: dict[str, Any]) -> int:
    blob = " ".join(
        display_text(product.get(field), "")
        for field in ("description", "spec_text", "product_name")
    )
    numbers = [int(match.group(1)) for match in re.finditer(r"(\d+)\s*个?可用\s*IP(?:v4)?", blob, re.IGNORECASE)]
    numbers.extend(int(match.group(1)) for match in re.finditer(r"包含\s*(\d+)", blob))
    return max(numbers) if numbers else 0


def _ip_description(product: dict[str, Any]) -> str:
    description = display_text(product.get("description"), "").strip()
    return description if "ip" in description.lower() else ""


def _setup_fee_amount(setup_fee_source: dict[str, Any] | None) -> float:
    if not setup_fee_source:
        return 0
    return _number(setup_fee_source.get("amount"))


def _ranking_bandwidth(
    down_mbps: int | None,
    up_mbps: int | None,
    required_bandwidth: int | None,
    needs_dedicated_quality: bool,
) -> int | None:
    values = [value for value in (down_mbps, up_mbps) if value is not None]
    if not values:
        return None
    if needs_dedicated_quality:
        return min(values)
    if required_bandwidth is None:
        return max(values)
    return min(values, key=lambda value: (abs(value - required_bandwidth), value))


def _bandwidth_distance(ranking_bandwidth: int | None, required_bandwidth: int | None) -> int:
    if ranking_bandwidth is None or required_bandwidth is None:
        return 0
    return abs(ranking_bandwidth - required_bandwidth)


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


def _build_pricing_input(
    product: dict[str, Any],
    setup_fee: float,
    setup_fee_source: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "pricing_stage": "module_pricing_input",
        "calculator_required": True,
        "module_key": "isp_private_line",
        "product_id": product.get("product_id"),
        "product_name": product.get("product_name"),
        "billing_cycle": product.get("billing_cycle"),
        "currency": product.get("price_currency", "CNY"),
        "quantity": 1,
        "unit": product.get("unit"),
        "unit_price": product.get("sale_price"),
        "price_source": {
            "product_id": product.get("product_id"),
            "source_product_no": product.get("source_product_no"),
            "product_name": product.get("product_name"),
            "spec_text": product.get("spec_text"),
            "billing_cycle": product.get("billing_cycle"),
            "sale_price": product.get("sale_price"),
            "currency": product.get("price_currency", "CNY"),
        },
        "setup_fee": setup_fee,
        "setup_fee_source": setup_fee_source,
        "discount_rule": None,
        "note": "ISP 模块只提供套餐费、初装费和计费周期，首期与总价由 calculator 统一确认。",
    }


def _build_quote(
    product: dict[str, Any],
    parsed: dict[str, Any],
    required_bandwidth: int | None,
    line_preference: str | None,
    setup_lookup: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    down_mbps, up_mbps = _extract_bandwidth_pair(product)
    billing_cycle = display_text(product.get("billing_cycle"))
    setup_fee_source = setup_lookup.get((display_text(product.get("product_name")), billing_cycle))
    setup_fee = _setup_fee_amount(setup_fee_source)
    pricing_input = _build_pricing_input(product, setup_fee, setup_fee_source)
    budget = parsed.get("budget") or {}
    budget_upper = budget.get("upper_cny")
    sale_price_reference = _number(product.get("sale_price"))
    budget_fit = budget_upper is None or sale_price_reference <= budget_upper
    budget_overrun = 0 if budget_fit or budget_upper is None else round(sale_price_reference - _number(budget_upper), 2)
    max_bandwidth = max(value for value in (down_mbps, up_mbps) if value is not None)
    min_bandwidth = min(value for value in (down_mbps, up_mbps) if value is not None)
    is_symmetric = down_mbps == up_mbps
    kind = _line_kind(product)
    ip_count = _ip_capacity(product)
    has_ip_resource = ip_count > 0 or bool(_ip_description(product))
    network = parsed.get("network_requirements", {})
    needs_ip = network.get("requires_public_ip") or network.get("requires_fixed_ip")
    ip_requirement_fit = not needs_ip or has_ip_resource
    needs_dedicated_quality = bool(needs_ip or network.get("requires_dedicated_line") or line_preference in {"domestic_premium", "international_premium"})
    comparable_bandwidth = min_bandwidth if required_bandwidth is not None else max_bandwidth
    ranking_bandwidth = _ranking_bandwidth(down_mbps, up_mbps, required_bandwidth, needs_dedicated_quality)
    bandwidth_distance = _bandwidth_distance(ranking_bandwidth, required_bandwidth)
    meets_bandwidth = required_bandwidth is None or comparable_bandwidth >= required_bandwidth

    score = 0
    if meets_bandwidth:
        score += 50
    elif required_bandwidth is not None:
        score -= max(0, required_bandwidth - max_bandwidth) // 5
    if line_preference and kind == line_preference:
        score += 30
    elif line_preference and kind in {"domestic_premium", "international_premium"}:
        score += 10
    if needs_ip and ip_count > 0:
        score += 25
    elif needs_ip:
        score -= 20
    if is_symmetric:
        score += 10
    if budget_fit:
        score += 20
    else:
        score -= 25
    requested_cycles = parsed.get("billing_cycles") or []
    if requested_cycles and billing_cycle in requested_cycles:
        score += 15
    if required_bandwidth is not None:
        if bandwidth_distance == 0:
            score += 25
        elif ranking_bandwidth is not None and ranking_bandwidth > required_bandwidth:
            score -= min(30, bandwidth_distance // 10)
        else:
            score -= min(20, bandwidth_distance // 5)
    score -= int(sale_price_reference // 10000)

    return {
        "product_id": product.get("product_id"),
        "product_name": product.get("product_name"),
        "line_kind": kind,
        "spec_text": product.get("spec_text"),
        "down_mbps": down_mbps,
        "up_mbps": up_mbps,
        "max_bandwidth_mbps": max_bandwidth,
        "min_bandwidth_mbps": min_bandwidth,
        "comparable_bandwidth_mbps": comparable_bandwidth,
        "ranking_bandwidth_mbps": ranking_bandwidth,
        "bandwidth_distance_mbps": bandwidth_distance,
        "is_symmetric": is_symmetric,
        "billing_cycle": billing_cycle,
        "sale_price": product.get("sale_price"),
        "setup_fee": setup_fee,
        "setup_fee_source": setup_fee_source,
        "pricing_input": pricing_input,
        "ip_count": ip_count,
        "description": product.get("description"),
        "meets_bandwidth": meets_bandwidth,
        "budget_fit_on_list_price": budget_fit,
        "budget_fit": budget_fit,
        "budget_overrun": budget_overrun,
        "ip_requirement_fit": ip_requirement_fit,
        "score": score,
        "advantages": _build_advantages(product, required_bandwidth, line_preference, needs_ip, budget_fit),
        "tradeoffs": _build_tradeoffs(product, required_bandwidth, line_preference, needs_ip, budget_fit),
    }


def _build_advantages(
    product: dict[str, Any],
    required_bandwidth: int | None,
    line_preference: str | None,
    needs_ip: bool,
    budget_fit: bool,
) -> list[str]:
    down_mbps, up_mbps = _extract_bandwidth_pair(product)
    max_bandwidth = max(value for value in (down_mbps, up_mbps) if value is not None)
    min_bandwidth = min(value for value in (down_mbps, up_mbps) if value is not None)
    kind = _line_kind(product)
    comparable_bandwidth = min_bandwidth if required_bandwidth is not None else max_bandwidth
    advantages = []
    if required_bandwidth is None:
        advantages.append("可作为 ISP 专线候选套餐")
    elif comparable_bandwidth >= required_bandwidth:
        advantages.append(f"带宽不低于需求 {required_bandwidth}M")
    if line_preference and kind == line_preference:
        advantages.append("线路类型匹配客户诉求")
    if down_mbps == up_mbps:
        advantages.append("上下行对称，适合专线和公网服务场景")
    ip_description = _ip_description(product)
    if needs_ip and ip_description:
        advantages.append(f"产品表描述：{ip_description}")
    elif needs_ip and _ip_capacity(product) > 0:
        advantages.append(f"包含可用 IP 资源（按产品表提取 {_ip_capacity(product)} 个）")
    if budget_fit:
        advantages.append("按模块预估口径在预算内")
    return advantages


def _build_tradeoffs(
    product: dict[str, Any],
    required_bandwidth: int | None,
    line_preference: str | None,
    needs_ip: bool,
    budget_fit: bool,
) -> list[str]:
    down_mbps, up_mbps = _extract_bandwidth_pair(product)
    max_bandwidth = max(value for value in (down_mbps, up_mbps) if value is not None)
    min_bandwidth = min(value for value in (down_mbps, up_mbps) if value is not None)
    kind = _line_kind(product)
    comparable_bandwidth = min_bandwidth if required_bandwidth is not None else max_bandwidth
    tradeoffs = []
    if required_bandwidth is not None and comparable_bandwidth < required_bandwidth:
        tradeoffs.append(f"带宽低于需求 {required_bandwidth}M")
    if line_preference and kind != line_preference:
        tradeoffs.append("线路类型不是最贴合诉求的优先类型")
    if needs_ip and _ip_capacity(product) <= 0:
        tradeoffs.append("未标注可用 IP 资源，需销售确认")
    if down_mbps != up_mbps:
        tradeoffs.append("上下行不对称，更偏普通互联网接入")
    if not budget_fit:
        tradeoffs.append("按模块预估口径超出当前预算")
    return tradeoffs


def build_isp_quote_response(query: str) -> dict[str, Any]:
    parsed = parse_requirement_payload(query)
    required_bandwidth, bandwidth_reason = _resolve_required_bandwidth(parsed)
    line_preference, line_reason = _resolve_line_preference(parsed)
    setup_lookup = _build_setup_fee_lookup()
    quotes = [
        _build_quote(product, parsed, required_bandwidth, line_preference, setup_lookup)
        for product in _load_isp_products()
    ]
    requested_cycles = parsed.get("billing_cycles") or []
    if requested_cycles:
        quotes = [quote for quote in quotes if quote["billing_cycle"] in requested_cycles] or quotes

    def recommendation_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        same_line_penalty = 0
        if line_preference:
            if item.get("line_kind") == line_preference:
                same_line_penalty = 0
            elif item.get("line_kind") in {"domestic_premium", "international_premium"}:
                same_line_penalty = 1
            else:
                same_line_penalty = 2
        return (
            not item["meets_bandwidth"],
            not item.get("ip_requirement_fit", True),
            not item["budget_fit"],
            item.get("budget_overrun", 0),
            same_line_penalty,
            item.get("bandwidth_distance_mbps", 0) if required_bandwidth is not None else 0,
            _number(item["sale_price"], float("inf")),
            -item["score"],
            item["max_bandwidth_mbps"],
            item["product_id"],
        )

    quotes.sort(key=recommendation_sort_key)
    recommendation = quotes[0] if quotes else None
    return {
        "raw_query": query,
        "parsed_requirements": parsed,
        "module": {
            "module_key": "isp_private_line",
            "module_name": "ISP 专线核算模块",
            "category": ISP_CATEGORY,
        },
        "assumptions": {
            "required_bandwidth_mbps": required_bandwidth,
            "bandwidth_reason": bandwidth_reason,
            "line_preference": line_preference,
            "line_reason": line_reason,
            "billing_cycles": requested_cycles,
        },
        "summary": {
            "candidate_count": len(quotes),
            "recommended_product_id": recommendation.get("product_id") if recommendation else None,
            "recommended_pricing_input": recommendation.get("pricing_input") if recommendation else None,
        },
        "recommendation": recommendation,
        "compare_items": _select_compare_items(quotes, recommendation, required_bandwidth, line_preference),
        "all_quotes": quotes,
    }


def _select_compare_items(
    quotes: list[dict[str, Any]],
    recommendation: dict[str, Any] | None,
    required_bandwidth: int | None = None,
    line_preference: str | None = None,
) -> list[dict[str, Any]]:
    if not quotes:
        return []
    selected = []
    seen_ids = set()

    def add_items(items: list[dict[str, Any]]) -> None:
        for item in items:
            if not item or item["product_id"] in seen_ids:
                continue
            selected.append(item)
            seen_ids.add(item["product_id"])
            if len(selected) >= DEFAULT_COMPARE_LIMIT:
                return

    def compare_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        bandwidth = item.get("comparable_bandwidth_mbps") or item.get("max_bandwidth_mbps") or 0
        bandwidth_distance = item.get("bandwidth_distance_mbps")
        if bandwidth_distance is None:
            bandwidth_distance = 0 if required_bandwidth is None else abs(bandwidth - required_bandwidth)
        same_line = bool(line_preference and item.get("line_kind") == line_preference)
        return (
            not item.get("meets_bandwidth"),
            not item.get("ip_requirement_fit", True),
            not same_line,
            item.get("budget_overrun", 0),
            bandwidth_distance,
            not item.get("budget_fit"),
            _number(item.get("sale_price"), float("inf")),
            item.get("product_id"),
        )

    add_items([recommendation] if recommendation else [])
    if len(selected) >= DEFAULT_COMPARE_LIMIT:
        return selected

    same_product = []
    if recommendation:
        same_product = [
            item
            for item in quotes
            if item.get("product_name") == recommendation.get("product_name")
            and item.get("meets_bandwidth")
            and (
                required_bandwidth is None
                or item.get("bandwidth_distance_mbps") == 0
            )
        ]
    add_items(sorted(same_product, key=compare_sort_key))
    if len(selected) >= DEFAULT_COMPARE_LIMIT:
        return selected

    same_line_meeting = [
        item
        for item in quotes
        if item.get("meets_bandwidth")
        and (not line_preference or item.get("line_kind") == line_preference)
    ]
    add_items(sorted(same_line_meeting, key=compare_sort_key))
    if len(selected) >= DEFAULT_COMPARE_LIMIT:
        return selected

    meeting_budget = [item for item in quotes if item.get("meets_bandwidth") and item.get("budget_fit")]
    add_items(sorted(meeting_budget, key=compare_sort_key))
    if len(selected) >= DEFAULT_COMPARE_LIMIT:
        return selected

    meeting_any = [item for item in quotes if item.get("meets_bandwidth")]
    add_items(sorted(meeting_any, key=compare_sort_key))
    if len(selected) >= DEFAULT_COMPARE_LIMIT:
        return selected

    # Only fall back to lower-spec products when the catalog has too few items that satisfy the hard bandwidth requirement.
    below_requirement = [
        item for item in quotes if required_bandwidth is not None and not item.get("meets_bandwidth")
    ]
    below_requirement.sort(
        key=lambda item: (
            -(item.get("comparable_bandwidth_mbps") or item.get("max_bandwidth_mbps") or 0),
            _number(item.get("sale_price"), float("inf")),
            item.get("product_id"),
        )
    )
    add_items(below_requirement)
    return selected


def _line_label(kind: str | None) -> str:
    labels = {
        "international_premium": "国际精品互联网",
        "domestic_premium": "国内精品互联网 / 互联网专线",
        "premium_access": "尊享互联网",
        "economy": "经济互联网",
        "standard": "标准互联网",
    }
    return labels.get(kind or "", kind or "-")


def render_isp_quote_response(response: dict[str, Any]) -> str:
    assumptions = response["assumptions"]
    recommendation = response.get("recommendation")
    lines = ["ISP 专线核算模块"]
    lines.append("说明：本模块只输出套餐推荐和 pricing_input；首期与总价由 calculator 统一确认。")

    parts = []
    if assumptions.get("required_bandwidth_mbps"):
        parts.append(f"带宽 {assumptions['required_bandwidth_mbps']}M")
    if assumptions.get("line_preference"):
        parts.append(f"线路偏好 {_line_label(assumptions['line_preference'])}")
    if assumptions.get("billing_cycles"):
        parts.append("计费周期 " + "、".join(assumptions["billing_cycles"]))
    if parts:
        lines.append("核算口径：" + "，".join(parts))

    notes = [
        item
        for item in (assumptions.get("bandwidth_reason"), assumptions.get("line_reason"))
        if item
    ]
    if notes:
        lines.append("口径说明：" + "；".join(notes))

    if not recommendation:
        lines.append("当前没有找到 ISP 套餐，请确认产品目录是否已导入。")
        return "\n".join(lines)

    lines.append(
        f"推荐套餐：{display_text(recommendation.get('product_name'))} "
        f"{display_text(recommendation.get('spec_text'))}（{recommendation.get('product_id')}），"
        f"{recommendation.get('billing_cycle')}费用 {_format_money(recommendation.get('sale_price'))}，"
        f"初装费 {_format_money(recommendation.get('setup_fee'))}，"
        f"已生成 pricing_input，首期与总价待 calculator 确认。"
    )
    setup_source = _source_label(recommendation.get("setup_fee_source"))
    if setup_source:
        lines.append(f"费用来源：初装费来自产品表 {setup_source}。")
    if not recommendation.get("budget_fit"):
        lines.append("预算提示：按模块预估口径可能超出预算，最终以 calculator 确认结果为准。")

    lines.append("")
    lines.append("套餐对比：")
    for quote in response.get("compare_items", []):
        ip_description = display_text(quote.get("description"), "").strip()
        if "ip" in ip_description.lower():
            ip_text = f"，产品表描述：{ip_description}"
        elif quote.get("ip_count"):
            ip_text = f"，可用 IP 按产品表提取 {quote['ip_count']} 个"
        else:
            ip_text = ""
        symmetry = "上下行对称" if quote.get("is_symmetric") else "上下行不对称"
        lines.append(
            f"- {display_text(quote.get('product_name'))} {display_text(quote.get('spec_text'))}："
            f"{quote.get('billing_cycle')} {_format_money(quote.get('sale_price'))}，"
            f"初装费 {_format_money(quote.get('setup_fee'))}，pricing_input 待 calculator 确认，"
            f"{symmetry}{ip_text}。"
        )
        quote_setup_source = _source_label(quote.get("setup_fee_source"))
        if quote_setup_source:
            lines.append(f"  费用来源：初装费来自产品表 {quote_setup_source}")
        if quote.get("advantages"):
            lines.append("  优势：" + "；".join(quote["advantages"]))
        if quote.get("tradeoffs"):
            lines.append("  取舍：" + "；".join(quote["tradeoffs"]))

    follow_up = _isp_follow_up_questions(response.get("parsed_requirements", {}))
    if follow_up:
        lines.append("")
        lines.append("我建议再和客户确认这几件事：")
        for question in follow_up:
            lines.append(f"- {question}")
    return "\n".join(lines)


def _isp_follow_up_questions(parsed: dict[str, Any]) -> list[str]:
    questions = []
    source_questions = parsed.get("human_follow_up_questions") or parsed.get("follow_up_questions", [])
    for question in source_questions:
        if "网络产品" in question and "带宽" in question:
            continue
        questions.append(question)
    return questions


@tool
def quote_isp_packages(query: str) -> str:
    """
    ISP 专线核算模块：用于对 ISP 互联网专线/精品互联网/经济互联网等套餐做优劣对比，
    结合带宽、计费周期、固定 IP/公网 IP、预算给出推荐套餐。
    """
    return render_isp_quote_response(build_isp_quote_response(query))


@tool
def quote_isp_packages_json(query: str) -> str:
    """
    返回 ISP 专线核算模块的结构化 JSON，便于调试套餐筛选、费用和推荐分数。
    """
    return json.dumps(build_isp_quote_response(query), ensure_ascii=False, indent=2)

