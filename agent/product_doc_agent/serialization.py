from __future__ import annotations

from typing import Any

from agent.product_doc_agent.models import Evidence, ExtractedFact, Release, ValidationIssue
from agent.product_doc_agent.storage import from_dict


def release_from_dict(payload: dict[str, Any]) -> Release:
    clean = dict(payload)
    clean.setdefault("effective_date", None)
    clean["facts"] = [from_dict(ExtractedFact, item) for item in clean.get("facts", [])]
    clean["evidences"] = [from_dict(Evidence, item) for item in clean.get("evidences", [])]
    clean["validation_issues"] = [from_dict(ValidationIssue, item) for item in clean.get("validation_issues", [])]
    return from_dict(Release, clean)
