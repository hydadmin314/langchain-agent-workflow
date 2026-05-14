import json
import re
from typing import Any

from langchain.tools import tool

from tools.sales_tools.product_tool import get_category_alias_map, load_flat_products, normalize_text


FEATURE_TERMS = [
    "pos",
    "传真",
    "poe",
    "sip",
    "视频会议",
    "会议",
    "邮箱",
    "海外访问",
    "跨境访问",
    "出海",
    "加速",
    "组网",
    "多分支",
    "专线",
    "公网ip",
    "固定ip",
    "大带宽",
    "低延迟",
    "无线",
    "监控",
    "稳定",
]

CUSTOMER_TYPE_HINTS = {
    "门店": ("门店", "店铺", "商铺", "pos", "收银"),
    "企业": ("企业", "公司", "办公", "办公室", "总部"),
    "连锁": ("连锁", "多门店", "多分支", "分店"),
    "跨境": ("跨境", "出海", "海外", "国际业务"),
}

INDUSTRY_HINTS = {
    "游戏": ("游戏", "手游", "页游", "电竞"),
    "电商": ("电商", "跨境电商", "店铺"),
    "办公": ("办公", "办公室", "总部"),
}

PAIN_POINT_HINTS = {
    "访问慢": ("访问很慢", "访问慢", "打开慢", "加载慢", "速度慢", "很慢", "卡顿"),
    "延迟高": ("高延迟", "延迟高", "时延高", "延迟"),
    "链路不稳定": ("不稳定", "抖动", "丢包", "掉线"),
}

SCENARIO_HINTS = {
    "语音通信": ("语音", "网关", "电话", "sip", "传真", "pos"),
    "国际访问优化": (
        "国际",
        "海外",
        "出海",
        "路由优化",
        "应用加速",
        "海外访问",
        "跨境访问",
        "境外访问",
        "海外服务器",
        "国外服务器",
    ),
    "组网互联": ("组网", "互联", "sd-wan", "多分支", "多站点", "分支互联", "总部门店"),
    "视频会议": ("zoom", "视频会议", "会议"),
    "企业邮箱": ("office365", "o365", "邮箱", "邮件"),
    "互联网接入": ("isp", "宽带", "互联网", "专线", "公网ip", "固定ip", "大带宽"),
}

CATEGORY_SCENARIO_HINTS = {
    "语音网关": ("语音", "网关", "电话", "sip", "传真", "pos"),
    "SD-WAN": ("sd-wan", "sdwan", "组网", "多分支", "多站点", "互联", "分支互联"),
    "国际路由优化": (
        "国际",
        "海外",
        "出海",
        "海外访问",
        "跨境访问",
        "境外访问",
        "加速",
        "路由优化",
        "应用加速",
    ),
    "ISP": ("isp", "宽带", "互联网", "接入", "专线", "公网ip", "固定ip", "大带宽"),
    "Zoom": ("zoom", "视频会议", "会议", "云会议"),
    "office365": ("office365", "office 365", "o365", "邮箱", "邮件", "微软365"),
}

SCENARIO_CATEGORY_MAP = {
    "语音通信": "语音网关",
    "国际访问优化": "国际路由优化",
    "组网互联": "SD-WAN",
    "视频会议": "Zoom",
    "企业邮箱": "office365",
    "互联网接入": "ISP",
}

DOMESTIC_LOCATION_HINTS = {
    "上海": ("上海",),
    "北京": ("北京",),
    "深圳": ("深圳",),
    "广州": ("广州",),
    "杭州": ("杭州",),
    "南京": ("南京",),
    "苏州": ("苏州",),
    "成都": ("成都",),
    "武汉": ("武汉",),
}

OVERSEAS_LOCATION_HINTS = {
    "美国": ("美国", "美区"),
    "日本": ("日本",),
    "新加坡": ("新加坡",),
    "中国香港": ("香港", "中国香港"),
    "中国台湾": ("台湾", "中国台湾"),
    "韩国": ("韩国",),
    "英国": ("英国",),
    "德国": ("德国",),
    "欧洲": ("欧洲",),
    "东南亚": ("东南亚",),
    "澳大利亚": ("澳洲", "澳大利亚"),
}

RECOMMEND_INTENT_TERMS = ("推荐", "适合", "方案", "套餐", "怎么选", "怎么配", "建议")
COMPARE_INTENT_TERMS = ("对比", "比较", "区别", "差别", "哪个好", "哪种好")
LOOKUP_INTENT_TERMS = ("多少钱", "价格", "报价", "是什么", "有哪些", "查一下", "查下")


def detect_intent(query_norm: str) -> str:
    if any(term in query_norm for term in COMPARE_INTENT_TERMS):
        return "comparison"
    if any(term in query_norm for term in RECOMMEND_INTENT_TERMS):
        return "recommendation"
    if any(term in query_norm for term in LOOKUP_INTENT_TERMS):
        return "lookup"
    return "general"


def convert_amount_to_cny(raw_value: str, unit: str | None) -> int | None:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None

    normalized_unit = (unit or "").lower()
    multiplier = 1
    if normalized_unit in {"万", "w"}:
        multiplier = 10000
    elif normalized_unit in {"千", "k"}:
        multiplier = 1000

    amount = round(value * multiplier)
    return int(amount)


def parse_budget(query: str) -> dict[str, Any] | None:
    query_norm = normalize_text(query)
    patterns = [
        r"(\d+(?:\.\d+)?)\s*(万|w|k|千|元)?\s*(以内|以下|之内|不超过|封顶)",
        r"预算\s*(\d+(?:\.\d+)?)\s*(万|w|k|千|元)?",
        r"控制在\s*(\d+(?:\.\d+)?)\s*(万|w|k|千|元)?",
    ]

    latest_budget = None
    latest_start = -1
    for pattern in patterns:
        for match in re.finditer(pattern, query_norm, re.IGNORECASE):
            amount = convert_amount_to_cny(match.group(1), match.group(2))
            if amount is None or match.start() < latest_start:
                continue
            latest_start = match.start()
            latest_budget = {
                "upper_cny": amount,
                "lower_cny": None,
                "currency": "CNY",
                "raw_text": match.group(0),
            }
    if latest_budget:
        return latest_budget

    above_matches = list(re.finditer(r"(\d+(?:\.\d+)?)\s*(万|w|k|千|元)?\s*(以上|起)", query_norm, re.IGNORECASE))
    if above_matches:
        match = above_matches[-1]
        amount = convert_amount_to_cny(match.group(1), match.group(2))
        if amount is None:
            return None
        return {
            "upper_cny": None,
            "lower_cny": amount,
            "currency": "CNY",
            "raw_text": match.group(0),
        }

    return None


def extract_categories(query_norm: str) -> list[str]:
    alias_map = get_category_alias_map()
    categories = {
        category_name
        for alias, category_name in alias_map.items()
        if alias and alias in query_norm
    }

    for category_name, hints in CATEGORY_SCENARIO_HINTS.items():
        if any(hint in query_norm for hint in hints):
            categories.add(category_name)

    return sorted(categories)


def extract_brands(query_norm: str) -> list[str]:
    brand_map = {
        normalize_text(product.get("brand")): str(product.get("brand"))
        for product in load_flat_products()
        if normalize_text(product.get("brand"))
    }
    brands = []
    for brand_norm in sorted(brand_map):
        if brand_norm and brand_norm in query_norm:
            brands.append(brand_map[brand_norm])
    return brands


def extract_customer_types(query_norm: str) -> list[str]:
    result = []
    for label, hints in CUSTOMER_TYPE_HINTS.items():
        if any(hint in query_norm for hint in hints):
            result.append(label)
    return result


def extract_industries(query_norm: str) -> list[str]:
    result = []
    for label, hints in INDUSTRY_HINTS.items():
        if any(hint in query_norm for hint in hints):
            result.append(label)
    return result


def extract_pain_points(query_norm: str) -> list[str]:
    result = []
    for label, hints in PAIN_POINT_HINTS.items():
        if any(hint in query_norm for hint in hints):
            result.append(label)
    return result


def extract_service_period(query_norm: str) -> dict[str, Any] | None:
    trial_requested = any(term in query_norm for term in ("先试", "试用", "体验", "试一个月")) or bool(
        re.search(r"先用\s*(?:1|一)\s*个?月", query_norm)
    )
    matches = []
    for match in re.finditer(r"(\d+)\s*个?月", query_norm):
        matches.append((match.start(), int(match.group(1)), match.group(0)))
    for match in re.finditer(r"(\d+)\s*年", query_norm):
        matches.append((match.start(), int(match.group(1)) * 12, match.group(0)))
    for term, months in (("一个月", 1), ("半年", 6), ("一年", 12)):
        for match in re.finditer(term, query_norm):
            matches.append((match.start(), months, term))
    if matches:
        _, months, raw_text = max(matches, key=lambda item: item[0])
        return {"months": months, "trial_requested": trial_requested, "raw_text": raw_text}

    if trial_requested:
        return {"months": None, "trial_requested": True, "raw_text": None}
    return None


def extract_billing_cycles(query_norm: str, service_period: dict[str, Any] | None = None) -> list[str]:
    explicit_matches = []
    for term in ("年付", "年价", "年费", "包年", "年度", "按年", "年结", "年框"):
        for match in re.finditer(term, query_norm):
            explicit_matches.append((match.start(), "年付"))
    for term in ("月付", "月价", "月费", "包月"):
        for match in re.finditer(term, query_norm):
            explicit_matches.append((match.start(), "月付"))

    if explicit_matches:
        return [max(explicit_matches, key=lambda item: item[0])[1]]

    cycles = []

    if service_period and not cycles:
        months = service_period.get("months")
        if months is not None and months >= 12:
            cycles.append("年付")
        elif service_period.get("trial_requested") or months == 1:
            cycles.append("月付")

    return list(dict.fromkeys(cycles))


def extract_contract_modes(query_norm: str) -> list[str]:
    matches = []
    for term in ("ict签约", "ict"):
        for match in re.finditer(term, query_norm):
            matches.append((match.start(), "ICT签约"))
    for match in re.finditer("直签", query_norm):
        matches.append((match.start(), "直签"))
    if not matches:
        return []
    return [max(matches, key=lambda item: item[0])[1]]


def extract_models(query: str) -> list[str]:
    models = set(re.findall(r"[A-Za-z][A-Za-z0-9.-]*\d[A-Za-z0-9.-]*", query))
    return sorted(model.upper() for model in models)


def extract_feature_keywords(query_norm: str) -> list[str]:
    return [term.upper() if term.isascii() else term for term in FEATURE_TERMS if term in query_norm]


def extract_spec_requirements(query: str) -> dict[str, Any]:
    bandwidth_terms = []
    bandwidth_mbps = []

    def add_bandwidth(raw_value: str) -> None:
        value = float(raw_value)
        normalized_value = int(value) if value.is_integer() else value
        bandwidth_terms.append(f"{raw_value}M")
        bandwidth_mbps.append(normalized_value)

    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*M(?![A-Za-z])", query, re.IGNORECASE):
        add_bandwidth(match.group(1))
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*兆(?:宽带|带宽)?", query, re.IGNORECASE):
        add_bandwidth(match.group(1))
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:宽带|带宽)", query, re.IGNORECASE):
        add_bandwidth(match.group(1))

    port_count = None
    port_match = re.search(r"(\d+)\s*口", query)
    if port_match:
        port_count = int(port_match.group(1))

    participant_count = None
    participant_match = re.search(r"(\d+)\s*方", query)
    if participant_match:
        participant_count = int(participant_match.group(1))

    account_count = None
    account_match = re.search(r"(\d+)\s*(?:个|名|位)\s*(?:账号|账户|邮箱|席位)", query)
    if account_match:
        account_count = int(account_match.group(1))

    user_count = None
    user_match = re.search(r"(\d+)\s*个?(?:人|员工|用户)(?:用|使用)?", query)
    if user_match:
        user_count = int(user_match.group(1))

    site_count = None
    site_match = re.search(r"(\d+)\s*个?(?:站点|门店|办公室|节点)", query)
    if site_match:
        site_count = int(site_match.group(1))

    branch_count = None
    branch_match = re.search(r"(\d+)\s*个?(?:分支|分店)", query)
    if branch_match:
        branch_count = int(branch_match.group(1))

    return {
        "bandwidth_terms": sorted(set(bandwidth_terms), key=lambda item: (len(item), item)),
        "bandwidth_mbps": sorted(set(bandwidth_mbps)),
        "port_count": port_count,
        "participant_count": participant_count,
        "account_count": account_count,
        "user_count": user_count,
        "site_count": site_count,
        "branch_count": branch_count,
    }


def classify_location_role(query_norm: str, alias: str) -> tuple[str, str]:
    escaped_alias = re.escape(alias)
    destination_patterns = (
        rf"(?:服务器|系统|应用|业务|网站)[^，,。；;]{{0,6}}(?:在|位于)?\s*{escaped_alias}",
        rf"(?:访问|连接|访问到|连接到)[^，,。；;]{{0,6}}{escaped_alias}",
        rf"{escaped_alias}(?:服务器|系统|应用|业务|网站)",
    )
    for pattern in destination_patterns:
        match = re.search(pattern, query_norm)
        if match:
            return "destination", match.group(0)

    source_patterns = (
        rf"{escaped_alias}(?:办公室|总部|门店|分支|员工|用户)",
        rf"(?:办公室|总部|门店|分支|员工|用户)[^，,。；;]{{0,6}}(?:在|位于)?\s*{escaped_alias}",
    )
    for pattern in source_patterns:
        match = re.search(pattern, query_norm)
        if match:
            return "source", match.group(0)

    return "mentioned", alias


def extract_locations(query_norm: str) -> list[dict[str, str]]:
    locations: list[dict[str, str]] = []
    seen_labels: set[str] = set()

    grouped_hints = (
        ("domestic", DOMESTIC_LOCATION_HINTS),
        ("overseas", OVERSEAS_LOCATION_HINTS),
    )
    for region_type, hint_map in grouped_hints:
        for label, aliases in hint_map.items():
            if label in seen_labels:
                continue
            matched_alias = next((alias for alias in aliases if alias in query_norm), None)
            if not matched_alias:
                continue
            role, evidence = classify_location_role(query_norm, matched_alias)
            locations.append(
                {
                    "label": label,
                    "region_type": region_type,
                    "role": role,
                    "evidence": evidence,
                }
            )
            seen_labels.add(label)

    return locations


def extract_network_requirements(
    query_norm: str,
    locations: list[dict[str, str]],
    pain_points: list[str],
) -> dict[str, Any]:
    source_locations = [item["label"] for item in locations if item["role"] == "source"]
    destination_locations = [item["label"] for item in locations if item["role"] == "destination"]
    has_domestic_source = any(
        item["region_type"] == "domestic" and item["role"] == "source"
        for item in locations
    )
    has_overseas_destination = any(
        item["region_type"] == "overseas" and item["role"] == "destination"
        for item in locations
    )
    cross_border_access = (
        has_domestic_source and has_overseas_destination
    ) or any(term in query_norm for term in ("海外访问", "跨境访问", "境外访问", "访问海外", "出海"))

    return {
        "source_locations": source_locations,
        "destination_locations": destination_locations,
        "cross_border_access": cross_border_access,
        "requires_public_ip": any(term in query_norm for term in ("公网ip", "公网 ip")),
        "requires_fixed_ip": any(term in query_norm for term in ("固定ip", "固定 ip", "静态ip", "静态 ip")),
        "requires_dedicated_line": "专线" in query_norm,
        "requires_large_bandwidth": any(term in query_norm for term in ("大带宽", "高带宽")),
        "has_latency_or_speed_issue": any(point in pain_points for point in ("访问慢", "延迟高", "链路不稳定")),
    }


def build_scenario_analysis(
    query_norm: str,
    spec_requirements: dict[str, Any],
    network_requirements: dict[str, Any],
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []

    for scenario_name, hints in SCENARIO_HINTS.items():
        matched_terms = [hint for hint in hints if hint in query_norm]
        score = len(matched_terms) * 2
        reasons: list[str] = []

        if matched_terms:
            reasons.append("命中关键词：" + "、".join(matched_terms))

        if scenario_name == "国际访问优化":
            if network_requirements["cross_border_access"]:
                score += 6
                reasons.append("识别到跨境访问链路")
            if network_requirements["has_latency_or_speed_issue"]:
                score += 3
                reasons.append("识别到访问慢、延迟或链路稳定性问题")

        if scenario_name == "组网互联":
            if spec_requirements.get("branch_count") or spec_requirements.get("site_count"):
                score += 5
                reasons.append("识别到多分支或多站点诉求")

        if scenario_name == "互联网接入":
            internet_signals = [
                network_requirements["requires_public_ip"],
                network_requirements["requires_fixed_ip"],
                network_requirements["requires_dedicated_line"],
                network_requirements["requires_large_bandwidth"],
            ]
            if any(internet_signals):
                score += 5
                reasons.append("识别到公网 IP、固定 IP、专线或大带宽诉求")

        if scenario_name == "语音通信" and spec_requirements.get("port_count") is not None:
            score += 4
            reasons.append("识别到语音端口数量")

        if scenario_name == "视频会议" and spec_requirements.get("participant_count") is not None:
            score += 4
            reasons.append("识别到会议参会规模")

        if (
            scenario_name == "企业邮箱"
            and spec_requirements.get("account_count") is not None
            and matched_terms
        ):
            score += 4
            reasons.append("识别到账号或邮箱数量")

        if score <= 0:
            continue

        if score >= 8:
            confidence = "high"
        elif score >= 4:
            confidence = "medium"
        else:
            confidence = "low"

        candidates.append(
            {
                "scenario": scenario_name,
                "score": score,
                "confidence": confidence,
                "matched_terms": matched_terms,
                "reasons": reasons,
            }
        )

    candidates.sort(key=lambda item: (-item["score"], item["scenario"]))
    primary = candidates[0] if candidates else None
    return {
        "primary_scenario": primary["scenario"] if primary else None,
        "primary_confidence": primary["confidence"] if primary else None,
        "candidates": candidates,
    }


def derive_target_categories(
    explicit_categories: list[str],
    scenario_analysis: dict[str, Any],
) -> list[str]:
    categories = set(explicit_categories)
    for candidate in scenario_analysis.get("candidates", []):
        mapped_category = SCENARIO_CATEGORY_MAP.get(candidate["scenario"])
        if not mapped_category:
            continue
        if candidate["confidence"] in {"high", "medium"}:
            categories.add(mapped_category)
    return sorted(categories)


def derive_price_preference(query_norm: str) -> str | None:
    if any(term in query_norm for term in ("最便宜", "最低价", "最低销售价", "便宜点", "省钱")):
        return "lowest_price_first"
    if any(term in query_norm for term in ("性价比", "划算")):
        return "value_first"
    if any(term in query_norm for term in ("稳定", "高配", "质量", "体验优先")):
        return "quality_first"
    return None


def build_follow_up_questions(parsed: dict[str, Any]) -> list[str]:
    questions = []
    categories = set(parsed["target_categories"])
    billing_cycles = parsed["billing_cycles"]
    specs = parsed["spec_requirements"]
    recurring_categories = {"SD-WAN", "国际路由优化", "ISP", "Zoom", "office365"}
    has_explicit_product_anchor = bool(parsed["model_keywords"]) or bool(
        specs["bandwidth_terms"] or specs["port_count"] or specs["participant_count"]
    )

    primary_scenario = parsed.get("scenario_analysis", {}).get("primary_scenario")
    network_requirements = parsed.get("network_requirements", {})

    if parsed["intent"] in {"recommendation", "comparison"} and not categories and not primary_scenario and not has_explicit_product_anchor:
        questions.append("需要先确认业务场景，比如语音网关、国际路由优化、SD-WAN、Zoom、office365 或 ISP。")
    if parsed["intent"] == "recommendation" and (not categories or categories & recurring_categories) and not billing_cycles:
        questions.append("需要确认客户偏向月付还是年付。")
    if parsed["intent"] == "recommendation" and parsed["budget"] is None:
        questions.append("如果要做推荐，最好补一个预算范围。")

    if categories & {"SD-WAN", "国际路由优化", "ISP"} and not specs["bandwidth_terms"]:
        questions.append("这类网络产品最好补充带宽规格，比如 10M、20M、50M。")
    if "语音网关" in categories and specs["port_count"] is None and not parsed["model_keywords"]:
        questions.append("语音网关建议补充口数需求，比如 2口、4口、8口。")
    if "Zoom" in categories and specs["participant_count"] is None:
        questions.append("Zoom 方案建议补充参会方数，比如 100方、300方。")
    if "office365" in categories and specs["account_count"] is None:
        questions.append("office365 方案建议补充账号数或邮箱数。")
    if primary_scenario == "国际访问优化" and not network_requirements.get("destination_locations"):
        questions.append("国际访问优化建议补充主要访问地区或服务器所在地。")

    return questions


def build_product_filters(parsed: dict[str, Any]) -> dict[str, Any]:
    spec_terms = []
    spec_terms.extend(parsed["spec_requirements"]["bandwidth_terms"])
    if parsed["spec_requirements"]["port_count"] is not None:
        spec_terms.append(f"{parsed['spec_requirements']['port_count']}口")
    if parsed["spec_requirements"]["participant_count"] is not None:
        spec_terms.append(f"{parsed['spec_requirements']['participant_count']}方")

    return {
        "categories": parsed["target_categories"],
        "brands": parsed["brand_preferences"],
        "billing_cycles": parsed["billing_cycles"],
        "contract_modes": parsed["contract_modes"],
        "model_keywords": parsed["model_keywords"],
        "spec_terms": spec_terms,
        "feature_keywords": parsed["feature_keywords"],
        "budget": parsed["budget"],
        "price_preference": parsed["price_preference"],
    }


def parse_requirement_payload(query: str) -> dict[str, Any]:
    query_norm = normalize_text(query)
    intent = detect_intent(query_norm)
    explicit_categories = extract_categories(query_norm)
    brands = extract_brands(query_norm)
    customer_types = extract_customer_types(query_norm)
    industries = extract_industries(query_norm)
    pain_points = extract_pain_points(query_norm)
    service_period = extract_service_period(query_norm)
    billing_cycles = extract_billing_cycles(query_norm, service_period)
    contract_modes = extract_contract_modes(query_norm)
    model_keywords = extract_models(query)
    spec_requirements = extract_spec_requirements(query)
    if (
        spec_requirements.get("account_count") is None
        and spec_requirements.get("user_count") is not None
        and any(term in query_norm for term in SCENARIO_HINTS["企业邮箱"])
    ):
        spec_requirements["account_count"] = spec_requirements["user_count"]
    locations = extract_locations(query_norm)
    network_requirements = extract_network_requirements(query_norm, locations, pain_points)
    scenario_analysis = build_scenario_analysis(query_norm, spec_requirements, network_requirements)
    categories = derive_target_categories(explicit_categories, scenario_analysis)
    budget = parse_budget(query)
    price_preference = derive_price_preference(query_norm)
    feature_keywords = extract_feature_keywords(query_norm)
    scenarios = [candidate["scenario"] for candidate in scenario_analysis["candidates"]]

    parsed: dict[str, Any] = {
        "raw_query": query,
        "normalized_query": query_norm,
        "intent": intent,
        "customer_types": customer_types,
        "industry_hints": industries,
        "scenarios": scenarios,
        "scenario_analysis": scenario_analysis,
        "explicit_categories": explicit_categories,
        "target_categories": categories,
        "brand_preferences": brands,
        "model_keywords": model_keywords,
        "billing_cycles": billing_cycles,
        "contract_modes": contract_modes,
        "service_period": service_period,
        "budget": budget,
        "price_preference": price_preference,
        "feature_keywords": feature_keywords,
        "pain_points": pain_points,
        "locations": locations,
        "network_requirements": network_requirements,
        "spec_requirements": spec_requirements,
        "needs_discount_price": any(term in query_norm for term in ("折扣", "折后", "优惠", "直签", "ict", "授权")),
        "needs_setup_fee": any(term in query_norm for term in ("调试费", "实施费", "安装费", "初装费")),
        "needs_min_sale_price": any(term in query_norm for term in ("最低销售价", "最低价", "最便宜", "最低")),
    }
    parsed["entities"] = {
        "customer": {
            "types": customer_types,
            "industries": industries,
            "user_count": spec_requirements.get("user_count"),
            "site_count": spec_requirements.get("site_count"),
            "branch_count": spec_requirements.get("branch_count"),
        },
        "locations": locations,
        "network": network_requirements,
        "commercial": {
            "billing_cycles": billing_cycles,
            "contract_modes": contract_modes,
            "service_period": service_period,
            "budget": budget,
            "price_preference": price_preference,
        },
    }
    parsed["follow_up_questions"] = build_follow_up_questions(parsed)
    parsed["ready_for_recommendation"] = len(parsed["follow_up_questions"]) == 0
    parsed["product_filters"] = build_product_filters(parsed)
    return parsed


@tool
def parse_requirements(query: str) -> str:
    """
    用于把销售咨询、客户需求、套餐推荐问题解析成结构化需求。
    适用于“帮我推荐一个方案”“客户要年付 10M 直签”
    “服务器在美国，上海办公室访问很慢，10 个人先试一个月，预算 1 万以内”等场景。

    Args:
        query: 用户的原始自然语言需求描述。
    """
    parsed = parse_requirement_payload(query)
    return json.dumps(parsed, ensure_ascii=False, indent=2)

