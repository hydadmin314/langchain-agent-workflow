from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from agent.product_doc_agent.document_loader import DocumentBlock, LoadedDocument


class MarkdownRenderer:
    """使用 MarkItDown 将原始文档转换为给大模型看的 Markdown。
    """

    def render_document(self, loaded_document: LoadedDocument, *, max_chars: int | None = None) -> str:
        """按 MarkItDown 的原始转换结果渲染文档。"""
        return self.render_file(loaded_document.source_path, max_chars=max_chars)

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
