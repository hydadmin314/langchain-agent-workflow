from __future__ import annotations

import json
from pathlib import Path

from agent.sales_recommendation_agent.product_repository.models import (
    ProductCandidate,
    ProductLoadError,
    ProductLoadResult,
)
from agent.sales_recommendation_agent.product_repository.normalizer import ProductDocumentNormalizer


class ProductRepository:
    """产品主数据仓库。

    当前实现读取本地 published JSON。后续如果接数据库或对象存储，也只需要替换这一层。
    """

    def __init__(
        self,
        root: str | Path = "data/product_doc_agent/published",
        *,
        normalizer: ProductDocumentNormalizer | None = None,
    ):
        self.root = Path(root)
        self.normalizer = normalizer or ProductDocumentNormalizer()

    def load_products(self) -> list[ProductCandidate]:
        """只返回成功加载的产品候选。

        推荐流程常用这个方法；如果需要排查失败文件，使用 load_result()。
        """

        return self.load_result().products

    def load_result(self) -> ProductLoadResult:
        """读取全部 JSON，并保留单文件失败信息。

        单个文件损坏不能影响其他产品进入推荐候选池。
        """

        result = ProductLoadResult()
        for json_path in self.iter_json_files():
            try:
                payload = self._read_json(json_path)
                result.products.append(self.normalizer.normalize(payload, json_path=json_path))
            except Exception as exc:  # noqa: BLE001 - 数据文件质量不可控，这里需要兜底记录。
                result.errors.append(
                    ProductLoadError(
                        path=str(json_path),
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
                )
        return result

    def iter_json_files(self) -> list[Path]:
        """递归扫描 published 目录，兼容后续分类子目录。"""

        if not self.root.exists():
            return []
        return sorted(path for path in self.root.rglob("*.json") if path.is_file())

    def _read_json(self, json_path: Path) -> dict:
        with json_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        if not isinstance(payload, dict):
            raise ValueError("product JSON root must be an object")
        return payload
