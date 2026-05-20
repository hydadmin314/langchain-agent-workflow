from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DocumentStatus(str, Enum):
    REGISTERED = "registered"
    PARSED = "parsed"
    DRAFTED = "drafted"


class ReleaseStatus(str, Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class EvidenceType(str, Enum):
    PARAGRAPH = "paragraph"
    TABLE_CELL = "table_cell"
    TABLE_ROW = "table_row"
    CHECKBOX = "checkbox"
    BLANK_FIELD = "blank_field"


@dataclass
class RawDocument:
    doc_id: str
    source_path: str
    stored_path: str
    file_name: str
    sha256: str
    product_family: str | None = None
    region: str | None = None
    version: str | None = None
    effective_date: str | None = None
    variant: str | None = None
    status: str = DocumentStatus.REGISTERED.value
    created_at: str = field(default_factory=utc_now)


@dataclass
class Paragraph:
    id: str
    text: str
    index: int
    style: str | None = None


@dataclass
class Table:
    id: str
    index: int
    rows: list[list[str]]


@dataclass
class Checkbox:
    id: str
    label: str
    checked: bool
    paragraph_id: str | None
    table_id: str | None = None
    row: int | None = None
    col: int | None = None


@dataclass
class BlankField:
    id: str
    label: str
    placeholder: str
    paragraph_id: str | None
    table_id: str | None = None
    row: int | None = None
    col: int | None = None


@dataclass
class ParsedDocument:
    doc_id: str
    paragraphs: list[Paragraph]
    tables: list[Table]
    checkboxes: list[Checkbox]
    blank_fields: list[BlankField]


@dataclass
class Section:
    id: str
    title: str
    start_paragraph: int
    end_paragraph: int
    paragraph_ids: list[str]


@dataclass
class NormalizedDocument:
    doc_id: str
    sections: list[Section]
    paragraphs: list[Paragraph]
    tables: list[Table]
    checkboxes: list[Checkbox]
    blank_fields: list[BlankField]


@dataclass
class Evidence:
    evidence_id: str
    doc_id: str
    evidence_type: str
    ref_id: str
    text: str
    position: dict[str, Any]


@dataclass
class ExtractedFact:
    fact_id: str
    release_id: str
    domain: str
    field: str
    value: Any
    evidence_ids: list[str]
    confidence: float = 1.0
    source: str = "rule"


@dataclass
class ValidationIssue:
    code: str
    message: str
    severity: str = "error"
    fact_id: str | None = None


@dataclass
class Release:
    release_id: str
    doc_id: str
    product_family: str
    version: str
    effective_date: str | None
    variant: str
    region: str | None
    document_type: str
    status: str
    facts: list[ExtractedFact]
    evidences: list[Evidence]
    validation_issues: list[ValidationIssue] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    published_at: str | None = None


@dataclass
class ReviewLogEntry:
    review_log_id: str
    release_id: str
    reviewer: str
    action: str
    comment: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    created_at: str = field(default_factory=utc_now)


def to_dict(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    if isinstance(obj, list):
        return [to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {key: to_dict(value) for key, value in obj.items()}
    return obj
