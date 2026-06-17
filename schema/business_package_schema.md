# 业务套餐字段设计

本文档用于描述业务套餐相关文件的结构化抽取字段。整体数据分为两大部分：

- `application_form_info`：来自产品申请表 `.docx`，承载申请表中的基础业务、套餐、费用、协议和限制规则。
- `supplementary_info`：来自产品介绍、关键词、资费表、申请手续提示等补充文件，承载产品说明、关键词、资费明细和办理材料要求。

## 一、整体结构

```json
{
  "application_form_info": {
    "document_info": {},
    "parties_and_application": {},
    "base_package": {},
    "optional_packages": [],
    "fee_and_term_rules": [],
    "agreement_rules": [],
    "eligibility_and_constraints": []
  },
  "supplementary_info": {
    "product_intro": {},
    "product_keywords": {},
    "pricing_info": {},
    "application_materials": []
  },
  "extraction_meta": {}
}
```

## 二、申请表基本信息

来源文件：产品申请表 `.docx`

申请表基本信息包括 7 类：

| 序号 | 字段 | 含义 |
| --- | --- | --- |
| 1 | `document_info` | 文档基本信息 |
| 2 | `parties_and_application` | 服务商、客户与办理信息 |
| 3 | `base_package` | 基础套餐信息 |
| 4 | `optional_packages` | 增值免费可选包、权益包、增值收费可选包 |
| 5 | `fee_and_term_rules` | 费用与期限规则 |
| 6 | `agreement_rules` | 协议条款、限制、违约、售后规则 |
| 7 | `eligibility_and_constraints` | 准入条件与限制规则 |

### 1. document_info：文档基本信息

用于记录文档自身属性、适用范围和来源信息。

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `document_id` | string | 文档唯一 ID |
| `document_type` | string | 文档类型，如申请表、营销规则、服务协议、资费表、附件 |
| `title` | string | 文档原始标题 |
| `product_name` | string | 产品或套餐名称 |
| `product_family` | string | 产品族，用于归类，如精品专线、小微业务 |
| `carrier` | string | 运营商或服务归属，如电信、联通、移动、其他 |
| `region` | string | 适用区域 |
| `version` | string | 文档版本号 |
| `effective_from` | string/date | 生效日期 |
| `effective_to` | string/date | 失效日期或截止日期 |
| `document_status` | string | 文档状态，如 `active`、`inactive` |
| `filename` | string | 原始文件名 |
| `source_path` | string | 原始文件路径 |
| `source_file_type` | string | 文件类型，如 `docx`、`pdf`、`xlsx` |

### 2. parties_and_application：服务商、客户与办理信息

用于记录服务提供方、客户填写情况、申请表字段和办理说明。

#### 2.1 service_provider

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `name` | string | 服务商名称 |
| `carrier` | string | 服务商所属运营商 |
| `region` | string | 服务区域 |
| `contact_channels` | array | 联系电话、热线、办理渠道等 |

#### 2.2 customer

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `name` | string | 客户名称；空白申请表为空 |
| `filled_values` | array | 已填写的客户信息 |
| `is_blank_form` | boolean | 是否为空白申请表 |

#### 2.3 application_fields

申请字段分为：

- `required`：必填字段。
- `optional`：选填字段，结构与 `required` 相同。

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `label` | string | 原始字段名 |
| `field_key` | string | 标准化字段名，便于系统使用 |
| `required` | boolean | 是否必填 |
| `value` | string | 已填写值 |
| `options` | array | 候选项，如企业规模、接口标准 |
| `value_type` | string | 字段类型，如 `text`、`single_select`、`multi_select` |
| `source_evidence` | string | 字段来源原文 |
| `source_location` | object | 来源位置，如表格、行、列 |
| `confidence` | number | 抽取置信度，取值 0 到 1 |

#### 2.4 application_notes

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `application_notes` | array | 填写说明、办理说明、注意事项 |

### 3. base_package：基础套餐信息

用于描述主套餐、套餐内包含内容、业务属性和 SLA。

#### 3.1 packages

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `package_name` | string | 套餐名称 |
| `package_code` | string | 套餐编号或产品编码 |
| `speed` | string | 原始速率描述 |
| `upstream_speed` | string | 上行速率 |
| `downstream_speed` | string | 下行速率 |
| `bandwidth_unit` | string | 带宽单位 |
| `has_voice` | boolean/null | 是否带语音 |
| `price` | number/null | 套餐价格 |
| `currency` | string, 默认 `CNY` | 币种 |
| `billing_period` | string | 计费周期，如月、年、2 年 |
| `contract_period` | string | 协议期 |
| `quantity_limit` | string | 数量限制 |
| `applicable_conditions` | array | 适用条件 |
| `source_evidence` | string | 来源证据 |
| `source_location` | object | 来源位置 |
| `confidence` | number | 抽取置信度，取值 0 到 1 |

#### 3.2 其他基础套餐字段

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `included_items` | array | 套餐内包含的服务、权益、设备、资源 |
| `service_attributes` | array | 基础业务属性，如接口标准、套餐类型、SLA 等级 |
| `sla` | object | 服务等级、网络保障、赔付规则 |

### 4. optional_packages：可选包信息

用于记录增值免费可选包、权益包、增值收费可选包。

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `package_type` | string | 可选包类型，如 `free_optional_package`、`paid_optional_package` |
| `name` | string | 可选包名称 |
| `category` | string | 原始分类 |
| `description` | string | 详细说明 |
| `fee_summary` | string | 费用摘要 |
| `price_items` | array | 价格明细 |
| `options` | array | 可选项 |
| `required_with` | array | 必须同时订购的产品或条件 |
| `incompatible_with` | array | 不兼容项 |
| `applicable_conditions` | array | 适用条件 |
| `source_evidence` | string | 来源证据 |
| `source_location` | object | 来源位置 |
| `confidence` | number | 抽取置信度，取值 0 到 1 |

### 5. fee_and_term_rules：费用与期限规则

用于抽取安装费、协议期、折扣、违约金等通用费用或期限规则。

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `rule_type` | string | 规则类型，如安装费、协议期、折扣、违约金 |
| `name` | string | 规则名称 |
| `description` | string | 规则说明 |
| `amount` | number/null | 金额 |
| `currency` | string, 默认 `CNY` | 币种 |
| `billing_period` | string | 计费周期 |
| `contract_period` | string | 协议期 |
| `conditions` | array | 触发条件或适用条件 |
| `applies_to` | array | 适用对象 |
| `source_evidence` | string | 来源证据 |
| `source_location` | object | 来源位置 |
| `confidence` | number | 抽取置信度，取值 0 到 1 |

### 6. agreement_rules：协议、限制、违约与售后规则

用于描述合同条款、使用限制、终止规则、售后规则等。

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `rule_type` | string | 规则类型，如 `restriction`、`termination`、`after_sales` |
| `title` | string | 规则标题 |
| `description` | string | 规则内容 |
| `severity` | string | 重要程度 |
| `applies_to` | array | 适用对象 |
| `obligation_party` | string | 责任主体，如客户、服务商、双方 |
| `conditions` | array | 触发条件 |
| `consequence` | string | 违反后的后果或处理方式 |
| `source_evidence` | string | 来源证据 |
| `source_location` | object | 来源位置 |
| `confidence` | number | 抽取置信度，取值 0 到 1 |

### 7. eligibility_and_constraints：准入条件与限制规则

用于记录会影响推荐、办理、材料、费用或合规判断的限制规则。

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `constraint_type` | string | 规则类型：`eligibility` 准入条件、`required_condition` 额外要求、`exclusion` 排除条件、`compliance` 合规要求、`recommendation_blocker` 推荐阻断规则 |
| `name` | string | 规则名称，便于业务人员识别，例如“外地公司担保要求”“16 个 IP 拓扑图要求”“停用套餐不可推荐” |
| `description` | string | 规则说明，用自然语言完整描述限制或准入要求 |
| `condition` | string | 触发条件，例如“客户为外地公司”“IP 地址数量 >= 16”“路径包含停用” |
| `result` | string | 触发结果，例如“需提供担保书并支付押金”“需提供拓扑图”“不可推荐该套餐” |
| `blocks_recommendation` | boolean | 是否阻止推荐；`true` 表示触发后 Agent 不能推荐该套餐，`false` 表示可推荐但需提示风险或补充要求 |
| `applies_to` | array | 适用对象，如产品、套餐、客户类型或办理动作 |
| `rule_source` | string | 规则来源类别，如 `path`、`document`、`contract`、`manual`、`system` |
| `normalized_logic` | object | 结构化条件表达式，供系统执行，例如 `{"field":"ip_count","operator":">=","value":16}` |
| `related_materials` | array | 关联材料，如拓扑图、担保书、承诺函 |
| `related_fee_rules` | array | 关联费用规则，如外地公司押金 2000 元 |
| `valid_from` | string/date | 规则生效日期 |
| `valid_to` | string/date | 规则失效日期 |
| `source_evidence` | string | 来源证据，从原文中截取的依据 |
| `source_location` | object | 来源位置，如页码、段落、表格、行列、sheet 名称 |
| `confidence` | number | 抽取置信度，取值 0 到 1 |

## 三、额外补充信息

来源文件：

- 产品介绍 `.docx`
- 关键词 `.docx`
- 资费表 `.xlsx`
- 申请手续提示 `.txt`

补充信息包括 4 类：

| 序号 | 字段 | 含义 |
| --- | --- | --- |
| 1 | `product_intro` | 产品介绍信息 |
| 2 | `product_keywords` | 产品关键词 |
| 3 | `pricing_info` | 资费信息 |
| 4 | `application_materials` | 申请资料手续信息 |

### 1. product_intro：产品介绍信息

用于记录产品介绍正文和典型应用场景。

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `product_name` | string | 产品名称 |
| `full_description` | string | 产品介绍正文 |
| `application_scenarios` | string/array | 应用场景，例如总部办公、视频会议、ERP 访问、企业专网、票务系统 |

### 2. product_keywords：产品关键词

用于记录关键词原文及归类后的关键词，支持检索、问答和推荐匹配。

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `raw_keywords` | array/string | 从关键词文档中原样提取的关键词，保留未归类前的完整列表 |
| `alias_keywords` | array/string | 别名、同义词、简称，例如固定公网 IP、静态公网 IPv4、公网 IP 地址 |
| `capability_keywords` | array/string | 产品能力关键词，例如上下行对称带宽、独享带宽、IP 地址扩容、域名反向解析 |
| `scenario_keywords` | array/string | 应用场景关键词，例如本地组网、国内组网、视频会议、企业专网 |
| `customer_need_keywords` | array/string | 客户需求或问题关键词，例如访问很慢、线路延时、带宽利用率、需要多少带宽 |
| `technical_keywords` | array/string | 技术属性关键词，例如端口开放、80 端口、8080 端口、443 端口、ICP备案 |
| `pricing_keywords` | array/string | 资费相关关键词，例如打折、月租费、一次性费用、合同协议期 |
| `procedure_keywords` | array/string | 办理和交付相关关键词，例如是否具备资源、需要多长时间开通 |
| `risk_or_constraint_keywords` | array/string | 风险、限制、违约相关关键词，例如协议期内拆机违约金 |
| `sales_keywords` | array/string | 销售引导关键词，例如 1 元手机卡、人数 50 到 100、同时使用终端超过 50 台 |

### 3. pricing_info：资费信息

资费信息按费用性质拆分为 4 类，避免主业务资费、一次性费用、附加费用和可选包费用混在一起。

| 分类 | 字段 | 放置内容 |
| --- | --- | --- |
| 一次性费用 | `one_time_fees` | 只放一次性收取的费用，例如初装费、接入费、新装/升降速费用 |
| 主业务基础资费 | `business_fees` | 只放主业务基础资费，例如月租、年付、两年付、端口费、授权价 |
| 其他附加费用 | `other_fees` | 放非主业务、非可选包的附加费用，例如 IP 升级费、国际精品 IP 升级费、违约金、赔付规则 |
| 可选包费用 | `fees_optional_packages` | 放客户可选择加购的包或增值服务，例如安全大脑、移动业务、商云通 |

#### 3.1 one_time_fees：一次性费用

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `fee_name` | string | 费用名称，例如初装费、一次性接入费、一次性费用合计 |
| `product_variant` | string | 产品变体，例如精品专线、本地 MSTP、国内 MSTP |
| `customer_type` | string | 客户或付费类型，例如月付用户、年付用户、2 年付用户 |
| `business_action` | string | 业务动作，例如新装、升降速、移机 |
| `resource_status` | string | 资源情况，例如有资源、无资源、需勘查 |
| `location_side` | string | 费用归属端，例如上海端、外地端、本地端 |
| `amount` | number/null | 结构化金额，纯数字；无法确定时为 `null` |
| `amount_text` | string | 金额原文，例如可减免、0 元、勘查后外地报价 |
| `unit` | string | 单位，例如元、元/线/次 |
| `waivable` | boolean | 是否可减免 |
| `quote_required` | boolean | 是否需要询价或现场勘查 |
| `conditions` | array | 适用条件，例如仅年付用户、无资源场景 |
| `remarks` | string | 备注 |

#### 3.2 business_fees：主业务基础资费

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `fee_name` | string | 费用名称，例如业务月租费、基础套餐费、端口月租费 |
| `product_variant` | string | 产品变体，例如精品专线、本地 MSTP、国内 MSTP |
| `service_subtype` | string | 业务子类型，例如本地、国内、国际精品网 |
| `bandwidth` | string | 速率原文，例如 100M/100M、100M、1G |
| `bandwidth_up` | string | 上行带宽，例如 100M |
| `bandwidth_down` | string | 下行带宽，例如 100M |
| `billing_cycle` | string | 计费周期，例如 `monthly`、`annual`、`biennial` |
| `standard_monthly_fee` | number/null | 标准月租费 |
| `monthly_fee` | number/null | 月付费用 |
| `annual_fee` | number/null | 年付费用 |
| `biennial_fee` | number/null | 两年付费用 |
| `authorized_discount_rate` | number/null | 授权折扣率，例如 0.5 |
| `authorized_monthly_fee` | number/null | 授权月租价，例如授权 5 折价格 |
| `local_port_fee` | number/null | 本地端口费，MSTP 适用 |
| `remote_port_fee` | number/null | 外地或国内端口费，MSTP 适用 |
| `domestic_circuit_fee` | number/null | 国内月租费或长途电路费用，国内 MSTP 适用 |
| `total_standard_fee` | number/null | 标准资费合计 |
| `included_resources` | array | 包含资源，例如赠送 5 个可用 IP 地址、IPv6 地址 |
| `gift_items` | array | 赠送内容，例如本地代维现场服务 |
| `conditions` | array | 适用条件，例如仅指定速率、仅企业客户 |
| `remarks` | string | 备注 |

#### 3.3 other_fees：其他附加费用

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `fee_name` | string | 费用名称，例如 IP 地址升级费、国际精品 IP 升级费、违约金 |
| `fee_category` | string | 费用类别，例如 `ip_upgrade`、`penalty`、`installation_extra`、`sla_compensation` |
| `product_variant` | string | 产品变体，例如精品专线、本地 MSTP、国内 MSTP |
| `option_name` | string | 选项名称，例如升级 13 个 IPv4、升级 5 个国际精品 IP 地址 |
| `amount` | number/null | 一次性金额；如不是一次性费用可为空 |
| `amount_text` | string | 金额原文 |
| `monthly_fee` | number/null | 月付费用 |
| `annual_fee` | number/null | 年付费用 |
| `biennial_fee` | number/null | 两年付费用 |
| `unit` | string | 单位，例如元/月、元/年、元/2 年 |
| `billing_cycle` | string | 计费周期，例如 `monthly`、`annual`、`biennial`、`one_time` |
| `conditions` | array | 适用条件 |
| `dependencies` | array | 依赖项，例如选择国际精品 IP 必须先升级到 13 个固定公网 IPv4 |
| `remarks` | string | 备注 |

#### 3.4 fees_optional_packages：可选包费用

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `package_name` | string | 可选包名称，例如天翼安全大脑、移动业务、商云通 |
| `option_name` | string | 子选项名称，例如专线版 300M、安全专家增值服务、5G 畅享套餐 99 元档 |
| `package_type` | string | 可选包类型，例如 `security`、`mobile`、`voice`、`maintenance` |
| `fee` | number/null | 结构化费用，纯数字；复杂资费可为空 |
| `fee_text` | string | 费用原文，例如 1 元/月/号、0 元包、600 元/月/线 |
| `billing_cycle` | string | 计费周期，例如 `monthly`、`annual` |
| `unit` | string | 单位，例如元/月/线、元/月/号、分钟/月/线 |
| `applicable_bandwidth` | string | 适用带宽，例如下行速率 300M 及以下 |
| `quota` | string | 额度，例如申请线数、2500 分钟/月/线、20GB/月 |
| `contract_term_months` | number/null | 协议期月数 |
| `included_content` | array | 包含内容，例如防火墙、病毒防护、国内通话 2500 分钟 |
| `conditions` | array | 适用条件，例如仅限新装、仅限一线、签约金额限制 |
| `remarks` | string | 备注 |

#### 3.5 pricing_notes：资费备注

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `pricing_notes` | array | 资费备注、特殊说明、询价说明、优惠说明 |

### 4. application_materials：申请资料手续信息

用于记录客户或内部办理时需要准备的材料清单，以及盖章、签字、模板、提交条件等要求。

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `material_name` | string | 材料名称，例如营业执照复印件、授权委托书、经办人身份证、担保书、申请表、拓扑图 |
| `material_type` | string | 材料类型，例如 `license`、`id_card`、`authorization`、`form`、`guarantee`、`contract`、`topology`、`commitment`、`other` |
| `required` | boolean | 是否必需；`true` 表示必须提供，`false` 表示特定情况下可选或仅作为补充材料 |
| `applies_to` | array | 适用对象，例如上海公司、外地公司、存量客户、新装、变更、拆机 |
| `condition` | string | 触发条件，例如外地公司办理时、IP 地址数量大于等于 16 个时、同地址同户名有存量宽带时 |
| `copies` | string | 份数要求，例如一份、两份、一式四份、每份都需盖章 |
| `format` | string | 材料形式，例如 `original`、`copy`、`scan`、`electronic`、`printed`、`photo` |
| `seal_required` | boolean | 是否需要盖章 |
| `seal_type` | string | 盖章类型，例如公章、合同章、骑缝章、不可盖合同章 |
| `signature_required` | boolean | 是否需要签字 |
| `signature_party` | string | 签字主体，例如经办人、法人、客户负责人、授权代表、服务商 |
| `date_required` | boolean | 是否需要填写日期 |
| `template_required` | boolean | 是否必须使用指定模板 |
| `template_document` | string | 对应模板文件名称或路径 |
| `notes` | string | 其他说明，例如“所有手续需机打，请勿手写” |
| `source_evidence` | string | 来源证据，从原文中截取的依据 |
| `source_location` | object | 来源位置，如页码、段落、表格、行列、sheet 名称 |
| `confidence` | number | 抽取置信度，取值 0 到 1 |

## 四、抽取元信息

### extraction_meta

用于记录本次结构化抽取过程的元数据，便于后续追踪、校验和审核。

| 字段 | 类型/默认值 | 含义 |
| --- | --- | --- |
| `extraction_meta` | object | 抽取元信息，例如抽取时间、抽取方法、文件哈希、校验问题、审核状态、发布状态 |

## 五、字段使用建议

- 与业务推荐强相关的阻断、准入、材料和费用条件，应优先放入 `eligibility_and_constraints`，再通过 `related_materials`、`related_fee_rules` 关联材料和费用。
- 套餐本身价格优先放在 `base_package.packages.price`；复杂资费明细放在 `supplementary_info.pricing_info`。
- 主业务资费、一次性费用、附加费用、可选包费用应分开存放，避免同一费用被重复解释。
- 所有从原文抽取的关键业务结论，都建议保留 `source_evidence`、`source_location`、`confidence`，方便人工复核。
