import json
import argparse
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "data" / "product_doc_agent" / "output"
SOURCE_FILE = OUTPUT_DIR / "all_documents.json"
ENRICHED_FILE = OUTPUT_DIR / "all_documents_enriched.json"


def strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    return text.strip()


def parse_json_response(text: str) -> dict:
    text = strip_code_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def build_llm_payload(doc: dict) -> dict:
    return {
        "document_id": doc.get("id"),
        "filename": doc.get("filename"),
        "title": doc.get("title"),
        "issuer": doc.get("issuer"),
        "version": doc.get("version"),
        "effective_from": doc.get("effective_from"),
        "product_name": doc.get("product_name"),
        "rule_summary": doc.get("summary"),
        "customer_application_fields": doc.get("customer_application_fields", []),
        "filled_customer_values": doc.get("filled_customer_values", []),
        "base_packages": doc.get("base_packages", []),
        "included_items": doc.get("included_items", []),
        "optional_services": doc.get("optional_services", []),
        "fees": doc.get("fees", []),
        "sla": doc.get("sla", {}),
        "supporting_forms": doc.get("supporting_forms", []),
        "marketing_rules": doc.get("marketing_rules", []),
        "raw_business_rows": doc.get("raw_business_rows", []),
        "quality": doc.get("quality", {}),
    }


def enrichment_prompt(payload: dict) -> str:
    return f"""你是运营商产品文档审核助手。下面是本地规则从 Word 表格中抽取出的事实数据，文档可能来自电信、联通、移动或其它服务商。

请严格基于这些事实做归一化、解释、补充校验和自检。不要编造本地事实中不存在的价格、速率、服务或条款。

请只返回 JSON，schema 如下：
{{
  "normalized_product": {{
    "product_name": "...",
    "document_type": "carrier_product_application_form",
    "issuer": "...",
    "effective_from": "...",
    "version": "..."
  }},
  "normalized_packages": [
    {{
      "package_name": "...",
      "speed": "...",
      "price": 0,
      "currency": "CNY",
      "billing_period": "月|年|2年|需填写",
      "price_status": "fixed|customer_input|unknown",
      "source_evidence": "引用本地事实中的原始片段"
    }}
  ],
  "optional_services_summary": [
    {{
      "service_name": "...",
      "fee_summary": "...",
      "notes": "..."
    }}
  ],
  "business_explanation": {{
    "one_sentence": "...",
    "key_points": ["...", "..."],
    "suitable_for_business_review": true
  }},
  "validation_issues": [
    {{
      "severity": "high|medium|low",
      "field": "...",
      "issue": "...",
      "suggested_action": "..."
    }}
  ],
  "self_check": {{
    "checked_package_count": 0,
    "checked_optional_service_count": 0,
    "missing_or_uncertain_items": ["..."],
    "confidence": 0.0
  }}
}}

本地事实 JSON：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


def get_llm():
    sys.path.insert(0, str(ROOT))
    from config.llm_config import get_llm as build_llm

    return build_llm()


def enrich_document(llm, doc: dict) -> dict:
    payload = build_llm_payload(doc)
    response = llm.invoke(enrichment_prompt(payload))
    content = response.content if hasattr(response, "content") else str(response)
    enrichment = parse_json_response(content)
    enriched = dict(doc)
    enriched["llm_enrichment"] = enrichment
    enriched["llm_enrichment_meta"] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "local_rule_extraction_then_llm_enrichment",
    }
    return enriched


def parse_args():
    parser = argparse.ArgumentParser(
        description="Enrich local rule extraction results with LLM normalization and validation."
    )
    parser.add_argument(
        "--only",
        type=int,
        help="Only enrich one 1-based document index from all_documents.json.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate enriched JSON even if document_N_enriched.json already exists.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not SOURCE_FILE.exists():
        raise FileNotFoundError(f"Run demo/extract_docs.py first: {SOURCE_FILE}")

    source = json.loads(SOURCE_FILE.read_text(encoding="utf-8"))
    llm = get_llm()
    enriched_documents = []

    total = source.get("document_count")
    for index, doc in enumerate(source.get("documents", []), 1):
        output_path = OUTPUT_DIR / f"document_{index}_enriched.json"
        if args.only and index != args.only:
            if output_path.exists():
                enriched_documents.append(json.loads(output_path.read_text(encoding="utf-8")))
            else:
                enriched_documents.append(doc)
            continue
        if output_path.exists() and not args.force:
            print(f"Skipping {index}/{total}: existing {output_path.name}")
            enriched_documents.append(json.loads(output_path.read_text(encoding="utf-8")))
            continue
        print(f"Enriching {index}/{source.get('document_count')}: {doc.get('title') or doc.get('filename')}")
        enriched = enrich_document(llm, doc)
        enriched_documents.append(enriched)
        output_path.write_text(
            json.dumps(enriched, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    bundle = dict(source)
    bundle["documents"] = enriched_documents
    bundle["enrichment"] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "method": "local_rule_extraction_then_llm_normalization_validation_self_check",
        "source_file": str(SOURCE_FILE.relative_to(ROOT)),
    }
    ENRICHED_FILE.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps({
        "document_count": len(enriched_documents),
        "bundle": str(ENRICHED_FILE.relative_to(ROOT)),
        "per_document_outputs": [
            str((OUTPUT_DIR / f"document_{i}_enriched.json").relative_to(ROOT))
            for i in range(1, len(enriched_documents) + 1)
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
