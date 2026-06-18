from __future__ import annotations

from schema import module_output_contract, schema_prompt_contract


ARRAY_EXTRACTION_RULES = """
重要数组规则：
1. 所有 array/list 字段都是“多记录容器”，不是摘要字段。
2. 原文出现几条业务事实，就输出几个对象。
3. 不要只抽第一条，不要把多条资费、规则、材料合并成一条长 description。
4. 资费中的基础套餐、增值项、一次性费用、期限规则、折扣政策必须分别进入 pricing_info 下对应数组。
5. 办理材料必须一份材料一个对象；签字、盖章、原件、复印件、办理注意事项要放入材料对象自己的字段。
"""


COMMON_EXTRACTION_RULES = f"""
{schema_prompt_contract()}

抽取原则：
1. 只依据输入 Markdown，不要编造。
2. 字符串无法确定填 ""；数字无法确定填 null；布尔无法确定时按 schema 填 false 或 null；数组没有内容填 []。
3. raw_text 必须来自原文，不能写推测内容。
4. 字段名必须完全使用 schema 定义的英文名，不能使用中文字段名、近义字段名或自创字段。
5. 当前模块输出必须是模块本身，不要额外包模块名，也不要输出完整顶层 JSON。
6. 申请表信息只进入 application_form_info；产品介绍、关键词、外部资费表、申请手续提示只进入 supplementary_info。
7. 文件名、目录名可以辅助判断来源，但具体业务字段必须从 Markdown 原文抽取。

{ARRAY_EXTRACTION_RULES}
"""


DOCUMENT_CONTEXT_TEMPLATE = """
文档内容如下，格式为转换后的 Markdown。
请只依据下面 Markdown 原文抽取。

{document_blocks}
"""


MODULE_PROMPTS: dict[str, str] = {
    "application_form_info.document_info": """
你只抽取申请表中的文档基本信息。
输出必须是 document_info 对象本身。
重点字段：
- document_id：文档唯一 ID；原文没有则留空。
- source_file：原始文件路径或文件名。
- product_name：产品或套餐名称。
- carrier：运营商或服务归属，如“中国电信股份有限公司上海分公司”。
- version：文档版本号，如“2025/B”。
- effective_date：生效日期，如有明确日期则填写。
- document_status：文档状态，active、inactive 或 null。
""",
    "application_form_info.parties_and_application": """
你只抽取申请表中的服务商、客户、代理商、办理信息。
输出必须是 parties_and_application 对象本身。
重点字段：
- agent：代理商名称、代理商业务人员姓名、联系方式。
- customer：客户名称、已填写客户信息、是否为空白申请表。
- application_fields.required：必填字段。表格中带 *、必须填写、客户必须提供的字段放这里。
- application_fields.optional：选填字段。需要客户填写或勾选但非必填的字段放这里。
- application_notes：填写说明、办理说明、注意事项。
字段对象必须保留 label、field_key、required、value、options、value_type、raw_text。
""",
    "application_form_info.pricing_info": """
你只抽取申请表中的产品套餐费用信息。
输出必须是 pricing_info 对象本身。
source_type 固定为 application_form。
重点字段：
- one_time_fees：一次性费用，如初装费、一次性接入费、安装费、接入费、施工费、升降速费。
- base_package_prices：基础套餐资费，如套餐名称、速率、上下行、是否带语音、价格、计费周期、协议期、包含内容。
- addon_prices：增值包、权益包、升级项资费，如天翼安全大脑、移动业务、升级 IPv4。
- fee_and_term_rules：费用与期限规则，如协议期、首月折算、续约、违约金、欠费、退款。
- discount_policy：折扣政策；申请表没有则输出空数组。
不要再输出旧 schema 的 base_package、optional_packages、fee_and_term_rules 顶层字段。
""",
    "application_form_info.agreement_rules": """
你只抽取申请表中的协议条款、限制、违约、售后规则。
输出必须是 agreement_rules 数组本身。
重点字段：
- rule_type：规则类型，如 restriction、termination、after_sales。
- description：规则内容。
- severity：重要程度。
- applies_to：适用对象。
- obligation_party：责任主体，如客户、服务商、双方。
- conditions：触发条件。
- consequence：违反后的后果或处理方式。
- raw_text：原文条款。
""",
    "application_form_info.eligibility_and_constraints": """
你只抽取申请表中的准入条件与限制规则。
输出必须是 eligibility_and_constraints 数组本身。
重点字段：
- name：规则名称，便于业务人员识别。
- description：规则说明，用自然语言完整描述。
- condition：触发条件，如客户为外地公司、IP 数量 >=16、欠费 2 个月以上。
- result：触发结果，如需补材料、需签承诺书、需支付押金。
- applies_to：适用对象，如产品、套餐、客户类型、办理动作、区域、业务场景。
- severity：重要程度：low、medium、high、critical。
- raw_text：原文。
""",
    "supplementary_info.product_intro": """
你只抽取产品介绍信息。
输出必须是 product_intro 对象本身。
重点字段：
- product_name：产品名称。
- full_description：产品介绍正文。
- application_scenarios：应用场景，例如总部办公、视频会议、ERP 访问、企业专网、票务系统。
只抽产品介绍、产品说明、业务介绍里的内容，不抽资费表和申请手续。
""",
    "supplementary_info.product_keywords": """
你只抽取产品关键词。
输出必须是 product_keywords 对象本身。
重点字段：
- raw_keywords：从关键词文件抽取到的关键词数组或原文关键词。
如果原文是多行关键词，可以用逗号或换行保留在 raw_keywords 中，不要扩写解释。
""",
    "supplementary_info.pricing_info": """
你只抽取补充资费表中的资费信息。
输出必须是 pricing_info 对象本身。
source_type 固定为 pricing_sheet。
重点字段：
- one_time_fees：一次性费用。
- base_package_prices：基础套餐资费。
- addon_prices：增值包、权益包、升级项资费。
- fee_and_term_rules：费用与期限规则。
- discount_policy：折扣政策。
资费表通常比申请表更完整；看到多档速率、多种计费周期、多种折扣时必须拆成多条记录。
""",
    "supplementary_info.application_materials": """
你只抽取申请资料手续信息。
输出必须是 application_materials 数组本身。
重点字段：
- material_type：材料类型，如表单、证件、合同、授权书、担保书、承诺书。
- name：材料名称。
- description：材料说明。
- required：是否必需。
- applicable_to：适用对象，如上海公司、外地公司、存量用户、商机冲突。
- conditions：触发条件。
- signature_required：是否需要签字。
- seal_required：是否需要盖章。
- copy_required：是否复印件。
- original_required：是否原件。
- pages_or_locations：签字/盖章页码或位置。
- handling_notes：办理注意事项，如机打、不可手写、不可盖合同章。
- related_constraints：可关联到准入限制，如外地公司需担保。
- raw_text：原文。
- source_file：来源文件。
""",
}


SELF_CHECK_PROMPT = """
你只做语义质量自检，并判断是否需要触发模块返工；本步骤不重新抽取、不补字段。
检查目标：
1. 是否存在数组字段只输出一条，但原文明显包含多条业务事实。
2. 是否存在资费、规则、材料放错模块。
3. 是否缺少 raw_text 或明显编造原文没有的信息。
4. 是否存在字段类型不符合 schema。

只输出一个对象：
{
  "llm_self_check": {
    "needs_rework": false,
    "rework_modules": []
  },
  "validation_issues": [],
  "schema_warnings": []
}
"""


COMPACT_EXTRACTION_RULES = """
你是业务产品文档结构化抽取器。现在是紧凑重试，只抽当前模块。
要求：
1. 只输出合法 JSON，不解释。
2. 字段名必须严格使用 schema。
3. 只依据输入 Markdown，不编造。
4. 多条业务事实必须拆成多条数组对象。
"""


def build_module_prompt(module_name: str, document_blocks: str) -> str:
    if module_name not in MODULE_PROMPTS:
        raise KeyError(f"Unknown extraction module: {module_name}")
    return "\n\n".join(
        [
            COMMON_EXTRACTION_RULES,
            module_output_contract(module_name),
            MODULE_PROMPTS[module_name],
            DOCUMENT_CONTEXT_TEMPLATE.format(document_blocks=document_blocks),
        ]
    )


def build_compact_module_prompt(module_name: str, document_blocks: str) -> str:
    if module_name not in MODULE_PROMPTS:
        raise KeyError(f"Unknown extraction module: {module_name}")
    return "\n\n".join(
        [
            COMPACT_EXTRACTION_RULES,
            module_output_contract(module_name),
            MODULE_PROMPTS[module_name],
            DOCUMENT_CONTEXT_TEMPLATE.format(document_blocks=document_blocks),
        ]
    )


def build_self_check_prompt(product_json: str) -> str:
    return "\n\n".join([COMMON_EXTRACTION_RULES, SELF_CHECK_PROMPT, f"待检查 JSON：\n{product_json}"])


def build_module_rework_prompt(
    module_name: str,
    document_blocks: str,
    previous_module_json: str,
    rework_reason: str,
) -> str:
    if module_name not in MODULE_PROMPTS:
        raise KeyError(f"Unknown extraction module: {module_name}")
    return "\n\n".join(
        [
            COMMON_EXTRACTION_RULES,
            module_output_contract(module_name),
            MODULE_PROMPTS[module_name],
            "下面是自检发现的问题。请回到原始 Markdown 重新抽取当前模块，不要只修补旧 JSON。",
            f"模块名：{module_name}",
            f"返工原因：\n{rework_reason}",
            f"上一轮模块 JSON：\n{previous_module_json}",
            DOCUMENT_CONTEXT_TEMPLATE.format(document_blocks=document_blocks),
        ]
    )


def build_module_repair_prompt(module_name: str, module_json: str, validation_issues: str) -> str:
    return "\n\n".join(
        [
            COMMON_EXTRACTION_RULES,
            module_output_contract(module_name),
            "下面是模型已经抽取出的模块 JSON，但它不符合 schema。请只做结构修复，不要新增原 JSON 中没有依据的业务事实。",
            "修复要求：只输出修复后的 JSON，不解释；删除 schema 之外字段；补齐 schema 字段；字段类型必须符合 schema。",
            f"模块名：{module_name}",
            f"校验问题：\n{validation_issues}",
            f"待修复 JSON：\n{module_json}",
        ]
    )
