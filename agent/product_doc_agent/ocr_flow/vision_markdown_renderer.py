from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from agent.product_doc_agent.ocr_flow.pdf_page_renderer import PDFPageRenderer, RenderedPDFPage
from config.llm_config import get_product_doc_ocr_llm


DEFAULT_VISION_MARKDOWN_PROMPT = """
You are a document OCR and layout reconstruction engine for Chinese telecom product forms.

Read every visible page image and convert the document into faithful Markdown.
Do not extract the final JSON schema in this step. Your only task is to restore the source
document content as Markdown for a later text extraction pipeline.

Requirements:
- Preserve all Chinese text exactly as visible.
- Preserve titles, section headings, numbered clauses, tables, checkboxes, blanks, and handwritten/printed values.
- Use Markdown tables for table-like forms whenever possible.
- If a cell is blank, keep it blank instead of inventing content.
- Keep page boundaries with headings like "## Page 1".
- Do not summarize, omit, or translate the document.
- Return Markdown only.
""".strip()


class VisionMarkdownRenderer:
    def __init__(
        self,
        *,
        llm: BaseChatModel | None = None,
        page_renderer: PDFPageRenderer | None = None,
        prompt: str = DEFAULT_VISION_MARKDOWN_PROMPT,
        max_pages_per_request: int = 4,
    ) -> None:
        self.llm = llm or get_product_doc_ocr_llm(temperature=0)
        self.page_renderer = page_renderer or PDFPageRenderer()
        self.prompt = prompt
        self.max_pages_per_request = max(1, max_pages_per_request)

    def render_pdf_to_markdown(
        self,
        pdf_path: str | Path,
        *,
        rendered_pages: Sequence[RenderedPDFPage] | None = None,
        page_output_dir: str | Path | None = None,
    ) -> str:
        pages = list(rendered_pages) if rendered_pages is not None else self.page_renderer.render(
            pdf_path,
            output_dir=page_output_dir,
        )
        chunks: list[str] = []
        for start in range(0, len(pages), self.max_pages_per_request):
            page_group = pages[start : start + self.max_pages_per_request]
            chunks.append(self._render_pages_to_markdown(page_group))
        return normalize_markdown("\n\n".join(chunks))

    def _render_pages_to_markdown(self, pages: Sequence[RenderedPDFPage]) -> str:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": self._build_prompt_for_pages(pages),
            }
        ]
        for page in pages:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": page.data_url},
                }
            )

        response = self.llm.invoke([HumanMessage(content=content)])
        return response_content_to_text(response.content)

    def _build_prompt_for_pages(self, pages: Sequence[RenderedPDFPage]) -> str:
        page_numbers = ", ".join(str(page.page_number) for page in pages)
        return f"{self.prompt}\n\nPages in this request: {page_numbers}"


def response_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
        return "\n".join(parts)
    return str(content or "")


def normalize_markdown(markdown: str) -> str:
    text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()
