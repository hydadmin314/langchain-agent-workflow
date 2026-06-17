from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

import fitz


@dataclass(frozen=True)
class RenderedPDFPage:
    page_number: int
    width: int
    height: int
    mime_type: str
    image_bytes: bytes
    image_path: Path | None = None

    @property
    def base64_data(self) -> str:
        return base64.b64encode(self.image_bytes).decode("ascii")

    @property
    def data_url(self) -> str:
        return f"data:{self.mime_type};base64,{self.base64_data}"


class PDFPageRenderer:
    def __init__(self, *, dpi: int = 200, image_format: str = "png") -> None:
        self.dpi = dpi
        self.image_format = image_format.lower().lstrip(".")
        if self.image_format != "png":
            raise ValueError("Only PNG rendering is currently supported.")

    def render(self, pdf_path: str | Path, *, output_dir: str | Path | None = None) -> list[RenderedPDFPage]:
        path = Path(pdf_path)
        output_path = Path(output_dir) if output_dir is not None else None
        if output_path is not None:
            output_path.mkdir(parents=True, exist_ok=True)

        pages: list[RenderedPDFPage] = []
        with fitz.open(path) as document:
            zoom = self.dpi / 72
            matrix = fitz.Matrix(zoom, zoom)
            for page_index, page in enumerate(document):
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                image_bytes = pixmap.tobytes(self.image_format)
                image_path = None
                if output_path is not None:
                    image_path = output_path / f"page_{page_index + 1:03d}.png"
                    image_path.write_bytes(image_bytes)
                pages.append(
                    RenderedPDFPage(
                        page_number=page_index + 1,
                        width=pixmap.width,
                        height=pixmap.height,
                        mime_type="image/png",
                        image_bytes=image_bytes,
                        image_path=image_path,
                    )
                )
        return pages
