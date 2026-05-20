from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import fields
from pathlib import Path
from typing import Any, TypeVar

from agent.product_doc_agent.models import RawDocument, Release, ReviewLogEntry, to_dict, utc_now

T = TypeVar("T")


class JsonStore:
    def __init__(self, root: str | Path = "data/product_doc_agent", upload_dir: str | Path = "data/raw/uploads") -> None:
        self.root = Path(root)
        self.registry_path = self.root / "registry.json"
        self.drafts_dir = self.root / "drafts"
        self.published_dir = self.root / "published"
        self.archive_dir = self.root / "archive"
        self.review_log_path = self.root / "review_log.jsonl"
        self.upload_dir = Path(upload_dir)
        for path in [self.root, self.drafts_dir, self.published_dir, self.archive_dir, self.upload_dir]:
            path.mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            self._write_json(self.registry_path, [])

    def register_document(self, source_path: str | Path) -> RawDocument:
        source = Path(source_path)
        if not source.exists():
            raise FileNotFoundError(f"Document does not exist: {source}")
        digest = sha256_file(source)
        registry = self.list_documents()
        existing = next((doc for doc in registry if doc.sha256 == digest and doc.file_name == source.name), None)
        if existing:
            return existing

        doc_id = build_doc_id(digest, source.name)
        stored_path = self.upload_dir / f"{doc_id}{source.suffix.lower()}"
        if not stored_path.exists():
            shutil.copy2(source, stored_path)
        doc = RawDocument(
            doc_id=doc_id,
            source_path=str(source),
            stored_path=str(stored_path),
            file_name=source.name,
            sha256=digest,
        )
        registry.append(doc)
        self._write_json(self.registry_path, [to_dict(item) for item in registry])
        return doc

    def update_document(self, doc: RawDocument) -> None:
        registry = self.list_documents()
        for index, current in enumerate(registry):
            if current.doc_id == doc.doc_id:
                registry[index] = doc
                break
        else:
            registry.append(doc)
        self._write_json(self.registry_path, [to_dict(item) for item in registry])

    def list_documents(self) -> list[RawDocument]:
        return [from_dict(RawDocument, item) for item in self._read_json(self.registry_path, [])]

    def save_draft(self, release: Release) -> None:
        if release.status == "published" or self.published_exists(release.release_id):
            raise ValueError("Published release is immutable")
        self._write_json(self.drafts_dir / f"{release.release_id}.json", to_dict(release))

    def draft_exists(self, release_id: str) -> bool:
        return (self.drafts_dir / f"{release_id}.json").exists()

    def load_draft(self, release_id: str) -> Release:
        return self._load_release(self.drafts_dir / f"{release_id}.json")

    def save_published(self, release: Release) -> None:
        path = self.published_dir / f"{release.release_id}.json"
        if path.exists():
            raise FileExistsError(f"Published release is immutable: {release.release_id}")
        release.status = "published"
        release.published_at = utc_now()
        release.updated_at = release.published_at
        self._write_json(path, to_dict(release))

    def published_exists(self, release_id: str) -> bool:
        return (self.published_dir / f"{release_id}.json").exists()

    def load_published(self, release_id: str) -> Release:
        return self._load_release(self.published_dir / f"{release_id}.json")

    def list_published(self) -> list[Release]:
        return [self._load_release(path) for path in self.published_dir.glob("*.json")]

    def append_review_log(self, entry: ReviewLogEntry) -> None:
        with self.review_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(to_dict(entry), ensure_ascii=False) + "\n")

    def _load_release(self, path: Path) -> Release:
        if not path.exists():
            raise KeyError(f"Release file not found: {path}")
        from agent.product_doc_agent.serialization import release_from_dict

        return release_from_dict(self._read_json(path, {}))

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        temp_path.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_doc_id(file_digest: str, file_name: str) -> str:
    logical_digest = hashlib.sha256(f"{file_digest}\n{file_name}".encode("utf-8")).hexdigest()
    return f"doc_{logical_digest[:12]}"


def from_dict(cls: type[T], payload: dict[str, Any]) -> T:
    names = {field.name for field in fields(cls)}
    return cls(**{key: value for key, value in payload.items() if key in names})
