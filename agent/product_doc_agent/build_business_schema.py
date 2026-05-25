import json
import argparse
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "data" / "product_doc_agent" / "output"
DATA_DIR = ROOT / "data" / "raw"
ENRICHED_FILE = OUTPUT_DIR / "all_documents_enriched.json"
LOCAL_FILE = OUTPUT_DIR / "all_documents.json"
SCHEMA_BUNDLE_FILE = OUTPUT_DIR / "all_documents_business_schema.json"


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Build the final business schema. It can reuse existing extraction files, "
            "or run local table extraction and LLM enrichment in one command."
        )
    )
    parser.add_argument(
        "--refresh-local",
        action="store_true",
        help="Scan data/*.docx and run local table extraction before schema conversion.",
    )
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help="Run LLM normalization, explanation, validation, and self-check before schema conversion.",
    )
    parser.add_argument(
        "--reuse-llm-cache",
        action="store_true",
        help="Reuse demo/output/document_N_enriched.json when present instead of calling the LLM again.",
    )
    parser.add_argument(
        "--only",
        type=int,
        help="Only process one 1-based document index. Other documents are omitted from the output bundle.",
    )
    parser.add_argument(
        "--write-intermediate",
        action="store_true",
        help="Also write local/enriched intermediate JSON files for debugging.",
    )
    return parser.parse_args()


def build_local_source(write_intermediate: bool) -> tuple[dict, str]:
    from agent.product_doc_agent.extract_docs import build_document

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    documents = []
    for index, path in enumerate(sorted(DATA_DIR.glob("*.docx")), 1):
        document = build_document(path)
        document["id"] = f"doc-{index}"
        documents.append(document)
        if write_intermediate:
            (OUTPUT_DIR / f"document_{index}.json").write_text(
                json.dumps(document, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    bundle = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_directory": str(DATA_DIR.relative_to(ROOT)),
        "document_count": len(documents),
        "documents": documents,
    }
    if write_intermediate:
        LOCAL_FILE.write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return bundle, "data/raw/*.docx"


def load_existing_source() -> tuple[dict, str]:
    if ENRICHED_FILE.exists():
        return json.loads(ENRICHED_FILE.read_text(encoding="utf-8")), str(ENRICHED_FILE.relative_to(ROOT))
    if LOCAL_FILE.exists():
        return json.loads(LOCAL_FILE.read_text(encoding="utf-8")), str(LOCAL_FILE.relative_to(ROOT))
    raise FileNotFoundError("Run demo/extract_docs.py first.")


def select_documents(source: dict, only: int | None) -> list[tuple[int, dict]]:
    documents = list(enumerate(source.get("documents", []), 1))
    if only is None:
        return documents
    return [(index, doc) for index, doc in documents if index == only]


def enrich_documents(
    indexed_documents: list[tuple[int, dict]],
    reuse_cache: bool,
    write_intermediate: bool,
) -> list[tuple[int, dict]]:
    from agent.product_doc_agent.enrich_with_llm import enrich_document, get_llm

    llm = get_llm()
    enriched_documents = []
    for index, doc in indexed_documents:
        cache_path = OUTPUT_DIR / f"document_{index}_enriched.json"
        if reuse_cache and cache_path.exists():
            print(f"Reusing LLM cache {cache_path.name}")
            enriched_documents.append((index, json.loads(cache_path.read_text(encoding="utf-8"))))
            continue

        print(f"LLM enriching {index}: {doc.get('title') or doc.get('filename')}")
        try:
            enriched = enrich_document(llm, doc)
        except Exception as exc:
            enriched = dict(doc)
            enriched["llm_enrichment_error"] = {
                "error_type": exc.__class__.__name__,
                "message": str(exc),
            }
            print(f"LLM enrichment failed for document {index}: {exc}")

        if write_intermediate:
            cache_path.write_text(
                json.dumps(enriched, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        enriched_documents.append((index, enriched))
    return enriched_documents


def non_empty(value):
    return value not in ("", None, [], {})


def compact_text(text, limit=900):
    if not isinstance(text, str):
        return text
    return text if len(text) <= limit else text[:limit] + "..."


def package_price_status(item):
    if item.get("pricing_type") == "customer_input":
        return "customer_input"
    if item.get("amount") is None or item.get("price") is None:
        return "unknown" if item.get("amount") is None and item.get("price") is None else "fixed"
    return "fixed"


def normalize_package(item):
    price = item.get("price", item.get("amount"))
    return {
        "package_name": item.get("package_name") or item.get("name") or item.get("speed", ""),
        "speed": item.get("speed", ""),
        "price": price,
        "currency": item.get("currency", "CNY"),
        "billing_period": item.get("billing_period", ""),
        "price_status": item.get("price_status") or package_price_status(item),
        "source_evidence": item.get("source_evidence") or item.get("raw", ""),
    }


def classify_optional_package(service):
    text = " ".join(str(service.get(k, "")) for k in ("service_name", "name", "fee_summary", "description"))
    if "0元" in text or "免费" in text or "免收" in text:
        return "free_optional_package"
    if "元/" in text or "费用" in text or "收费" in text:
        return "paid_optional_package"
    if "权益" in text:
        return "benefit_package"
    return "optional_package"


def normalize_optional_from_llm(service):
    return {
        "package_type": classify_optional_package(service),
        "name": service.get("service_name", ""),
        "fee_summary": service.get("fee_summary", ""),
        "description": service.get("notes", ""),
        "source_evidence": "",
    }


def normalize_optional_from_local(item):
    return {
        "package_type": classify_optional_package(item),
        "name": item.get("name", item.get("category", "")),
        "fee_summary": "",
        "description": compact_text(item.get("description", "")),
        "source_evidence": item.get("description", ""),
        "options": item.get("options", []),
    }


def fee_rule_from_item(item):
    return {
        "rule_type": "fee_rule",
        "name": item.get("name", item.get("category", "")),
        "description": compact_text(item.get("description", "")),
        "source_evidence": item.get("description", ""),
    }


def agreement_rule(rule, index):
    return {
        "rule_type": infer_agreement_rule_type(rule),
        "title": f"规则{index}",
        "description": compact_text(rule),
        "source_evidence": rule,
    }


def infer_agreement_rule_type(text):
    keywords = [
        ("违约", "breach_or_penalty"),
        ("退订", "termination"),
        ("注销", "termination"),
        ("拆机", "termination"),
        ("协议期", "term"),
        ("续约", "renewal"),
        ("自动延展", "renewal"),
        ("限制", "restriction"),
        ("不得", "restriction"),
        ("售后", "after_sales"),
        ("维修", "after_sales"),
        ("服务热线", "after_sales"),
    ]
    for key, rule_type in keywords:
        if key in text:
            return rule_type
    return "agreement_rule"


def build_document_info(doc, enriched):
    normalized = enriched.get("normalized_product", {})
    return {
        "document_type": normalized.get("document_type") or doc.get("document_type"),
        "title": doc.get("title", ""),
        "product_name": normalized.get("product_name") or doc.get("product_name", ""),
        "issuer": normalized.get("issuer") or doc.get("issuer", ""),
        "version": normalized.get("version") or doc.get("version", ""),
        "effective_from": normalized.get("effective_from") or doc.get("effective_from", ""),
        "filename": doc.get("filename", ""),
        "source_path": doc.get("source_path", ""),
        "summary": enriched.get("business_explanation", {}).get("one_sentence") or doc.get("summary", ""),
    }


def build_parties_and_application(doc):
    fields = doc.get("customer_application_fields", [])
    return {
        "service_provider": {
            "name": doc.get("issuer", ""),
        },
        "customer": {
            "filled_values": doc.get("filled_customer_values", []),
            "is_blank_form": not bool(doc.get("filled_customer_values")),
        },
        "application_fields": {
            "required": [field for field in fields if field.get("required")],
            "optional": [field for field in fields if not field.get("required")],
        },
        "application_notes": [
            warning for warning in doc.get("quality", {}).get("warnings", [])
            if "空白" in warning or "填写" in warning
        ],
    }


def build_base_package(doc, enriched):
    normalized_packages = enriched.get("normalized_packages") or doc.get("base_packages", [])
    return {
        "packages": [normalize_package(item) for item in normalized_packages],
        "included_items": [
            {
                "category": item.get("category", ""),
                "name": item.get("name", ""),
                "description": compact_text(item.get("description", "")),
                "source_evidence": item.get("description", ""),
            }
            for item in doc.get("included_items", [])
        ],
        "sla": doc.get("sla", {}),
    }


def build_optional_packages(doc, enriched):
    if enriched.get("optional_services_summary"):
        return [normalize_optional_from_llm(item) for item in enriched["optional_services_summary"]]
    return [normalize_optional_from_local(item) for item in doc.get("optional_services", [])]


def build_fee_and_term_rules(doc, enriched):
    rules = [fee_rule_from_item(item) for item in doc.get("fees", [])]
    for issue in enriched.get("validation_issues", []):
        field = issue.get("field", "")
        if field in {"base_packages", "fee_and_term_rules"}:
            rules.append({
                "rule_type": "validation_issue",
                "severity": issue.get("severity", ""),
                "name": field,
                "description": issue.get("issue", ""),
                "suggested_action": issue.get("suggested_action", ""),
            })
    return rules


def build_agreement_rules(doc, enriched):
    rules = [agreement_rule(rule, index) for index, rule in enumerate(doc.get("marketing_rules", []), 1)]
    for issue in enriched.get("validation_issues", []):
        field = issue.get("field", "")
        if field not in {"base_packages", "fee_and_term_rules"}:
            rules.append({
                "rule_type": "validation_issue",
                "severity": issue.get("severity", ""),
                "title": field,
                "description": issue.get("issue", ""),
                "suggested_action": issue.get("suggested_action", ""),
            })
    return rules


def build_supplemental_rules(doc):
    supplemental = []
    for form in doc.get("supporting_forms", []):
        item = {
            "source_type": "supporting_form",
            "name": form.get("name", ""),
            "table_index": form.get("table_index"),
        }
        if non_empty(form.get("required_fields")):
            item["required_fields"] = form["required_fields"]
        if non_empty(form.get("columns")):
            item["columns"] = form["columns"]
        supplemental.append(item)
    if doc.get("sla", {}).get("compensation_rules"):
        supplemental.append({
            "source_type": "sla_attachment",
            "name": "SLA赔付规则",
            "rules": doc["sla"]["compensation_rules"],
        })
    return supplemental


def build_extraction_meta(doc, enriched, source_file):
    self_check = enriched.get("self_check", {})
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_file": source_file,
        "method": "local_rule_extraction_then_llm_business_schema",
        "local_rule_quality": doc.get("quality", {}),
        "llm_confidence": self_check.get("confidence"),
        "llm_self_check": self_check,
        "validation_issue_count": len(enriched.get("validation_issues", [])),
        "validation_issues": enriched.get("validation_issues", []),
        "raw_document_id": doc.get("id", ""),
    }


def transform_document(doc, source_file):
    enriched = doc.get("llm_enrichment", {})
    return {
        "document_info": build_document_info(doc, enriched),
        "parties_and_application": build_parties_and_application(doc),
        "base_package": build_base_package(doc, enriched),
        "optional_packages": build_optional_packages(doc, enriched),
        "fee_and_term_rules": build_fee_and_term_rules(doc, enriched),
        "agreement_rules": build_agreement_rules(doc, enriched),
        "supplemental_rules": build_supplemental_rules(doc),
        "extraction_meta": build_extraction_meta(doc, enriched, source_file),
    }


def main():
    args = parse_args()
    if args.refresh_local:
        source, source_file = build_local_source(args.write_intermediate)
    else:
        source, source_file = load_existing_source()

    indexed_documents = select_documents(source, args.only)
    if args.with_llm:
        indexed_documents = enrich_documents(
            indexed_documents,
            reuse_cache=args.reuse_llm_cache,
            write_intermediate=args.write_intermediate,
        )

    documents = []
    output_paths = []
    for index, doc in indexed_documents:
        transformed = transform_document(doc, source_file)
        documents.append(transformed)
        output_path = OUTPUT_DIR / f"document_{index}_business_schema.json"
        output_paths.append(output_path)
        output_path.write_text(
            json.dumps(transformed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    bundle = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_file": source_file,
        "document_count": len(documents),
        "pipeline": {
            "refresh_local": args.refresh_local,
            "with_llm": args.with_llm,
            "reuse_llm_cache": args.reuse_llm_cache,
            "only": args.only,
            "write_intermediate": args.write_intermediate,
        },
        "documents": documents,
    }
    SCHEMA_BUNDLE_FILE.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "document_count": len(documents),
        "bundle": str(SCHEMA_BUNDLE_FILE.relative_to(ROOT)),
        "per_document_outputs": [str(path.relative_to(ROOT)) for path in output_paths],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
