from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from agent.product_doc_agent.models import BlankField, Checkbox, Paragraph, ParsedDocument, Table

WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
CHECKBOX_RE = re.compile(r"([□☐■☑√])\s*([^□☐■☑√\n\r]+)")
BLANK_RE = re.compile(r"(?P<label>[\u4e00-\u9fffA-Za-z0-9（）() /-]{1,24})[:：]?\s*(?P<blank>_{2,}|\[\s*\])")


class DocxParser:
    def parse(self, path: str | Path, doc_id: str) -> ParsedDocument:
        docx_path = Path(path)
        if docx_path.suffix.lower() != ".docx":
            raise ValueError(f"Only .docx is supported in MVP: {docx_path}")
        with zipfile.ZipFile(docx_path) as package:
            root = ET.fromstring(package.read("word/document.xml"))
        paragraphs = self._extract_paragraphs(root)
        tables = self._extract_tables(root)
        return ParsedDocument(
            doc_id=doc_id,
            paragraphs=paragraphs,
            tables=tables,
            checkboxes=self._extract_checkboxes(paragraphs, tables),
            blank_fields=self._extract_blank_fields(paragraphs, tables),
        )

    def _extract_paragraphs(self, root: ET.Element) -> list[Paragraph]:
        paragraphs: list[Paragraph] = []
        for index, para in enumerate([p for p in root.findall(".//w:p", WORD_NS) if text_of(p).strip()], start=1):
            paragraphs.append(Paragraph(id=f"p{index:04d}", text=collapse_space(text_of(para).strip()), index=index))
        return paragraphs

    def _extract_tables(self, root: ET.Element) -> list[Table]:
        tables: list[Table] = []
        for table_index, table_node in enumerate(root.findall(".//w:tbl", WORD_NS), start=1):
            rows: list[list[str]] = []
            for row_node in table_node.findall("./w:tr", WORD_NS):
                row = []
                for cell_node in row_node.findall("./w:tc", WORD_NS):
                    cell_parts = [
                        collapse_space(text_of(para).strip())
                        for para in cell_node.findall("./w:p", WORD_NS)
                        if text_of(para).strip()
                    ]
                    row.append("\n".join(cell_parts))
                if any(cell.strip() for cell in row):
                    rows.append(row)
            tables.append(Table(id=f"t{table_index:03d}", index=table_index, rows=rows))
        return tables

    def _extract_checkboxes(self, paragraphs: list[Paragraph], tables: list[Table]) -> list[Checkbox]:
        checkboxes: list[Checkbox] = []
        counter = 0
        for para in paragraphs:
            for label, checked in find_checkboxes(para.text):
                counter += 1
                checkboxes.append(Checkbox(id=f"cb{counter:04d}", label=label, checked=checked, paragraph_id=para.id))
        for table in tables:
            for row_index, row in enumerate(table.rows):
                for col_index, cell_text in enumerate(row):
                    for label, checked in find_checkboxes(cell_text):
                        counter += 1
                        checkboxes.append(Checkbox(id=f"cb{counter:04d}", label=label, checked=checked, paragraph_id=None, table_id=table.id, row=row_index, col=col_index))
        return checkboxes

    def _extract_blank_fields(self, paragraphs: list[Paragraph], tables: list[Table]) -> list[BlankField]:
        blanks: list[BlankField] = []
        counter = 0
        for para in paragraphs:
            for label, placeholder in find_blanks(para.text):
                counter += 1
                blanks.append(BlankField(id=f"bf{counter:04d}", label=label, placeholder=placeholder, paragraph_id=para.id))
        for table in tables:
            for row_index, row in enumerate(table.rows):
                for col_index, cell_text in enumerate(row):
                    for label, placeholder in find_blanks(cell_text):
                        counter += 1
                        blanks.append(BlankField(id=f"bf{counter:04d}", label=label, placeholder=placeholder, paragraph_id=None, table_id=table.id, row=row_index, col=col_index))
        return blanks


def text_of(node: ET.Element) -> str:
    return "".join(text_node.text or "" for text_node in node.findall(".//w:t", WORD_NS))


def collapse_space(value: str) -> str:
    return re.sub(r"[ \t]+", " ", value).strip()


def find_checkboxes(text: str) -> list[tuple[str, bool]]:
    results: list[tuple[str, bool]] = []
    for match in CHECKBOX_RE.finditer(text):
        mark = match.group(1)
        label = re.split(r"\s{2,}", match.group(2).strip())[0].strip()
        if label:
            results.append((label, mark in {"■", "☑", "√"}))
    return results


def find_blanks(text: str) -> list[tuple[str, str]]:
    return [(m.group("label").strip(" ：:"), m.group("blank")) for m in BLANK_RE.finditer(text) if m.group("label").strip()]
