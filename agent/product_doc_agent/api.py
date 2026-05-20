from __future__ import annotations

from typing import Any

from agent.product_doc_agent.models import Release, to_dict
from agent.product_doc_agent.pipeline import ProductDocumentPipeline
from agent.product_doc_agent.release import ReleaseService
from agent.product_doc_agent.storage import JsonStore


class ProductDocAgentAPI:
    def __init__(self, store: JsonStore | None = None, enable_llm: bool = True) -> None:
        self.store = store or JsonStore()
        self.pipeline = ProductDocumentPipeline(store=self.store, enable_llm=enable_llm)
        self.release_service = ReleaseService(store=self.store)

    def upload_and_create_draft(self, path: str) -> dict[str, Any]:
        return to_dict(self.pipeline.run_to_draft(path))


class PublishedQueryAPI:
    def __init__(self, store: JsonStore | None = None) -> None:
        self.store = store or JsonStore()

    def list_releases(self, product_family: str | None = None, region: str | None = None, version: str | None = None, effective_date: str | None = None, variant: str | None = None) -> list[dict[str, Any]]:
        return [
            to_dict(release)
            for release in self.store.list_published()
            if matches(release, product_family, region, version, effective_date, variant)
        ]

    def get_release(self, release_id: str) -> dict[str, Any]:
        return to_dict(self.store.load_published(release_id))

    def find_facts(self, release_id: str, domain: str | None = None, field: str | None = None) -> list[dict[str, Any]]:
        release = self.store.load_published(release_id)
        return [to_dict(fact) for fact in release.facts if (domain is None or fact.domain == domain) and (field is None or fact.field == field)]


def matches(release: Release, product_family: str | None, region: str | None, version: str | None, effective_date: str | None, variant: str | None) -> bool:
    return (
        (product_family is None or release.product_family == product_family)
        and (region is None or release.region == region)
        and (version is None or release.version == version)
        and (effective_date is None or release.effective_date == effective_date)
        and (variant is None or release.variant == variant)
    )
