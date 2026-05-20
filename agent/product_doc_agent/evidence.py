from __future__ import annotations

from agent.product_doc_agent.models import BlankField, Checkbox, Evidence, EvidenceType, NormalizedDocument, Paragraph, Table


class EvidenceIndex:
    def __init__(self, normalized: NormalizedDocument) -> None:
        self.normalized = normalized
        self.evidences: dict[str, Evidence] = {}
        self.by_ref: dict[str, str] = {}
        self._build()

    def evidence_for_paragraph(self, paragraph_id: str) -> Evidence:
        return self.evidences[self.by_ref[paragraph_id]]

    def evidence_for_checkbox(self, checkbox_id: str) -> Evidence:
        return self.evidences[self.by_ref[checkbox_id]]

    def evidence_for_blank(self, blank_id: str) -> Evidence:
        return self.evidences[self.by_ref[blank_id]]

    def all(self) -> list[Evidence]:
        return list(self.evidences.values())

    def _build(self) -> None:
        for paragraph in self.normalized.paragraphs:
            self._add_paragraph(paragraph)
        for table in self.normalized.tables:
            self._add_table(table)
        for checkbox in self.normalized.checkboxes:
            self._add_checkbox(checkbox)
        for blank in self.normalized.blank_fields:
            self._add_blank(blank)

    def _add_paragraph(self, paragraph: Paragraph) -> None:
        self._add(paragraph.id, Evidence(f"ev_{paragraph.id}", self.normalized.doc_id, EvidenceType.PARAGRAPH.value, paragraph.id, paragraph.text, {"paragraph_index": paragraph.index}))

    def _add_table(self, table: Table) -> None:
        for row_index, row in enumerate(table.rows):
            row_key = f"{table.id}:r{row_index}"
            self._add(row_key, Evidence(f"ev_{table.id}_r{row_index}", self.normalized.doc_id, EvidenceType.TABLE_ROW.value, row_key, " | ".join(row), {"table_id": table.id, "row": row_index}))
            for col_index, cell in enumerate(row):
                cell_key = f"{table.id}:r{row_index}:c{col_index}"
                self._add(cell_key, Evidence(f"ev_{table.id}_r{row_index}_c{col_index}", self.normalized.doc_id, EvidenceType.TABLE_CELL.value, cell_key, cell, {"table_id": table.id, "row": row_index, "col": col_index}))

    def _add_checkbox(self, checkbox: Checkbox) -> None:
        self._add(checkbox.id, Evidence(f"ev_{checkbox.id}", self.normalized.doc_id, EvidenceType.CHECKBOX.value, checkbox.id, checkbox.label, {"paragraph_id": checkbox.paragraph_id, "table_id": checkbox.table_id, "row": checkbox.row, "col": checkbox.col}))

    def _add_blank(self, blank: BlankField) -> None:
        self._add(blank.id, Evidence(f"ev_{blank.id}", self.normalized.doc_id, EvidenceType.BLANK_FIELD.value, blank.id, f"{blank.label}{blank.placeholder}", {"paragraph_id": blank.paragraph_id, "table_id": blank.table_id, "row": blank.row, "col": blank.col}))

    def _add(self, ref_id: str, evidence: Evidence) -> None:
        self.evidences[evidence.evidence_id] = evidence
        self.by_ref[ref_id] = evidence.evidence_id
