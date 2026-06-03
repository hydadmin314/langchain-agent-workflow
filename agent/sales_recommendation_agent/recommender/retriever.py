from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from agent.sales_recommendation_agent.intent_parser.models import CustomerDemand, RouteDecision
from agent.sales_recommendation_agent.product_repository.models import ProductCandidate
from agent.sales_recommendation_agent.recommender.models import (
    CandidateRetrievalResult,
    DemandCategoryMatch,
    RetrievedCandidate,
)


@dataclass(frozen=True)
class DemandCategoryRule:
    """产品需求分类体系中的一条分类规则。"""

    category_id: str
    category_name: str
    demand_keywords: tuple[str, ...]
    product_keywords: tuple[str, ...]
    product_path_keywords: tuple[str, ...] = field(default_factory=tuple)
    route_boosts: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)


# 来自《产品需求分类体系 v1.0》。这里放通用分类、产品族和目录词，不绑定某一份具体文档。
DEMAND_CATEGORY_RULES: tuple[DemandCategoryRule, ...] = (
    DemandCategoryRule(
        category_id="1",
        category_name="企业上网与办公宽带",
        demand_keywords=("办公上网", "办公室网络", "网络慢", "日常上网", "稳定宽带", "预算有限", "办公", "宽带"),
        product_keywords=("商务专线", "精品专线", "智能专线", "智享云宽", "智光专线", "沃商务", "小微上线", "小微在线", "商企云宽"),
        product_path_keywords=("商务专线", "精品专线", "智能专线", "小微", "智享云宽", "智光专线", "沃商务"),
        route_boosts=("flow_dedicated_ip_bandwidth",),
    ),
    DemandCategoryRule(
        category_id="2",
        category_name="固定IP_高带宽_互联网专线",
        demand_keywords=("固定公网IP", "固定IP", "公网IP", "服务器对外", "企业官网", "远程办公入口", "高带宽", "公网地址", "备案", "专线"),
        product_keywords=("IPMAN", "BGP", "精品专线", "商务专线", "网站备案", "PCDN", "互联网专线", "IP地址", "公网IP"),
        product_path_keywords=("IPMAN", "BGP", "精品专线", "商务专线", "网站备案", "PCDN"),
        route_boosts=("flow_dedicated_ip_bandwidth",),
    ),
    DemandCategoryRule(
        category_id="3",
        category_name="国内组网与点对点专线",
        demand_keywords=("总部", "分公司", "互联", "门店组网", "多地互联", "内网专线", "数据中心互联", "点对点", "组网", "互通"),
        product_keywords=("MSTP", "IPRAN", "MPLS", "MPLS-VPN", "SDH", "OTN", "SD-WAN", "点对点", "组网"),
        product_path_keywords=("MSTP", "IPRAN", "MPLS", "SDH", "OTN", "SD-WAN"),
        route_boosts=("flow_domestic_networking",),
    ),
    DemandCategoryRule(
        category_id="4",
        category_name="海外访问与跨境加速",
        demand_keywords=("海外服务器", "美国", "跨境", "海外 SaaS", "海外SaaS", "外贸", "海外站点", "访问海外", "国外", "国际"),
        product_keywords=("精品专线", "IPMAN", "BGP", "SD-WAN", "智能专线", "互联网专线"),
        product_path_keywords=("精品专线", "IPMAN", "BGP", "SD-WAN", "智能专线"),
        route_boosts=("flow_overseas_access",),
        notes=("跨境访问需求如涉及合规跨境专线，必须人工确认，Agent 不直接承诺。",),
    ),
    DemandCategoryRule(
        category_id="5",
        category_name="门店_商铺_小微经营",
        demand_keywords=("门店", "商铺", "收银", "餐饮", "连锁小店", "开店宝", "小微企业", "5G融合", "酒店", "公寓"),
        product_keywords=("旺铺宽带", "长租公寓", "酒店完美联盟", "小微上线", "小微在线", "沃商务企微宽带", "商铺宽带", "开店宝"),
        product_path_keywords=("旺铺宽带", "长租公寓", "酒店", "小微", "沃商务", "商铺"),
    ),
    DemandCategoryRule(
        category_id="6",
        category_name="固定电话_语音中继_呼叫业务",
        demand_keywords=("固定电话", "30B+D", "DID", "中继线", "云中继", "总机", "呼叫中心", "坐席电话", "商云通", "商继通", "长途主叫"),
        product_keywords=("30B+D", "DID", "直线", "商云通", "商继通", "云中继", "IP17969", "固话", "语音"),
        product_path_keywords=("30B+D", "DID", "直线", "商云通", "商继通", "云中继", "IP17969"),
    ),
    DemandCategoryRule(
        category_id="7",
        category_name="移动通信_流量_固移融合",
        demand_keywords=("手机卡", "员工移动套餐", "大流量卡", "5G套餐", "语音流量融合", "宽带+手机", "固移融合", "移动套餐"),
        product_keywords=("5G畅享", "行业大流量", "固移融合", "移动套餐", "流量", "手机卡"),
        product_path_keywords=("5G畅享", "行业大流量", "固移融合", "移动套餐"),
    ),
    DemandCategoryRule(
        category_id="8",
        category_name="云资源_IDC_算力托管",
        demand_keywords=("服务器托管", "云主机", "机房", "IDC", "云资源", "数据中心", "服务器上云", "算力"),
        product_keywords=("天翼云主机", "IDC", "IDC审批", "云资源", "机房", "服务器托管"),
        product_path_keywords=("天翼云主机", "IDC", "IDC审批"),
    ),
    DemandCategoryRule(
        category_id="9",
        category_name="云办公_协同_云电脑",
        demand_keywords=("云电脑", "远程办公", "云桌面", "企业网盘", "在线文档", "视频会议", "办公软件", "云办公"),
        product_keywords=("WPS云文档", "天翼云会议", "天翼云桌面", "天翼云电脑", "企业云盘", "小龙虾云办公"),
        product_path_keywords=("WPS云文档", "天翼云会议", "天翼云桌面", "天翼云电脑", "企业云盘", "云办公"),
    ),
    DemandCategoryRule(
        category_id="10",
        category_name="安全防护_运维代维_托管",
        demand_keywords=("网络安全", "防攻击", "防护", "运维外包", "网络托管", "设备代维", "云网托管", "上门维护"),
        product_keywords=("云堤", "智云护航", "小型集成云网托管", "本地代维", "智能组网", "安全防护"),
        product_path_keywords=("云堤", "智云护航", "云网托管", "本地代维", "智能组网"),
    ),
    DemandCategoryRule(
        category_id="11",
        category_name="行业场景_物联_视频_电梯",
        demand_keywords=("监控", "视频看护", "电梯", "物业", "园区", "行业终端", "图片", "地图", "看店", "看厂"),
        product_keywords=("天翼云眼", "智慧电梯", "天翼云图", "视频监控", "物联"),
        product_path_keywords=("天翼云眼", "智慧电梯", "天翼云图"),
    ),
    DemandCategoryRule(
        category_id="12",
        category_name="营销触达_来电展示_短信录音",
        demand_keywords=("来电显示", "企业名片", "电话录音", "短信触达", "挂机短信", "400电话", "预付费400", "来电展示"),
        product_keywords=("预付费400", "来电名片", "云录音", "挂机短信", "音证宝", "400"),
        product_path_keywords=("预付费400", "来电名片", "云录音", "挂机短信", "音证宝"),
    ),
    DemandCategoryRule(
        category_id="13",
        category_name="办理变更_续约_拆机_撤单",
        demand_keywords=("移机", "过户", "改套餐", "改账单", "付款方式变更", "工商更名", "拆机", "撤单", "续约", "退款"),
        product_keywords=("变更", "拆机", "撤单", "续约", "退款", "移机", "过户", "付费方式", "账单余额"),
        product_path_keywords=("变更", "拆机", "撤单", "续约", "退款", "移机", "过户"),
        notes=("该类属于售后办理流程，通常不走新销售套餐推荐模式。",),
    ),
    DemandCategoryRule(
        category_id="99",
        category_name="停用历史_待确认",
        demand_keywords=("停用", "历史", "旧版", "已停", "停止申请", "待确认"),
        product_keywords=("停用", "历史", "旧版", "已停", "停止申请", "待确认"),
        product_path_keywords=("停用", "历史", "旧版", "已停", "待确认"),
    ),
)


ROUTE_FALLBACK_CATEGORY_IDS: dict[str, tuple[str, ...]] = {
    "flow_overseas_access": ("4",),
    "flow_domestic_networking": ("3",),
    "flow_dedicated_ip_bandwidth": ("2", "1"),
}


class CandidateRetriever:
    """按产品需求分类体系召回候选产品。

    本层只负责“召回可能相关的产品”，不做最终推荐结论，也不做报价核算。
    """

    def __init__(
        self,
        *,
        category_rules: Iterable[DemandCategoryRule] = DEMAND_CATEGORY_RULES,
        min_score: float = 10.0,
        default_top_k: int = 20,
    ):
        self.category_rules = tuple(category_rules)
        self.rules_by_id = {rule.category_id: rule for rule in self.category_rules}
        self.min_score = min_score
        self.default_top_k = default_top_k

    def retrieve(
        self,
        *,
        demand: CustomerDemand,
        decision: RouteDecision,
        products: list[ProductCandidate],
        raw_text: str = "",
        top_k: int | None = None,
    ) -> CandidateRetrievalResult:
        if decision.route == "flow_clarify_requirements":
            return CandidateRetrievalResult(
                route=decision.route,
                total_products=len(products),
                matched_count=0,
                demand_categories=[],
                candidates=[],
                clarify_questions=self._build_clarify_questions(demand),
            )

        demand_categories = self.classify_demand(demand=demand, decision=decision, raw_text=raw_text)
        retrieved: list[RetrievedCandidate] = []

        for product in products:
            candidate = self._score_product(
                product=product,
                demand=demand,
                decision=decision,
                demand_categories=demand_categories,
            )
            if candidate and candidate.retrieval_score >= self.min_score:
                retrieved.append(candidate)

        retrieved.sort(key=lambda item: item.retrieval_score, reverse=True)
        limited = retrieved[: top_k or self.default_top_k]
        return CandidateRetrievalResult(
            route=decision.route,
            total_products=len(products),
            matched_count=len(limited),
            demand_categories=demand_categories,
            candidates=limited,
        )

    def classify_demand(
        self,
        *,
        demand: CustomerDemand,
        decision: RouteDecision,
        raw_text: str = "",
    ) -> list[DemandCategoryMatch]:
        """根据客户需求和 Scenario Router 结果命中分类体系。"""

        demand_text = self._build_demand_text(demand, raw_text=raw_text)
        matches: list[DemandCategoryMatch] = []

        for rule in self.category_rules:
            score = 0.0
            matched_keywords = _matched_keywords(demand_text, rule.demand_keywords)
            if matched_keywords:
                score += 6 * len(matched_keywords)
            if decision.route in rule.route_boosts:
                score += 8
            if score > 0:
                matches.append(
                    DemandCategoryMatch(
                        category_id=rule.category_id,
                        category_name=rule.category_name,
                        score=score,
                        matched_keywords=matched_keywords,
                        reason=self._build_category_reason(rule, matched_keywords, decision.route),
                    )
                )

        if not matches:
            for category_id in ROUTE_FALLBACK_CATEGORY_IDS.get(decision.route, ()):
                rule = self.rules_by_id.get(category_id)
                if rule:
                    matches.append(
                        DemandCategoryMatch(
                            category_id=rule.category_id,
                            category_name=rule.category_name,
                            score=5,
                            matched_keywords=[],
                            reason=f"未命中明确需求关键词，按场景路由 {decision.route} 兜底归类。",
                        )
                    )

        matches.sort(key=lambda item: item.score, reverse=True)
        return matches[:3]

    def _score_product(
        self,
        *,
        product: ProductCandidate,
        demand: CustomerDemand,
        decision: RouteDecision,
        demand_categories: list[DemandCategoryMatch],
    ) -> RetrievedCandidate | None:
        if not product.is_active:
            return None

        search_text = self._build_product_search_text(product)
        score = 0.0
        matched_reasons: list[str] = []
        matched_keywords: list[str] = []
        warnings: list[str] = []

        for category_match in demand_categories:
            rule = self.rules_by_id.get(category_match.category_id)
            if not rule:
                continue

            product_keywords = _matched_keywords(search_text, rule.product_keywords)
            path_keywords = _matched_keywords(search_text, rule.product_path_keywords)
            if product_keywords:
                score += 10 + 2 * len(product_keywords)
                matched_keywords.extend(product_keywords)
                matched_reasons.append(
                    f"命中需求分类「{rule.category_name}」对应产品关键词：{', '.join(product_keywords[:5])}"
                )
            if path_keywords:
                score += 8 + 2 * len(path_keywords)
                matched_keywords.extend(path_keywords)
                matched_reasons.append(
                    f"命中需求分类「{rule.category_name}」对应产品目录/产品族：{', '.join(path_keywords[:5])}"
                )
            if product_keywords or path_keywords:
                score += min(category_match.score, 12)

        if demand.requires_fixed_ip:
            ip_keywords = _matched_keywords(search_text, ("固定IP", "公网IP", "IP地址", "IPMAN", "BGP", "备案"))
            if ip_keywords:
                score += 8
                matched_keywords.extend(ip_keywords)
                matched_reasons.append(f"客户需要固定公网 IP，产品文本命中：{', '.join(ip_keywords[:5])}")

        target_keywords = _target_keywords(demand)
        target_hits = _matched_keywords(search_text, target_keywords)
        if target_hits:
            score += 5
            matched_keywords.extend(target_hits)
            matched_reasons.append(f"命中客户目标区域/场景关键词：{', '.join(target_hits[:5])}")

        bandwidth_hits = self._match_bandwidth(product, demand)
        if bandwidth_hits:
            score += 4
            matched_keywords.extend(bandwidth_hits)
            matched_reasons.append(f"命中客户带宽需求：{', '.join(bandwidth_hits[:5])}")

        if decision.route == "flow_overseas_access" and product.category_path:
            if any(keyword in search_text for keyword in ("精品专线", "IPMAN", "BGP", "SD-WAN", "智能专线")):
                warnings.append("海外/跨境访问涉及合规和资源确认，不能直接承诺跨境专线能力。")

        if not product.packages and not product.optional_packages and not product.fee_rules:
            warnings.append("该产品 JSON 的套餐/可选包/费用规则较少，后续推荐需要人工复核数据完整性。")
            score -= 2

        if score <= 0:
            return None

        return RetrievedCandidate(
            product=product,
            retrieval_score=round(score, 2),
            demand_categories=demand_categories,
            matched_reasons=_dedupe(matched_reasons),
            matched_keywords=_dedupe(matched_keywords),
            warnings=_dedupe(warnings),
        )

    def _build_product_search_text(self, product: ProductCandidate) -> str:
        values: list[str] = [
            product.title,
            product.product_name,
            product.product_family,
            product.carrier,
            product.region,
            product.document_type,
            product.filename,
            product.source_path,
            product.category_path,
            *product.category_levels,
            *product.keywords,
        ]

        for package in product.packages:
            values.extend(
                [
                    package.package_name,
                    package.package_code,
                    package.speed,
                    package.upstream_speed,
                    package.downstream_speed,
                    package.billing_period,
                    package.contract_period,
                    package.source_evidence,
                    *package.applicable_conditions,
                ]
            )

        for optional in product.optional_packages:
            values.extend(
                [
                    optional.name,
                    optional.package_type,
                    optional.category,
                    optional.description,
                    optional.fee_summary,
                    optional.source_evidence,
                    *optional.applicable_conditions,
                ]
            )

        for fee_rule in product.fee_rules:
            values.extend(
                [
                    fee_rule.rule_type,
                    fee_rule.name,
                    fee_rule.description,
                    fee_rule.billing_period,
                    fee_rule.contract_period,
                    fee_rule.source_evidence,
                    *fee_rule.conditions,
                    *fee_rule.applies_to,
                ]
            )

        for constraint in product.constraints:
            values.extend(
                [
                    constraint.constraint_type,
                    constraint.name,
                    constraint.description,
                    constraint.condition,
                    constraint.result,
                    constraint.source_evidence,
                    *constraint.applies_to,
                ]
            )

        return " ".join(value for value in values if value)

    def _build_demand_text(self, demand: CustomerDemand, *, raw_text: str = "") -> str:
        values = [
            raw_text,
            demand.access_source or "",
            demand.target_region or "",
            demand.duration or "",
            demand.scenario_type.value,
            *demand.raw_keywords,
            "固定IP" if demand.requires_fixed_ip else "",
            f"{demand.bandwidth_est_mbps}M" if demand.bandwidth_est_mbps else "",
        ]
        return " ".join(value for value in values if value)

    def _build_category_reason(
        self,
        rule: DemandCategoryRule,
        matched_keywords: list[str],
        route: str,
    ) -> str:
        parts: list[str] = []
        if matched_keywords:
            parts.append(f"命中需求关键词：{', '.join(matched_keywords)}")
        if route in rule.route_boosts:
            parts.append(f"Scenario Router 命中：{route}")
        return "；".join(parts)

    def _match_bandwidth(self, product: ProductCandidate, demand: CustomerDemand) -> list[str]:
        if not demand.bandwidth_est_mbps:
            return []
        expected = f"{demand.bandwidth_est_mbps}M"
        search_text = self._build_product_search_text(product)
        if expected in search_text:
            return [expected]
        if demand.bandwidth_est_mbps >= 1000 and ("1G" in search_text or "千兆" in search_text):
            return ["1G/千兆"]
        return []

    def _build_clarify_questions(self, demand: CustomerDemand) -> list[str]:
        questions_by_field = {
            "access_source": "客户从哪里访问或使用业务？例如上海办公室、门店、总部或分公司。",
            "target_region": "客户要访问哪里？例如国内总部、海外 SaaS、美国服务器或数据中心。",
            "user_count": "预计多少人或多少终端使用？",
            "duration": "客户希望试用、月付、年付还是签约几年？",
            "budget": "客户大致预算是多少？",
        }
        questions = [questions_by_field[field] for field in demand.missing_fields if field in questions_by_field]
        return questions or ["当前需求信息不足，需要补充访问来源、访问目标、人数规模和预算。"]


def _matched_keywords(text: str, keywords: Iterable[str]) -> list[str]:
    normalized = text.lower()
    matched: list[str] = []
    for keyword in keywords:
        if keyword and keyword.lower() in normalized:
            matched.append(keyword)
    return _dedupe(matched)


def _target_keywords(demand: CustomerDemand) -> tuple[str, ...]:
    values: list[str] = []
    if demand.target_region:
        values.append(demand.target_region)
    values.extend(demand.raw_keywords)
    if demand.target_scope.value == "overseas":
        values.extend(["海外", "国外", "国际", "跨境"])
    return tuple(value for value in values if value)


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
