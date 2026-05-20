from __future__ import annotations

import uuid
from typing import Any

from agent.product_doc_agent.models import Release, ReleaseStatus, ReviewLogEntry, utc_now
from agent.product_doc_agent.storage import JsonStore
from agent.product_doc_agent.validation import SchemaValidator


class ReleaseService:
    def __init__(self, store: JsonStore | None = None) -> None:
        self.store = store or JsonStore()
        self.validator = SchemaValidator()

    def submit_for_review(self, release_id: str, reviewer: str = "system", comment: str | None = None) -> Release:
        release = self.store.load_draft(release_id)
        self._ensure_mutable(release)
        before = snapshot_status(release)
        release.status = ReleaseStatus.IN_REVIEW.value
        release.updated_at = utc_now()
        self.store.save_draft(release)
        self._log(release_id, reviewer, "submit_for_review", comment, before, snapshot_status(release))
        return release

    def approve(self, release_id: str, reviewer: str, comment: str | None = None) -> Release:
        release = self.store.load_draft(release_id)
        self._ensure_mutable(release)
        errors = [issue for issue in release.validation_issues if issue.severity == "error"]
        if errors:
            raise ValueError(f"Release has validation errors: {[issue.code for issue in errors]}")
        before = snapshot_status(release)
        release.status = ReleaseStatus.APPROVED.value
        release.updated_at = utc_now()
        self.store.save_draft(release)
        self._log(release_id, reviewer, "approve", comment, before, snapshot_status(release))
        return release

    def publish(self, release_id: str, reviewer: str, comment: str | None = None) -> Release:
        release = self.store.load_draft(release_id)
        self._ensure_mutable(release)
        if release.status != ReleaseStatus.APPROVED.value:
            raise ValueError("Only approved draft releases can be published")
        before = snapshot_status(release)
        self.store.save_published(release)
        published = self.store.load_published(release_id)
        self._log(release_id, reviewer, "publish", comment, before, snapshot_status(published))
        return published

    def _ensure_mutable(self, release: Release) -> None:
        if release.status == ReleaseStatus.PUBLISHED.value or self.store.published_exists(release.release_id):
            raise ValueError("Published release is immutable")

    def _log(self, release_id: str, reviewer: str, action: str, comment: str | None, before: dict[str, Any] | None, after: dict[str, Any] | None) -> None:
        self.store.append_review_log(ReviewLogEntry(f"review_{uuid.uuid4().hex[:12]}", release_id, reviewer, action, comment, before, after))


def snapshot_status(release: Release) -> dict[str, Any]:
    return {"release_id": release.release_id, "status": release.status, "updated_at": release.updated_at}
