from __future__ import annotations

import re

from agent.product_doc_agent.models import NormalizedDocument, ParsedDocument, Section


SECTION_HINTS = ["申请登记表", "基础套餐申请信息", "填表说明", "营销规则", "客户特别关注", "网络及信息安全承诺书", "授权委托"]


class StructureNormalizer:
    def normalize(self, parsed: ParsedDocument) -> NormalizedDocument:
        return NormalizedDocument(
            doc_id=parsed.doc_id,
            sections=self._split_sections(parsed),
            paragraphs=parsed.paragraphs,
            tables=parsed.tables,
            checkboxes=parsed.checkboxes,
            blank_fields=parsed.blank_fields,
        )

    def _split_sections(self, parsed: ParsedDocument) -> list[Section]:
        if not parsed.paragraphs:
            return []
        starts: list[tuple[int, str]] = [(0, "Document")]
        for index, paragraph in enumerate(parsed.paragraphs):
            if index and is_section_title(paragraph.text):
                starts.append((index, paragraph.text))
        sections: list[Section] = []
        for section_index, (start, title) in enumerate(starts):
            end = starts[section_index + 1][0] - 1 if section_index + 1 < len(starts) else len(parsed.paragraphs) - 1
            sections.append(
                Section(
                    id=f"s{section_index + 1:03d}",
                    title=title,
                    start_paragraph=parsed.paragraphs[start].index,
                    end_paragraph=parsed.paragraphs[end].index,
                    paragraph_ids=[para.id for para in parsed.paragraphs[start : end + 1]],
                )
            )
        return sections


def is_section_title(text: str) -> bool:
    stripped = text.strip()
    return (
        any(hint in stripped for hint in SECTION_HINTS)
        or bool(re.match(r"^[一二三四五六七八九十]+、", stripped))
        or bool(re.match(r"^[（(][一二三四五六七八九十]+[）)]", stripped))
        or (len(stripped) <= 16 and stripped in {"商云通", "移动业务", "上行升速包"})
    )
