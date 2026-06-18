from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from typing import Any


SCHEMA_VERSION = "2.0"
SCHEMA_NAME = "product_document_extraction"

# 顶层结构按产品资料包组织：申请表信息 + 补充资料信息 + 抽取元数据。
TOP_LEVEL_SCHEMA_KEYS = [
    "application_form_info",
    "supplementary_info",
    "extraction_meta",
]

# 新 schema 顶层没有 list 字段；保留常量是为了兼容旧 merger 的导入。
TOP_LEVEL_LIST_KEYS: list[str] = []

APPLICATION_FORM_MODULES = [
    "application_form_info.document_info",
    "application_form_info.parties_and_application",
    "application_form_info.pricing_info",
    "application_form_info.agreement_rules",
    "application_form_info.eligibility_and_constraints",
]

SUPPLEMENTARY_MODULES = [
    "supplementary_info.product_intro",
    "supplementary_info.product_keywords",
    "supplementary_info.pricing_info",
    "supplementary_info.application_materials",
]

EXTRACTION_MODULES = [*APPLICATION_FORM_MODULES, *SUPPLEMENTARY_MODULES]


def string_schema(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def nullable_number_schema(description: str) -> dict[str, Any]:
    return {"type": ["number", "null"], "description": description}


def nullable_boolean_schema(description: str) -> dict[str, Any]:
    return {"type": ["boolean", "null"], "description": description}


def string_array_schema(description: str) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "description": description}


def object_schema(properties: dict[str, Any], description: str, *, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "properties": properties,
        "required": required or list(properties),
        "additionalProperties": False,
    }


def array_schema(item_schema: dict[str, Any], description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "description": description,
        "items": item_schema,
    }


APPLICATION_FIELD_SCHEMA = object_schema(
    {
        "label": string_schema("原始字段名。"),
        "field_key": string_schema("标准化字段名，便于系统使用。"),
        "required": {"type": "boolean", "description": "是否必填。"},
        "value": {"type": ["string", "number", "boolean", "null"], "description": "已填写值；空白表为空字符串或 null。"},
        "options": string_array_schema("候选项，如企业规模、接口标准。"),
        "value_type": string_schema("字段类型，如 text、single_select、multi_select。"),
        "raw_text": string_schema("原文。"),
    },
    "申请表字段。",
)

PRICING_INFO_SCHEMA = object_schema(
    {
        "source_type": string_schema("来源类型：application_form 或 pricing_sheet。"),
        "source_file": string_schema("来源文件。"),
        "one_time_fees": array_schema(
            object_schema(
                {
                    "name": string_schema("费用名称，如初装费、一次性接入费、安装费、接入费、施工费、升降速费。"),
                    "amount": nullable_number_schema("金额。"),
                    "currency": string_schema("币种，默认 CNY。"),
                    "unit": string_schema("单位，如元/次、元/线/次。"),
                    "applicable_to": string_array_schema("适用对象，如月付用户、年付用户、国内 MSTP。"),
                    "conditions": string_array_schema("适用条件，如有资源、无资源、新装、升降速。"),
                    "description": string_schema("说明。"),
                    "raw_text": string_schema("原文。"),
                },
                "一次性费用。",
            ),
            "一次性费用。",
        ),
        "base_package_prices": array_schema(
            object_schema(
                {
                    "name": string_schema("套餐名称。"),
                    "speed": string_schema("原始速率描述，如 100M/100M、1G。"),
                    "upstream_speed": string_schema("上行速率。"),
                    "downstream_speed": string_schema("下行速率。"),
                    "bandwidth_unit": string_schema("带宽单位：M、G。"),
                    "has_voice": nullable_boolean_schema("是否带语音。"),
                    "price": nullable_number_schema("价格。"),
                    "currency": string_schema("币种，默认 CNY。"),
                    "unit": string_schema("价格单位，如元/月、元/年、元/2年。"),
                    "billing_period": string_schema("计费周期，如月付、年付、2年付。"),
                    "contract_period": string_schema("协议期。"),
                    "included_items": string_array_schema("包含内容，如 5 个 IP、定制网关、SLA 服务。"),
                    "conditions": string_array_schema("适用条件。"),
                    "description": string_schema("说明。"),
                    "raw_text": string_schema("原文。"),
                },
                "基础套餐资费。",
            ),
            "基础套餐资费。",
        ),
        "addon_prices": array_schema(
            object_schema(
                {
                    "name": string_schema("增值项名称，如天翼安全大脑、移动业务、升级 13 个 IPv4。"),
                    "category": string_schema("分类，如免费可选包、收费可选包、权益包、升级项。"),
                    "spec": string_schema("规格，如专线版 300M、13 个 IPv4、5 个国际精品 IP。"),
                    "price": nullable_number_schema("价格。"),
                    "currency": string_schema("币种，默认 CNY。"),
                    "unit": string_schema("价格单位，如元/月/线、元/月/号。"),
                    "billing_period": string_schema("计费周期。"),
                    "contract_period": string_schema("协议期。"),
                    "included_items": string_array_schema("包含内容。"),
                    "required_with": string_array_schema("依赖项，如必须同时选择某基础套餐。"),
                    "conditions": string_array_schema("适用条件或限制。"),
                    "description": string_schema("说明。"),
                    "raw_text": string_schema("原文。"),
                },
                "增值包、权益包、升级项资费。",
            ),
            "增值包、权益包、升级项资费。",
        ),
        "fee_and_term_rules": array_schema(
            object_schema(
                {
                    "name": string_schema("规则名称。"),
                    "category": string_schema("规则分类，如协议期、首月折算、续约、违约金、欠费、退款。"),
                    "applicable_to": string_array_schema("适用对象。"),
                    "amount": nullable_number_schema("涉及固定金额，没有则为空。"),
                    "currency": string_schema("币种，默认 CNY。"),
                    "unit": string_schema("金额单位。"),
                    "period": string_schema("涉及期限，如 12 个月、24 个月、30 日、60 日。"),
                    "formula": string_schema("计算公式，如违约金公式。"),
                    "conditions": string_array_schema("触发条件。"),
                    "description": string_schema("规则说明。"),
                    "raw_text": string_schema("原文。"),
                },
                "费用与期限规则。",
            ),
            "费用与期限规则。",
        ),
        "discount_policy": array_schema(
            object_schema(
                {
                    "name": string_schema("折扣名称，如授权 5 折、月付 8 折、年付包。"),
                    "category": string_schema("折扣分类，如比例折扣、包年价、减免。"),
                    "applicable_to": string_array_schema("适用对象。"),
                    "standard_price": nullable_number_schema("标准价。"),
                    "discount_rate": nullable_number_schema("折扣率。"),
                    "discounted_price": nullable_number_schema("折后价。"),
                    "currency": string_schema("币种，默认 CNY。"),
                    "unit": string_schema("单位。"),
                    "conditions": string_array_schema("折扣条件。"),
                    "description": string_schema("说明。"),
                    "raw_text": string_schema("原文。"),
                },
                "折扣政策。",
            ),
            "折扣政策；申请表没有可为空数组。",
        ),
    },
    "统一资费目录，申请表和资费表共用。",
)

AGREEMENT_RULE_SCHEMA = object_schema(
    {
        "rule_type": string_schema("规则类型，如 restriction、termination、after_sales。"),
        "description": string_schema("规则内容。"),
        "severity": string_schema("重要程度。"),
        "applies_to": string_array_schema("适用对象。"),
        "obligation_party": string_schema("责任主体，如客户、服务商、双方。"),
        "conditions": string_array_schema("触发条件。"),
        "consequence": string_schema("违反后的后果或处理方式。"),
        "raw_text": string_schema("原文条款。"),
    },
    "协议条款、限制、违约、售后规则。",
)

ELIGIBILITY_CONSTRAINT_SCHEMA = object_schema(
    {
        "name": string_schema("规则名称，便于业务人员识别。"),
        "description": string_schema("规则说明，用自然语言完整描述。"),
        "condition": string_schema("触发条件，如客户为外地公司、IP 数量 >=16、欠费 2 个月以上。"),
        "result": string_schema("触发结果，如需补材料、需签承诺书、需支付押金。"),
        "applies_to": string_array_schema("适用对象，如产品、套餐、客户类型、办理动作、区域、业务场景。"),
        "severity": string_schema("重要程度：low、medium、high、critical。"),
        "raw_text": string_schema("原文。"),
    },
    "准入条件与限制规则。",
)

APPLICATION_MATERIAL_SCHEMA = object_schema(
    {
        "material_type": string_schema("材料类型，如表单、证件、合同、授权书、担保书、承诺书。"),
        "name": string_schema("材料名称。"),
        "description": string_schema("材料说明。"),
        "required": {"type": "boolean", "description": "是否必需。"},
        "applicable_to": string_array_schema("适用对象，如上海公司、外地公司、存量用户、商机冲突。"),
        "conditions": string_array_schema("触发条件。"),
        "signature_required": {"type": "boolean", "description": "是否需要签字。"},
        "seal_required": {"type": "boolean", "description": "是否需要盖章。"},
        "copy_required": {"type": "boolean", "description": "是否复印件。"},
        "original_required": {"type": "boolean", "description": "是否原件。"},
        "pages_or_locations": string_array_schema("签字/盖章页码或位置。"),
        "handling_notes": string_array_schema("办理注意事项，如机打、不可手写、不可盖合同章。"),
        "related_constraints": string_array_schema("可关联到准入限制，如外地公司需担保。"),
        "raw_text": string_schema("原文。"),
        "source_file": string_schema("来源文件。"),
    },
    "申请资料手续信息。",
)

DOCUMENT_INFO_SCHEMA = object_schema(
    {
        "document_id": string_schema("文档唯一 ID。"),
        "source_file": string_schema("原始文件路径或文件名。"),
        "product_name": string_schema("产品或套餐名称。"),
        "carrier": string_schema("运营商或服务归属，如中国电信股份有限公司上海分公司。"),
        "version": string_schema("文档版本号，如 2025/B。"),
        "effective_date": {"type": ["string", "null"], "description": "生效日期，如有明确日期则填写。"},
        "document_status": {"type": ["string", "null"], "description": "文档状态：active、inactive、null。"},
    },
    "文档基本信息。",
)

PARTIES_AND_APPLICATION_SCHEMA = object_schema(
    {
        "agent": object_schema(
            {
                "agent_name": string_schema("代理商名称。"),
                "sales_name": string_schema("代理商业务人员姓名。"),
                "sales_contact": string_schema("代理商业务人员联系方式。"),
            },
            "代理商信息。",
        ),
        "customer": object_schema(
            {
                "name": string_schema("客户名称，空白表为空。"),
                "filled_values": string_array_schema("已填写的客户信息。"),
                "is_blank_form": {"type": "boolean", "description": "是否为空白申请表。"},
            },
            "客户信息。",
        ),
        "application_fields": object_schema(
            {
                "required": array_schema(APPLICATION_FIELD_SCHEMA, "必填字段。"),
                "optional": array_schema(APPLICATION_FIELD_SCHEMA, "选填字段。"),
            },
            "申请字段。",
        ),
        "application_notes": string_array_schema("填写说明、办理说明、注意事项。"),
    },
    "代理商、客户与办理信息。",
)

PRODUCT_INTRO_SCHEMA = object_schema(
    {
        "product_name": string_schema("产品名称。"),
        "full_description": string_schema("产品介绍正文。"),
        "application_scenarios": string_schema("应用场景，例如总部办公、视频会议、ERP 访问、企业专网、票务系统。"),
    },
    "产品介绍信息。",
)

PRODUCT_KEYWORDS_SCHEMA = object_schema(
    {
        "raw_keywords": string_schema("从关键词文件抽取到的关键词数组或原文关键词。"),
    },
    "产品关键词。",
)

PRODUCT_DOCUMENT_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": SCHEMA_NAME,
    "type": "object",
    "properties": {
        "application_form_info": object_schema(
            {
                "document_info": DOCUMENT_INFO_SCHEMA,
                "parties_and_application": PARTIES_AND_APPLICATION_SCHEMA,
                "pricing_info": PRICING_INFO_SCHEMA,
                "agreement_rules": array_schema(AGREEMENT_RULE_SCHEMA, "协议条款、限制、违约、售后规则。"),
                "eligibility_and_constraints": array_schema(ELIGIBILITY_CONSTRAINT_SCHEMA, "准入条件与限制规则。"),
            },
            "申请表基本信息，来源申请表文档。",
        ),
        "supplementary_info": object_schema(
            {
                "product_intro": PRODUCT_INTRO_SCHEMA,
                "product_keywords": PRODUCT_KEYWORDS_SCHEMA,
                "pricing_info": PRICING_INFO_SCHEMA,
                "application_materials": array_schema(APPLICATION_MATERIAL_SCHEMA, "申请资料手续信息。"),
            },
            "额外补充信息，来源产品介绍、关键词、资费表、申请手续提示。",
        ),
        "extraction_meta": object_schema(
            {
                "generated_at": string_schema("生成时间。"),
                "source_file": string_schema("来源文件。"),
                "method": string_schema("抽取方法。"),
                "validation_issues": {"type": "array", "items": {"type": "object", "additionalProperties": True}, "description": "校验问题。"},
                "validation_issue_count": {"type": "integer", "minimum": 0, "description": "校验问题数量。"},
            },
            "抽取元数据。",
        ),
    },
    "required": TOP_LEVEL_SCHEMA_KEYS,
    "additionalProperties": False,
}

MODULE_SCHEMAS: dict[str, dict[str, Any]] = {
    "application_form_info.document_info": DOCUMENT_INFO_SCHEMA,
    "application_form_info.parties_and_application": PARTIES_AND_APPLICATION_SCHEMA,
    "application_form_info.pricing_info": PRICING_INFO_SCHEMA,
    "application_form_info.agreement_rules": PRODUCT_DOCUMENT_JSON_SCHEMA["properties"]["application_form_info"]["properties"]["agreement_rules"],
    "application_form_info.eligibility_and_constraints": PRODUCT_DOCUMENT_JSON_SCHEMA["properties"]["application_form_info"]["properties"]["eligibility_and_constraints"],
    "supplementary_info.product_intro": PRODUCT_INTRO_SCHEMA,
    "supplementary_info.product_keywords": PRODUCT_KEYWORDS_SCHEMA,
    "supplementary_info.pricing_info": PRICING_INFO_SCHEMA,
    "supplementary_info.application_materials": PRODUCT_DOCUMENT_JSON_SCHEMA["properties"]["supplementary_info"]["properties"]["application_materials"],
}

EMPTY_PRODUCT_DOCUMENT: dict[str, Any] = {
    "application_form_info": {
        "document_info": {
            "document_id": "",
            "source_file": "",
            "product_name": "",
            "carrier": "",
            "version": "",
            "effective_date": None,
            "document_status": None,
        },
        "parties_and_application": {
            "agent": {"agent_name": "", "sales_name": "", "sales_contact": ""},
            "customer": {"name": "", "filled_values": [], "is_blank_form": True},
            "application_fields": {"required": [], "optional": []},
            "application_notes": [],
        },
        "pricing_info": {
            "source_type": "application_form",
            "source_file": "",
            "one_time_fees": [],
            "base_package_prices": [],
            "addon_prices": [],
            "fee_and_term_rules": [],
            "discount_policy": [],
        },
        "agreement_rules": [],
        "eligibility_and_constraints": [],
    },
    "supplementary_info": {
        "product_intro": {"product_name": "", "full_description": "", "application_scenarios": ""},
        "product_keywords": {"raw_keywords": ""},
        "pricing_info": {
            "source_type": "pricing_sheet",
            "source_file": "",
            "one_time_fees": [],
            "base_package_prices": [],
            "addon_prices": [],
            "fee_and_term_rules": [],
            "discount_policy": [],
        },
        "application_materials": [],
    },
    "extraction_meta": {
        "generated_at": "",
        "source_file": "",
        "method": "",
        "validation_issues": [],
        "validation_issue_count": 0,
    },
}


def make_empty_product_document(*, generated_at: str | None = None) -> dict[str, Any]:
    """生成一份符合新 schema 的空产品资料包。"""

    data = deepcopy(EMPTY_PRODUCT_DOCUMENT)
    data["extraction_meta"]["generated_at"] = generated_at or datetime.now().isoformat(timespec="seconds")
    return data


def get_product_document_json_schema() -> dict[str, Any]:
    """返回完整产品资料包 JSON Schema。"""

    return deepcopy(PRODUCT_DOCUMENT_JSON_SCHEMA)


def get_module_json_schema(module_name: str) -> dict[str, Any]:
    """按点路径返回单个抽取模块的 JSON Schema。"""

    if module_name not in MODULE_SCHEMAS:
        raise KeyError(f"Unknown product document module: {module_name}")
    return deepcopy(MODULE_SCHEMAS[module_name])


def get_module_output_template(module_name: str) -> Any:
    """返回单个模块期望的空输出模板。"""

    return _example_from_schema(get_module_json_schema(module_name))


def module_output_contract(module_name: str) -> str:
    """生成可放入提示词的模块输出契约。"""

    template = get_module_output_template(module_name)
    return "\n".join(
        [
            "本模块必须严格使用下面 JSON 模板中的字段名和层级。",
            "禁止新增模板以外的字段名；没有抽到值也要按空值规则填充。",
            "字符串字段无法确定时填空字符串，数字字段无法确定时填 null，布尔字段无法确定时填 false 或 null，数组字段没有内容时填 []。",
            "输出必须是当前模块本身，不要额外包一层模块名。",
            "本模块输出模板：",
            json.dumps(template, ensure_ascii=False, separators=(",", ":")),
        ]
    )


def schema_prompt_contract() -> str:
    """生成全局 schema 约束说明，供提示词复用。"""

    return (
        "你必须只输出合法 JSON，结构必须符合 product_document_extraction schema。"
        "当前 schema 顶层只有 application_form_info、supplementary_info、extraction_meta。"
        "申请表文件只抽 application_form_info 下的模块；产品介绍、关键词、资费表、申请手续提示只抽 supplementary_info 下的对应模块。"
        "所有数组字段都是多记录容器，原文出现多条业务事实时必须拆成多条对象，不要合并成长文本。"
        "raw_text 必须保留可追溯原文，不能编造原文没有的信息。"
    )


def validate_product_document(data: dict[str, Any]) -> list[dict[str, str]]:
    """轻量结构校验；完整业务校验后续在 validator 中逐步迁移。"""

    issues: list[dict[str, str]] = []
    if not isinstance(data, dict):
        return [{"severity": "error", "path": "$", "message": "data must be a JSON object"}]

    expected_keys = set(TOP_LEVEL_SCHEMA_KEYS)
    actual_keys = set(data)
    for key in TOP_LEVEL_SCHEMA_KEYS:
        if key not in data:
            issues.append({"severity": "error", "path": key, "message": "missing required top-level key"})
    for key in sorted(actual_keys - expected_keys):
        issues.append({"severity": "error", "path": key, "message": "unexpected top-level key"})

    for key in ("application_form_info", "supplementary_info", "extraction_meta"):
        if key in data and not isinstance(data[key], dict):
            issues.append({"severity": "error", "path": key, "message": "must be an object"})

    meta = data.get("extraction_meta", {})
    if isinstance(meta, dict):
        validation_issues = meta.get("validation_issues", [])
        issue_count = meta.get("validation_issue_count", 0)
        if isinstance(validation_issues, list) and issue_count != len(validation_issues):
            issues.append(
                {
                    "severity": "warning",
                    "path": "extraction_meta.validation_issue_count",
                    "message": "validation_issue_count does not match validation_issues length",
                }
            )

    return issues


def _example_from_schema(schema: dict[str, Any]) -> Any:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        non_null_types = [item for item in schema_type if item != "null"]
        if not non_null_types:
            return None
        if "boolean" in non_null_types:
            return None
        if "number" in non_null_types or "integer" in non_null_types:
            return None
        schema_type = non_null_types[0]

    if schema_type == "object":
        return {key: _example_from_schema(value) for key, value in schema.get("properties", {}).items()}
    if schema_type == "array":
        item_schema = schema.get("items", {})
        if isinstance(item_schema, dict) and item_schema.get("type") == "object":
            return [_example_from_schema(item_schema)]
        return []
    if schema_type == "string":
        return ""
    if schema_type == "number":
        return None
    if schema_type == "integer":
        return 0
    if schema_type == "boolean":
        return False
    return None
