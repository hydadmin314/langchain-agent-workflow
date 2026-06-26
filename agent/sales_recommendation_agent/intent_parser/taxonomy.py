from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DemandCategoryRule:
    """销售套餐六类分类体系中的一条分类规则。

    这份 taxonomy 是 Intent Parser 和后续候选召回共享的分类来源。
    运营商不放在这里做分类，而是作为 carrier_preference 或产品资源筛选条件处理。
    """

    category_id: str
    category_name: str
    primary_domain: str
    demand_keywords: tuple[str, ...]
    product_keywords: tuple[str, ...]
    product_path_keywords: tuple[str, ...] = field(default_factory=tuple)
    recommendation_mode: str = "new_sale"
    clarify_questions: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)


DEMAND_CATEGORY_RULES: tuple[DemandCategoryRule, ...] = (
    DemandCategoryRule(
        category_id="internet_store_street",
        category_name="上网-沿街店铺",
        primary_domain="上网",
        demand_keywords=(
            "沿街店铺",
            "路边店",
            "路边商铺",
            "门店",
            "商铺",
            "小商场",
            "餐饮",
            "收银",
            "开店",
            "店铺WiFi",
            "店铺上网",
            "5人以内",
            "五人以内",
            "预算低",
            "3000元以内",
            "低成本宽带",
        ),
        product_keywords=(
            "旺铺宽带",
            "开店宝",
            "开店宝基础版",
            "开店宝升级版",
            "开店宝餐饮",
            "智联",
            "沃商铺",
            "商铺宽带",
        ),
        product_path_keywords=("旺铺宽带", "开店宝", "沃商铺", "商铺宽带"),
        clarify_questions=(
            "客户是沿街店铺、商铺、小商场还是其他经营场所？",
            "大概有多少终端或员工使用？",
            "预算是否在3000元以内，是否需要语音或收银/Wi-Fi能力？",
        ),
        notes=("适合路边、沿街店铺、小商场、5人以内和低预算上网场景。",),
    ),
    DemandCategoryRule(
        category_id="internet_office_dynamic_ip",
        category_name="上网-中小企业动态IP办公",
        primary_domain="上网",
        demand_keywords=(
            "办公室上网",
            "办公室",
            "企业办公",
            "办公上网",
            "企业宽带",
            "商企宽带",
            "中小企业",
            "公司上网",
            "员工上网",
            "动态IP",
            "不需要固定IP",
            "不需要公网IP",
            "5到50人",
            "5-50人",
            "几十人",
            "普通办公",
            "办公网络",
        ),
        product_keywords=(
            "智云上海专线",
            "商企云宽",
            "智云随选商企云宽",
            "智享",
            "沃快车",
            "移动互联网宽带",
            "移动光网宽带",
        ),
        product_path_keywords=("智云上海专线", "商企云宽", "沃快车", "移动互联网宽带", "移动光网宽带"),
        clarify_questions=(
            "客户办公人数或终端规模大概是多少？",
            "是否明确不需要固定公网IP？",
            "期望带宽和预算范围是多少？",
        ),
        notes=("适合5到50人左右、动态IP、普通企业办公上网场景。",),
    ),
    DemandCategoryRule(
        category_id="internet_fixed_ip",
        category_name="上网-固定IP上网",
        primary_domain="上网",
        demand_keywords=(
            "固定IP",
            "固定公网IP",
            "公网IP",
            "公网地址",
            "IP地址",
            "多个IP",
            "IPMAN",
            "服务器对外",
            "企业官网",
            "网站备案",
            "IP报备",
            "直播",
            "视频会议不卡顿",
            "高保障上网",
            "公网出口",
            "互联网专线",
        ),
        product_keywords=(
            "IPMAN",
            "精品专线",
            "智光",
            "沃动车",
            "沃专线",
            "互联网专线",
            "固定IP",
            "IP地址",
        ),
        product_path_keywords=("IPMAN", "精品专线", "智光", "沃动车", "沃专线", "互联网专线"),
        clarify_questions=(
            "客户是否必须要固定公网IP？",
            "大概需要几个公网IP？",
            "是否涉及服务器对外访问、网站备案、直播或视频会议等场景？",
        ),
        notes=("固定IP数量是关键分流条件：IP数量较少和IP数量较多时候选方向不同。",),
    ),
    DemandCategoryRule(
        category_id="network_point_to_point",
        category_name="组网-点对点",
        primary_domain="组网",
        demand_keywords=(
            "点对点",
            "两个点",
            "两点",
            "两地互联",
            "总部到分公司",
            "总部到一个分支",
            "办公室到机房",
            "专线互联",
            "内网互通",
            "本地IPRAN",
            "MSTP",
            "OTN",
            "电路出租",
        ),
        product_keywords=("IPRAN", "MSTP", "OTN", "电路出租", "点对点", "以太专线"),
        product_path_keywords=("IPRAN", "MSTP", "OTN", "电路出租", "点对点", "以太专线"),
        clarify_questions=(
            "是两个固定地址之间互联吗？",
            "两端地址分别在哪里？",
            "需要多大带宽和什么级别的稳定性？",
        ),
        notes=("适合两个固定站点之间的专线互联。",),
    ),
    DemandCategoryRule(
        category_id="network_point_to_multipoint",
        category_name="组网-点对多",
        primary_domain="组网",
        demand_keywords=(
            "点对多",
            "多点",
            "多个分支",
            "多个门店",
            "多门店",
            "总部连多个分支",
            "总部连多个门店",
            "一个中心多个分支",
            "星型组网",
            "爪形",
            "多地互联",
            "多站点",
            "MPLS-VPN",
            "MPLS",
        ),
        product_keywords=("MPLS-VPN", "MPLS", "IPRAN", "点对多", "星型", "爪形"),
        product_path_keywords=("MPLS-VPN", "MPLS", "IPRAN", "点对多"),
        clarify_questions=(
            "一共有几个站点需要互联？",
            "是否是一个总部连接多个分支或门店？",
            "更关注成本还是网状互联能力？",
        ),
        notes=("适合总部连接多个分支、多个门店或多站点互联。",),
    ),
    DemandCategoryRule(
        category_id="network_smart",
        category_name="组网-智能组网",
        primary_domain="组网",
        demand_keywords=(
            "智能组网",
            "SD-WAN",
            "SDWAN",
            "已有宽带",
            "通过设备组网",
            "设备上网",
            "快速组网",
            "灵活组网",
            "公网宽带组网",
            "MSTP-VPN",
            "VPN组网",
            "多分支快速接入",
        ),
        product_keywords=("SD-WAN", "SDWAN", "翼互联", "翼际联", "MSTP-VPN", "智能组网", "VPN"),
        product_path_keywords=("SD-WAN", "SDWAN", "翼互联", "翼际联", "MSTP-VPN", "VPN"),
        clarify_questions=(
            "客户是否已有宽带，希望通过设备快速组网？",
            "涉及多少个分支或门店？",
            "是否需要传统拉线专线，还是可以接受SD-WAN等智能组网方式？",
        ),
        notes=("适合有设备接入、已有宽带、快速部署或灵活多分支组网场景。",),
    ),
)


DEMAND_CATEGORY_RULES_BY_ID = {rule.category_id: rule for rule in DEMAND_CATEGORY_RULES}
DEMAND_CATEGORY_RULES_BY_NAME = {rule.category_name: rule for rule in DEMAND_CATEGORY_RULES}
