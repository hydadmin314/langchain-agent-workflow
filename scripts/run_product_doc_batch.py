from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = Path("data") / "product_doc_agent"
LEGACY_DOC_EXTENSION = ".doc"
IGNORED_NAMES = {"Thumbs.db", ".DS_Store"}
RESULT_PREFIX = "PRODUCT_DOC_BATCH_RESULT="


@dataclass(frozen=True)
class ProductFolderTask:
    """批量任务中的一个产品目录。"""

    folder_path: Path
    raw_root: Path
    source_files: tuple[Path, ...]

    @property
    def relative_path(self) -> Path:
        """产品目录相对 PRODUCT_DOC_RAW_ROOT 的路径。"""

        return self.folder_path.relative_to(self.raw_root)

    @property
    def category_levels(self) -> list[str]:
        """产品目录层级，后续可以直接用于前端筛选或日志展示。"""

        return list(self.relative_path.parts)


@dataclass(frozen=True)
class RawDocumentTask:
    """旧版单文件任务，只保留给 .doc 转 .docx 测试和兼容代码复用。"""

    source_path: Path
    raw_root: Path

    @property
    def relative_path(self) -> Path:
        """原始文件相对 raw 根目录的路径。"""

        return self.source_path.relative_to(self.raw_root)

    @property
    def category_path(self) -> Path:
        """单文件所在的产品目录相对路径。"""

        return self.relative_path.parent

    @property
    def category_levels(self) -> list[str]:
        """单文件所在的产品目录层级。"""

        return list(self.category_path.parts)


def main() -> int:
    """正式批量入口：按 PRODUCT_DOC_RAW_ROOT 扫描产品目录并逐目录抽取。"""

    ensure_project_root_on_path()
    parser = build_arg_parser()
    args = parser.parse_args()
    return run_batch(args)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按产品目录批量抽取 PRODUCT_DOC_RAW_ROOT 下的文档。")
    parser.add_argument(
        "--root",
        default="",
        help="扫描目录，默认使用 .env 里的 PRODUCT_DOC_RAW_ROOT；也可以传某个产品分类子目录。",
    )
    parser.add_argument(
        "--raw-root",
        default="",
        help="原始数据总根目录，默认使用 .env 里的 PRODUCT_DOC_RAW_ROOT，用于计算产品相对路径。",
    )
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT), help="ProductDocAgent 输出根目录。")
    parser.add_argument("--yes", action="store_true", help="不逐个产品暂停确认，直接连续处理。")
    parser.add_argument("--dry-run", action="store_true", help="只列出将处理的产品目录，不调用大模型。")
    parser.add_argument("--skip-existing", action="store_true", help="如果该产品目录已经存在 review JSON，则跳过。")
    parser.add_argument("--no-debug", action="store_true", help="关闭 Markdown 切块 debug 文件输出。")
    parser.add_argument("--no-post-processing", action="store_true", help="关闭归一化和程序校验，只保留大模型合并结果。")
    parser.add_argument("--max-concurrency", type=int, default=4, help="模块抽取时的大模型并发数。")
    parser.add_argument("--max-context-chars", type=int, default=12000, help="单个模块传给大模型的最大上下文字符数。")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少个产品目录；0 表示不限制。")
    return parser


def run_batch(args: argparse.Namespace) -> int:
    """扫描产品目录并调用产品目录级 workflow。"""

    from agent.product_doc_agent.workflow import ProductDocAgentWorkflow, build_product_folder_id

    raw_root = resolve_raw_root(args.raw_root)
    scan_root = Path(args.root).resolve() if args.root else raw_root
    data_root = Path(args.data_root).resolve()

    tasks = scan_product_folders(scan_root, raw_root)
    if args.skip_existing:
        tasks = [task for task in tasks if not review_json_exists(data_root, build_product_folder_id(task.folder_path))]
    if args.limit > 0:
        tasks = tasks[: args.limit]

    print(f"raw_root: {raw_root}")
    print(f"scan_root: {scan_root}")
    print(f"产品目录数: {len(tasks)}")

    if args.dry_run:
        for index, task in enumerate(tasks, start=1):
            print_product_task(index, len(tasks), task)
        return 0

    workflow = ProductDocAgentWorkflow(
        data_root=data_root,
        raw_root=raw_root,
        enable_debug_markdown=not args.no_debug,
        enable_post_processing=not args.no_post_processing,
        max_concurrency=args.max_concurrency,
        max_context_chars=args.max_context_chars,
    )

    for index, task in enumerate(tasks, start=1):
        print_product_task(index, len(tasks), task)
        if not args.yes and not confirm_product_task():
            continue

        started_at = datetime.now().isoformat(timespec="seconds")
        try:
            result = workflow.run_product_folder(task.folder_path)
            review_json = load_json(result.review_path)
            payload = {
                "status": "success",
                "product_id": result.product_id,
                "source_folder": str(result.source_folder),
                "raw_relative_path": path_to_posix(task.relative_path),
                "category_levels": task.category_levels,
                "source_file_count": len(task.source_files),
                "document_count": result.document_count,
                "module_count": result.module_count,
                "review_path": str(result.review_path),
                "debug_context_path": str(result.debug_context_path) if result.debug_context_path else "",
                "validation_issue_count": review_json.get("extraction_meta", {}).get("validation_issue_count", 0),
                "started_at": started_at,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            }
        except Exception as exc:
            payload = {
                "status": "failed",
                "source_folder": str(task.folder_path),
                "raw_relative_path": path_to_posix(task.relative_path),
                "category_levels": task.category_levels,
                "source_file_count": len(task.source_files),
                "error_type": exc.__class__.__name__,
                "error": str(exc),
                "started_at": started_at,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            }

        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"{RESULT_PREFIX}{json.dumps(payload, ensure_ascii=False)}")

    return 0


def ensure_project_root_on_path() -> None:
    """把项目根目录加入导入路径，保证脚本从 scripts 目录启动时也能找到 agent/config/schema。"""

    project_root = str(PROJECT_ROOT)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


def resolve_raw_root(raw_root_arg: str) -> Path:
    """优先使用命令行参数，否则读取 .env 中的 PRODUCT_DOC_RAW_ROOT。"""

    if raw_root_arg:
        return Path(raw_root_arg).resolve()

    from config.settings import PRODUCT_DOC_RAW_ROOT

    return Path(PRODUCT_DOC_RAW_ROOT).resolve()


def scan_product_folders(scan_root: Path, raw_root: Path) -> list[ProductFolderTask]:
    """递归扫描产品目录：只要目录下直接包含已知角色文档，就视为一个产品目录。"""

    from agent.product_doc_agent.document_classifier import UNKNOWN, classify_document

    if not scan_root.exists():
        raise FileNotFoundError(f"扫描目录不存在: {scan_root}")
    if not raw_root.exists():
        raise FileNotFoundError(f"PRODUCT_DOC_RAW_ROOT 不存在: {raw_root}")

    by_folder: dict[Path, list[Path]] = {}
    for path in sorted(scan_root.rglob("*")):
        if not path.is_file() or should_ignore_file(path):
            continue
        classification = classify_document(path)
        if classification.role == UNKNOWN:
            continue
        folder = path.parent.resolve()
        by_folder.setdefault(folder, []).append(path.resolve())

    return [
        ProductFolderTask(folder_path=folder, raw_root=raw_root, source_files=tuple(files))
        for folder, files in sorted(by_folder.items(), key=lambda item: str(item[0]))
    ]


def should_ignore_file(path: Path) -> bool:
    """过滤系统文件、临时文件和缓存文件。"""

    name = path.name
    if name in IGNORED_NAMES or name.startswith("~$"):
        return True
    if path.suffix.lower() in {".tmp", ".pyc"}:
        return True
    return "__pycache__" in path.parts


def review_json_exists(data_root: Path, product_id: str) -> bool:
    """根据 workflow 的产品目录 ID 判断 review JSON 是否已经存在。"""

    return (data_root / "review" / f"{product_id}.json").exists()


def print_product_task(index: int, total: int, task: ProductFolderTask) -> None:
    """打印一个产品目录任务的摘要。"""

    print("")
    print(f"[{index}/{total}] {path_to_posix(task.relative_path)}")
    print(f"产品目录: {task.folder_path}")
    print(f"文件数: {len(task.source_files)}")
    for source_file in task.source_files:
        print(f"  - {source_file.name}")


def confirm_product_task() -> bool:
    """批量运行时的人工确认入口。"""

    answer = input("按回车开始处理；输入 s 跳过；输入 q 退出：").strip().lower()
    if answer == "q":
        raise SystemExit(0)
    return answer != "s"


def convert_doc_to_docx_with_word(task: RawDocumentTask, data_root: Path) -> Path:
    """使用 Microsoft Word COM 把 .doc 转成 .docx。

    转换结果保存到 data/product_doc_agent/converted_docx 下，不污染原始数据目录。
    这个函数目前主要给真实文档切块测试复用；正式 workflow 内部也有同样的转换策略。
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

    temp_dir_manager = tempfile.TemporaryDirectory(prefix="product_doc_agent_doc_")
    work_doc_path = prepare_ascii_doc_work_file(task, Path(temp_dir_manager.name))
    word = None
    document = None
    pythoncom.CoInitialize()
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = word.Documents.Open(str(work_doc_path), ReadOnly=True, AddToRecentFiles=False)
        document.SaveAs2(str(output_path), FileFormat=16)
        return output_path
    finally:
        if document is not None:
            document.Close(False)
        if word is not None:
            word.Quit()
        temp_dir_manager.cleanup()
        pythoncom.CoUninitialize()


def prepare_ascii_doc_work_file(task: RawDocumentTask, work_dir: Path) -> Path:
    """把 .doc 复制到纯英文临时路径，降低 Word COM 打开中文路径失败的概率。"""

    digest = hashlib.sha1(str(task.source_path).encode("utf-8")).hexdigest()[:16]
    work_dir.mkdir(parents=True, exist_ok=True)
    work_doc_path = work_dir / f"source_{digest}{LEGACY_DOC_EXTENSION}"
    shutil.copy2(task.source_path, work_doc_path)
    return work_doc_path


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def path_to_posix(path: Path) -> str:
    return path.as_posix() if str(path) != "." else ""


if __name__ == "__main__":
    raise SystemExit(main())
