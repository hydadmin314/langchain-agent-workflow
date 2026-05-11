import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain.tools import tool


PRODUCTS_PATH = Path("data/normalized/products.json")
MAX_LIMIT = 10

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


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def display_text(value: Any, default: str = "-") -> str:
    if value is None or value == "":
        return default
    return str(value)


@lru_cache(maxsize=1)
def load_product_catalog() -> dict[str, Any]:
    with PRODUCTS_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


@lru_cache(maxsize=1)
def load_flat_products() -> list[dict[str, Any]]:
    catalog = load_product_catalog()
    categories = catalog.get("categories", {})
    flat_products = []

    for category_name, category_payload in categories.items():
        category_key = category_payload.get("category_key")
        for product in category_payload.get("products", []):
            product_row = dict(product)
            product_row["category"] = category_name
            product_row["category_key"] = category_key
            flat_products.append(product_row)

    return flat_products


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
        for sub_token in re.findall(r"[a-z0-9\.-]+", piece):
            if sub_token != piece:
                tokens.append(sub_token)

    keywords = []
    seen = set()
    for token in tokens:
        token = token.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        keywords.append(token)
    return keywords


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


def score_product(product: dict[str, Any], query: str, keywords: list[str]) -> int:
    query_norm = normalize_text(query)
    search_blob = build_search_blob(product)
    score = 0

    if not query_norm:
        return score

    if query_norm == search_blob["product_id"]:
        score += 120
    if query_norm and query_norm == search_blob["model"]:
        score += 100
    if query_norm and query_norm == search_blob["product_name"]:
        score += 95
    if query_norm and query_norm == search_blob["spec_text"]:
        score += 85

    if query_norm and query_norm in search_blob["product_name"]:
        score += 40
    if query_norm and query_norm in search_blob["model"]:
        score += 45
    if query_norm and query_norm in search_blob["spec_text"]:
        score += 30
    if query_norm and query_norm in search_blob["brand"]:
        score += 25
    if query_norm and query_norm in search_blob["category"]:
        score += 20
    if query_norm and query_norm in search_blob["discounts"]:
        score += 18
    if query_norm and query_norm in search_blob["description"]:
        score += 12

    for keyword in keywords:
        if keyword in search_blob["product_name"]:
            score += 20
        if keyword in search_blob["model"]:
            score += 22
        if keyword in search_blob["spec_text"]:
            score += 14
        if keyword in search_blob["brand"]:
            score += 10
        if keyword in search_blob["category"]:
            score += 8
        if keyword in search_blob["discounts"]:
            score += 10
        if keyword in search_blob["description"]:
            score += 4

    return score


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


def format_discount_rate(discount_rate: float) -> str:
    if discount_rate >= 1:
        return str(discount_rate)
    fold = discount_rate * 10
    if fold.is_integer():
        return f"{int(fold)}折"
    return f"{fold:.1f}折"


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

    if product.get("normalization_flags"):
        parts.append("标记: " + "、".join(product["normalization_flags"]))

    return " | ".join(parts)


def list_category_summary() -> str:
    catalog = load_product_catalog()
    summary = catalog.get("summary", {})
    categories = summary.get("category_summary", [])
    if not categories:
        return "当前没有可查询的产品数据。"

    lines = ["可查询的产品分类："]
    for category in categories:
        lines.append(
            f"- {category['category']}：{category['product_count']} 条"
        )
    return "\n".join(lines)


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
    keywords = extract_keywords(query)
    flat_products = load_flat_products()

    scored = []
    for product in flat_products:
        score = score_product(product, query, keywords)
        if score > 0:
            scored.append((score, product))

    if not scored:
        return "没有找到匹配的产品。可先按分类查询，例如：语音网关、ISP、SD-WAN、国际路由优化、Zoom、office365。"

    scored.sort(
        key=lambda item: (
            -item[0],
            display_text(item[1].get("category")),
            display_text(item[1].get("source_product_no")),
        )
    )
    top_matches = [product for _, product in scored[:limit]]

    lines = [f"共找到 {len(scored)} 条匹配结果，展示前 {len(top_matches)} 条："]
    for index, product in enumerate(top_matches, start=1):
        lines.append(f"{index}. {format_product(product)}")

    return "\n".join(lines)
