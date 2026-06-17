from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from agent.product_doc_agent.ocr_flow.pdf_page_renderer import RenderedPDFPage


class OCRFlowDebugWriter:
    def __init__(self, *, debug_root: str | Path = Path("data") / "product_doc_agent" / "debug" / "ocr") -> None:
        self.debug_root = Path(debug_root)

    def write(
        self,
        *,
        document_id: str,
        pages: Sequence[RenderedPDFPage],
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        output_dir = self.debug_root / document_id
        output_dir.mkdir(parents=True, exist_ok=True)

        page_records: list[dict[str, Any]] = []
        for page in pages:
            page_path = output_dir / f"page_{page.page_number:03d}.png"
            if page.image_path is not None and page.image_path.resolve() == page_path.resolve():
                saved_path = page.image_path
            else:
                page_path.write_bytes(page.image_bytes)
                saved_path = page_path
            page_records.append(
                {
                    "page_number": page.page_number,
                    "width": page.width,
                    "height": page.height,
                    "mime_type": page.mime_type,
                    "image_path": str(saved_path),
                }
            )

        meta = dict(metadata or {})
        meta["pages"] = page_records
        (output_dir / "vision_request_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_dir
