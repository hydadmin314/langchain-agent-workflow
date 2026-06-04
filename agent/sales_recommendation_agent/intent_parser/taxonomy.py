from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DemandCategoryRule:
    """产品需求分类体系中的一条分类规则。

    这份 taxonomy 是 Intent Parser 和 Candidate Retriever 共用的唯一分类来源。
    后续新增产品类时，优先维护这里，而不是在多个模块里散落关键词。
    """

    category_id: str
    category_name: str
    demand_keywords: tuple[str, ...]
    product_keywords: tuple[str, ...]
    product_path_keywords: tuple[str, ...] = field(default_factory=tuple)
    recommendation_mode: str = "new_sale"
    clarify_questions: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)


DEMAND_CATEGORY_RULES: tuple[DemandCategoryRule, ...] = (
    DemandCategoryRule(
        category_id="1",
        category_name="企业上网与办公宽带",
        demand_keywords=("办公上网", "办公室网络", "网络慢", "日常上网", "稳定宽带", "预算有限", "办公", "宽带"),
        product_keywords=("商务专线", "精品专线", "智能专线", "智享云宽", "智光专线", "沃商务", "小微上线", "小微在线", "商企云宽"),
        product_path_keywords=("商务专线", "精品专线", "智能专线", "小微", "智享云宽", "智光专线", "沃商务"),
        clarify_questions=("客户办公地点在哪里？", "大约多少人或终端使用？", "预算范围和期望带宽是多少？"),
    ),
    DemandCategoryRule(
        category_id="2",
        category_name="固定IP_高带宽_互联网专线",
        demand_keywords=("固定公网IP", "固定IP", "公网IP", "服务器对外", "企业官网", "远程办公入口", "高带宽", "公网地址", "备案", "互联网专线"),
        product_keywords=("IPMAN", "BGP", "精品专线", "商务专线", "网站备案", "PCDN", "互联网专线", "IP地址", "公网IP"),
        product_path_keywords=("IPMAN", "BGP", "精品专线", "商务专线", "网站备案", "PCDN"),
        clarify_questions=("是否必须固定公网 IP？", "需要几个公网 IP？", "是否涉及网站备案或服务器对外访问？"),
    ),
    DemandCategoryRule(
        category_id="3",
        category_name="国内组网与点对点专线",
        demand_keywords=("总部", "分公司", "互联", "门店组网", "多地互联", "内网专线", "数据中心互联", "点对点", "组网", "互通", "专线互联"),
        product_keywords=("MSTP", "IPRAN", "MPLS", "MPLS-VPN", "SDH", "OTN", "SD-WAN", "点对点", "组网"),
        product_path_keywords=("MSTP", "IPRAN", "MPLS", "SDH", "OTN", "SD-WAN"),
        clarify_questions=("有几个站点需要互联？", "站点分别在哪些城市？", "是点对点还是多点组网？"),
    ),
    DemandCategoryRule(
        category_id="4",
        category_name="海外访问与跨境加速",
        demand_keywords=("海外服务器", "美国", "跨境", "海外 SaaS", "海外SaaS", "外贸", "海外站点", "访问海外", "国外", "国际"),
        product_keywords=("精品专线", "IPMAN", "BGP", "SD-WAN", "智能专线", "互联网专线"),
        product_path_keywords=("精品专线", "IPMAN", "BGP", "SD-WAN", "智能专线"),
        clarify_questions=("访问目标是哪个国家或应用？", "是否涉及合规跨境专线要求？", "是否有多地分支同时访问海外？"),
        notes=("跨境访问涉及合规和资源确认，Agent 不能直接承诺跨境专线能力。",),
    ),
    DemandCategoryRule(
        category_id="5",
        category_name="门店_商铺_小微经营",
        demand_keywords=("门店", "商铺", "收银", "餐饮", "连锁小店", "开店宝", "小微企业", "5G融合", "酒店", "公寓"),
        product_keywords=("旺铺宽带", "长租公寓", "酒店完美联盟", "小微上线", "小微在线", "沃商务企微宽带", "商铺宽带", "开店宝"),
        product_path_keywords=("旺铺宽带", "长租公寓", "酒店", "小微", "沃商务", "商铺"),
        clarify_questions=("是单门店还是连锁门店？", "是否需要收银、Wi-Fi 或 5G 融合？", "门店地址和预算是多少？"),
    ),
    DemandCategoryRule(
        category_id="6",
        category_name="固定电话_语音中继_呼叫业务",
        demand_keywords=("固定电话", "30B+D", "DID", "中继线", "云中继", "总机", "呼叫中心", "坐席电话", "商云通", "商继通", "长途主叫"),
        product_keywords=("30B+D", "DID", "直线", "商云通", "商继通", "云中继", "IP17969", "固话", "语音"),
        product_path_keywords=("30B+D", "DID", "直线", "商云通", "商继通", "云中继", "IP17969"),
        clarify_questions=("需要多少条线或多少坐席？", "是否已有号码需要保留？", "是否涉及呼叫中心或外呼业务？"),
    ),
    DemandCategoryRule(
        category_id="7",
        category_name="移动通信_流量_固移融合",
        demand_keywords=("手机卡", "员工移动套餐", "大流量卡", "大流量", "5G", "5G套餐", "语音流量融合", "宽带+手机", "固移融合", "移动套餐"),
        product_keywords=("5G畅享", "行业大流量", "固移融合", "移动套餐", "流量", "手机卡"),
        product_path_keywords=("5G畅享", "行业大流量", "固移融合", "移动套餐"),
        clarify_questions=("需要多少张手机卡？", "更关注语音还是流量？", "是否要和宽带一起办理？"),
    ),
    DemandCategoryRule(
        category_id="8",
        category_name="云资源_IDC_算力托管",
        demand_keywords=("服务器托管", "云主机", "机房", "IDC", "云资源", "数据中心", "服务器上云", "算力"),
        product_keywords=("天翼云主机", "IDC审批", "云资源", "服务器托管"),
        product_path_keywords=("天翼云主机", "IDC审批"),
        clarify_questions=("需要物理服务器托管还是云主机？", "机房位置是否有要求？", "是否需要专线接入？"),
    ),
    DemandCategoryRule(
        category_id="9",
        category_name="云办公_协同_云电脑",
        demand_keywords=("云电脑", "远程办公", "云桌面", "企业网盘", "在线文档", "视频会议", "办公软件", "云办公"),
        product_keywords=("WPS云文档", "天翼云会议", "天翼云桌面", "天翼云电脑", "企业云盘", "小龙虾云办公"),
        product_path_keywords=("WPS云文档", "天翼云会议", "天翼云桌面", "天翼云电脑", "企业云盘", "云办公"),
        clarify_questions=("多少员工需要云办公？", "主要需要云电脑、网盘还是会议？", "是否有远程办公安全要求？"),
    ),
    DemandCategoryRule(
        category_id="10",
        category_name="安全防护_运维代维_托管",
        demand_keywords=("网络安全", "防攻击", "防护", "运维外包", "网络托管", "设备代维", "云网托管", "上门维护"),
        product_keywords=("云堤", "智云护航", "小型集成云网托管", "本地代维", "智能组网", "安全防护"),
        product_path_keywords=("云堤", "智云护航", "云网托管", "本地代维", "智能组网"),
        clarify_questions=("主要是安全防护还是运维代维？", "是否已有网络设备？", "是否需要上门维护？"),
    ),
    DemandCategoryRule(
        category_id="11",
        category_name="行业场景_物联_视频_电梯",
        demand_keywords=("监控", "视频看护", "电梯", "物业", "园区", "行业终端", "图片", "地图", "看店", "看厂"),
        product_keywords=("天翼云眼", "智慧电梯", "天翼云图", "视频监控", "物联"),
        product_path_keywords=("天翼云眼", "智慧电梯", "天翼云图"),
        clarify_questions=("具体行业场景是什么？", "需要视频监控、电梯还是园区能力？", "终端数量是多少？"),
    ),
    DemandCategoryRule(
        category_id="12",
        category_name="营销触达_来电展示_短信录音",
        demand_keywords=("来电显示", "企业名片", "电话录音", "短信触达", "挂机短信", "400电话", "预付费400", "来电展示"),
        product_keywords=("预付费400", "来电名片", "云录音", "挂机短信", "音证宝", "400"),
        product_path_keywords=("预付费400", "来电名片", "云录音", "挂机短信", "音证宝"),
        clarify_questions=("需要 400 电话、来电名片还是录音？", "预计号码数量和通话量是多少？", "是否需要短信触达？"),
    ),
    DemandCategoryRule(
        category_id="13",
        category_name="办理变更_续约_拆机_撤单",
        demand_keywords=("移机", "过户", "改套餐", "改账单", "付款方式变更", "工商更名", "拆机", "撤单", "续约", "退款"),
        product_keywords=("变更", "拆机", "撤单", "续约", "退款", "移机", "过户", "付费方式", "账单余额"),
        product_path_keywords=("变更", "拆机", "撤单", "续约", "退款", "移机", "过户"),
        recommendation_mode="service_process",
        clarify_questions=("客户要办理哪一种变更？", "原业务号码或合同信息是什么？", "是否已有授权材料？"),
        notes=("该类属于售后办理流程，通常不走新销售套餐推荐模式。",),
    ),
    DemandCategoryRule(
        category_id="99",
        category_name="停用历史_待确认",
        demand_keywords=("停用", "历史", "旧版", "已停", "停止申请", "待确认"),
        product_keywords=("停用", "历史", "旧版", "已停", "停止申请", "待确认"),
        product_path_keywords=("停用", "历史", "旧版", "已停", "待确认"),
        recommendation_mode="inactive_or_pending",
    ),
)


DEMAND_CATEGORY_RULES_BY_ID = {rule.category_id: rule for rule in DEMAND_CATEGORY_RULES}
