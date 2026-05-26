from __future__ import annotations

from schema.schema import schema_prompt_contract


ARRAY_EXTRACTION_RULES = """
重要数组规则：
1. schema 中所有 array/list 字段都是“多记录容器”，不是摘要字段。
2. 原文出现几条业务事实，就输出几个对象。
3. 不要只抽第一条，不要合并成一条长 description。
4. 多个套餐档位写入 base_package.packages 多个对象。
5. 多个套餐内资源写入 base_package.included_items 多个对象。
6. 多个接口标准、套餐类型、SLA 等基础属性写入 base_package.service_attributes 多个对象。
7. 多个权益包/可选包/增值包写入 optional_packages 多个对象。
8. 多条费用、期限、折扣、押金、安装费、违约金规则写入 fee_and_term_rules 多个对象。
9. 多条合同限制、违约、退订、售后、SLA、赔付规则写入 agreement_rules 多个对象。
10. 多份办理材料写入 application_materials 多个对象。
11. 多条准入条件、限制规则、推荐阻断规则写入 eligibility_and_constraints 多个对象。
12. 多个附件、承诺书、备案表、SLA 附页中的规则写入 supplemental_rules 多个对象。
13. 每个对象都必须尽量带 source_evidence、source_location、confidence。
"""


COMMON_EXTRACTION_RULES = f"""
{schema_prompt_contract()}

抽取原则：
1. 只依据输入文档块，不要编造。
2. 无法确定的字符串填 ""，数字填 null，布尔填 null，数组填 []，对象填 {{}}。
3. 保留原文证据，source_evidence 必须是文档中真实出现的片段。
4. source_location 必须来自文档块中的 source_location。
5. 同一事实不要重复输出；但不同套餐、不同费用、不同材料、不同条款必须分别输出。
6. 不要把可选权益包误填到基础套餐字段。
7. 不要把表单字段标签误当作办理材料，例如“身份证号码”不是“经办人身份证复印件”。

{ARRAY_EXTRACTION_RULES}
"""


DOCUMENT_CONTEXT_TEMPLATE = """
文档块如下。每个块都有 block_id、block_type、source_location 和 text。

{document_blocks}
"""


MODULE_PROMPTS: dict[str, str] = {
    "document_info": """
你只抽取 document_info。
输出必须是 document_info 对象本身，不要包外层字段。
重点识别：文档类型、标题、产品名、产品族、运营商、区域、版本、生效/失效日期、状态、文件名、路径、文件类型。
""",
    "parties_and_application": """
你只抽取 parties_and_application。
输出必须是 parties_and_application 对象本身，不要包外层字段。
重点识别：服务商、客户信息、是否空白表单、必填字段、选填字段、候选项、填写说明、办理说明。
application_fields.required 和 application_fields.optional 都是列表，所有表单字段必须逐条输出。
""",
    "base_package": """
你只抽取 base_package。
输出必须是 base_package 对象本身，不要包外层字段。
重点识别：基础套餐档位、速率、上下行、价格、计费周期、协议期、是否带语音、套餐内包含服务、基础业务属性、SLA。
base_package.packages、included_items、service_attributes 都是列表，原文有多项就输出多项。
""",
    "optional_packages": """
你只抽取 optional_packages。
输出必须是 optional_packages 数组，不要包外层字段。
重点识别：免费可选包、收费增值包、权益包、配套业务、上行升速包、移动业务、固话/商云通等。
每个权益包或可选包必须单独一个对象。
""",
    "fee_and_term_rules": """
你只抽取 fee_and_term_rules。
输出必须是 fee_and_term_rules 数组，不要包外层字段。
重点识别：月费、年费、两年费、一次性费用、安装调测费、手续费、押金、折扣、协议期、续约、违约金。
每条费用或期限规则必须单独一个对象。
""",
    "agreement_rules": """
你只抽取 agreement_rules。
输出必须是 agreement_rules 数组，不要包外层字段。
重点识别：合同限制、退订、变更、注销、违约责任、售后、SLA、赔付、使用限制、客户义务、服务商义务。
每条条款必须单独一个对象。
""",
    "application_materials": """
你只抽取 application_materials。
输出必须是 application_materials 数组，不要包外层字段。
重点识别：营业执照、授权委托书、经办人身份证、担保书、申请表、承诺书、备案表、拓扑图、责任人证件复印件等。
只有原文明确要求提供、提交、须附、复印件、盖章、签字的内容才算办理材料。
每份材料必须单独一个对象。
""",
    "eligibility_and_constraints": """
你只抽取 eligibility_and_constraints。
输出必须是 eligibility_and_constraints 数组，不要包外层字段。
重点识别：准入条件、排除条件、额外要求、推荐阻断、合规要求、客户类型限制、IP 数量触发规则、停用/不可推荐规则。
每条条件或限制必须单独一个对象。
""",
    "supplemental_rules": """
你只抽取 supplemental_rules。
输出必须是 supplemental_rules 数组，不要包外层字段。
重点识别：附件、附录、承诺书、备案表、SLA附页、IP地址报备表、网络安全责任人信息等补充材料中的字段、列、规则。
每个补充材料或补充规则必须单独一个对象。
""",
}


SELF_CHECK_PROMPT = """
你只做 JSON 自检，不重新抽取。
检查目标：
1. 是否存在列表字段只输出一条但 source_evidence 中明显包含多条业务事实。
2. 是否把可选包/权益包误放入基础套餐。
3. 是否把表单字段标签误认为办理材料。
4. 是否缺少 source_evidence、source_location 或 confidence。
5. 是否存在金额、期限、适用条件归属错误。
6. 是否有字段类型不符合 schema。

只输出一个对象：
{
  "llm_self_check": {},
  "validation_issues": [],
  "schema_warnings": []
}
"""


def build_module_prompt(module_name: str, document_blocks: str) -> str:
    if module_name not in MODULE_PROMPTS:
        raise KeyError(f"Unknown extraction module: {module_name}")
    return "\n\n".join(
        [
            COMMON_EXTRACTION_RULES,
            MODULE_PROMPTS[module_name],
            DOCUMENT_CONTEXT_TEMPLATE.format(document_blocks=document_blocks),
        ]
    )


def build_self_check_prompt(product_json: str) -> str:
    return "\n\n".join([COMMON_EXTRACTION_RULES, SELF_CHECK_PROMPT, f"待检查 JSON：\n{product_json}"])
