from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np


@dataclass(frozen=True)
class Region:
    name: str
    page: int
    box: tuple[float, float, float, float]
    kind: str
    description: str


def main() -> None:
    args = parse_args()
    pdf_path = Path(args.pdf)
    template_path = Path(args.template)
    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)

    template = load_json(template_path)
    regions = parse_regions(template)

    manifest = render_and_crop(
        pdf_path=pdf_path,
        regions=regions,
        output_dir=output_dir,
        dpi=args.dpi,
    )

    ocr_results = build_empty_ocr_results(manifest)
    if args.ocr:
        ocr_results = run_paddle_ocr(ocr_results)

    write_json(output_dir / "manifest.json", manifest)
    write_json(output_dir / "ocr_result.json", ocr_results)

    print(f"Wrote manifest: {output_dir / 'manifest.json'}")
    print(f"Wrote OCR result: {output_dir / 'ocr_result.json'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Independent inkprint PDF OCR prototype.")
    parser.add_argument("--pdf", required=True, help="Scanned PDF path.")
    parser.add_argument("--template", required=True, help="Template JSON path.")
    parser.add_argument("--out", required=True, help="Output directory.")
    parser.add_argument("--dpi", type=int, default=300, help="Render DPI. Default: 300.")
    parser.add_argument("--ocr", action="store_true", help="Run PaddleOCR if available.")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_regions(template: dict[str, Any]) -> list[Region]:
    result: list[Region] = []
    for item in template.get("regions", []):
        box = item["box"]
        if len(box) != 4:
            raise ValueError(f"Invalid region box for {item.get('name')}: {box}")
        result.append(
            Region(
                name=item["name"],
                page=int(item["page"]),
                box=(float(box[0]), float(box[1]), float(box[2]), float(box[3])),
                kind=item.get("kind", "text"),
                description=item.get("description", ""),
            )
        )
    return result


def render_and_crop(
    *,
    pdf_path: Path,
    regions: list[Region],
    output_dir: Path,
    dpi: int,
) -> dict[str, Any]:
    pages_dir = output_dir / "pages"
    regions_dir = output_dir / "regions"
    pages_dir.mkdir(parents=True, exist_ok=True)
    regions_dir.mkdir(parents=True, exist_ok=True)

    page_records: list[dict[str, Any]] = []
    region_records: list[dict[str, Any]] = []
    scale = dpi / 72.0

    with fitz.open(pdf_path) as document:
        for page_index, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            images = page.get_images(full=True)
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            page_path = pages_dir / f"page_{page_index:03d}.png"
            pix.save(page_path)

            image = cv2.imread(str(page_path), cv2.IMREAD_COLOR)
            metrics = image_metrics(image)
            page_records.append(
                {
                    "page": page_index,
                    "pdf_size": [page.rect.width, page.rect.height],
                    "image_size": [pix.width, pix.height],
                    "text_chars": len(text),
                    "image_objects": len(images),
                    "is_scanned_page": len(text) == 0 and len(images) > 0,
                    "rendered_path": str(page_path),
                    "image_metrics": metrics,
                }
            )

            for region in [r for r in regions if r.page == page_index]:
                crop_record = crop_region(
                    image=image,
                    region=region,
                    output_path=regions_dir / f"p{page_index:03d}_{region.name}.png",
                )
                region_records.append(crop_record)

    return {
        "source_pdf": str(pdf_path),
        "template_regions": len(regions),
        "pages": page_records,
        "regions": region_records,
    }


def crop_region(*, image: np.ndarray, region: Region, output_path: Path) -> dict[str, Any]:
    height, width = image.shape[:2]
    x0, y0, x1, y1 = region.box
    px0 = clamp(round(x0 * width), 0, width - 1)
    py0 = clamp(round(y0 * height), 0, height - 1)
    px1 = clamp(round(x1 * width), px0 + 1, width)
    py1 = clamp(round(y1 * height), py0 + 1, height)
    crop = image[py0:py1, px0:px1]
    processed = preprocess_for_ocr(crop)
    cv2.imwrite(str(output_path), processed)
    return {
        "name": region.name,
        "page": region.page,
        "kind": region.kind,
        "description": region.description,
        "relative_box": list(region.box),
        "pixel_box": [px0, py0, px1, py1],
        "image_path": str(output_path),
        "image_metrics": image_metrics(processed),
    }


def preprocess_for_ocr(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(gray, h=8)
    normalized = cv2.normalize(denoised, None, 0, 255, cv2.NORM_MINMAX)
    return cv2.adaptiveThreshold(
        normalized,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        12,
    )


def image_metrics(image: np.ndarray) -> dict[str, Any]:
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image
    return {
        "mean_gray": round(float(np.mean(gray)), 2),
        "std_gray": round(float(np.std(gray)), 2),
        "dark_pixel_ratio": round(float(np.mean(gray < 120)), 4),
        "width": int(gray.shape[1]),
        "height": int(gray.shape[0]),
    }


def build_empty_ocr_results(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_pdf": manifest["source_pdf"],
        "engine": "none",
        "regions": [
            {
                "name": region["name"],
                "page": region["page"],
                "kind": region["kind"],
                "description": region["description"],
                "image_path": region["image_path"],
                "pixel_box": region["pixel_box"],
                "text": "",
                "confidence": 0.0,
                "lines": [],
            }
            for region in manifest["regions"]
        ],
    }


def run_paddle_ocr(ocr_results: dict[str, Any]) -> dict[str, Any]:
    try:
        from paddleocr import PaddleOCR
    except Exception as exc:  # pragma: no cover - depends on local install
        ocr_results["engine"] = "paddleocr_unavailable"
        ocr_results["engine_error"] = str(exc)
        return ocr_results

    engine = None
    init_errors: list[str] = []
    for kwargs in (
        {"use_textline_orientation": True, "lang": "ch"},
        {"use_angle_cls": True, "lang": "ch"},
        {"lang": "ch"},
    ):
        try:
            engine = PaddleOCR(**kwargs)
            break
        except Exception as exc:  # pragma: no cover - version/runtime dependent
            init_errors.append(f"{kwargs}: {exc}")
    if engine is None:
        ocr_results["engine"] = "paddleocr_init_failed"
        ocr_results["engine_error"] = " | ".join(init_errors)
        return ocr_results

    ocr_results["engine"] = "paddleocr"
    for region in ocr_results["regions"]:
        try:
            raw = engine.ocr(region["image_path"], cls=True)
            lines = flatten_paddle_result(raw)
            region["lines"] = lines
            region["text"] = "\n".join(line["text"] for line in lines)
            confidences = [line["confidence"] for line in lines if line["confidence"] is not None]
            region["confidence"] = round(statistics.mean(confidences), 4) if confidences else 0.0
        except Exception as exc:  # pragma: no cover - model/runtime dependent
            region["error"] = str(exc)
    return ocr_results


def flatten_paddle_result(raw: Any) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    if not raw:
        return lines
    page_items = raw[0] if isinstance(raw, list) and raw and isinstance(raw[0], list) else raw
    for item in page_items or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        box, payload = item[0], item[1]
        if not isinstance(payload, (list, tuple)) or len(payload) < 2:
            continue
        text, confidence = payload[0], payload[1]
        lines.append(
            {
                "text": str(text),
                "confidence": float(confidence) if confidence is not None else None,
                "box": box,
            }
        )
    return lines


def clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(value, upper))


if __name__ == "__main__":
    main()
