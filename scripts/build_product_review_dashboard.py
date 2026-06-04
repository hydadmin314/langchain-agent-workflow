"""Build the static product review dashboard data from published JSON files."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_SOURCE_DIR = Path("data") / "product_doc_agent" / "published"
DEFAULT_OUTPUT = Path("dashboard") / "product_review" / "data.js"


def text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, list):
        items = [text(item) for item in value if text(item)]
        return "、".join(items) if items else fallback
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    value_text = str(value).strip()
    return value_text if value_text else fallback


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = re.search(r"\d+(?:\.\d+)?", value.replace(",", ""))
    return float(match.group(0)) if match else None


def money_display(value: Any) -> str:
    value_number = number(value)
    if value_number is None:
        return text(value, "待确认")
    if value_number.is_integer():
        return f"{int(value_number)}元"
    return f"{value_number:g}元"


def compact_location(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: compact_location(item) for key, item in value.items() if key not in {"path", "source_path"}}
    if isinstance(value, list):
        return [compact_location(item) for item in value]
    return value


def clean_record(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"source_location"}:
                cleaned[key] = compact_location(item)
            else:
                cleaned[key] = clean_record(item)
        return cleaned
    if isinstance(value, list):
        return [clean_record(item) for item in value]
    return value


def collect_price_items(doc: dict[str, Any]) -> list[dict[str, Any]]:
    prices: list[dict[str, Any]] = []
    info = as_dict(doc.get("document_info"))
    product_name = text(info.get("product_name") or info.get("title"), "未命名产品")

    for pkg in as_list(as_dict(doc.get("base_package")).get("packages")):
        if not isinstance(pkg, dict):
            continue
        value = number(pkg.get("price"))
        if value is None:
            continue
        prices.append(
            {
                "name": text(pkg.get("package_name"), product_name),
                "price": value,
                "billingPeriod": text(pkg.get("billing_period"), "待确认"),
                "speed": text(pkg.get("speed") or pkg.get("bandwidth_unit")),
                "kind": "基础套餐",
            }
        )

    for item in as_list(doc.get("optional_packages")):
        if not isinstance(item, dict):
            continue
        fee_summary = text(item.get("fee_summary"))
        value = number(fee_summary)
        if value is None:
            continue
        prices.append(
            {
                "name": text(item.get("name"), "可选包"),
                "price": value,
                "billingPeriod": fee_summary,
                "speed": "",
                "kind": "可选包",
            }
        )

    return prices


def price_summary(prices: list[dict[str, Any]]) -> dict[str, Any]:
    values = [item["price"] for item in prices if isinstance(item.get("price"), (int, float))]
    periods = sorted({text(item.get("billingPeriod")) for item in prices if text(item.get("billingPeriod"))})
    return {
        "count": len(values),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "periods": periods[:5],
    }


def quality_label(score: int, issue_count: int) -> str:
    if issue_count >= 4 or score < 50:
        return "需复核"
    if issue_count > 0 or score < 78:
        return "可补充"
    return "可使用"


def score_document(doc: dict[str, Any]) -> int:
    info = as_dict(doc.get("document_info"))
    base = as_dict(doc.get("base_package"))
    meta = as_dict(doc.get("extraction_meta"))
    checks = [
        bool(text(info.get("product_name") or info.get("title"))),
        bool(text(info.get("carrier"))),
        bool(as_list(base.get("packages"))),
        bool(as_list(doc.get("optional_packages"))),
        bool(as_list(doc.get("application_materials"))),
        bool(as_list(doc.get("fee_and_term_rules")) or as_list(doc.get("agreement_rules"))),
        bool(text(as_dict(meta.get("raw_category")).get("category_path"))),
    ]
    issue_count = int(number(meta.get("validation_issue_count")) or len(as_list(meta.get("validation_issues"))))
    warning_count = len(as_list(meta.get("schema_warnings")))
    score = round(sum(checks) / len(checks) * 100)
    return max(0, min(100, score - min(issue_count * 5 + warning_count * 2, 35)))


def normalize_document(path: Path, source_dir: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    info = as_dict(raw.get("document_info"))
    meta = as_dict(raw.get("extraction_meta"))
    raw_category = as_dict(meta.get("raw_category"))
    base_package = as_dict(raw.get("base_package"))
    parties = as_dict(raw.get("parties_and_application"))
    application_fields = as_dict(parties.get("application_fields"))

    prices = collect_price_items(raw)
    score = score_document(raw)
    issue_count = int(number(meta.get("validation_issue_count")) or len(as_list(meta.get("validation_issues"))))
    title = text(info.get("title") or info.get("product_name") or path.stem, path.stem)
    product_name = text(info.get("product_name") or title, title)
    category_path = text(raw_category.get("category_path"))
    category_levels = as_list(raw_category.get("category_levels"))

    return {
        "id": text(info.get("document_id"), path.stem),
        "title": title,
        "productName": product_name,
        "productFamily": text(info.get("product_family") or (category_levels[-1] if category_levels else ""), "未归类"),
        "carrier": text(info.get("carrier") or as_dict(parties.get("service_provider")).get("carrier"), "未标注"),
        "region": text(info.get("region"), "未标注"),
        "version": text(info.get("version")),
        "documentType": text(info.get("document_type")),
        "documentStatus": text(info.get("document_status"), "active"),
        "filename": text(info.get("filename"), path.name),
        "sourcePath": text(info.get("source_path") or meta.get("source_file")),
        "reviewFile": str(path.as_posix()),
        "relativeReviewFile": str(path.relative_to(source_dir).as_posix()),
        "categoryPath": category_path or "未归类",
        "categoryLevels": [text(item) for item in category_levels],
        "generatedAt": text(meta.get("generated_at")),
        "reviewStatus": text(meta.get("review_status"), "published"),
        "qualityScore": score,
        "qualityLabel": quality_label(score, issue_count),
        "issueCount": issue_count,
        "warningCount": len(as_list(meta.get("schema_warnings"))),
        "validationIssues": clean_record(as_list(meta.get("validation_issues"))),
        "schemaWarnings": clean_record(as_list(meta.get("schema_warnings"))),
        "basePackages": clean_record(as_list(base_package.get("packages"))),
        "includedItems": clean_record(as_list(base_package.get("included_items"))),
        "serviceAttributes": clean_record(as_list(base_package.get("service_attributes"))),
        "optionalPackages": clean_record(as_list(raw.get("optional_packages"))),
        "feeRules": clean_record(as_list(raw.get("fee_and_term_rules"))),
        "agreementRules": clean_record(as_list(raw.get("agreement_rules"))),
        "materials": clean_record(as_list(raw.get("application_materials"))),
        "constraints": clean_record(as_list(raw.get("eligibility_and_constraints"))),
        "supplementalRules": clean_record(as_list(raw.get("supplemental_rules"))),
        "requiredFields": clean_record(as_list(application_fields.get("required"))),
        "optionalFields": clean_record(as_list(application_fields.get("optional"))),
        "priceItems": prices,
        "priceSummary": price_summary(prices),
        "raw": clean_record(raw),
    }


def build_dataset(source_dir: Path) -> dict[str, Any]:
    documents = [normalize_document(path, source_dir) for path in sorted(source_dir.glob("*.json"))]
    packages: list[dict[str, Any]] = []
    for doc in documents:
        for pkg in doc["basePackages"]:
            if not isinstance(pkg, dict):
                continue
            packages.append(
                {
                    "documentId": doc["id"],
                    "documentTitle": doc["title"],
                    "productName": doc["productName"],
                    "packageName": text(pkg.get("package_name"), doc["productName"]),
                    "speed": text(pkg.get("speed") or pkg.get("bandwidth_unit")),
                    "price": number(pkg.get("price")),
                    "priceText": money_display(pkg.get("price")),
                    "billingPeriod": text(pkg.get("billing_period"), "待确认"),
                    "contractPeriod": text(pkg.get("contract_period"), "待确认"),
                    "hasVoice": pkg.get("has_voice"),
                }
            )

    families = sorted({doc["productFamily"] for doc in documents})
    carriers = sorted({doc["carrier"] for doc in documents})
    return {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sourceDir": str(source_dir.as_posix()),
        "documents": documents,
        "packages": packages,
        "families": families,
        "carriers": carriers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    source_dir = args.source_dir
    if not source_dir.exists():
        raise SystemExit(f"Source directory does not exist: {source_dir}")

    dataset = build_dataset(source_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dataset, ensure_ascii=False, indent=2)
    args.output.write_text(f"window.REVIEW_DASHBOARD_DATA = {payload};\n", encoding="utf-8")
    print(f"Wrote {args.output} from {len(dataset['documents'])} published JSON files.")


if __name__ == "__main__":
    main()
