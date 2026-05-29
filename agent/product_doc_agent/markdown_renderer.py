from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from agent.product_doc_agent.document_loader import DocumentBlock, LoadedDocument


@dataclass(frozen=True)
class MarkdownRenderOptions:
    """Options for LLM-facing Markdown rendering."""

    include_document_header: bool = True
    split_long_table_cells: bool = True
    long_cell_min_chars: int = 280
    preserve_empty_table_headers: bool = True


class MarkdownRenderer:
    """Render loader blocks into MarkItDown-like Markdown with source metadata.

    MarkItDown produces readable Markdown by preserving Word tables and list-like
    text. This renderer follows the same presentation idea while keeping our
    DocumentLoader blocks as the source of truth so every extracted field can
    still cite block_id and source_location.
    """

    def __init__(self, options: MarkdownRenderOptions | None = None) -> None:
        self.options = options or MarkdownRenderOptions()

    def render_document(self, loaded_document: LoadedDocument, *, max_chars: int | None = None) -> str:
        parts: list[str] = []
        if self.options.include_document_header:
            parts.extend(render_document_header(loaded_document))
        parts.extend(self.render_block(block) for block in loaded_document.blocks)
        return join_with_budget(parts, max_chars=max_chars)

    def render_blocks(self, blocks: Iterable[DocumentBlock], *, max_chars: int | None = None) -> str:
        return join_with_budget((self.render_block(block) for block in blocks), max_chars=max_chars)

    def render_block(self, block: DocumentBlock) -> str:
        source = render_source_comment(block)
        if block.block_type in {"table_row", "sheet_row"}:
            body = self.render_table_row(block)
        else:
            body = normalize_paragraph_text(block.text)
        return f"{source}\n{body}".strip()

    def render_table_row(self, block: DocumentBlock) -> str:
        cells = block.metadata.get("cells") if isinstance(block.metadata, dict) else None
        if not isinstance(cells, list) or not cells:
            return normalize_paragraph_text(block.text)

        normalized_cells = trim_trailing_empty_cells([normalize_cell_text(cell) for cell in cells])
        if should_render_as_field_row(normalized_cells):
            return self.render_field_row(normalized_cells)
        return render_markdown_table(
            normalized_cells,
            preserve_empty_headers=self.options.preserve_empty_table_headers,
        )

    def render_field_row(self, cells: list[str]) -> str:
        label = normalize_label(cells[0])
        value = normalize_cell_text(" ".join(cell for cell in cells[1:] if cell))
        detail_items: list[str] = []
        if self.options.split_long_table_cells and len(value) >= self.options.long_cell_min_chars:
            detail_items = split_long_text(value)

        # For very long cells, keep the table shape but move the full content into
        # bullet details. Repeating the whole paragraph both in the table cell and
        # in details makes module prompts noisy and can cause duplicate extraction.
        rendered_value = "see details below" if len(detail_items) > 1 else value
        lines = [
            "| field | value |",
            "| --- | --- |",
            f"| {escape_table_cell(label)} | {escape_table_cell(rendered_value)} |",
        ]

        if len(detail_items) > 1:
            lines.extend(["", "details:"])
            lines.extend(format_detail_item(item) for item in detail_items)
        return "\n".join(lines)


def render_document_header(loaded_document: LoadedDocument) -> list[str]:
    metadata = {
        "document_id": loaded_document.document_id,
        "filename": loaded_document.filename,
        "source_path": loaded_document.source_path,
        "source_file_type": loaded_document.source_file_type,
        "source_file_hash": loaded_document.source_file_hash,
        "modified_at": loaded_document.modified_at,
    }
    return [
        "# Product Document",
        "",
        "<!-- document_metadata: " + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + " -->",
    ]


def render_source_comment(block: DocumentBlock) -> str:
    source = {
        "block_id": block.block_id,
        "block_type": block.block_type,
        "source_location": compact_source_location(block.source_location),
    }
    return "<!-- source: " + json.dumps(source, ensure_ascii=False, separators=(",", ":")) + " -->"


def compact_source_location(source_location: dict[str, Any]) -> dict[str, Any]:
    """Keep block-level coordinates in comments without repeating the file path."""
    return {key: value for key, value in source_location.items() if key != "path"}


def should_render_as_field_row(cells: list[str]) -> bool:
    non_empty = [cell for cell in cells if cell]
    return len(cells) <= 2 and len(non_empty) <= 2


def render_markdown_table(cells: list[str], *, preserve_empty_headers: bool) -> str:
    if preserve_empty_headers:
        columns = ["" for _ in cells]
    else:
        columns = [f"cell_{index}" for index in range(len(cells))]
    header = "| " + " | ".join(escape_table_cell(column) for column in columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    row = "| " + " | ".join(escape_table_cell(cell) for cell in cells) + " |"
    return "\n".join([header, separator, row])


def normalize_paragraph_text(value: Any) -> str:
    return normalize_label(str(value or "").strip())


def normalize_cell_text(value: Any) -> str:
    text = str(value or "").replace("\u3000", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    return normalize_label(text.strip())


def trim_trailing_empty_cells(cells: list[str]) -> list[str]:
    trimmed = list(cells)
    while trimmed and not trimmed[-1]:
        trimmed.pop()
    return trimmed or [""]


def normalize_label(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "").replace("\u3000", " ")).strip()
    if len(text) <= 40:
        cjk_space_count = len(re.findall(r"[\u4e00-\u9fff]\s+(?=[\u4e00-\u9fff])", text))
        if cjk_space_count >= 2:
            text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    return text


def escape_table_cell(value: Any) -> str:
    text = str(value or "")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def split_long_text(text: str) -> list[str]:
    normalized = normalize_label(text)
    for splitter in (split_numbered_items, split_lettered_items, split_sentences):
        parts = splitter(normalized)
        if len(parts) > 1:
            return parts
    return [normalized] if normalized else []


def split_numbered_items(text: str) -> list[str]:
    # Match real list markers such as "1、", "2.", "十五、"; never split plain numbers like 17909.
    pattern = re.compile(r"(?=(?:^|\s)(?:\d{1,2}|[一二三四五六七八九十]{1,3})[、.．])")
    return clean_split_parts(pattern.split(text))


def split_lettered_items(text: str) -> list[str]:
    # Marketing rules often use a), b) or （a） style subclauses.
    pattern = re.compile(r"(?=(?:^|\s)(?:[a-zA-Z][).]|[（(][a-zA-Z][）)]))")
    return clean_split_parts(pattern.split(text))


def split_sentences(text: str) -> list[str]:
    return clean_split_parts(re.split(r"(?<=[。；;])\s*", text))


def clean_split_parts(parts: list[str]) -> list[str]:
    return [part.strip() for part in parts if part and part.strip()]


def format_detail_item(item: str) -> str:
    return f"- {item}"


def join_with_budget(parts: Iterable[str], *, max_chars: int | None) -> str:
    if max_chars is None:
        return "\n\n".join(part for part in parts if part)

    rendered_parts: list[str] = []
    total = 0
    truncated = False
    warning = "\n\n<!-- context_truncated: markdown context exceeds max_chars; trailing content was omitted. -->"
    budget = max(0, max_chars - len(warning))

    for part in parts:
        if not part:
            continue
        separator_len = 2 if rendered_parts else 0
        if total + separator_len + len(part) > budget:
            truncated = True
            break
        rendered_parts.append(part)
        total += separator_len + len(part)

    markdown = "\n\n".join(rendered_parts)
    if truncated:
        return markdown + warning
    return markdown
