import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd


EXCEL_PATH = Path("data/raw/产品资费表.xlsx")
NORMALIZED_OUTPUT_PATH = Path("data/normalized/products.json")

SKIP_SHEET_KEYWORDS = ("三级-产品分类",)
PRODUCT_NAME_FIELD = "产品名称"
ID_FIELD = "产品编号"

CATEGORY_KEY_MAP = {
    "语音网关": "voice_gateway",
    "office365": "office365",
    "Zoom": "zoom",
    "SD-WAN": "sd_wan",
    "国际路由优化": "intl_route_optimization",
    "ISP": "isp",
}

CATEGORY_DISCOUNT_RULES = {
    "SD-WAN": [
        {
            "rule_code": "ict_signed",
            "label": "ICT签约",
            "discount_rate": 0.8,
            "applies_to": "annual",
            "description": "授权折扣：ICT签约 8 折",
        },
        {
            "rule_code": "direct_signed",
            "label": "直签",
            "discount_rate": 0.7,
            "applies_to": "annual",
            "description": "授权折扣：直签 7 折",
        },
    ],
    "国际路由优化": [
        {
            "rule_code": "annual_authorized_discount",
            "label": "年付授权折扣",
            "discount_rate": 0.95,
            "applies_to": "annual",
            "description": "授权折扣：年付 95 折",
        }
    ],
}

CORE_FIELDS = {
    "产品编号",
    "产品名称",
    "品牌",
    "型号",
    "规格型号",
    "单位",
    "销售价",
    "销售价（元）",
    "销售价（元/年）",
    "单价（元/月）",
    "最低销售价",
    "调试费",
    "描述",
    "产品描述",
    "备注",
    "产品分类",
}


def write_json(file_path: Path, payload: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def find_header_index(df: pd.DataFrame) -> int:
    for idx, row in df.iterrows():
        if any(isinstance(value, str) and PRODUCT_NAME_FIELD in value for value in row.values):
            return idx
    raise ValueError(f"未找到包含“{PRODUCT_NAME_FIELD}”的表头行。")


def clean_headers(raw_headers: list[Any]) -> list[str]:
    headers = []
    for header in raw_headers:
        text = "" if pd.isna(header) else str(header)
        headers.append(text.strip().replace("\n", ""))
    return headers


def normalize_cell(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def extract_category_items(df: pd.DataFrame) -> list[dict[str, Any]]:
    header_idx = find_header_index(df)
    headers = clean_headers(df.iloc[header_idx].tolist())

    df_data = df.iloc[header_idx + 1 :].copy()
    df_data.columns = headers

    product_name_col = next((col for col in headers if PRODUCT_NAME_FIELD in col), None)
    if product_name_col:
        df_data = df_data.dropna(subset=[product_name_col])
        df_data = df_data[df_data[product_name_col].astype(str).str.strip() != ""]
    else:
        df_data = df_data.dropna(how="all")

    items = []
    counter = 1

    for _, row in df_data.iterrows():
        item = {ID_FIELD: counter}
        for col in headers:
            if ID_FIELD in col:
                continue
            item[col] = normalize_cell(row[col])
        items.append(item)
        counter += 1

    return items


def extract_products_from_excel() -> dict[str, list[dict[str, Any]]]:
    if not EXCEL_PATH.exists():
        raise FileNotFoundError(
            f"找不到输入文件 {EXCEL_PATH}，请确认 data/raw 目录下文件是否存在。"
        )

    print(f"正在读取 Excel 文件: {EXCEL_PATH} ...")
    sheets = pd.read_excel(EXCEL_PATH, sheet_name=None, header=None)

    extracted_payload = {}
    for sheet_name, df in sheets.items():
        if any(keyword in sheet_name for keyword in SKIP_SHEET_KEYWORDS):
            continue

        category_name = sheet_name.strip()
        print(f"正在提取分类: {category_name} ...")
        extracted_payload[category_name] = extract_category_items(df)

    return extracted_payload


def safe_number(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if number.is_integer():
        return int(number)
    return number


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_amount(value: float | int | None) -> int | float | None:
    if value is None:
        return None
    rounded = round(float(value), 2)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def category_key_for(category_name: str) -> str:
    return CATEGORY_KEY_MAP.get(category_name, category_name.lower().replace(" ", "_"))


def build_product_id(category_name: str, source_product_no: Any) -> str:
    category_key = category_key_for(category_name)
    try:
        return f"{category_key}-{int(source_product_no):03d}"
    except (TypeError, ValueError):
        return f"{category_key}-{source_product_no}"


def correct_spec_format(spec_text: str) -> tuple[str, list[str]]:
    flags = []
    match = re.match(r"^(月付|年付)，(\d+(?:\.\d+)?[MG])(\d+(?:\.\d+)?[MG])$", spec_text)
    if match:
        spec_text = f"{match.group(1)}，{match.group(2)}/{match.group(3)}"
        flags.append("spec_auto_corrected")
    return spec_text, flags


def normalize_spec_fields(category_name: str, item: dict[str, Any]) -> dict[str, Any]:
    flags: list[str] = []
    model = normalize_text(item.get("型号"))
    raw_spec_text = normalize_text(item.get("规格型号"))
    unit = normalize_text(item.get("单位"))

    if category_name == "SD-WAN" and raw_spec_text in {"年", "月"} and unit:
        shifted_match = re.match(r"^(年付|月付)，(.+)$", unit)
        if shifted_match:
            raw_spec_text = unit
            unit = "年" if shifted_match.group(1) == "年付" else "月"
            flags.append("column_shift_corrected")

    if raw_spec_text:
        corrected_spec_text, correction_flags = correct_spec_format(raw_spec_text)
        raw_spec_text = corrected_spec_text
        flags.extend(correction_flags)

    billing_cycle = None
    spec_detail = None
    if raw_spec_text:
        spec_match = re.match(r"^(月付|年付)，(.+)$", raw_spec_text)
        if spec_match:
            billing_cycle = spec_match.group(1)
            spec_detail = spec_match.group(2)
        elif raw_spec_text in {"月付", "年付"}:
            billing_cycle = raw_spec_text
        else:
            spec_detail = raw_spec_text

    cycle_from_unit = None
    if unit == "月":
        cycle_from_unit = "月付"
    elif unit == "年":
        cycle_from_unit = "年付"

    if cycle_from_unit:
        if billing_cycle and billing_cycle != cycle_from_unit:
            billing_cycle = cycle_from_unit
            raw_spec_text = f"{billing_cycle}，{spec_detail}" if spec_detail else billing_cycle
            flags.append("billing_cycle_corrected_from_unit")
        elif not billing_cycle:
            billing_cycle = cycle_from_unit

    return {
        "model": model,
        "spec_text": raw_spec_text,
        "spec_detail": spec_detail,
        "billing_cycle": billing_cycle,
        "unit": unit,
        "flags": flags,
    }


def normalize_price_fields(item: dict[str, Any], billing_cycle: str | None, unit: str | None) -> dict[str, Any]:
    sale_price = None
    monthly_price = None
    annual_price = None

    if "销售价" in item:
        sale_price = safe_number(item.get("销售价"))
    elif "销售价（元）" in item:
        sale_price = safe_number(item.get("销售价（元）"))
    elif "销售价（元/年）" in item:
        sale_price = safe_number(item.get("销售价（元/年）"))

    if "单价（元/月）" in item:
        monthly_price = safe_number(item.get("单价（元/月）"))

    if "销售价（元/年）" in item:
        annual_price = safe_number(item.get("销售价（元/年）"))

    if sale_price is not None:
        if billing_cycle == "月付" and monthly_price is None:
            monthly_price = sale_price
        elif billing_cycle == "年付" and annual_price is None:
            annual_price = sale_price
        elif unit == "月" and monthly_price is None:
            monthly_price = sale_price
        elif unit == "年" and annual_price is None:
            annual_price = sale_price

    if annual_price is not None:
        price_basis = "annual"
    elif monthly_price is not None:
        price_basis = "monthly"
    else:
        price_basis = "one_time"

    return {
        "sale_price": sale_price,
        "monthly_price": monthly_price,
        "annual_price": annual_price,
        "price_basis": price_basis,
        "min_sale_price": safe_number(item.get("最低销售价")),
        "setup_fee": safe_number(item.get("调试费")),
    }


def build_raw_attributes(item: dict[str, Any]) -> dict[str, Any]:
    raw_attributes = {}
    for key, value in item.items():
        if key not in CORE_FIELDS and value is not None:
            raw_attributes[key] = value
    return raw_attributes


def build_discount_options(category_name: str, normalized: dict[str, Any]) -> list[dict[str, Any]]:
    rules = CATEGORY_DISCOUNT_RULES.get(category_name, [])
    if not rules:
        return []

    options = []
    for rule in rules:
        if rule["applies_to"] == "annual":
            base_price = normalized.get("annual_price")
            if base_price is None:
                continue
            discounted_price = normalize_amount(base_price * rule["discount_rate"])
            options.append(
                {
                    "rule_code": rule["rule_code"],
                    "label": rule["label"],
                    "description": rule["description"],
                    "discount_rate": rule["discount_rate"],
                    "base_field": "annual_price",
                    "base_price": base_price,
                    "discounted_price": discounted_price,
                }
            )
    return options


def normalize_product(category_name: str, item: dict[str, Any]) -> dict[str, Any]:
    spec_fields = normalize_spec_fields(category_name, item)
    price_fields = normalize_price_fields(item, spec_fields["billing_cycle"], spec_fields["unit"])

    description = normalize_text(item.get("描述")) or normalize_text(item.get("产品描述"))
    notes = normalize_text(item.get("备注"))
    source_product_no = item.get("产品编号")
    product_name = normalize_text(item.get("产品名称"))
    brand = normalize_text(item.get("品牌"))
    source_product_category = normalize_text(item.get("产品分类"))

    normalized = {
        "product_id": build_product_id(category_name, source_product_no),
        "category": category_name,
        "category_key": category_key_for(category_name),
        "source_product_no": source_product_no,
        "product_name": product_name,
        "brand": brand,
        "model": spec_fields["model"],
        "spec_text": spec_fields["spec_text"],
        "spec_detail": spec_fields["spec_detail"],
        "billing_cycle": spec_fields["billing_cycle"],
        "unit": spec_fields["unit"],
        "price_currency": "CNY",
        "price_basis": price_fields["price_basis"],
        "sale_price": price_fields["sale_price"],
        "monthly_price": price_fields["monthly_price"],
        "annual_price": price_fields["annual_price"],
        "min_sale_price": price_fields["min_sale_price"],
        "setup_fee": price_fields["setup_fee"],
        "description": description,
        "notes": notes,
        "normalization_flags": list(dict.fromkeys(spec_fields["flags"])),
        "raw_attributes": build_raw_attributes(item),
    }
    if source_product_category and source_product_category != category_name:
        normalized["source_product_category"] = source_product_category
    normalized["discount_options"] = build_discount_options(category_name, normalized)
    return normalized


def add_flag(product: dict[str, Any], flag: str) -> None:
    if flag not in product["normalization_flags"]:
        product["normalization_flags"].append(flag)


def attach_post_normalization_flags(products: list[dict[str, Any]]) -> None:
    duplicate_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    billing_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)

    for product in products:
        duplicate_key = (
            product["category_key"],
            product["product_name"],
            product["brand"],
            product["model"],
            product["spec_text"],
            product["unit"],
        )
        duplicate_groups[duplicate_key].append(product)

        if product["spec_detail"]:
            billing_key = (
                product["category_key"],
                product["product_name"],
                product["brand"],
                product["spec_detail"],
            )
            billing_groups[billing_key].append(product)

    for group in duplicate_groups.values():
        distinct_prices = {product["sale_price"] for product in group if product["sale_price"] is not None}
        if len(group) > 1 and len(distinct_prices) > 1:
            for product in group:
                add_flag(product, "duplicate_price_conflict")

    for group in billing_groups.values():
        monthly = next((product for product in group if product["price_basis"] == "monthly"), None)
        annual = next((product for product in group if product["price_basis"] == "annual"), None)
        if not monthly or not annual:
            continue
        if monthly["monthly_price"] in (None, 0) or annual["annual_price"] is None:
            continue
        if not monthly["spec_detail"] or "初装费" in monthly["spec_detail"] or "IPv4" in monthly["spec_detail"]:
            continue
        ratio = annual["annual_price"] / monthly["monthly_price"]
        if ratio < 6 or ratio > 15:
            add_flag(monthly, "billing_ratio_anomaly")
            add_flag(annual, "billing_ratio_anomaly")


def build_summary(products: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for product in products:
        grouped[product["category"]].append(product)

    category_summary = []
    for category_name, category_products in grouped.items():
        category_summary.append(
            {
                "category": category_name,
                "category_key": category_key_for(category_name),
                "product_count": len(category_products),
                "flagged_product_count": sum(
                    1 for product in category_products if product["normalization_flags"]
                ),
            }
        )

    return {
        "product_count": len(products),
        "category_count": len(grouped),
        "flagged_product_count": sum(1 for product in products if product["normalization_flags"]),
        "category_summary": sorted(category_summary, key=lambda item: item["category_key"]),
    }


def build_grouped_categories(products: list[dict[str, Any]]) -> dict[str, Any]:
    grouped_payload: dict[str, Any] = {}
    grouped_products: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for product in products:
        grouped_products[product["category"]].append(product)

    for category_name, category_products in grouped_products.items():
        products_for_output = []
        for product in category_products:
            product_view = {
                key: value
                for key, value in product.items()
                if key not in {"category", "category_key"}
            }
            products_for_output.append(product_view)

        grouped_payload[category_name] = {
            "category_key": category_key_for(category_name),
            "product_count": len(category_products),
            "flagged_product_count": sum(
                1 for product in category_products if product["normalization_flags"]
            ),
            "products": products_for_output,
        }

    return grouped_payload


def normalize_extracted_payload(extracted_payload: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    normalized_products = []
    for category_name, items in extracted_payload.items():
        for item in items:
            normalized_products.append(normalize_product(category_name, item))

    attach_post_normalization_flags(normalized_products)
    normalized_products.sort(key=lambda product: (product["category_key"], product["source_product_no"]))

    return {
        "summary": build_summary(normalized_products),
        "categories": build_grouped_categories(normalized_products),
    }


def extract_and_normalize_products_from_excel() -> dict[str, Any]:
    extracted_payload = extract_products_from_excel()
    normalized_payload = normalize_extracted_payload(extracted_payload)
    write_json(NORMALIZED_OUTPUT_PATH, normalized_payload)

    print("")
    print(f"标准化结果已输出到: {NORMALIZED_OUTPUT_PATH}")
    print(
        "处理汇总: "
        f"{normalized_payload['summary']['category_count']} 个分类, "
        f"{normalized_payload['summary']['product_count']} 条产品, "
        f"{normalized_payload['summary']['flagged_product_count']} 条带标记记录"
    )
    return normalized_payload


if __name__ == "__main__":
    extract_and_normalize_products_from_excel()
