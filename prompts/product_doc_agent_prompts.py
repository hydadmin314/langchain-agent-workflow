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
4. 当前输入是 MarkItDown 生成的纯 Markdown，不再包含 source_location 注释；source_evidence 必须来自原文，source_location 无法精确定位时填 {{}}。
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
文档内容如下，格式为 MarkItDown 转换后的纯 Markdown。
请只依据下面的 Markdown 原文抽取；不要假设存在隐藏的 source 注释或额外元数据。
source_evidence 必须截取自 Markdown 原文；source_location 无法从 Markdown 精确定位时填 {{}}。

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
你只做语义质量自检，并判断是否需要触发模块返工；本步骤不重新抽取、不补字段。
检查目标：
1. 是否存在列表字段只输出一条但 source_evidence 中明显包含多条业务事实。
2. 是否把可选包/权益包误放入基础套餐。
3. 是否把表单字段标签误认为办理材料。
4. 是否缺少 source_evidence、source_location 或 confidence。
5. 是否存在金额、期限、适用条件归属错误。
6. 是否有字段类型不符合 schema。
7. 是否存在字段之间的明显矛盾，例如同一事实在两个模块里表达冲突。

自检边界：
1. 只根据当前 JSON 判断语义质量问题；不要补字段，不要输出修复后的业务数据。
2. 不要要求补充原文没有明确列出的 included_items；例如不能因为套餐有带宽，就建议凭推断新增“互联网接入”等包含项。
3. schema_warnings 只输出简短字符串，不要输出对象、字典或嵌套 JSON。
4. validation_issues 每条必须包含 severity、path、message 三个字段；不要使用 type、field_path、issue 等自创字段。
5. 如果问题需要回到原始 Markdown 重新抽取，请在 llm_self_check.rework_modules 中明确列出模块名和原因。
6. rework_modules 只能使用这 9 个模块名：document_info、parties_and_application、base_package、optional_packages、fee_and_term_rules、agreement_rules、application_materials、eligibility_and_constraints、supplemental_rules。
7. 申请表字段完全为空、schema 字段缺失、confidence 类型错误、currency 格式错误、路径状态矛盾等确定性问题由程序校验负责；除非它们导致语义错放或字段冲突，否则不要在 self_check 中重复报告。

只输出一个对象：
{
  "llm_self_check": {
    "needs_rework": false,
    "rework_modules": []
  },
  "validation_issues": [],
  "schema_warnings": []
}

补充自检边界：
1. 不要给自由文本字段发明枚举限制。只要本地 schema 没有定义 enum，就不要因为某个值不是你偏好的标准词而报错。
2. 不要要求输出 schema 模板里不存在的字段。例如 price_items 里没有 tier/speed 字段时，只要档位已经能通过 source_evidence、item_name、name 或 options 追溯，就不要报缺字段。
3. 不要把 schema 中定义为 string 的字段强行要求改成 array。一个字符串里包含多个相关后果时，只有在它导致无法理解或与原文矛盾时才报告。
4. 你检查的是已经经过程序归一化后的最终 JSON。不要报告当前 JSON 中已经不存在、已经修复或仅属于中间抽取过程的问题。
5. confidence=0.0 是合法数字；只有缺少 confidence 字段、不是数字、或超出 0 到 1 范围时才报告。
6. 只报告真正影响审核、入库、发布或后续推荐的数据问题；不要输出“虽然可以接受”“无需处理”“可忽略”这类假问题。
7. 空白申请表中的下划线、空格、待填写框、"________"、"______________" 只是占位符，不是已抽取到的金额或文本值；不要因为原文有“月租费：____”就要求 price 必须有数值。
8. base_package.packages 中多个档位可以共享同一个 package_name，只要 speed、price、billing_period、contract_period 或 package_code 能区分，不要把重复 package_name 报为错误。
9. optional_packages.options 的 schema 是字符串数组；字符串可以保留原文中的必要说明。不要因为 options 不是纯标签就报 schema 错误，除非它明显混入了其它无关产品或整段合同条款。
10. base_package.included_items 的 schema 是开放对象，不要求必须有 item_name；不要发明 included_items 的必填字段。
11. 如果发现可选包被放入基础套餐、费用规则归属错误、source_evidence 中多条事实被合并成一条、或者模块之间语义冲突，可以把对应模块加入 rework_modules。
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
            "返工要求：",
            "1. 只输出当前模块本身的合法 JSON，不解释。",
            "2. 必须优先解决自检指出的漏抽、错放、字段为空或列表不完整问题。",
            "3. 只能依据下面的 Markdown 原文重新抽取；旧 JSON 只用于理解问题，不作为事实来源。",
            "4. 返工不是自由扩写；没有原文明确证据的内容仍然保持空值或空数组。",
            rework_guardrails(module_name),
            f"模块名：{module_name}",
            f"返工原因：\n{rework_reason}",
            f"上一次模块 JSON：\n{previous_module_json}",
            DOCUMENT_CONTEXT_TEMPLATE.format(document_blocks=document_blocks),
        ]
    )


def rework_guardrails(module_name: str) -> str:
    """按模块补充返工边界，避免返工时为了修问题而过度抽取。"""

    if module_name == "optional_packages":
        return """
optional_packages 返工边界：
1. 只有原文明确出现可售卖、可勾选、可单独订购的可选包/权益包/增值业务时，才创建 optional_package。
2. “配套业务”“业务说明”“协议条款”“办理说明”“承诺书”“限制规则”等泛称，不能单独作为 optional_package。
3. 如果 self_check 只是根据其它模块里的泛称推测存在可选包，但 Markdown 没有明确可选包名称、费用、勾选项或订购条件，则输出 []。
4. 每个可选包一条对象；不要把多个权益、多个业务、多个价格档位合并成一条。
5. 返工时重点检查原文中的“可选包、权益包、增值服务、可选产品、配套产品、上行升速包、移动业务、固话、商云通”等明确栏目或行。
"""
    return ""


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
