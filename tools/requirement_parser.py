import json
import re
from typing import Any

from langchain.tools import tool

from tools.product_tool import get_category_alias_map, load_flat_products, normalize_text


FEATURE_TERMS = [
    "pos",
    "传真",
    "poe",
    "sip",
    "视频会议",
    "会议",
    "邮箱",
    "海外访问",
    "出海",
    "加速",
    "组网",
    "专线",
    "公网ip",
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

SCENARIO_HINTS = {
    "语音通信": ("语音", "网关", "电话", "sip", "传真", "pos"),
    "国际访问优化": ("国际", "海外", "出海", "路由优化", "应用加速", "海外访问"),
    "组网互联": ("组网", "互联", "sd-wan", "专线", "多分支"),
    "视频会议": ("zoom", "视频会议", "会议"),
    "企业邮箱": ("office365", "o365", "邮箱", "邮件"),
    "互联网接入": ("isp", "宽带", "互联网", "专线", "公网ip"),
}

CATEGORY_SCENARIO_HINTS = {
    "语音网关": ("语音", "网关", "电话", "sip", "传真", "pos"),
    "SD-WAN": ("sd-wan", "sdwan", "组网", "多分支", "互联"),
    "国际路由优化": ("国际", "海外", "出海", "海外访问", "加速", "路由优化", "应用加速"),
    "ISP": ("isp", "宽带", "互联网", "接入", "专线", "公网ip"),
    "Zoom": ("zoom", "视频会议", "会议", "云会议"),
    "office365": ("office365", "office 365", "o365", "邮箱", "邮件", "微软365"),
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

    for pattern in patterns:
        match = re.search(pattern, query_norm, re.IGNORECASE)
        if not match:
            continue
        amount = convert_amount_to_cny(match.group(1), match.group(2))
        if amount is None:
            continue
        return {
            "upper_cny": amount,
            "lower_cny": None,
            "currency": "CNY",
            "raw_text": match.group(0),
        }

    above_match = re.search(r"(\d+(?:\.\d+)?)\s*(万|w|k|千|元)?\s*(以上|起)", query_norm, re.IGNORECASE)
    if above_match:
        amount = convert_amount_to_cny(above_match.group(1), above_match.group(2))
        if amount is not None:
            return {
                "upper_cny": None,
                "lower_cny": amount,
                "currency": "CNY",
                "raw_text": above_match.group(0),
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


def extract_scenarios(query_norm: str) -> list[str]:
    result = []
    for label, hints in SCENARIO_HINTS.items():
        if any(hint in query_norm for hint in hints):
            result.append(label)
    return result


def extract_billing_cycles(query_norm: str) -> list[str]:
    cycles = []
    if any(term in query_norm for term in ("年付", "年价", "年费", "包年")):
        cycles.append("年付")
    if any(term in query_norm for term in ("月付", "月价", "月费", "包月")):
        cycles.append("月付")
    return cycles


def extract_contract_modes(query_norm: str) -> list[str]:
    modes = []
    if "ict签约" in query_norm or "ict" in query_norm:
        modes.append("ICT签约")
    if "直签" in query_norm:
        modes.append("直签")
    return modes


def extract_models(query: str) -> list[str]:
    models = set(re.findall(r"[A-Za-z][A-Za-z0-9.-]*\d[A-Za-z0-9.-]*", query))
    return sorted(model.upper() for model in models)


def extract_feature_keywords(query_norm: str) -> list[str]:
    return [term.upper() if term.isascii() else term for term in FEATURE_TERMS if term in query_norm]


def extract_spec_requirements(query: str) -> dict[str, Any]:
    bandwidth_terms = []
    bandwidth_mbps = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*M(?![A-Za-z])", query, re.IGNORECASE):
        value = float(match.group(1))
        bandwidth_terms.append(f"{match.group(1)}M")
        bandwidth_mbps.append(int(value) if value.is_integer() else value)

    port_count = None
    port_match = re.search(r"(\d+)\s*口", query)
    if port_match:
        port_count = int(port_match.group(1))

    participant_count = None
    participant_match = re.search(r"(\d+)\s*方", query)
    if participant_match:
        participant_count = int(participant_match.group(1))

    account_count = None
    account_match = re.search(r"(\d+)\s*个?(?:账号|账户|邮箱|用户|席位|人)", query)
    if account_match:
        account_count = int(account_match.group(1))

    return {
        "bandwidth_terms": sorted(set(bandwidth_terms), key=lambda item: (len(item), item)),
        "bandwidth_mbps": sorted(set(bandwidth_mbps)),
        "port_count": port_count,
        "participant_count": participant_count,
        "account_count": account_count,
    }


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

    if parsed["intent"] in {"recommendation", "comparison"} and not categories and not has_explicit_product_anchor:
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
    categories = extract_categories(query_norm)
    brands = extract_brands(query_norm)
    customer_types = extract_customer_types(query_norm)
    scenarios = extract_scenarios(query_norm)
    billing_cycles = extract_billing_cycles(query_norm)
    contract_modes = extract_contract_modes(query_norm)
    model_keywords = extract_models(query)
    spec_requirements = extract_spec_requirements(query)
    budget = parse_budget(query)
    price_preference = derive_price_preference(query_norm)
    feature_keywords = extract_feature_keywords(query_norm)

    parsed: dict[str, Any] = {
        "raw_query": query,
        "intent": intent,
        "customer_types": customer_types,
        "scenarios": scenarios,
        "target_categories": categories,
        "brand_preferences": brands,
        "model_keywords": model_keywords,
        "billing_cycles": billing_cycles,
        "contract_modes": contract_modes,
        "budget": budget,
        "price_preference": price_preference,
        "feature_keywords": feature_keywords,
        "spec_requirements": spec_requirements,
        "needs_discount_price": any(term in query_norm for term in ("折扣", "折后", "优惠", "直签", "ict", "授权")),
        "needs_setup_fee": any(term in query_norm for term in ("调试费", "实施费", "安装费", "初装费")),
        "needs_min_sale_price": any(term in query_norm for term in ("最低销售价", "最低价", "最便宜", "最低")),
    }
    parsed["follow_up_questions"] = build_follow_up_questions(parsed)
    parsed["ready_for_recommendation"] = len(parsed["follow_up_questions"]) == 0
    parsed["product_filters"] = build_product_filters(parsed)
    return parsed


@tool
def parse_requirements(query: str) -> str:
    """
    用于把销售咨询、客户需求、套餐推荐问题解析成结构化需求。
    适用于“帮我推荐一个方案”“客户要年付 10M 直签”“门店要 4 口语音网关，预算 1000 内”等场景。

    Args:
        query: 用户的原始自然语言需求描述。
    """
    parsed = parse_requirement_payload(query)
    return json.dumps(parsed, ensure_ascii=False, indent=2)
