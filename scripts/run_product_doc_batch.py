from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_ROOT = Path("data") / "raw"
DEFAULT_DATA_ROOT = Path("data") / "product_doc_agent"
# MarkItDown 当前对老 Word .doc 支持不稳定，默认不进入深度抽取队列。
# 如后续接入 LibreOffice/antiword 转换器，再把 .doc 放开或使用 --include-doc。
SUPPORTED_EXTENSIONS = {".docx", ".xlsx", ".xls", ".pdf", ".txt"}
LEGACY_DOC_EXTENSION = ".doc"
IGNORED_NAMES = {"Thumbs.db", ".DS_Store"}
RESULT_PREFIX = "PRODUCT_DOC_BATCH_RESULT="


@dataclass(frozen=True)
class RawDocumentTask:
    """待处理原始文档任务。"""

    source_path: Path
    raw_root: Path

    @property
    def relative_path(self) -> Path:
        """原始文件相对 raw 根目录的路径。"""
        return self.source_path.relative_to(self.raw_root)

    @property
    def category_path(self) -> Path:
        """按 raw 目录结构推导出的产品分类路径。"""
        return self.relative_path.parent

    @property
    def category_levels(self) -> list[str]:
        """分类层级，后续前端可以直接用于树形筛选。"""
        return list(self.category_path.parts)


def main() -> int:
    # 子进程以 scripts 目录下的脚本启动时，sys.path 默认不一定包含项目根目录。
    # 这里显式加入项目根目录，保证 agent/config/schema 等项目包都能被导入。
    ensure_project_root_on_path()

    parser = build_arg_parser()
    args = parser.parse_args()

    if args.single_file:
        return run_single_file(args)
    return run_batch(args)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量抽取 data/raw 下的产品文档。")
    parser.add_argument("--root", default=str(DEFAULT_RAW_ROOT), help="raw 根目录或某个分类子目录。")
    parser.add_argument("--raw-root", default=str(DEFAULT_RAW_ROOT), help="用于计算分类路径的 raw 总根目录。")
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT), help="ProductDocAgent 输出根目录。")
    parser.add_argument("--yes", action="store_true", help="不逐个文件暂停确认，直接连续处理。")
    parser.add_argument("--dry-run", action="store_true", help="只列出将处理的文件，不实际抽取。")
    parser.add_argument("--skip-existing", action="store_true", help="如果 source_path 已经存在于 review JSON 中，则跳过。")
    parser.add_argument("--no-debug", action="store_true", help="关闭 Markdown/debug 文件输出。")
    parser.add_argument("--include-doc", action="store_true", help="包含老 Word .doc 文件；默认跳过，因为 MarkItDown 通常不支持。")
    parser.add_argument(
        "--convert-doc",
        action="store_true",
        help="用本机 Microsoft Word COM 把 .doc 转成 .docx 后再抽取；需要已安装 Word。",
    )
    parser.add_argument(
        "--single-file",
        default="",
        help="内部参数：只处理一个文件。父进程会为每个文件启动一次子进程，方便每个文件前切换 API。",
    )
    return parser


def run_batch(args: argparse.Namespace) -> int:
    raw_root = Path(args.raw_root).resolve()
    scan_root = Path(args.root).resolve()
    data_root = Path(args.data_root).resolve()

    tasks = scan_raw_documents(scan_root, raw_root, include_doc=args.include_doc or args.convert_doc)
    if args.skip_existing:
        existing_sources = collect_existing_source_paths(data_root)
        tasks = [task for task in tasks if normalize_path(task.source_path) not in existing_sources]

    print(f"raw_root: {raw_root}")
    print(f"scan_root: {scan_root}")
    print(f"待处理文件数: {len(tasks)}")

    for index, task in enumerate(tasks, start=1):
        print("")
        print(f"[{index}/{len(tasks)}] {task.relative_path}")
        print(f"分类: {format_category(task.category_levels)}")
        print(f"文件: {task.source_path}")

        if args.dry_run:
            continue

        # 每个文件前暂停，给你留出修改 .env / API Key / 模型名的时间。
        if not args.yes:
            answer = input("按回车开始处理；输入 s 跳过；输入 q 退出：").strip().lower()
            if answer == "q":
                print("已退出。")
                return 0
            if answer == "s":
                print("已跳过。")
                continue

        result = run_child_process(task, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    return 0


def ensure_project_root_on_path() -> None:
    """把项目根目录加入 Python 导入路径，解决子进程找不到 agent 包的问题。"""

    project_root = str(PROJECT_ROOT)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


def run_single_file(args: argparse.Namespace) -> int:
    """子进程入口：只处理一个文件，确保每次都重新加载 .env 和 LLM 配置。"""

    from agent.product_doc_agent.workflow import ProductDocAgentWorkflow

    raw_root = Path(args.raw_root).resolve()
    original_source_path = Path(args.single_file).resolve()
    data_root = Path(args.data_root).resolve()
    task = RawDocumentTask(source_path=original_source_path, raw_root=raw_root)

    started_at = datetime.now().isoformat(timespec="seconds")
    try:
        # 老 .doc 先转换成 .docx，再进入现有 MarkItDown + LLM 抽取流程。
        source_path = prepare_source_for_extraction(task, data_root, convert_doc=args.convert_doc)
        workflow = ProductDocAgentWorkflow(
            data_root=data_root,
            enable_debug_markdown=not args.no_debug,
        )
        result = workflow.run(source_path)

        review_path = Path(result.review_path)
        product_json = load_json(review_path)
        enrich_category_metadata(product_json, task, extraction_source_path=source_path)
        write_json(review_path, product_json)

        classified_review_path = copy_to_category_review(product_json, review_path, task, data_root)
        payload = {
            "status": "success",
            "document_id": result.document_id,
            "source_path": str(original_source_path),
            "extraction_source_path": str(source_path),
            "raw_relative_path": path_to_posix(task.relative_path),
            "category_path": path_to_posix(task.category_path),
            "category_levels": task.category_levels,
            "review_path": str(review_path),
            "classified_review_path": str(classified_review_path),
            "validation_issue_count": result.validation_issue_count,
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        print(f"{RESULT_PREFIX}{json.dumps(payload, ensure_ascii=False)}")
        return 0
    except Exception as exc:
        # 批量处理时单个文件失败不能中断整批；父进程会继续处理下一个文件。
        payload = {
            "status": "failed",
            "source_path": str(original_source_path),
            "raw_relative_path": path_to_posix(task.relative_path),
            "category_path": path_to_posix(task.category_path),
            "category_levels": task.category_levels,
            "error_type": exc.__class__.__name__,
            "error": str(exc),
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        print(f"{RESULT_PREFIX}{json.dumps(payload, ensure_ascii=False)}")
        return 1


def scan_raw_documents(scan_root: Path, raw_root: Path, *, include_doc: bool = False) -> list[RawDocumentTask]:
    """递归扫描 raw 文档，跳过临时文件和不支持的后缀。"""

    if not scan_root.exists():
        raise FileNotFoundError(f"扫描目录不存在: {scan_root}")
    if not raw_root.exists():
        raise FileNotFoundError(f"raw 根目录不存在: {raw_root}")

    tasks: list[RawDocumentTask] = []
    for path in sorted(scan_root.rglob("*")):
        if not path.is_file() or should_ignore_file(path):
            continue
        suffix = path.suffix.lower()
        if suffix == LEGACY_DOC_EXTENSION and not include_doc:
            continue
        if suffix not in SUPPORTED_EXTENSIONS and suffix != LEGACY_DOC_EXTENSION:
            continue
        tasks.append(RawDocumentTask(source_path=path.resolve(), raw_root=raw_root))
    return tasks


def should_ignore_file(path: Path) -> bool:
    """过滤 Word 临时文件、系统文件和缓存文件。"""

    name = path.name
    if name in IGNORED_NAMES:
        return True
    if name.startswith("~$"):
        return True
    if path.suffix.lower() in {".tmp", ".pyc"}:
        return True
    return "__pycache__" in path.parts


def run_child_process(task: RawDocumentTask, args: argparse.Namespace) -> dict[str, Any]:
    """启动子进程处理单个文件；子进程会重新读取当前 API 配置。"""

    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--single-file",
        str(task.source_path),
        "--raw-root",
        str(task.raw_root),
        "--data-root",
        str(Path(args.data_root)),
    ]
    if args.no_debug:
        command.append("--no-debug")
    if args.convert_doc:
        command.append("--convert-doc")

    completed = subprocess.run(
        command,
        cwd=Path.cwd(),
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_process_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(completed.stdout)

    result = parse_child_result(completed.stdout)
    if completed.returncode != 0:
        return {
            "status": "failed",
            "source_path": str(task.source_path),
            "category_path": path_to_posix(task.category_path),
            "returncode": completed.returncode,
            "error": result.get("error") or "子进程处理失败，请查看上方日志。",
        }
    return result


def child_process_env() -> dict[str, str]:
    """子进程强制使用 UTF-8 输出，避免中文路径和错误信息乱码。"""

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def parse_child_result(output: str) -> dict[str, Any]:
    """从子进程日志中提取结构化结果。"""

    for line in reversed(output.splitlines()):
        if not line.startswith(RESULT_PREFIX):
            continue
        try:
            return json.loads(line[len(RESULT_PREFIX) :])
        except json.JSONDecodeError as exc:
            return {"status": "failed", "error": f"结果 JSON 解析失败: {exc}"}
    return {"status": "failed", "error": "没有找到子进程结果输出。"}


def prepare_source_for_extraction(task: RawDocumentTask, data_root: Path, *, convert_doc: bool) -> Path:
    """准备实际进入抽取流程的文件；老 .doc 可先转成 .docx。"""

    if task.source_path.suffix.lower() != LEGACY_DOC_EXTENSION:
        return task.source_path
    if not convert_doc:
        raise RuntimeError("老 Word .doc 文件需要加 --convert-doc 才能转换后抽取。")
    return convert_doc_to_docx_with_word(task, data_root)


def convert_doc_to_docx_with_word(task: RawDocumentTask, data_root: Path) -> Path:
    """使用 Microsoft Word COM 将 .doc 转换为 .docx。

    转换文件按 raw 原目录结构保存到 data/product_doc_agent/converted_docx 下，
    不污染原始 raw 目录，也方便后续检查转换质量。
    """

    output_path = (data_root / "converted_docx" / task.relative_path.with_suffix(".docx")).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_mtime >= task.source_path.stat().st_mtime:
        return output_path

    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise RuntimeError("缺少 Word COM 依赖，请先安装 pywin32：pip install pywin32") from exc

    work_doc_path = prepare_ascii_doc_work_file(task, data_root)
    word = None
    document = None
    pythoncom.CoInitialize()
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        # Word COM 对中文路径、特殊字符路径和旧 .doc 组合比较敏感。
        # 先复制到纯英文工作路径，再打开转换，能显著减少“不是有效文件名”的误报。
        document = word.Documents.Open(str(work_doc_path), ReadOnly=True, AddToRecentFiles=False)
        # FileFormat=16 表示 wdFormatXMLDocument，即 .docx。
        document.SaveAs2(str(output_path), FileFormat=16)
        return output_path
    finally:
        if document is not None:
            document.Close(False)
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()


def prepare_ascii_doc_work_file(task: RawDocumentTask, data_root: Path) -> Path:
    """把 .doc 复制到纯英文临时路径，避免 Word COM 受中文路径影响。"""

    digest = hashlib.sha1(str(task.source_path).encode("utf-8")).hexdigest()[:16]
    work_dir = (data_root / "conversion_work").resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    work_doc_path = work_dir / f"source_{digest}.doc"
    if not work_doc_path.exists() or work_doc_path.stat().st_mtime < task.source_path.stat().st_mtime:
        shutil.copy2(task.source_path, work_doc_path)
    return work_doc_path


def enrich_category_metadata(product_json: dict[str, Any], task: RawDocumentTask, *, extraction_source_path: Path) -> None:
    """把 raw 目录分类写入 JSON，方便审核前端按分类展示。"""

    meta = product_json.setdefault("extraction_meta", {})
    meta["raw_category"] = {
        "raw_root": str(task.raw_root),
        "raw_relative_path": path_to_posix(task.relative_path),
        "category_path": path_to_posix(task.category_path),
        "category_levels": task.category_levels,
    }
    meta["source_conversion"] = {
        "original_source_path": str(task.source_path),
        "extraction_source_path": str(extraction_source_path),
        "converted": task.source_path != extraction_source_path,
        "converter": "microsoft_word_com" if task.source_path != extraction_source_path else "",
    }


def copy_to_category_review(
    product_json: dict[str, Any],
    flat_review_path: Path,
    task: RawDocumentTask,
    data_root: Path,
) -> Path:
    """方案 B：保留平铺 review，同时复制一份到分类目录。

    后续如果确定只保留一种存储方式：
    1. 只保留平铺文件：删除本函数调用和 review_by_category 相关代码。
    2. 只保留分类文件：删除 workflow 默认平铺写入逻辑，改成直接写分类路径。
    3. 两者都保留：保持当前代码即可。
    """

    category_dir = data_root / "review_by_category" / task.category_path
    category_dir.mkdir(parents=True, exist_ok=True)
    classified_path = category_dir / flat_review_path.name
    write_json(classified_path, product_json)
    return classified_path


def collect_existing_source_paths(data_root: Path) -> set[str]:
    """扫描已有 review JSON，用 source_path 判断是否已抽取过。"""

    roots = [data_root / "review", data_root / "review_by_category"]
    existing: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.json"):
            try:
                data = load_json(path)
            except Exception:
                continue
            source_path = data.get("document_info", {}).get("source_path") or data.get("extraction_meta", {}).get("source_file")
            if source_path:
                existing.add(normalize_path(Path(source_path)))
    return existing


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_path(path: Path) -> str:
    return str(path.resolve()).lower()


def path_to_posix(path: Path) -> str:
    return path.as_posix() if str(path) != "." else ""


def format_category(levels: list[str]) -> str:
    return " / ".join(levels) if levels else "未分类"


if __name__ == "__main__":
    raise SystemExit(main())
