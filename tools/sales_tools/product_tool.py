import json
import re
from pathlib import Path
from typing import Any

from langchain.tools import tool


PRODUCTS_PATH = Path("data/normalized/products.json")
MAX_LIMIT = 10
MODEL_PATTERN = re.compile(r"[a-z][a-z0-9.-]*\d[a-z0-9.-]*", re.IGNORECASE)
SPEC_PATTERN = re.compile(r"\d+(?:\.\d+)?(?:m/g|m|g|方|口)", re.IGNORECASE)

FILLER_PATTERNS = [
    "多少钱",
    "什么价格",
    "什么价",
    "报价",
    "售价",
    "价格",
    "资费",
    "折后价",
    "折后",
    "折扣价",
    "折扣",
    "优惠价",
    "优惠",
    "是什么",
    "有哪些",
    "有什么",
    "有没有",
    "查一下",
    "查下",
    "查查",
    "帮我",
    "给我",
    "看看",
    "列出",
    "介绍",
    "信息",
    "产品",
]

LIST_INTENT_TERMS = ("有哪些", "有什么", "列出", "清单", "列表")
MIN_PRICE_TERMS = ("最低销售价", "最低价", "最便宜", "最低")
ANNUAL_HINT_TERMS = ("年付", "年价", "年费", "包年")
MONTHLY_HINT_TERMS = ("月付", "月价", "月费", "包月")
SETUP_FEE_TERMS = ("调试费", "实施费", "安装费", "初装费")

STATIC_CATEGORY_ALIASES = {
    "语音网关": {"语音网关", "网关", "语音"},
    "SD-WAN": {"sd-wan", "sdwan", "组网", "组网优化"},
    "国际路由优化": {"国际路由优化", "国际路由", "路由优化", "应用加速", "海外访问"},
    "ISP": {"isp", "互联网", "宽带", "专线"},
    "Zoom": {"zoom", "云视频会议", "视频会议"},
    "office365": {"office365", "office 365", "o365", "微软邮箱", "微软365"},
}

_CATALOG_CACHE: dict[str, Any] | None = None
_CATALOG_MTIME_NS: int | None = None
_FLAT_PRODUCTS_CACHE: list[dict[str, Any]] | None = None


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def display_text(value: Any, default: str = "-") -> str:
    if value is None or value == "":
        return default
    return str(value)


def load_product_catalog() -> dict[str, Any]:
    global _CATALOG_CACHE, _CATALOG_MTIME_NS, _FLAT_PRODUCTS_CACHE

    stat = PRODUCTS_PATH.stat()
    if _CATALOG_CACHE is None or _CATALOG_MTIME_NS != stat.st_mtime_ns:
        with PRODUCTS_PATH.open("r", encoding="utf-8") as file:
            _CATALOG_CACHE = json.load(file)
        _CATALOG_MTIME_NS = stat.st_mtime_ns
        _FLAT_PRODUCTS_CACHE = None

    return _CATALOG_CACHE


def load_flat_products() -> list[dict[str, Any]]:
    global _FLAT_PRODUCTS_CACHE

    if _FLAT_PRODUCTS_CACHE is not None:
        return _FLAT_PRODUCTS_CACHE

    catalog = load_product_catalog()
    categories = catalog.get("categories", {})
    flat_products: list[dict[str, Any]] = []

    for category_name, category_payload in categories.items():
        category_key = category_payload.get("category_key")
        for product in category_payload.get("products", []):
            product_row = dict(product)
            product_row["category"] = category_name
            product_row["category_key"] = category_key
            flat_products.append(product_row)

    _FLAT_PRODUCTS_CACHE = flat_products
    return flat_products


def get_category_alias_map() -> dict[str, str]:
    catalog = load_product_catalog()
    alias_map: dict[str, str] = {}

    for category_name, category_payload in catalog.get("categories", {}).items():
        aliases = {category_name, category_payload.get("category_key")}
        aliases.update(STATIC_CATEGORY_ALIASES.get(category_name, set()))
        for alias in aliases:
            alias_text = normalize_text(alias)
            if alias_text:
                alias_map[alias_text] = category_name

    return alias_map


def get_known_brands() -> set[str]:
    return {
        normalize_text(product.get("brand"))
        for product in load_flat_products()
        if normalize_text(product.get("brand"))
    }


def extract_keywords(query: str) -> list[str]:
    cleaned = query.lower()
    for pattern in FILLER_PATTERNS:
        cleaned = cleaned.replace(pattern, " ")
    cleaned = re.sub(r"[，,。！？?\-_/（）()\[\]【】:：]+", " ", cleaned)
    tokens = []
    for piece in cleaned.split():
        piece = piece.strip()
        if not piece:
            continue
        tokens.append(piece)
        tokens.extend(match.group(0) for match in MODEL_PATTERN.finditer(piece))
        tokens.extend(match.group(0) for match in SPEC_PATTERN.finditer(piece))

    keywords = []
    seen = set()
    for token in tokens:
        token = token.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        keywords.append(token)
    return keywords


def parse_query_signals(query: str) -> dict[str, Any]:
    query_norm = normalize_text(query)
    alias_map = get_category_alias_map()

    categories = {
        category_name
        for alias, category_name in alias_map.items()
        if alias and alias in query_norm
    }
    brands = {
        brand
        for brand in get_known_brands()
        if brand and brand in query_norm
    }
    billing_cycles = set()
    if any(term in query_norm for term in ANNUAL_HINT_TERMS):
        billing_cycles.add("年付")
    if any(term in query_norm for term in MONTHLY_HINT_TERMS):
        billing_cycles.add("月付")

    spec_terms = {match.group(0).upper() for match in SPEC_PATTERN.finditer(query)}
    models = {match.group(0).lower() for match in MODEL_PATTERN.finditer(query)}
    has_discount_intent = any(term in query_norm for term in ("折扣", "折后", "优惠", "直签", "ict", "授权"))
    has_setup_fee_intent = any(term in query_norm for term in SETUP_FEE_TERMS)
    wants_list = any(term in query_norm for term in LIST_INTENT_TERMS)
    sort_mode = "default"
    if any(term in query_norm for term in MIN_PRICE_TERMS):
        sort_mode = "min_price"

    return {
        "query_norm": query_norm,
        "categories": categories,
        "brands": brands,
        "billing_cycles": billing_cycles,
        "spec_terms": spec_terms,
        "models": models,
        "has_discount_intent": has_discount_intent,
        "has_setup_fee_intent": has_setup_fee_intent,
        "wants_list": wants_list,
        "sort_mode": sort_mode,
    }


def build_search_blob(product: dict[str, Any]) -> dict[str, str]:
    discount_text = " ".join(
        " ".join(
            [
                display_text(option.get("label"), ""),
                display_text(option.get("description"), ""),
                display_text(option.get("rule_code"), ""),
            ]
        )
        for option in product.get("discount_options", [])
    )
    return {
        "product_id": normalize_text(product.get("product_id")),
        "category": normalize_text(product.get("category")),
        "category_key": normalize_text(product.get("category_key")),
        "product_name": normalize_text(product.get("product_name")),
        "brand": normalize_text(product.get("brand")),
        "model": normalize_text(product.get("model")),
        "spec_text": normalize_text(product.get("spec_text")),
        "spec_detail": normalize_text(product.get("spec_detail")),
        "billing_cycle": normalize_text(product.get("billing_cycle")),
        "unit": normalize_text(product.get("unit")),
        "description": normalize_text(product.get("description")),
        "discounts": normalize_text(discount_text),
        "all_text": normalize_text(
            " ".join(
                [
                    display_text(product.get("product_id"), ""),
                    display_text(product.get("category"), ""),
                    display_text(product.get("product_name"), ""),
                    display_text(product.get("brand"), ""),
                    display_text(product.get("model"), ""),
                    display_text(product.get("spec_text"), ""),
                    display_text(product.get("description"), ""),
                    discount_text,
                ]
            )
        ),
    }


def product_matches_signals(product: dict[str, Any], signals: dict[str, Any]) -> bool:
    search_blob = build_search_blob(product)

    if signals["categories"] and product.get("category") not in signals["categories"]:
        return False
    if signals["brands"] and search_blob["brand"] not in signals["brands"]:
        return False
    if signals["billing_cycles"] and product.get("billing_cycle") not in signals["billing_cycles"]:
        return False
    if signals["models"] and not any(model in search_blob["model"] for model in signals["models"]):
        return False
    if signals["spec_terms"] and not any(
        spec_term.lower() in search_blob["spec_text"] or spec_term.lower() in search_blob["description"]
        for spec_term in signals["spec_terms"]
    ):
        return False
    if signals["has_discount_intent"] and not product.get("discount_options"):
        return False
    if signals["has_setup_fee_intent"] and product.get("setup_fee") is None:
        return False

    return True


def score_product(
    product: dict[str, Any],
    query: str,
    keywords: list[str],
    signals: dict[str, Any],
) -> int:
    query_norm = normalize_text(query)
    search_blob = build_search_blob(product)
    score = 0

    if not query_norm:
        return score

    if query_norm == search_blob["product_id"]:
        score += 140
    if query_norm and query_norm == search_blob["model"]:
        score += 120
    if query_norm and query_norm == search_blob["product_name"]:
        score += 100
    if query_norm and query_norm == search_blob["spec_text"]:
        score += 90

    if query_norm and query_norm in search_blob["product_name"]:
        score += 50
    if query_norm and query_norm in search_blob["model"]:
        score += 45
    if query_norm and query_norm in search_blob["spec_text"]:
        score += 35
    if query_norm and query_norm in search_blob["brand"]:
        score += 25
    if query_norm and query_norm in search_blob["category"]:
        score += 25

    for keyword in keywords:
        if keyword in search_blob["product_name"]:
            score += 20
        if keyword in search_blob["model"]:
            score += 24
        if keyword in search_blob["spec_text"]:
            score += 18
        if keyword in search_blob["brand"]:
            score += 12
        if keyword in search_blob["category"]:
            score += 10
        if keyword in search_blob["discounts"]:
            score += 12
        if keyword in search_blob["description"]:
            score += 5

    if signals["categories"] and product.get("category") in signals["categories"]:
        score += 30
    if signals["brands"] and search_blob["brand"] in signals["brands"]:
        score += 20
    if signals["billing_cycles"] and product.get("billing_cycle") in signals["billing_cycles"]:
        score += 18
    if signals["spec_terms"] and any(spec.lower() in search_blob["spec_text"] for spec in signals["spec_terms"]):
        score += 24
    if signals["models"] and any(model in search_blob["model"] for model in signals["models"]):
        score += 35
    if signals["has_discount_intent"] and product.get("discount_options"):
        score += 15
    if signals["has_setup_fee_intent"] and product.get("setup_fee") is not None:
        score += 12

    return score


def format_discount_rate(discount_rate: float) -> str:
    if discount_rate >= 1:
        return str(discount_rate)
    fold = discount_rate * 10
    if fold.is_integer():
        return f"{int(fold)}折"
    return f"{fold:.1f}折"


def format_discount_options(product: dict[str, Any]) -> list[str]:
    lines = []
    for option in product.get("discount_options", []):
        discounted_price = display_text(option.get("discounted_price"))
        discount_rate = option.get("discount_rate")
        if isinstance(discount_rate, (int, float)):
            discount_label = format_discount_rate(float(discount_rate))
        else:
            discount_label = display_text(discount_rate)
        lines.append(
            f"{display_text(option.get('label'))}: {discounted_price}元"
            f" ({discount_label}, 基于年价 {display_text(option.get('base_price'))}元)"
        )
    return lines


def format_product(product: dict[str, Any]) -> str:
    parts = [
        f"[{display_text(product.get('category'))}] {display_text(product.get('product_name'))}",
        f"ID: {display_text(product.get('product_id'))}",
        f"品牌: {display_text(product.get('brand'))}",
    ]

    if product.get("model"):
        parts.append(f"型号: {display_text(product.get('model'))}")
    if product.get("spec_text"):
        parts.append(f"规格: {display_text(product.get('spec_text'))}")
    if product.get("unit"):
        parts.append(f"单位: {display_text(product.get('unit'))}")

    parts.append(f"标价: {display_text(product.get('sale_price'))}元")

    if product.get("monthly_price") is not None:
        parts.append(f"月价: {display_text(product.get('monthly_price'))}元")
    if product.get("annual_price") is not None:
        parts.append(f"年价: {display_text(product.get('annual_price'))}元")
    if product.get("min_sale_price") is not None:
        parts.append(f"最低销售价: {display_text(product.get('min_sale_price'))}元")
    if product.get("setup_fee") is not None:
        parts.append(f"调试费: {display_text(product.get('setup_fee'))}元")
    if product.get("description"):
        parts.append(f"描述: {display_text(product.get('description'))}")

    discount_lines = format_discount_options(product)
    if discount_lines:
        parts.append("折扣: " + "；".join(discount_lines))

    return " | ".join(parts)


def list_category_summary() -> str:
    catalog = load_product_catalog()
    summary = catalog.get("summary", {})
    categories = summary.get("category_summary", [])
    if not categories:
        return "当前没有可查询的产品数据。"

    lines = ["可查询的产品分类："]
    for category in categories:
        lines.append(f"- {category['category']}：{category['product_count']} 条")
    return "\n".join(lines)


def price_sort_key(product: dict[str, Any]) -> tuple[float, str]:
    price = product.get("min_sale_price")
    if price is None:
        price = product.get("sale_price")
    if price is None:
        price = product.get("monthly_price")
    if price is None:
        price = product.get("annual_price")
    if price is None:
        price = float("inf")
    return float(price), display_text(product.get("product_id"))


def default_sort_key(item: tuple[int, dict[str, Any]]) -> tuple[Any, ...]:
    score, product = item
    return (
        -score,
        display_text(product.get("category")),
        display_text(product.get("source_product_no")),
    )


@tool
def query_products(query: str, limit: int = 5) -> str:
    """
    用于查询产品目录、型号、品牌、价格、最低销售价、调试费、计费周期和折扣规则。
    适用于用户询问“某个产品多少钱”“某个型号是什么”“某个分类有哪些产品”“某种签约折扣后多少钱”等场景。

    Args:
        query: 用户的产品查询问题，例如“HX404G多少钱”“SD-WAN 10M 直签多少钱”
        limit: 最多返回多少条结果，默认 5，最大 10
    """
    if not PRODUCTS_PATH.exists():
        return f"产品数据文件不存在：{PRODUCTS_PATH}"

    if not query or not query.strip():
        return list_category_summary()

    limit = max(1, min(int(limit), MAX_LIMIT))
    signals = parse_query_signals(query)
    keywords = extract_keywords(query)
    flat_products = load_flat_products()

    candidates = [product for product in flat_products if product_matches_signals(product, signals)]
    if not candidates and (signals["categories"] or signals["brands"] or signals["models"] or signals["spec_terms"]):
        candidates = flat_products

    scored = []
    for product in candidates:
        score = score_product(product, query, keywords, signals)
        if score > 0 or product_matches_signals(product, signals):
            scored.append((score, product))

    if not scored:
        return "没有找到匹配的产品。可先按分类查询，例如：语音网关、ISP、SD-WAN、国际路由优化、Zoom、office365。"

    if signals["sort_mode"] == "min_price":
        top_matches = [
            product
            for _, product in sorted(scored, key=lambda item: price_sort_key(item[1]))[:limit]
        ]
        header = f"共找到 {len(scored)} 条匹配结果，按价格从低到高展示前 {len(top_matches)} 条："
    else:
        scored.sort(key=default_sort_key)
        top_matches = [product for _, product in scored[:limit]]
        header = f"共找到 {len(scored)} 条匹配结果，展示前 {len(top_matches)} 条："

    lines = [header]
    for index, product in enumerate(top_matches, start=1):
        lines.append(f"{index}. {format_product(product)}")

    return "\n".join(lines)

