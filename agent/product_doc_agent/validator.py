from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from schema import PRODUCT_DOCUMENT_JSON_SCHEMA


EVIDENCE_LIST_KEYS = {
    "optional_packages",
    "fee_and_term_rules",
    "agreement_rules",
    "application_materials",
    "eligibility_and_constraints",
    "supplemental_rules",
}

ALLOWED_CURRENCIES = {"", "CNY", None}
INACTIVE_STATUS_HINTS = ("停用", "停止申请", "停止使用", "已停", "废止")


class ProductDocumentValidator:
    """归一化后的确定性程序校验。

    Validator 不做业务语义判断，只检查稳定的程序规则：schema 结构、证据字段、
    标量格式，以及无需理解具体运营商产品也能确认的显式矛盾。
    """

    def validate(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        """执行最终程序校验。"""

        issues = validate_against_schema(product_document, PRODUCT_DOCUMENT_JSON_SCHEMA)
        issues.extend(self._validate_evidence(product_document))
        issues.extend(self._validate_scalar_formats(product_document))
        issues.extend(self._validate_deterministic_consistency(product_document))
        issues.extend(self._validate_required_module_completeness(product_document))
        issues.extend(self._validate_cross_module_duplicates(product_document))
        return issues

    def attach_issues(self, product_document: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
        """把程序校验问题追加到 extraction_meta.validation_issues。"""

        meta = product_document.setdefault("extraction_meta", {})
        existing = meta.get("validation_issues", [])
        if not isinstance(existing, list):
            existing = []
        meta["validation_issues"] = [*existing, *issues]
        meta["validation_issue_count"] = len(meta["validation_issues"])
        meta.setdefault("schema_warnings", [])
        return product_document

    def _validate_evidence(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        """校验关键列表对象是否保留证据、来源和置信度。"""

        issues: list[dict[str, Any]] = []
        for key in EVIDENCE_LIST_KEYS:
            for index, item in enumerate(product_document.get(key, [])):
                if not isinstance(item, dict):
                    continue
                issues.extend(validate_item_evidence(item, f"{key}[{index}]"))

        for index, item in enumerate(product_document.get("base_package", {}).get("packages", [])):
            if isinstance(item, dict):
                issues.extend(validate_item_evidence(item, f"base_package.packages[{index}]"))

        for index, item in enumerate(product_document.get("base_package", {}).get("included_items", [])):
            if isinstance(item, dict):
                issues.extend(validate_item_evidence(item, f"base_package.included_items[{index}]"))

        for index, item in enumerate(product_document.get("base_package", {}).get("service_attributes", [])):
            if isinstance(item, dict):
                issues.extend(validate_item_evidence(item, f"base_package.service_attributes[{index}]"))
        return issues

    def _validate_scalar_formats(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        """校验金额、币种、置信度、日期等标量格式。"""

        issues: list[dict[str, Any]] = []
        for path, item in iter_dicts(product_document):
            if "confidence" in item:
                confidence = item.get("confidence")
                if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
                    issues.append(
                        {
                            "severity": "warning",
                            "path": f"{path}.confidence",
                            "message": "confidence 必须是 0 到 1 之间的数字",
                            "actual": confidence,
                        }
                    )

            if "currency" in item and item.get("currency") not in ALLOWED_CURRENCIES:
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"{path}.currency",
                        "message": "currency 应归一化为 CNY 或留空",
                        "actual": item.get("currency"),
                    }
                )

            if "price" in item and item.get("price") is not None and not is_number(item.get("price")):
                issues.append(
                    {
                        "severity": "error",
                        "path": f"{path}.price",
                        "message": "price 有值时必须是数字",
                        "actual": item.get("price"),
                    }
                )

            if "amount" in item and item.get("amount") is not None and not is_number(item.get("amount")):
                issues.append(
                    {
                        "severity": "error",
                        "path": f"{path}.amount",
                        "message": "amount 有值时必须是数字",
                        "actual": item.get("amount"),
                    }
                )

            contract_period = str(item.get("contract_period", "")).strip()
            if contract_period and looks_like_billing_unit(contract_period):
                issues.append(
                    {
                        "severity": "error",
                        "path": f"{path}.contract_period",
                        "message": "contract_period 看起来是计费/计量单位，不是真实协议期限",
                        "actual": contract_period,
                    }
                )

        document_info = product_document.get("document_info", {})
        if isinstance(document_info, dict):
            for key in ("effective_from", "effective_to"):
                value = document_info.get(key)
                if value and not looks_like_date(value):
                    issues.append(
                        {
                            "severity": "warning",
                            "path": f"document_info.{key}",
                            "message": "日期值不是可识别格式",
                            "actual": value,
                        }
                    )
        return issues

    def _validate_deterministic_consistency(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        """校验文件名、路径、状态等确定性一致性。"""

        issues: list[dict[str, Any]] = []
        document_info = product_document.get("document_info", {})
        base_package = product_document.get("base_package", {})
        if not isinstance(document_info, dict):
            return issues

        filename = str(document_info.get("filename", ""))
        source_path = str(document_info.get("source_path", ""))
        document_status = str(document_info.get("document_status", ""))
        effective_from = str(document_info.get("effective_from", ""))

        if any(hint in f"{filename} {source_path}" for hint in INACTIVE_STATUS_HINTS) and document_status == "active":
            issues.append(
                {
                    "severity": "error",
                    "path": "document_info.document_status",
                    "message": "路径或文件名显示文档已停用，但 document_status 为 active",
                }
            )

        if is_future_date(effective_from) and document_status == "active":
            issues.append(
                {
                    "severity": "warning",
                    "path": "document_info.document_status",
                    "message": "effective_from 是未来日期，发布前需要复核 document_status",
                }
            )

        if isinstance(base_package, dict):
            expected_voice = voice_hint_from_text(f"{filename} {source_path}")
            if expected_voice is not None:
                for index, package in enumerate(base_package.get("packages", [])):
                    if not isinstance(package, dict):
                        continue
                    actual_voice = package.get("has_voice")
                    if actual_voice is not None and actual_voice is not expected_voice:
                        issues.append(
                            {
                                "severity": "error",
                                "path": f"base_package.packages[{index}].has_voice",
                                "message": "has_voice 与文件名或来源路径矛盾",
                                "expected": expected_voice,
                                "actual": actual_voice,
                            }
                        )
        return issues

    def _validate_required_module_completeness(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        """校验申请类文档的关键模块是否为空。

        这里不判断具体业务语义，只做结构完整性兜底：申请表/登记表如果完全没有申请字段，
        后续审核和推荐都无法使用，必须暴露为程序问题。
        """

        document_info = product_document.get("document_info", {})
        parties = product_document.get("parties_and_application", {})
        if not isinstance(document_info, dict) or not isinstance(parties, dict):
            return []

        document_text = " ".join(
            str(document_info.get(key, ""))
            for key in ("document_type", "title", "filename", "source_path")
        )
        if not any(marker in document_text for marker in ("申请登记表", "申请表", "登记表", "办理单", "受理单")):
            return []

        fields = parties.get("application_fields", {})
        required = fields.get("required", []) if isinstance(fields, dict) else []
        optional = fields.get("optional", []) if isinstance(fields, dict) else []
        if required or optional:
            return []

        return [
            {
                "severity": "error",
                "path": "parties_and_application.application_fields",
                "message": "申请表没有抽取到任何 application_fields",
            }
        ]

    def _validate_cross_module_duplicates(self, product_document: dict[str, Any]) -> list[dict[str, Any]]:
        """校验客户申请字段是否被重复放入基础套餐属性。"""

        application_names, application_evidence = collect_application_field_signatures(product_document)
        issues: list[dict[str, Any]] = []
        for index, item in enumerate(product_document.get("base_package", {}).get("service_attributes", [])):
            if not isinstance(item, dict):
                continue
            name = canonical_name(str(item.get("attribute_name") or item.get("label") or item.get("name") or ""))
            evidence = normalize_compact_text(str(item.get("source_evidence", "")))
            if evidence and evidence in application_evidence:
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"base_package.service_attributes[{index}]",
                        "message": "基础套餐属性重复了申请字段的 source_evidence",
                    }
                )
            elif name and name in application_names and is_customer_application_name(name):
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"base_package.service_attributes[{index}]",
                        "message": f"客户申请字段不应重复出现在基础套餐属性中：{name}",
                    }
                )
        return issues


def validate_item_evidence(item: dict[str, Any], path: str) -> list[dict[str, Any]]:
    """校验单条业务对象的证据信息。"""

    issues: list[dict[str, Any]] = []
    if not item.get("source_evidence"):
        issues.append({"severity": "warning", "path": f"{path}.source_evidence", "message": "缺少 source_evidence"})

    location = item.get("source_location")
    if item.get("source_evidence") and isinstance(location, dict) and not location.get("path"):
        issues.append({"severity": "warning", "path": f"{path}.source_location.path", "message": "缺少来源文件路径"})

    if "confidence" not in item:
        issues.append({"severity": "warning", "path": f"{path}.confidence", "message": "缺少 confidence"})
    return issues


def validate_against_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[dict[str, Any]]:
    """递归校验抽取 JSON 是否符合本地 schema 定义。"""

    issues: list[dict[str, Any]] = []
    expected_type = schema.get("type")

    if not _matches_type(value, expected_type):
        issues.append(
            {
                "severity": "error",
                "path": path,
                "message": "类型不匹配",
                "expected": expected_type,
                "actual": type(value).__name__,
            }
        )
        return issues

    enum_values = schema.get("enum")
    if enum_values is not None and value not in enum_values:
        issues.append(
            {
                "severity": "error",
                "path": path,
                "message": "值不在允许的枚举范围内",
                "expected": enum_values,
                "actual": value,
            }
        )

    effective_type = _effective_schema_type(expected_type, value)
    if effective_type == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                issues.append(
                    {
                        "severity": "error",
                        "path": f"{path}.{key}",
                        "message": "缺少 schema 必需字段",
                    }
                )
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    issues.append(
                        {
                        "severity": "error",
                        "path": f"{path}.{key}",
                        "message": "出现 schema 未定义的多余字段",
                        }
                    )
        for key, child_value in value.items():
            child_schema = properties.get(key)
            if child_schema is None:
                continue
            issues.extend(validate_against_schema(child_value, child_schema, f"{path}.{key}"))

    if effective_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                issues.extend(validate_against_schema(item, item_schema, f"{path}[{index}]"))

    return issues


def collect_application_field_signatures(product_document: dict[str, Any]) -> tuple[set[str], set[str]]:
    """收集申请字段的名称和证据签名，用于跨模块重复检测。"""

    fields = product_document.get("parties_and_application", {}).get("application_fields", {})
    names: set[str] = set()
    evidence_values: set[str] = set()
    if not isinstance(fields, dict):
        return names, evidence_values

    for key in ("required", "optional"):
        for item in fields.get(key, []):
            if not isinstance(item, dict):
                continue
            label = canonical_name(str(item.get("label", "")))
            field_key = canonical_name(str(item.get("field_key", "")))
            evidence = normalize_compact_text(str(item.get("source_evidence", "")))
            if label:
                names.add(label)
            if field_key:
                names.add(field_key)
            if evidence:
                evidence_values.add(evidence)
    return names, evidence_values


def iter_dicts(value: Any, path: str = "$"):
    """遍历嵌套 JSON 中的所有 dict 节点。"""

    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from iter_dicts(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_dicts(child, f"{path}[{index}]")


def _matches_type(value: Any, expected_type: Any) -> bool:
    """判断值是否匹配 schema type。"""

    if expected_type is None:
        return True
    if isinstance(expected_type, list):
        return any(_matches_single_type(value, item) for item in expected_type)
    return _matches_single_type(value, expected_type)


def _matches_single_type(value: Any, expected_type: str) -> bool:
    """判断值是否匹配单个 schema type。"""

    if expected_type == "null":
        return value is None
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    return True


def _effective_schema_type(expected_type: Any, value: Any) -> str | None:
    """从联合类型中选择当前值实际命中的 schema type。"""

    if isinstance(expected_type, str):
        return expected_type
    if not isinstance(expected_type, list):
        return None
    for item in expected_type:
        if _matches_single_type(value, item):
            return item
    return None


def is_number(value: Any) -> bool:
    """判断值是否为非布尔数字。"""

    return isinstance(value, (int, float)) and not isinstance(value, bool)


def looks_like_billing_unit(value: str) -> bool:
    """判断 contract_period 是否被误填成计费/计量单位。"""

    compact = normalize_compact_text(value)
    invalid_values = {"线", "次", "月", "年", "元", "元/月", "元/年", "月/线", "年/线", "元/月/线", "元/年/线"}
    if compact in invalid_values:
        return True
    return bool(re.fullmatch(r"[./\\-]*(线|次|月|年)", compact))


def looks_like_date(value: Any) -> bool:
    """判断日期字符串是否为可识别格式。"""

    text = str(value).strip()
    if not text:
        return True
    return bool(
        re.fullmatch(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", text)
        or re.fullmatch(r"\d{8}", text)
    )


def is_future_date(value: str) -> bool:
    """判断日期是否晚于当前日期。"""

    parsed = parse_date(value)
    return bool(parsed and parsed > date.today())


def parse_date(value: str) -> date | None:
    """解析常见中文/数字日期格式。"""

    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").replace(".", "-")
    if re.fullmatch(r"\d{8}", normalized):
        normalized = f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:]}"
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").date()
    except ValueError:
        return None


def voice_hint_from_text(value: str) -> bool | None:
    """从文件名或路径中提取带语音/不带语音提示。"""

    if "不带语音" in value or "无语音" in value:
        return False
    if "带语音" in value or "含语音" in value:
        return True
    return None


def is_customer_application_name(value: str) -> bool:
    """判断字段名是否属于客户申请表字段。"""

    names = {
        "企业规模",
        "计算机数量",
        "经办人",
        "联系电话",
        "身份证号码",
        "邮编",
        "付款方式",
        "账单地址",
        "安装地址",
        "企业全称",
        "统一社会信用代码",
    }
    return value in names


def canonical_name(value: str) -> str:
    """生成字段名比较用的规范化文本。"""

    return normalize_compact_text(value).replace("*", "").replace("□", "")


def normalize_compact_text(value: str) -> str:
    """去除空白，便于做文本比较。"""

    return re.sub(r"\s+", "", value or "")
