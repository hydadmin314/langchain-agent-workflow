from __future__ import annotations

from pathlib import Path

from agent.product_doc_agent.classification import DocumentClassifier, ProductFamilyMatcher
from agent.product_doc_agent.data_normalization import DataNormalizer
from agent.product_doc_agent.docx_parser import DocxParser
from agent.product_doc_agent.evidence import EvidenceIndex
from agent.product_doc_agent.extractors import DEFAULT_EXTRACTORS, DomainExtractor, ExtractionMerger
from agent.product_doc_agent.llm_client import LLMClient
from agent.product_doc_agent.llm_extractors import LLMStructuredExtractor
from agent.product_doc_agent.models import DocumentStatus, Release, ReleaseStatus, ValidationIssue, utc_now
from agent.product_doc_agent.normalization import StructureNormalizer
from agent.product_doc_agent.storage import JsonStore
from agent.product_doc_agent.structured_data import BusinessStructuredDataBuilder
from agent.product_doc_agent.validation import AISelfReviewer, ConflictDetector, SchemaValidator


class ProductDocumentPipeline:
    def __init__(self, store: JsonStore | None = None, parser: DocxParser | None = None, extractors: list[DomainExtractor] | None = None, enable_llm: bool = True, llm_client: LLMClient | None = None) -> None:
        self.store = store or JsonStore()
        self.parser = parser or DocxParser()
        self.normalizer = StructureNormalizer()
        self.family_matcher = ProductFamilyMatcher()
        self.classifier = DocumentClassifier()
        self.enable_llm = enable_llm
        self.llm_client = llm_client or (LLMClient(temperature=0.1) if self.enable_llm else None)
        self.extractors = list(extractors or DEFAULT_EXTRACTORS)
        if self.enable_llm and self.llm_client:
            self.extractors.append(LLMStructuredExtractor(self.llm_client))
        self.merger = ExtractionMerger()
        self.data_normalizer = DataNormalizer()
        self.validator = SchemaValidator()
        self.conflict_detector = ConflictDetector()
        self.ai_self_reviewer = AISelfReviewer(self.llm_client if self.enable_llm else None)
        self.structured_data_builder = BusinessStructuredDataBuilder()

    def run_to_draft(self, source_path: str | Path) -> Release:
        raw_doc = self.store.register_document(source_path)
        parsed = self.parser.parse(raw_doc.stored_path, raw_doc.doc_id)
        raw_doc.status = DocumentStatus.PARSED.value
        normalized = self.normalizer.normalize(parsed)
        raw_doc = self.family_matcher.match(raw_doc, normalized)
        self.store.update_document(raw_doc)
        document_type = self.classifier.classify(normalized)
        release_id = build_release_id(raw_doc.product_family or "unknown_product_family", raw_doc.version or "unversioned", raw_doc.variant or "default", raw_doc.effective_date)
        if self.store.published_exists(release_id):
            return self.store.load_published(release_id)
        if self.store.draft_exists(release_id):
            return self.store.load_draft(release_id)
        evidence_index = EvidenceIndex(normalized)
        fact_groups: list[list] = []
        extraction_issues: list[ValidationIssue] = []
        for extractor in self.extractors:
            try:
                fact_groups.append(extractor.extract(release_id, normalized, evidence_index))
            except Exception as exc:
                extraction_issues.append(ValidationIssue("extractor_failed", f"{extractor.__class__.__name__} failed: {exc}", "warning"))
        facts = self.data_normalizer.normalize(self.merger.merge(fact_groups))
        structured_data = self.structured_data_builder.build(facts, raw_doc, document_type)
        release = Release(
            release_id=release_id,
            doc_id=raw_doc.doc_id,
            product_family=raw_doc.product_family or "unknown_product_family",
            version=raw_doc.version or "unversioned",
            effective_date=raw_doc.effective_date,
            variant=raw_doc.variant or "default",
            region=raw_doc.region,
            document_type=document_type,
            status=ReleaseStatus.DRAFT.value,
            facts=facts,
            evidences=evidence_index.all(),
            structured_data=structured_data,
        )
        release.validation_issues = self.validator.validate(release) + self.conflict_detector.detect(release) + self.ai_self_reviewer.review(release) + extraction_issues
        release.updated_at = utc_now()
        self.store.save_draft(release)
        raw_doc.status = DocumentStatus.DRAFTED.value
        self.store.update_document(raw_doc)
        return release


def build_release_id(product_family: str, version: str, variant: str, effective_date: str | None = None) -> str:
    version_key = effective_date.replace("-", "") if effective_date else version
    return f"rel_{slug(product_family)}-{slug(version_key)}-{slug(variant)}"


def slug(value: str) -> str:
    chars = []
    for char in value.lower().replace("/", "-"):
        if char.isalnum():
            chars.append(char)
        elif char in {"-", "_"}:
            chars.append("-")
    return "".join(chars).strip("-") or "unknown"
