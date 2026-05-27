from __future__ import annotations

from schema.schema import module_output_contract, schema_prompt_contract


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
8. 字段名必须完全使用 schema 中定义的英文名，不能使用近义字段、中文字段、自创字段。
9. 输出前逐项检查：每个对象不得缺少模板字段，不得出现模板之外字段。
10. 对申请登记表类文档，营销规则之前的申请表区域是高优先级内容，必须优先完整抽取其中的基础信息、套餐、可选包和表单字段。
11. 表格行中带“*”的字段通常是必填申请字段，必须进入 application_fields.required；无“*”但需要客户填写或勾选的字段进入 application_fields.optional。
12. 表格行中的“□”候选项必须保留到 options，不能只抽字段名。
13. 所有名为 options 的字段都必须是字符串数组，例如 ["固话", "商云通"]，禁止输出 [{{"option_name": "..."}}] 这类对象。
14. 所有 confidence 字段都必须是 0 到 1 的数字，例如 0.9；禁止填 null、空字符串或中文说明。

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
必须抽取申请表前部基础字段，例如企业全称、统一社会信用代码、企业所属行业、企业规模、计算机数量、安装地址、邮编、经办人、身份证号码、联系电话、经办人职务、E-MAIL、传真、账单地址、付款方式等。
字段名前带 * 的放入 required；企业规模、计算机数量这类带多个“□”选项的字段，options 必须逐项保留，例如 10人以下、10-30人、31-80人。
""",
    "base_package": """
你只抽取 base_package。
输出必须是 base_package 对象本身，不要包外层字段。
严禁输出 {"base_package": {...}}，也严禁只输出 packages 数组；最外层必须直接包含 packages、included_items、service_attributes、sla。
重点识别：基础套餐档位、速率、上下行、价格、计费周期、协议期、是否带语音、套餐内包含服务、基础业务属性、SLA。
base_package.packages、included_items、service_attributes 都是列表，原文有多项就输出多项。
必须抽取“基础套餐申请信息”下面的基础套餐档位；同一行里出现月付、年付、2年付等多个资费时，必须拆成多个 packages 对象。
“套餐类型”“接口标准”“是否带语音”等产品或服务属性不要丢失，应进入 service_attributes。
“企业规模”“计算机数量”“经办人”“联系电话”“邮编”“付款方式”等客户填写字段只进入 parties_and_application.application_fields，不要重复放入 base_package.service_attributes。
included_items 只放基础套餐默认包含且不额外收费的内容；凡是出现“可付费申请、费用增加、另行付费、可选、增值服务”的项目，不要放入 included_items，应进入 optional_packages 或 fee_and_term_rules。
contract_period 只能填写“一年、二年、24个月、至某日期”等真实协议期限；“线、次、月、年、元/月/线”是计量/计费单位，严禁填入 contract_period。
""",
    "optional_packages": """
你只抽取 optional_packages。
输出必须是 optional_packages 数组，不要包外层字段。
重点识别：免费可选包、收费增值包、权益包、配套业务、上行升速包、移动业务、固话/商云通等。
每个权益包或可选包必须单独一个对象。
如果同一行里有多个可勾选业务，例如“固话/商云通”“固话（含翼名片）/商云通”等，必须拆成多个 optional_packages 对象，不能合并成一个 name。
判断拆分边界时，以“一个可销售/可勾选/可订购的权益或业务”为一个对象；同一个对象内部只保留该业务自己的 options 和 price_items。
申请表中基础套餐下面、填表说明之前的固话/商云通、移动业务、上行升速包等都是高优先级可选包，必须逐项抽取。
如果表格行形如“可选增值服务 | 天翼安全大脑”“可选增值服务 | 移动业务 1元/月/号”“可选增值服务 | 商云通”，第一列是分类，第二列才是可选包名称；这些行都必须进入 optional_packages。
如果表格行形如“可选产品申请信息”后紧跟“智能专线入云专线”等产品行，这些后续产品行也必须进入 optional_packages。
同一可选包下有多个价格档位或勾选项时，保留到 price_items 和 options，不要合并成一句描述。
只要原文出现“数字+元/月、数字+元/年、数字+元/2年、数字+元/线/次”等价格，必须写入 price_items.price、currency、billing_period 和 source_evidence，不要只写在 description。
注意：optional_packages.options 的 schema 是 array[string]，只能输出字符串；如果原文选项有说明，把说明合并进同一个字符串，例如 "固话：申请线数，每线含翼名片"。
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
协议/营销规则内容较多时，优先抽取会影响推荐、成交、退订、违约、开通、售后风险的重要规则，不需要把每一句普通说明都机械抽完。
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
            module_output_contract(module_name),
            MODULE_PROMPTS[module_name],
            DOCUMENT_CONTEXT_TEMPLATE.format(document_blocks=document_blocks),
        ]
    )


COMPACT_EXTRACTION_RULES = """
你是业务产品文档结构化抽取器。现在是超时后的紧凑重试，只抽当前模块。
要求：
1. 只输出合法 JSON，不解释。
2. 字段名必须严格使用 schema，不得自创字段。
3. 只依据输入文档块，不编造。
4. 每个 array 字段都是多记录容器；多条业务事实必须拆成多个对象。
5. 每条对象尽量保留 source_evidence、source_location、confidence。
6. 客户填写字段不要重复放到基础套餐属性中。
7. contract_period 只能是真实协议期限，不能是“线、次、月、年、元/月/线”等单位。
"""


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


def build_module_repair_prompt(module_name: str, module_json: str, validation_issues: str) -> str:
    return "\n\n".join(
        [
            COMMON_EXTRACTION_RULES,
            module_output_contract(module_name),
            "下面是模型已经抽取出的模块 JSON，但它不符合 schema。请只做结构修复，不要新增原 JSON 中没有依据的业务事实。",
            "修复要求：",
            "1. 只输出修复后的 JSON，不要解释。",
            "1.1 输出必须是当前模块本身的完整结构，不能额外包一层模块名，也不能只输出局部数组。",
            "2. 删除 schema 之外的字段。",
            "3. 补齐 schema 要求的字段；无法确定时按空值规则填充。",
            "4. confidence 必须是数字，不能是 null；有 source_evidence 的记录通常可填 0.8 或 0.9，证据较弱填 0.5。",
            "5. 所有 options 字段必须是字符串数组；如果当前是对象数组，请把每个对象压平成一个字符串并保留关键信息。",
            "6. 如果模块名是 base_package，输出必须是一个对象，并且必须包含 packages、included_items、service_attributes、sla 四个字段。",
            f"模块名：{module_name}",
            f"校验问题：\n{validation_issues}",
            f"待修复 JSON：\n{module_json}",
        ]
    )
