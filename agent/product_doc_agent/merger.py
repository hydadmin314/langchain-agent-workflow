from __future__ import annotations

from copy import deepcopy
from typing import Any

from schema import make_empty_product_document


class ProductDocumentMerger:
    """把模块级大模型输出合并成新 schema 的产品资料包 JSON。"""

    def merge(
        self,
        module_outputs: dict[str, Any],
        *,
        document_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """合并模块输出；支持 application_form_info.xxx 这类点路径模块名。"""

        product_document = make_empty_product_document()
        document_metadata = document_metadata or {}
        module_source_files = normalize_module_source_files(document_metadata.get("module_source_files", {}))

        for module_name, module_value in module_outputs.items():
            if module_name.startswith("__"):
                continue
            source_file = module_source_files.get(module_name) or metadata_source_file(document_metadata)
            self._set_module_value(product_document, module_name, with_source_file(module_value, source_file))

        self._fill_extraction_meta(product_document, document_metadata)
        self._fill_document_info_fallback(product_document, document_metadata)
        return product_document

    def merge_many(
        self,
        module_outputs_list: list[dict[str, Any]],
        *,
        document_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """合并多个文件的模块输出，用于产品目录级抽取。"""

        product_document = make_empty_product_document()
        document_metadata = document_metadata or {}
        for module_outputs in module_outputs_list:
            module_source_files = normalize_module_source_files(module_outputs.get("__module_source_files__", {}))
            for module_name, module_value in module_outputs.items():
                if module_name.startswith("__"):
                    continue
                source_file = module_source_files.get(module_name) or metadata_source_file(document_metadata)
                self._set_module_value(product_document, module_name, with_source_file(module_value, source_file))

        self._fill_extraction_meta(product_document, document_metadata)
        self._fill_document_info_fallback(product_document, document_metadata)
        return product_document

    def apply_self_check(self, product_document: dict[str, Any], self_check: dict[str, Any]) -> dict[str, Any]:
        """保留兼容入口；当前阶段不主动跑 self_check，只在需要时写入元数据。"""

        result = deepcopy(product_document)
        meta = result.setdefault("extraction_meta", {})
        meta["validation_issues"] = normalize_validation_issues(self_check.get("validation_issues", []))
        meta["validation_issue_count"] = len(meta["validation_issues"])
        return result

    def _set_module_value(self, product_document: dict[str, Any], module_name: str, value: Any) -> None:
        """按点路径写入模块值；只允许写入 schema 已有路径。"""

        parts = module_name.split(".")
        if len(parts) != 2:
            return
        top_key, child_key = parts
        top_value = product_document.get(top_key)
        if not isinstance(top_value, dict) or child_key not in top_value:
            return
        top_value[child_key] = value

    def _fill_extraction_meta(self, product_document: dict[str, Any], metadata: dict[str, Any]) -> None:
        """补齐抽取元数据，不做业务归一化和校验。"""

        meta = product_document.setdefault("extraction_meta", {})
        meta["source_file"] = metadata_source_file(metadata)
        meta["method"] = "llm_schema_module_extraction_no_normalization"
        meta.setdefault("validation_issues", [])
        meta["validation_issue_count"] = len(meta.get("validation_issues", []))

    def _fill_document_info_fallback(self, product_document: dict[str, Any], metadata: dict[str, Any]) -> None:
        """申请表 document_info 用文件元数据兜底，避免空白 ID 影响审查定位。"""

        info = product_document.get("application_form_info", {}).get("document_info", {})
        if not isinstance(info, dict):
            return
        info["document_id"] = info.get("document_id") or metadata.get("document_id", "")
        info["source_file"] = info.get("source_file") or metadata_source_file(metadata)


def ensure_list(value: Any) -> list[Any]:
    """把任意值转成列表，供 self_check 兼容逻辑使用。"""

    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def metadata_source_file(metadata: dict[str, Any]) -> str:
    """按优先级取本次抽取的来源；目录级抽取用目录路径表示来源包。"""

    return str(
        metadata.get("source_file")
        or metadata.get("source_path")
        or metadata.get("source_folder")
        or ""
    )


def normalize_module_source_files(value: Any) -> dict[str, str]:
    """规范化模块到来源文件的映射，避免合并时依赖模型填写 source_file。"""

    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if item}


def with_source_file(value: Any, source_file: str) -> Any:
    """给模块输出补齐 source_file；列表模块会逐条补齐。"""

    if not source_file:
        return value
    if isinstance(value, list):
        return [with_source_file(item, source_file) for item in value]
    if isinstance(value, dict):
        result = deepcopy(value)
        if "source_file" in result:
            result["source_file"] = source_file
        for key, child in list(result.items()):
            if isinstance(child, (dict, list)):
                result[key] = with_source_file(child, source_file)
        return result
    return value


def normalize_validation_issues(value: Any) -> list[dict[str, Any]]:
    """规范化 self_check 输出的问题列表。"""

    issues: list[dict[str, Any]] = []
    for index, item in enumerate(ensure_list(value)):
        if isinstance(item, dict):
            issues.append(item)
        elif item is not None:
            issues.append(
                {
                    "severity": "warning",
                    "path": f"llm_self_check.validation_issues[{index}]",
                    "message": str(item),
                }
            )
    return issues
