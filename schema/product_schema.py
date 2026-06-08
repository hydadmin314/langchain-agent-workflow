from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from typing import Any


SCHEMA_VERSION = "1.0"
SCHEMA_NAME = "product_document_extraction"
TOP_LEVEL_SCHEMA_KEYS = [
    "document_info",
    "parties_and_application",
    "base_package",
    "optional_packages",
    "fee_and_term_rules",
    "agreement_rules",
    "application_materials",
    "eligibility_and_constraints",
    "supplemental_rules",
    "extraction_meta",
]
TOP_LEVEL_LIST_KEYS = [
    "optional_packages",
    "fee_and_term_rules",
    "agreement_rules",
    "application_materials",
    "eligibility_and_constraints",
    "supplemental_rules",
]


def _location_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "来源位置，如页码、段落、表格、行列、sheet 名称等。",
        "properties": {
            "page": {"type": ["integer", "null"]},
            "paragraph_index": {"type": ["integer", "null"]},
            "table_index": {"type": ["integer", "null"]},
            "row_index": {"type": ["integer", "null"]},
            "column_index": {"type": ["integer", "null"]},
            "sheet_name": {"type": "string"},
            "path": {"type": "string"},
        },
        "additionalProperties": True,
    }


def _evidence_fields() -> dict[str, Any]:
    return {
        "source_evidence": {
            "type": "string",
            "description": "来源证据，从原文中截取的依据。关键字段必须尽量填写。",
        },
        "source_location": _location_schema(),
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "抽取置信度，0 到 1 之间。",
        },
    }


APPLICATION_FIELD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "申请表字段，包括必填字段和选填字段。",
    "properties": {
        "label": {"type": "string", "description": "原始字段名。"},
        "field_key": {"type": "string", "description": "标准化字段名，便于系统使用。"},
        "required": {"type": "boolean", "description": "是否必填。"},
        "value": {"type": ["string", "number", "boolean", "null"], "description": "已填写值，空白表为空。"},
        "options": {"type": "array", "items": {"type": "string"}, "description": "候选项，如企业规模、接口标准。"},
        "value_type": {
            "type": "string",
            "description": "字段类型，如 text、single_select、multi_select、date、number、phone。",
        },
        **_evidence_fields(),
    },
    "required": ["label", "field_key", "required", "value", "options", "value_type", "source_evidence", "source_location", "confidence"],
    "additionalProperties": False,
}


PACKAGE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "基础套餐中的一个可售卖套餐或资费档位。",
    "properties": {
        "package_name": {"type": "string", "description": "套餐名称。"},
        "package_code": {"type": "string", "description": "套餐编号或产品编码。"},
        "speed": {"type": "string", "description": "原始速率描述。"},
        "upstream_speed": {"type": "string", "description": "上行速率。"},
        "downstream_speed": {"type": "string", "description": "下行速率。"},
        "bandwidth_unit": {"type": "string", "description": "带宽单位。"},
        "has_voice": {"type": ["boolean", "null"], "description": "是否带语音，无法判断时为 null。"},
        "price": {"type": ["number", "null"], "description": "套餐价格。"},
        "currency": {"type": "string", "description": "币种，默认 CNY。"},
        "billing_period": {"type": "string", "description": "计费周期，如月、年、2年。"},
        "contract_period": {"type": "string", "description": "协议期。"},
        "quantity_limit": {"type": "string", "description": "数量限制。"},
        "applicable_conditions": {"type": "array", "items": {"type": "string"}, "description": "适用条件。"},
        **_evidence_fields(),
    },
    "required": [
        "package_name",
        "package_code",
        "speed",
        "upstream_speed",
        "downstream_speed",
        "bandwidth_unit",
        "has_voice",
        "price",
        "currency",
        "billing_period",
        "contract_period",
        "quantity_limit",
        "applicable_conditions",
        "source_evidence",
        "source_location",
        "confidence",
    ],
    "additionalProperties": False,
}


OPTIONAL_PACKAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "增值免费可选包、权益包、增值收费可选包。",
    "properties": {
        "package_type": {
            "type": "string",
            "description": "可选包类型，如 free_optional_package、paid_optional_package、benefit_package。",
        },
        "name": {"type": "string", "description": "可选包名称。"},
        "category": {"type": "string", "description": "原始分类。"},
        "description": {"type": "string", "description": "详细说明。"},
        "fee_summary": {"type": "string", "description": "费用摘要。"},
        "price_items": {"type": "array", "items": {"type": "object", "additionalProperties": True}, "description": "价格明细。"},
        "options": {"type": "array", "items": {"type": "string"}, "description": "可选项。"},
        "required_with": {"type": "array", "items": {"type": "string"}, "description": "必须同时订购的产品或条件。"},
        "incompatible_with": {"type": "array", "items": {"type": "string"}, "description": "不兼容项。"},
        "applicable_conditions": {"type": "array", "items": {"type": "string"}, "description": "适用条件。"},
        **_evidence_fields(),
    },
    "required": [
        "package_type",
        "name",
        "category",
        "description",
        "fee_summary",
        "price_items",
        "options",
        "required_with",
        "incompatible_with",
        "applicable_conditions",
        "source_evidence",
        "source_location",
        "confidence",
    ],
    "additionalProperties": False,
}


FEE_RULE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "费用与期限规则。",
    "properties": {
        "rule_type": {"type": "string", "description": "规则类型，如安装费、协议期、折扣、违约金。"},
        "name": {"type": "string", "description": "规则名称。"},
        "description": {"type": "string", "description": "规则说明。"},
        "amount": {"type": ["number", "null"], "description": "金额。"},
        "currency": {"type": "string", "description": "币种。"},
        "billing_period": {"type": "string", "description": "计费周期。"},
        "contract_period": {"type": "string", "description": "协议期。"},
        "conditions": {"type": "array", "items": {"type": "string"}, "description": "触发条件或适用条件。"},
        "applies_to": {"type": "array", "items": {"type": "string"}, "description": "适用对象。"},
        **_evidence_fields(),
    },
    "required": [
        "rule_type",
        "name",
        "description",
        "amount",
        "currency",
        "billing_period",
        "contract_period",
        "conditions",
        "applies_to",
        "source_evidence",
        "source_location",
        "confidence",
    ],
    "additionalProperties": False,
}


AGREEMENT_RULE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "协议条款、限制、违约、售后规则。",
    "properties": {
        "rule_type": {"type": "string", "description": "规则类型，如 restriction、termination、after_sales。"},
        "title": {"type": "string", "description": "规则标题。"},
        "description": {"type": "string", "description": "规则内容。"},
        "severity": {"type": "string", "description": "重要程度。"},
        "applies_to": {"type": "array", "items": {"type": "string"}, "description": "适用对象。"},
        "obligation_party": {"type": "string", "description": "责任主体，如客户、服务商、双方。"},
        "conditions": {"type": "array", "items": {"type": "string"}, "description": "触发条件。"},
        "consequence": {"type": "string", "description": "违反后的后果或处理方式。"},
        **_evidence_fields(),
    },
    "required": [
        "rule_type",
        "title",
        "description",
        "severity",
        "applies_to",
        "obligation_party",
        "conditions",
        "consequence",
        "source_evidence",
        "source_location",
        "confidence",
    ],
    "additionalProperties": False,
}


APPLICATION_MATERIAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "办理材料清单。",
    "properties": {
        "material_name": {"type": "string", "description": "材料名称，如营业执照复印件、授权委托书、经办人身份证、担保书、申请表、拓扑图。"},
        "material_type": {
            "type": "string",
            "description": "材料类型，如 license、id_card、authorization、form、guarantee、contract、topology、commitment、other。",
        },
        "required": {"type": "boolean", "description": "是否必需。"},
        "applies_to": {"type": "array", "items": {"type": "string"}, "description": "适用对象，如上海公司、外地公司、存量客户、新装、变更、拆机。"},
        "condition": {"type": "string", "description": "触发条件。"},
        "copies": {"type": "string", "description": "份数要求。"},
        "format": {"type": "string", "description": "材料形式，如 original、copy、scan、electronic、printed、photo。"},
        "seal_required": {"type": "boolean", "description": "是否需要盖章。"},
        "seal_type": {"type": "string", "description": "盖章类型，如公章、合同章、骑缝章、不可盖合同章。"},
        "signature_required": {"type": "boolean", "description": "是否需要签字。"},
        "signature_party": {"type": "string", "description": "签字主体，如经办人、法人、客户负责人、授权代表、服务商。"},
        "date_required": {"type": "boolean", "description": "是否需要填写日期。"},
        "template_required": {"type": "boolean", "description": "是否必须使用指定模板。"},
        "template_document": {"type": "string", "description": "对应模板文件。"},
        "notes": {"type": "string", "description": "其他说明。"},
        **_evidence_fields(),
    },
    "required": [
        "material_name",
        "material_type",
        "required",
        "applies_to",
        "condition",
        "copies",
        "format",
        "seal_required",
        "seal_type",
        "signature_required",
        "signature_party",
        "date_required",
        "template_required",
        "template_document",
        "notes",
        "source_evidence",
        "source_location",
        "confidence",
    ],
    "additionalProperties": False,
}


ELIGIBILITY_CONSTRAINT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "准入条件与限制规则。",
    "properties": {
        "constraint_type": {
            "type": "string",
            "description": "规则类型：eligibility、required_condition、exclusion、compliance、recommendation_blocker。",
        },
        "name": {"type": "string", "description": "规则名称。"},
        "description": {"type": "string", "description": "规则说明。"},
        "condition": {"type": "string", "description": "触发条件。"},
        "result": {"type": "string", "description": "触发结果。"},
        "blocks_recommendation": {"type": "boolean", "description": "是否阻止推荐。"},
        "applies_to": {"type": "array", "items": {"type": "string"}, "description": "适用对象。"},
        "rule_source": {"type": "string", "description": "规则来源类别：path、document、contract、manual、system。"},
        "normalized_logic": {"type": "object", "description": "结构化条件表达式。", "additionalProperties": True},
        "related_materials": {"type": "array", "items": {"type": "string"}, "description": "关联材料。"},
        "related_fee_rules": {"type": "array", "items": {"type": "string"}, "description": "关联费用规则。"},
        "valid_from": {"type": "string", "description": "规则生效日期。"},
        "valid_to": {"type": "string", "description": "规则失效日期。"},
        **_evidence_fields(),
    },
    "required": [
        "constraint_type",
        "name",
        "description",
        "condition",
        "result",
        "blocks_recommendation",
        "applies_to",
        "rule_source",
        "normalized_logic",
        "related_materials",
        "related_fee_rules",
        "valid_from",
        "valid_to",
        "source_evidence",
        "source_location",
        "confidence",
    ],
    "additionalProperties": False,
}


SUPPLEMENTAL_RULE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "来自其它文件的补充规则。",
    "properties": {
        "source_type": {"type": "string", "description": "来源类型，如附件、附录、承诺书、备案表、SLA附页。"},
        "name": {"type": "string", "description": "补充材料名称。"},
        "description": {"type": "string", "description": "补充说明。"},
        "fields": {"type": "array", "items": APPLICATION_FIELD_SCHEMA, "description": "表单字段。"},
        "required_fields": {"type": "array", "items": {"type": "string"}, "description": "必填字段。"},
        "columns": {"type": "array", "items": {"type": "string"}, "description": "表格列名。"},
        "rules": {"type": "array", "items": {"type": "object", "additionalProperties": True}, "description": "补充材料中抽取出的规则。"},
        **_evidence_fields(),
    },
    "required": [
        "source_type",
        "name",
        "description",
        "fields",
        "required_fields",
        "columns",
        "rules",
        "source_evidence",
        "source_location",
        "confidence",
    ],
    "additionalProperties": False,
}


PRODUCT_DOCUMENT_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": f"https://local/{SCHEMA_NAME}/v{SCHEMA_VERSION}.schema.json",
    "title": "业务产品套餐文档结构化抽取结果",
    "type": "object",
    "description": "用于大模型从业务产品文档中抽取可审核、可追溯的结构化中间态数据。",
    "properties": {
        "document_info": {
            "type": "object",
            "description": "文档基本信息：这份文件是谁的、什么产品、什么类型、哪个版本、什么时候生效、原始路径在哪里。",
            "properties": {
                "document_id": {"type": "string", "description": "文档唯一 ID。"},
                "document_type": {"type": "string", "description": "文档类型，如申请表、营销规则、服务协议、资费表、附件。"},
                "title": {"type": "string", "description": "文档原始标题。"},
                "product_name": {"type": "string", "description": "产品或套餐名称。"},
                "product_family": {"type": "string", "description": "产品族，用于归类，如精品专线、小微业务。"},
                "carrier": {"type": "string", "description": "运营商或服务归属，如电信、联通、移动、其他。"},
                "region": {"type": "string", "description": "适用区域。"},
                "version": {"type": "string", "description": "文档版本号。"},
                "effective_from": {"type": "string", "description": "生效日期。"},
                "effective_to": {"type": "string", "description": "失效日期或截止日期。"},
                "document_status": {"type": "string", "enum": ["active", "inactive", ""], "description": "文档状态。"},
                "filename": {"type": "string", "description": "原始文件名。"},
                "source_path": {"type": "string", "description": "原始文件路径。"},
                "source_file_type": {"type": "string", "description": "文件类型，如 docx、pdf、xlsx。"},
            },
            "required": [
                "document_id",
                "document_type",
                "title",
                "product_name",
                "product_family",
                "carrier",
                "region",
                "version",
                "effective_from",
                "effective_to",
                "document_status",
                "filename",
                "source_path",
                "source_file_type",
            ],
            "additionalProperties": False,
        },
        "parties_and_application": {
            "type": "object",
            "description": "服务商、客户与办理信息。",
            "properties": {
                "service_provider": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "服务商名称。"},
                        "carrier": {"type": "string", "description": "服务商所属运营商。"},
                        "region": {"type": "string", "description": "服务区域。"},
                        "contact_channels": {"type": "array", "items": {"type": "string"}, "description": "联系电话、热线、办理渠道等。"},
                    },
                    "required": ["name", "carrier", "region", "contact_channels"],
                    "additionalProperties": False,
                },
                "customer": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "客户名称，空白表为空。"},
                        "filled_values": {"type": "array", "items": {"type": "object", "additionalProperties": True}, "description": "已填写的客户信息。"},
                        "is_blank_form": {"type": "boolean", "description": "是否为空白申请表。"},
                    },
                    "required": ["name", "filled_values", "is_blank_form"],
                    "additionalProperties": False,
                },
                "application_fields": {
                    "type": "object",
                    "properties": {
                        "required": {"type": "array", "items": APPLICATION_FIELD_SCHEMA, "description": "必填字段。"},
                        "optional": {"type": "array", "items": APPLICATION_FIELD_SCHEMA, "description": "选填字段。"},
                    },
                    "required": ["required", "optional"],
                    "additionalProperties": False,
                },
                "application_notes": {"type": "array", "items": {"type": "string"}, "description": "填写说明、办理说明、注意事项。"},
            },
            "required": ["service_provider", "customer", "application_fields", "application_notes"],
            "additionalProperties": False,
        },
        "base_package": {
            "type": "object",
            "description": "基础套餐信息。",
            "properties": {
                "packages": {"type": "array", "items": PACKAGE_ITEM_SCHEMA},
                "included_items": {"type": "array", "items": {"type": "object", "additionalProperties": True}, "description": "套餐内包含的服务、权益、设备、资源。"},
                "service_attributes": {"type": "array", "items": {"type": "object", "additionalProperties": True}, "description": "基础业务属性，如接口标准、套餐类型、SLA等级。"},
                "sla": {"type": "object", "description": "服务等级、网络保障、赔付规则。", "additionalProperties": True},
            },
            "required": ["packages", "included_items", "service_attributes", "sla"],
            "additionalProperties": False,
        },
        "optional_packages": {"type": "array", "items": OPTIONAL_PACKAGE_SCHEMA},
        "fee_and_term_rules": {"type": "array", "items": FEE_RULE_SCHEMA},
        "agreement_rules": {"type": "array", "items": AGREEMENT_RULE_SCHEMA},
        "application_materials": {"type": "array", "items": APPLICATION_MATERIAL_SCHEMA},
        "eligibility_and_constraints": {"type": "array", "items": ELIGIBILITY_CONSTRAINT_SCHEMA},
        "supplemental_rules": {"type": "array", "items": SUPPLEMENTAL_RULE_SCHEMA},
        "extraction_meta": {
            "type": "object",
            "description": "抽取元信息。",
            "properties": {
                "generated_at": {"type": "string", "description": "生成时间。"},
                "source_file": {"type": "string", "description": "来源文件。"},
                "source_file_hash": {"type": "string", "description": "文件哈希，用于版本追踪。"},
                "method": {"type": "string", "description": "抽取方法。"},
                "llm_self_check": {"type": "object", "description": "大模型自检结果。", "additionalProperties": True},
                "validation_issues": {"type": "array", "items": {"type": "object", "additionalProperties": True}, "description": "校验问题列表。"},
                "validation_issue_count": {"type": "integer", "minimum": 0, "description": "校验问题数量。"},
                "schema_warnings": {"type": "array", "items": {"type": "string"}, "description": "schema 结构告警。"},
                "review_status": {
                    "type": "string",
                    "enum": ["draft", "reviewed", "published", "rejected", ""],
                    "description": "审核状态。",
                },
            },
            "required": [
                "generated_at",
                "source_file",
                "source_file_hash",
                "method",
                "llm_self_check",
                "validation_issues",
                "validation_issue_count",
                "schema_warnings",
                "review_status",
            ],
            "additionalProperties": False,
        },
    },
    "required": TOP_LEVEL_SCHEMA_KEYS,
    "additionalProperties": False,
}


EMPTY_PRODUCT_DOCUMENT: dict[str, Any] = {
    "document_info": {
        "document_id": "",
        "document_type": "",
        "title": "",
        "product_name": "",
        "product_family": "",
        "carrier": "",
        "region": "",
        "version": "",
        "effective_from": "",
        "effective_to": "",
        "document_status": "",
        "filename": "",
        "source_path": "",
        "source_file_type": "",
    },
    "parties_and_application": {
        "service_provider": {
            "name": "",
            "carrier": "",
            "region": "",
            "contact_channels": [],
        },
        "customer": {
            "name": "",
            "filled_values": [],
            "is_blank_form": True,
        },
        "application_fields": {
            "required": [],
            "optional": [],
        },
        "application_notes": [],
    },
    "base_package": {
        "packages": [],
        "included_items": [],
        "service_attributes": [],
        "sla": {},
    },
    "optional_packages": [],
    "fee_and_term_rules": [],
    "agreement_rules": [],
    "application_materials": [],
    "eligibility_and_constraints": [],
    "supplemental_rules": [],
    "extraction_meta": {
        "generated_at": "",
        "source_file": "",
        "source_file_hash": "",
        "method": "",
        "llm_self_check": {},
        "validation_issues": [],
        "validation_issue_count": 0,
        "schema_warnings": [],
        "review_status": "draft",
    },
}


def make_empty_product_document(*, generated_at: str | None = None) -> dict[str, Any]:
    """Return a fresh empty extraction document following this schema."""
    data = deepcopy(EMPTY_PRODUCT_DOCUMENT)
    data["extraction_meta"]["generated_at"] = generated_at or datetime.now().isoformat(timespec="seconds")
    return data


def get_product_document_json_schema() -> dict[str, Any]:
    """Return a deep copy of the JSON Schema for LLM structured output or validation."""
    return deepcopy(PRODUCT_DOCUMENT_JSON_SCHEMA)


def get_module_json_schema(module_name: str) -> dict[str, Any]:
    """Return the JSON Schema fragment expected from one LLM extraction module."""
    properties = PRODUCT_DOCUMENT_JSON_SCHEMA["properties"]
    if module_name not in properties:
        raise KeyError(f"Unknown product document module: {module_name}")
    return deepcopy(properties[module_name])


def get_module_output_template(module_name: str) -> Any:
    """Return an empty JSON template for the exact module-level LLM output."""
    if module_name not in PRODUCT_DOCUMENT_JSON_SCHEMA["properties"]:
        raise KeyError(f"Unknown product document module: {module_name}")
    schema = PRODUCT_DOCUMENT_JSON_SCHEMA["properties"][module_name]
    return _example_from_schema(schema)


def module_output_contract(module_name: str) -> str:
    """Prompt-ready contract for one module, including exact field names."""
    template = get_module_output_template(module_name)
    return "\n".join(
        [
            "本模块必须严格使用下面 JSON 模板中的字段名和层级。",
            "禁止新增模板以外的字段名；禁止把字段改成同义词，例如 name 不能替代 package_name，billing_cycle 不能替代 billing_period。",
            "对象中模板出现的字段都必须保留；没有抽到值也要按空值规则填充。",
            "confidence 字段必须是 0 到 1 之间的数字，不能填 null；低置信度也要填 0.3、0.5 等数字。",
            "数组字段如果没有内容填 []；如果有多条业务事实，数组中输出多个同结构对象。",
            "本模块输出模板：",
            json.dumps(template, ensure_ascii=False, separators=(",", ":")),
        ]
    )


def schema_prompt_contract() -> str:
    """Short contract text that can be inserted into an LLM extraction prompt."""
    return (
        "你必须只输出一个 JSON 对象，结构必须符合 product_document_extraction schema。"
        "顶层只能包含 document_info、parties_and_application、base_package、optional_packages、fee_and_term_rules、"
        "agreement_rules、application_materials、eligibility_and_constraints、supplemental_rules、extraction_meta。"
        "无法确定的字符串字段填空字符串，无法确定的普通数字填 null，无法确定的布尔值填 null；"
        "但 confidence 字段永远不能填 null，必须填 0 到 1 的数字。"
        "数组字段没有内容时填 []，对象字段没有内容时填 {}。"
        "所有数组字段都允许并且应当承载多条记录：例如有三四个权益包时，必须在 optional_packages 中输出三四个对象；"
        "有多条费用、协议、材料、准入限制或补充规则时，也必须逐条放入对应列表，不能合并成一条长文本。"
        "凡是从原文抽出的关键业务字段，都必须填写 source_evidence、source_location 和 confidence。"
        "不要把可选包权益误填为基础套餐字段；保留原文证据，避免猜测。"
    )


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
        properties = schema.get("properties", {})
        return {key: _example_from_schema(value) for key, value in properties.items()}
    if schema_type == "array":
        item_schema = schema.get("items", {})
        if isinstance(item_schema, dict) and item_schema.get("type") == "object":
            return [_example_from_schema(item_schema)]
        return []
    if schema_type == "string":
        return ""
    if schema_type == "number":
        return 0.0
    if schema_type == "integer":
        return 0
    if schema_type == "boolean":
        return False
    return None


def validate_product_document(data: dict[str, Any]) -> list[dict[str, str]]:
    """Lightweight structural validation without external dependencies.

    This is not a full JSON Schema validator. It catches missing top-level keys,
    common type mistakes, and extraction_meta count mismatches.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(data, dict):
        return [{"severity": "error", "path": "$", "message": "data must be a JSON object"}]

    expected_keys = set(TOP_LEVEL_SCHEMA_KEYS)
    actual_keys = set(data.keys())
    for key in TOP_LEVEL_SCHEMA_KEYS:
        if key not in data:
            issues.append({"severity": "error", "path": key, "message": "missing required top-level key"})
    for key in sorted(actual_keys - expected_keys):
        issues.append({"severity": "error", "path": key, "message": "unexpected top-level key"})

    for key in TOP_LEVEL_LIST_KEYS:
        if key in data and not isinstance(data[key], list):
            issues.append({"severity": "error", "path": key, "message": "must be a list"})

    object_keys = ["document_info", "parties_and_application", "base_package", "extraction_meta"]
    for key in object_keys:
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
