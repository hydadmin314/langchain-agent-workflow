from __future__ import annotations

import argparse
import base64
import mimetypes
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.product_doc_agent.ocr_flow.pdf_page_renderer import PDFPageRenderer, RenderedPDFPage


DEFAULT_BASE_URL = "https://qianfan.baidubce.com/v2"
DEFAULT_PROMPT = """
请对这张中文电信业务申请表/协议页做 OCR 文档解析，并输出 Markdown。

要求：
1. 保留原文顺序，不要总结，不要改写。
2. 表格尽量输出为 Markdown 表格。
3. 协议条款按原段落输出。
4. 看不清的文字用 [无法识别] 标注，不要猜测。
5. 不要输出解释说明，只输出 Markdown 正文。
6. 保留费用、速率、缴费期、违约金、逾期、暂停服务、经营许可证等关键字段原文。
""".strip()

COMPARE_KEYWORDS = (
    "客户名称",
    "速率",
    "一次性费用",
    "月使用费",
    "缴费期",
    "乙方同意向甲方提供服务",
    "本协议费用包括",
    "逾期未支付通信费用",
    "违约金",
    "暂停服务",
    "经营许可证",
    "经营资质",
    "IDC",
    "ISP",
    "CDN",
)


@dataclass(frozen=True)
class OCRPage:
    page_number: int
    mime_type: str
    image_bytes: bytes
    image_path: Path | None = None

    @property
    def data_url(self) -> str:
        encoded = base64.b64encode(self.image_bytes).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"


def main() -> None:
    load_dotenv()
    args = parse_args()
    input_path = Path(args.input).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")

    output_path = resolve_output_path(args.output, input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    base_url = args.base_url or env_first("BAIDU_MULTIMODAL_BASE_URL", "QIANFAN_BASE_URL") or DEFAULT_BASE_URL
    api_key = args.api_key or env_first(
        "BAIDU_MULTIMODAL_API_KEY",
        "QIANFAN_API_KEY",
        "PRODUCT_DOC_OCR_LLM_API_KEY",
        "OPENAI_API_KEY",
    )
    model = args.model or env_first("BAIDU_MULTIMODAL_MODEL", "QIANFAN_MODEL", "PRODUCT_DOC_OCR_LLM_MODEL")
    if not api_key:
        raise ValueError(
            "Missing API key. Set BAIDU_MULTIMODAL_API_KEY or QIANFAN_API_KEY, "
            "or pass --api-key."
        )
    if not model:
        raise ValueError(
            "Missing model. Set BAIDU_MULTIMODAL_MODEL or QIANFAN_MODEL, "
            "or pass --model."
        )

    pages = load_pages(
        input_path,
        page_spec=args.pages,
        dpi=args.dpi,
        page_image_dir=output_path.with_suffix("").parent / f"{output_path.stem}_pages" if args.save_pages else None,
    )
    if args.max_pages is not None:
        pages = pages[: args.max_pages]
    if not pages:
        raise ValueError("No pages/images selected for OCR.")

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=args.timeout)
    prompt = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else DEFAULT_PROMPT
    chunks: list[str] = []

    print(f"input: {input_path}")
    print(f"model: {model}")
    print(f"base_url: {base_url}")
    print(f"pages: {', '.join(str(page.page_number) for page in pages)}")
    print(f"output: {output_path}")

    started_at = time.perf_counter()
    for group in chunked(pages, max(1, args.pages_per_request)):
        group_started_at = time.perf_counter()
        page_numbers = ", ".join(str(page.page_number) for page in group)
        print(f"OCR request pages={page_numbers}")
        chunks.append(
            call_multimodal_ocr(
                client=client,
                model=model,
                pages=group,
                prompt=prompt,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
            )
        )
        print(f"OCR response pages={page_numbers}, elapsed_seconds={time.perf_counter() - group_started_at:.1f}")

    markdown = normalize_markdown("\n\n".join(chunks))
    output_path.write_text(markdown, encoding="utf-8")
    print(f"written: {output_path}")
    print(f"total_elapsed_seconds={time.perf_counter() - started_at:.1f}")

    if args.baseline:
        baseline_path = Path(args.baseline).resolve()
        report_path = output_path.with_suffix(".compare.txt")
        report_path.write_text(
            build_compare_report(
                baseline_path=baseline_path,
                candidate_path=output_path,
                baseline_text=baseline_path.read_text(encoding="utf-8"),
                candidate_text=markdown,
            ),
            encoding="utf-8",
        )
        print(f"compare_report: {report_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Try a Baidu/Qianfan-compatible multimodal OCR API on a PDF or image, without changing the main pipeline."
    )
    parser.add_argument("--input", required=True, help="PDF or image path.")
    parser.add_argument("--output", help="Markdown output path.")
    parser.add_argument("--baseline", help="Optional existing Markdown file for a simple comparison report.")
    parser.add_argument("--pages", help="PDF pages to OCR, e.g. 1,3-5. Defaults to all pages.")
    parser.add_argument("--max-pages", type=int, help="Limit selected pages after --pages filtering.")
    parser.add_argument("--pages-per-request", type=int, default=1, help="How many page images to send per API request.")
    parser.add_argument("--dpi", type=int, default=220, help="PDF render DPI.")
    parser.add_argument("--save-pages", action="store_true", help="Write rendered PDF page images next to the output.")
    parser.add_argument("--prompt-file", help="Optional custom prompt text file.")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL. Defaults to BAIDU_MULTIMODAL_BASE_URL/QIANFAN_BASE_URL.")
    parser.add_argument("--api-key", help="API key. Prefer env vars instead of passing secrets on the command line.")
    parser.add_argument("--model", help="Vision/multimodal model name.")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args()


def load_pages(
    input_path: Path,
    *,
    page_spec: str | None,
    dpi: int,
    page_image_dir: Path | None,
) -> list[OCRPage]:
    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        rendered_pages = PDFPageRenderer(dpi=dpi).render(input_path, output_dir=page_image_dir)
        selected_page_numbers = parse_page_spec(page_spec, total_pages=len(rendered_pages))
        return [to_ocr_page(page) for page in rendered_pages if page.page_number in selected_page_numbers]

    mime_type = mimetypes.guess_type(input_path.name)[0] or "image/png"
    if not mime_type.startswith("image/"):
        raise ValueError(f"Only PDF or image files are supported by this trial script: {input_path}")
    return [
        OCRPage(
            page_number=1,
            mime_type=mime_type,
            image_bytes=input_path.read_bytes(),
            image_path=input_path,
        )
    ]


def to_ocr_page(page: RenderedPDFPage) -> OCRPage:
    return OCRPage(
        page_number=page.page_number,
        mime_type=page.mime_type,
        image_bytes=page.image_bytes,
        image_path=page.image_path,
    )


def parse_page_spec(page_spec: str | None, *, total_pages: int) -> set[int]:
    if not page_spec:
        return set(range(1, total_pages + 1))

    selected: set[int] = set()
    for part in page_spec.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            selected.update(range(start, end + 1))
        else:
            selected.add(int(token))
    return {page for page in selected if 1 <= page <= total_pages}


def call_multimodal_ocr(
    *,
    client: OpenAI,
    model: str,
    pages: list[OCRPage],
    prompt: str,
    temperature: float,
    max_tokens: int,
) -> str:
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": f"{prompt}\n\nPages in this request: {', '.join(str(page.page_number) for page in pages)}",
        }
    ]
    for page in pages:
        content.append({"type": "image_url", "image_url": {"url": page.data_url}})

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


def build_compare_report(
    *,
    baseline_path: Path,
    candidate_path: Path,
    baseline_text: str,
    candidate_text: str,
) -> str:
    lines = [
        "# OCR Compare Report",
        "",
        f"- baseline: {baseline_path}",
        f"- candidate: {candidate_path}",
        "",
        "| metric | baseline | candidate |",
        "| --- | ---: | ---: |",
        f"| chars | {len(baseline_text)} | {len(candidate_text)} |",
        f"| markdown_table_rows | {count_table_rows(baseline_text)} | {count_table_rows(candidate_text)} |",
        f"| blank_table_rows | {count_blank_table_rows(baseline_text)} | {count_blank_table_rows(candidate_text)} |",
        f"| unreadable_markers | {baseline_text.count('[无法识别]')} | {candidate_text.count('[无法识别]')} |",
        "",
        "## Keyword Hits",
        "",
        "| keyword | baseline | candidate |",
        "| --- | ---: | ---: |",
    ]
    for keyword in COMPARE_KEYWORDS:
        lines.append(f"| {keyword} | {baseline_text.count(keyword)} | {candidate_text.count(keyword)} |")
    return "\n".join(lines).strip() + "\n"


def count_table_rows(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip().startswith("|") and line.strip().endswith("|"))


def count_blank_table_rows(text: str) -> int:
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells and not any(cells):
            count += 1
    return count


def chunked(items: list[OCRPage], chunk_size: int) -> list[list[OCRPage]]:
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]


def resolve_output_path(output: str | None, input_path: Path) -> Path:
    if output:
        return Path(output).resolve()
    output_dir = Path("data/product_doc_agent/debug/multimodal_ocr_trials").resolve()
    return output_dir / f"{safe_stem(input_path)}_ocr.md"


def safe_stem(path: Path) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", path.stem).strip("._") or "document"


def normalize_markdown(markdown: str) -> str:
    return str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def env_first(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


if __name__ == "__main__":
    main()
