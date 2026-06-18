from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from scripts.run_product_doc_batch import scan_product_folders


class ProductDocBatchScriptTest(unittest.TestCase):
    def test_scan_product_folders_groups_known_documents_by_parent_folder(self) -> None:
        """批量入口应该按产品目录分组，而不是把每个文档当成独立任务。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            raw_root = Path(temp_dir)
            product_a = raw_root / "上网" / "电信" / "精品专线"
            product_b = raw_root / "组网" / "电信" / "本地国内MSTP"
            product_a.mkdir(parents=True)
            product_b.mkdir(parents=True)

            (product_a / "1 产品介绍.docx").write_text("intro", encoding="utf-8")
            (product_a / "2 资费表.xlsx").write_text("pricing", encoding="utf-8")
            (product_a / "临时说明.docx").write_text("unknown", encoding="utf-8")
            (product_b / "4 申请表_转定用的大客户需求表.doc").write_text("form", encoding="utf-8")
            (product_b / "4-5 本地国内MSTP申请手续提示.txt").write_text("materials", encoding="utf-8")

            tasks = scan_product_folders(raw_root, raw_root)

        self.assertEqual([task.relative_path.as_posix() for task in tasks], [
            "上网/电信/精品专线",
            "组网/电信/本地国内MSTP",
        ])
        self.assertEqual([len(task.source_files) for task in tasks], [2, 2])


if __name__ == "__main__":
    unittest.main()
