from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from agent.product_doc_agent.document_loader import DocumentBlock, LoadedDocument
from agent.product_doc_agent.ocr_flow.pdf_page_renderer import PDFPageRenderer, RenderedPDFPage
from agent.product_doc_agent.ocr_flow.vision_markdown_renderer import VisionMarkdownRenderer


# 这些格式本身就是图片输入，跳过 MarkItDown，直接交给视觉模型转 Markdown。
DEFAULT_OCR_DIRECT_FILE_TYPES = {"jpg", "jpeg", "png"}
# 这些格式先尝试 MarkItDown；如果提取出的 Markdown 太短，再走视觉模型兜底。
DEFAULT_OCR_FALLBACK_FILE_TYPES = {"pdf"}


@dataclass(frozen=True)
class MarkdownRenderResult:
    markdown: str
    source: str
    pages: list[RenderedPDFPage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class MarkdownRenderer:
    """使用 MarkItDown 将原始文档转换为给大模型看的 Markdown。
    """
    def __init__(
        self,
        *,
        enable_ocr_flow: bool = True,
        ocr_page_dpi: int = 160,
        ocr_markdown_min_chars: int = 50,
        ocr_direct_file_types: Collection[str] | None = None,
        ocr_fallback_file_types: Collection[str] | None = None,
    ) -> None:
        self.enable_ocr_flow = enable_ocr_flow
        self.ocr_markdown_min_chars = max(0, ocr_markdown_min_chars)
        self.ocr_direct_file_types = normalize_file_types(
            ocr_direct_file_types or DEFAULT_OCR_DIRECT_FILE_TYPES
        )
        self.ocr_fallback_file_types = normalize_file_types(
            ocr_fallback_file_types or DEFAULT_OCR_FALLBACK_FILE_TYPES
        )
        self.ocr_page_renderer = PDFPageRenderer(dpi=ocr_page_dpi)
        self.vision_markdown_renderer = VisionMarkdownRenderer(page_renderer=self.ocr_page_renderer)

    def render_document(self, loaded_document: LoadedDocument, *, max_chars: int | None = None) -> str:
        """按最终内容解析策略渲染文档，返回可进入后续抽取流程的 Markdown。"""
        return self.render_document_result(loaded_document, max_chars=max_chars).markdown

    def render_document_result(
        self,
        loaded_document: LoadedDocument,
        *,
        max_chars: int | None = None,
    ) -> MarkdownRenderResult:
        """统一负责文件转 Markdown：优先 MarkItDown，必要时使用视觉模型兜底。"""

        file_type = normalize_file_type(loaded_document.source_file_type)
        if file_type in self.ocr_direct_file_types and self.enable_ocr_flow:
            markdown = ""
        else:
            markdown = self.render_file(loaded_document.source_path, max_chars=None)

        if not self.should_use_ocr_flow(file_type, markdown):
            return MarkdownRenderResult(
                markdown=truncate_markdown(markdown, max_chars=max_chars),
                source="markitdown",
                metadata={
                    "markdown_length": len(markdown),
                    "file_type": file_type,
                },
            )

        pages = self.ocr_page_renderer.render(loaded_document.source_path)
        ocr_markdown = self.vision_markdown_renderer.render_pdf_to_markdown(
            loaded_document.source_path,
            rendered_pages=pages,
        )
        return MarkdownRenderResult(
            markdown=truncate_markdown(ocr_markdown, max_chars=max_chars),
            source="vision_ocr_markdown",
            pages=pages,
            metadata={
                "markdown_length": len(ocr_markdown),
                "markitdown_markdown_length": len(markdown),
                "file_type": file_type,
                "page_count": len(pages),
                "ocr_markdown_min_chars": self.ocr_markdown_min_chars,
            },
        )

    def should_use_ocr_flow(self, file_type: str, markdown: str) -> bool:
        if not self.enable_ocr_flow:
            return False
        normalized_file_type = normalize_file_type(file_type)
        if normalized_file_type in self.ocr_direct_file_types:
            return True
        if normalized_file_type in self.ocr_fallback_file_types:
            return len(str(markdown or "").strip()) < self.ocr_markdown_min_chars
        return False

    def render_file(self, file_path: str | Path, *, max_chars: int | None = None) -> str:
        """转换单个文件，并按需要截断到最大字符数。"""
        markdown = convert_file_with_markitdown(file_path)
        return truncate_markdown(markdown, max_chars=max_chars)

    def render_blocks(self, blocks: Iterable[DocumentBlock], *, max_chars: int | None = None) -> str:
        """兼容旧调用：仅拼接块文本，不再生成 source 注释。

        新流程会优先使用 render_document() 的 MarkItDown 输出；保留这个方法是为了
        避免旧的调试/测试入口直接调用时崩溃。
        """
        markdown = "\n\n".join(str(block.text or "").strip() for block in blocks if str(block.text or "").strip())
        return truncate_markdown(markdown, max_chars=max_chars)


def convert_file_with_markitdown(file_path: str | Path) -> str:
    """调用开源 MarkItDown；未安装时给出明确的依赖安装提示。"""
    try:
        from markitdown import MarkItDown
    except ImportError as exc:
        raise RuntimeError(
            "MarkItDown is not installed. Please run: "
            "pip install 'markitdown[docx,xlsx,xls,pdf]'"
        ) from exc

    converter = MarkItDown()
    result = converter.convert(str(Path(file_path)))
    text_content = getattr(result, "text_content", "")
    return normalize_markdown_text(text_content)


def normalize_markdown_text(value: Any) -> str:
    """统一 Markdown 文本边界，保留 MarkItDown 的正文结构。"""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def truncate_markdown(markdown: str, *, max_chars: int | None) -> str:
    """超出上下文预算时从尾部截断，并用中文说明截断原因。"""
    if max_chars is None or len(markdown) <= max_chars:
        return markdown

    warning = "\n\n<!-- 内容超过 max_chars，后续 Markdown 已截断。 -->"
    budget = max(0, max_chars - len(warning))
    return markdown[:budget].rstrip() + warning


def normalize_file_type(file_type: str) -> str:
    return str(file_type or "").lower().lstrip(".")


def normalize_file_types(file_types: Collection[str]) -> set[str]:
    return {normalize_file_type(file_type) for file_type in file_types if str(file_type).strip()}
