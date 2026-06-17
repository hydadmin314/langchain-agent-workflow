from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


SUPPORTED_FILE_TYPES = {"txt", "docx", "xlsx", "pdf", "jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff"}
PENDING_FILE_TYPES = {"doc", "xls", "ppt", "pptx"}


@dataclass(frozen=True)
class DocumentBlock:
    block_id: str
    block_type: str
    text: str
    source_location: dict[str, Any]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LoadedDocument:
    document_id: str
    source_path: str
    filename: str
    source_file_type: str
    source_file_hash: str
    modified_at: str
    blocks: list[DocumentBlock]
    warnings: list[str]

    def to_context(self, *, max_chars: int = 60000) -> str:
        """兼容旧调试入口；正式内容解析已迁移到 MarkdownRenderer。"""

        parts: list[str] = []
        total = 0
        for block in self.blocks:
            item = {
                "block_id": block.block_id,
                "block_type": block.block_type,
                "source_location": block.source_location,
                "text": block.text,
            }
            rendered = repr(item)
            if total + len(rendered) > max_chars:
                parts.append(
                    repr(
                        {
                            "block_id": "context_truncated",
                            "block_type": "warning",
                            "source_location": {},
                            "text": "文档内容较长，后续块已被截断。",
                        }
                    )
                )
                break
            parts.append(rendered)
            total += len(rendered)
        return "\n".join(parts)


class DocumentLoader:
    """只加载文件身份和元数据。

    正式正文解析统一由 MarkdownRenderer 负责，避免 loader 和 renderer 重复读取/解析文件。
    blocks 字段暂时保留为空列表，用于兼容旧调用和 WorkflowResult.block_count。
    """

    async def load_async(self, file_path: str | Path) -> LoadedDocument:
        return await asyncio.to_thread(self.load, file_path)

    def load(self, file_path: str | Path) -> LoadedDocument:
        path = Path(file_path).resolve()
        file_type = path.suffix.lower().lstrip(".")
        file_hash = sha256_file(path)
        stat = path.stat()
        warnings: list[str] = []

        if file_type in PENDING_FILE_TYPES:
            warnings.append(f"{file_type} 暂未进入正式内容解析流程，后续可接入格式转换或视觉解析。")
        elif file_type not in SUPPORTED_FILE_TYPES:
            warnings.append(f"不支持的文件类型：{file_type}")

        return LoadedDocument(
            document_id=build_document_id(path, file_hash),
            source_path=str(path),
            filename=path.name,
            source_file_type=file_type,
            source_file_hash=file_hash,
            modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            blocks=[],
            warnings=warnings,
        )


def build_document_id(path: Path, file_hash: str) -> str:
    key = f"{path.resolve()}|{file_hash}".encode("utf-8", errors="ignore")
    return "doc_" + hashlib.sha1(key).hexdigest()[:12]


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
