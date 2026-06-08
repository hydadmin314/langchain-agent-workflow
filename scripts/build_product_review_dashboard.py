from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT_ROOT / "data" / "product_doc_agent" / "published"
OUTPUT_FILE = PROJECT_ROOT / "web" / "product_review" / "data.js"
CHINA_TZ = timezone(timedelta(hours=8))


def main() -> None:
    documents = [_build_document(path) for path in _iter_json_files(SOURCE_DIR)]
    packages = _flatten_packages(documents)
    dataset = {
        "generatedAt": datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
        "sourceDir": _relative_path(SOURCE_DIR),
        "documents": documents,
        "packages": packages,
        "families": _unique_sorted(doc["productFamily"] for doc in documents),
        "carriers": _unique_sorted(doc["carrier"] for doc in documents),
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dataset, ensure_ascii=False, indent=2)
    OUTPUT_FILE.write_text(f"window.REVIEW_DASHBOARD_DATA = {payload};\n", encoding="utf-8")
    print(f"Wrote {len(documents)} documents and {len(packages)} packages to {_relative_path(OUTPUT_FILE)}")


def _iter_json_files(source_dir: Path) -> list[Path]:
    if not source_dir.exists():
        return []
    return sorted(path for path in source_dir.rglob("*.json") if path.is_file())


def _build_document(json_path: Path) -> dict[str, Any]:
    raw = _read_json(json_path)
    document_info = _as_dict(raw.get("document_info"))
    extraction_meta = _as_dict(raw.get("extraction_meta"))
    raw_category = _as_dict(extraction_meta.get("raw_category"))
    base_package = _as_dict(raw.get("base_package"))

    document_id = _first_text(document_info.get("document_id"), json_path.stem)
    category_levels = _text_list(raw_category.get("category_levels"))
    category_path = _first_text(raw_category.get("category_path"), "/".join(category_levels))
    product_family = _first_text(
        document_info.get("product_family"),
        category_levels[-1] if category_levels else "",
        category_path,
        "未分类",
    )
    title = _first_text(
        document_info.get("title"),
        document_info.get("product_name"),
        document_info.get("filename"),
        document_id,
    )

    base_packages = [_normalize_package(item) for item in _dict_items(base_package.get("packages"))]
    optional_packages = _dict_items(raw.get("optional_packages"))
    materials = _dict_items(raw.get("application_materials"))
    fee_rules = _dict_items(raw.get("fee_and_term_rules"))
    agreement_rules = _dict_items(raw.get("agreement_rules"))
    constraints = _dict_items(raw.get("eligibility_and_constraints"))
    supplemental_rules = _dict_items(raw.get("supplemental_rules"))
    validation_issues = _as_list(extraction_meta.get("validation_issues"))
    schema_warnings = _as_list(extraction_meta.get("schema_warnings"))
    price_summary = _price_summary(base_packages)
    quality_score = _quality_score(
        title=title,
        base_packages=base_packages,
        price_count=price_summary["count"],
        materials=materials,
        rules=[*fee_rules, *agreement_rules, *constraints, *supplemental_rules],
        issue_count=len(validation_issues),
        warning_count=len(schema_warnings),
    )

    return {
        "id": document_id,
        "title": title,
        "productName": _first_text(document_info.get("product_name"), title),
        "productFamily": product_family,
        "carrier": _first_text(document_info.get("carrier"), category_levels[0] if category_levels else "", "未标注"),
        "region": _first_text(document_info.get("region"), "未标注"),
        "version": _text(document_info.get("version")),
        "documentType": _text(document_info.get("document_type")),
        "documentStatus": _first_text(document_info.get("document_status"), "active"),
        "filename": _text(document_info.get("filename")),
        "sourcePath": _text(document_info.get("source_path")),
        "reviewFile": _relative_path(json_path),
        "relativeReviewFile": json_path.name,
        "categoryPath": category_path or "未分类",
        "categoryLevels": category_levels,
        "generatedAt": _text(extraction_meta.get("generated_at")),
        "reviewStatus": _first_text(extraction_meta.get("review_status"), "published"),
        "qualityScore": quality_score,
        "qualityLabel": _quality_label(quality_score),
        "issueCount": len(validation_issues),
        "warningCount": len(schema_warnings),
        "validationIssues": validation_issues,
        "schemaWarnings": schema_warnings,
        "basePackages": base_packages,
        "optionalPackages": optional_packages,
        "materials": materials,
        "feeRules": fee_rules,
        "agreementRules": agreement_rules,
        "constraints": constraints,
        "supplementalRules": supplemental_rules,
        "serviceAttributes": _dict_items(base_package.get("service_attributes")),
        "includedItems": _dict_items(base_package.get("included_items")),
        "priceSummary": price_summary,
        "raw": raw,
    }


def _read_json(json_path: Path) -> dict[str, Any]:
    with json_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"{json_path} root must be a JSON object")
    return payload


def _normalize_package(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    normalized["price"] = _number_or_none(item.get("price"))
    return normalized


def _price_summary(packages: list[dict[str, Any]]) -> dict[str, Any]:
    priced = [pkg for pkg in packages if isinstance(pkg.get("price"), (int, float))]
    periods = _unique_sorted(_text(pkg.get("billing_period")) for pkg in priced)
    if not priced:
        return {"count": 0, "min": None, "max": None, "periods": periods}
    prices = [pkg["price"] for pkg in priced]
    return {"count": len(priced), "min": min(prices), "max": max(prices), "periods": periods}


def _quality_score(
    *,
    title: str,
    base_packages: list[dict[str, Any]],
    price_count: int,
    materials: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    issue_count: int,
    warning_count: int,
) -> int:
    score = 0
    if title:
        score += 10
    if base_packages:
        score += 30
    if price_count:
        score += 20
    if materials:
        score += 15
    if rules:
        score += 15
    if not issue_count and not warning_count:
        score += 10
    score -= min(issue_count * 6 + warning_count * 3, 35)
    return max(0, min(100, score))


def _quality_label(score: int) -> str:
    if score >= 70:
        return "可使用"
    if score >= 35:
        return "可补充"
    return "需复核"


def _flatten_packages(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for doc in documents:
        for package in doc["basePackages"]:
            items.append(
                {
                    "documentId": doc["id"],
                    "documentTitle": doc["title"],
                    "productName": doc["productName"],
                    "packageName": _first_text(package.get("package_name"), package.get("speed"), "未命名套餐"),
                    "speed": _text(package.get("speed")),
                    "price": package.get("price"),
                    "priceText": _price_text(package.get("price")),
                    "billingPeriod": _text(package.get("billing_period")),
                    "contractPeriod": _first_text(package.get("contract_period"), "待确认"),
                    "hasVoice": package.get("has_voice"),
                }
            )
    return items


def _price_text(price: Any) -> str:
    if not isinstance(price, (int, float)):
        return "待确认"
    if float(price).is_integer():
        return f"{int(price)}元"
    return f"{price:.2f}元"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _dict_items(value: Any) -> list[dict[str, Any]]:
    return [item for item in _as_list(value) if isinstance(item, dict)]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _text_list(value: Any) -> list[str]:
    result: list[str] = []
    for item in _as_list(value):
        text = _text(item)
        if text:
            result.append(text)
    return result


def _number_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unique_sorted(values: Any) -> list[str]:
    return sorted({text for value in values if (text := _text(value))})


def _relative_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


if __name__ == "__main__":
    main()
