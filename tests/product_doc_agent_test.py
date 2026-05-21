from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.product_doc_agent.api import PublishedQueryAPI
from agent.product_doc_agent.models import Release, to_dict
from agent.product_doc_agent.pipeline import ProductDocumentPipeline
from agent.product_doc_agent.release import ReleaseService
from agent.product_doc_agent.storage import JsonStore


DEFAULT_STORE_ROOT = PROJECT_ROOT / "data" / "product_doc_agent"
DEFAULT_UPLOAD_DIR = PROJECT_ROOT / "data" / "raw" / "uploads"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run product_doc_agent test flow.")
    parser.add_argument("--doc", help="DOCX path. If omitted, the first data/raw/*.docx file is used.")
    parser.add_argument("--disable-llm", action="store_true", help="Disable LLM only for local troubleshooting.")
    parser.add_argument("--publish", action="store_true", help="Run submit-review, approve, and publish.")
    parser.add_argument("--keep-data", action="store_true", help="Do not clear data/product_doc_agent before running.")
    parser.add_argument("--reviewer", default="tester")
    parser.add_argument("--sample-size", type=int, default=5)
    args = parser.parse_args()

    doc_path = resolve_doc_path(args.doc)
    if not args.keep_data:
        clear_runtime_data()

    store = JsonStore(root=DEFAULT_STORE_ROOT, upload_dir=DEFAULT_UPLOAD_DIR)
    enable_llm = not args.disable_llm
    pipeline = ProductDocumentPipeline(store=store, enable_llm=enable_llm)
    release_service = ReleaseService(store=store)
    published_api = PublishedQueryAPI(store=store)

    print_step("1. Input Document")
    print_json({"doc": str(doc_path), "enable_llm": enable_llm, "publish": args.publish})

    print_step("2. Create Draft")
    release = pipeline.run_to_draft(doc_path)
    print_json(release_summary(release))

    if args.publish and release.status != "published":
        print_step("3. Review And Publish")
        release_service.submit_for_review(release.release_id, reviewer=args.reviewer)
        release_service.approve(release.release_id, reviewer=args.reviewer)
        published = release_service.publish(release.release_id, reviewer=args.reviewer)
        print_json(release_summary(published))

        print_step("4. Published Query Samples")
        print_json(
            {
                "product": published_api.find_facts(published.release_id, domain="product")[: args.sample_size],
                "plan": published_api.find_facts(published.release_id, domain="plan")[: args.sample_size],
                "fee": published_api.find_facts(published.release_id, domain="fee")[: args.sample_size],
                "form_field": published_api.find_facts(published.release_id, domain="form_field")[: args.sample_size],
            }
        )
    else:
        print_step("3. Structured Data For Review")
        print_json(structured_data_sample(release, args.sample_size))

        print_step("4. Atomic Fact Samples")
        print_json(fact_samples(release, args.sample_size))

    print_step("5. Runtime Files")
    print_json(
        {
            "store_root": str(DEFAULT_STORE_ROOT),
            "upload_dir": str(DEFAULT_UPLOAD_DIR),
            "registry": str(DEFAULT_STORE_ROOT / "registry.json"),
            "draft": str(DEFAULT_STORE_ROOT / "drafts" / f"{release.release_id}.json"),
            "published": str(DEFAULT_STORE_ROOT / "published" / f"{release.release_id}.json"),
            "review_log": str(DEFAULT_STORE_ROOT / "review_log.jsonl"),
        }
    )


def resolve_doc_path(doc_arg: str | None) -> Path:
    if doc_arg:
        path = Path(doc_arg)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {path}")
        return path

    candidates = sorted((PROJECT_ROOT / "data" / "raw").glob("*.docx"))
    if not candidates:
        raise FileNotFoundError("No .docx files found under data/raw. Put a document there or pass --doc.")
    return candidates[0]


def clear_runtime_data() -> None:
    for path in [DEFAULT_STORE_ROOT, DEFAULT_UPLOAD_DIR]:
        if path.exists():
            shutil.rmtree(path)


def release_summary(release: Release) -> dict[str, Any]:
    return {
        "release_id": release.release_id,
        "doc_id": release.doc_id,
        "product_family": release.product_family,
        "version": release.version,
        "effective_date": release.effective_date,
        "variant": release.variant,
        "region": release.region,
        "document_type": release.document_type,
        "status": release.status,
        "fact_count": len(release.facts),
        "fact_source_counts": fact_source_counts(release),
        "evidence_count": len(release.evidences),
        "structured_data_sections": list(release.structured_data.keys()),
        "validation_issues": [to_dict(issue) for issue in release.validation_issues],
    }


def fact_samples(release: Release, sample_size: int) -> dict[str, list[dict[str, Any]]]:
    domains = ["product", "plan", "fee", "form_field", "compliance", "required_document"]
    return {
        domain: [to_dict(fact) for fact in release.facts if fact.domain == domain][:sample_size]
        for domain in domains
    }


def structured_data_sample(release: Release, sample_size: int) -> dict[str, Any]:
    data = release.structured_data
    form = data.get("form", {})
    facts_by_domain = data.get("facts_by_domain", {})
    return {
        "schema_version": data.get("schema_version"),
        "document": data.get("document"),
        "summary": data.get("summary"),
        "product_info": data.get("product_info"),
        "business_objects": {
            domain: items[:sample_size]
            for domain, items in data.get("business_objects", {}).items()
        },
        "charges": data.get("charges", [])[:sample_size],
        "rules": data.get("rules", [])[:sample_size],
        "form": {
            "fields": form.get("fields", [])[:sample_size],
            "checkbox_groups": form.get("checkbox_groups", [])[:sample_size],
            "checkbox_group_count": len(form.get("checkbox_groups", [])),
            "checkbox_count": len(form.get("checkboxes", [])),
            "blank_field_count": len(form.get("blank_fields", [])),
        },
        "compliance": data.get("compliance", [])[:sample_size],
        "required_documents": data.get("required_documents", [])[:sample_size],
        "facts_by_domain_counts": {
            domain: len(items)
            for domain, items in facts_by_domain.items()
        },
        "review_notes": data.get("review_notes"),
    }


def fact_source_counts(release: Release) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fact in release.facts:
        counts[fact.source] = counts.get(fact.source, 0) + 1
    return counts


def print_step(title: str) -> None:
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
