from __future__ import annotations

from agent.sales_recommendation_agent.config import SalesRecommendationSettings
from agent.sales_recommendation_agent.models import CustomerDemand, RegionScope, RouteDecision, ScenarioType


class ScenarioRouter:
    """把结构化需求路由到后续产品推荐分支。

    这里先只做确定性分流，不直接推荐产品，也不做报价核算。
    """

    def __init__(self, settings: SalesRecommendationSettings):
        self.settings = settings

    def route(self, demand: CustomerDemand) -> RouteDecision:
        matched_rules: list[str] = []

        if (
            demand.requires_fixed_ip
            or demand.bandwidth_est_mbps > self.settings.high_bandwidth_threshold_mbps
            or demand.scenario_type == ScenarioType.dedicated_ip_or_high_bandwidth
        ):
            matched_rules.append("requires_fixed_ip_or_bandwidth_gt_threshold")
            return RouteDecision(
                route="flow_dedicated_ip_bandwidth",
                action="进入固定 IP / 高带宽 / 专线类产品推荐分支",
                matched_rules=matched_rules,
                priority=90,
                reason=(
                    "需求包含固定 IP、公网 IP、专线语义，或估算带宽超过 "
                    f"{self.settings.high_bandwidth_threshold_mbps} Mbps。"
                ),
            )

        if demand.target_scope == RegionScope.overseas or demand.scenario_type == ScenarioType.overseas_access:
            matched_rules.append("domestic_source_to_overseas_destination")
            return RouteDecision(
                route="flow_overseas_access",
                action="进入海外访问类产品推荐分支",
                matched_rules=matched_rules,
                priority=80,
                reason="目标位置或应用被识别为海外，适合进入海外访问方案分支。",
            )

        if (
            demand.source_scope == RegionScope.domestic
            and demand.target_scope in {RegionScope.domestic, RegionScope.unknown}
        ) or demand.scenario_type == ScenarioType.domestic_networking:
            matched_rules.append("domestic_network_or_multi_site")
            return RouteDecision(
                route="flow_domestic_networking",
                action="进入国内多点组网类产品推荐分支",
                matched_rules=matched_rules,
                priority=70,
                reason="来源和目标均偏国内，或文本包含国内组网、多点互通语义。",
            )

        matched_rules.append("insufficient_structured_fields")
        return RouteDecision(
            route="flow_clarify_requirements",
            action="进入需求澄清分支",
            matched_rules=matched_rules,
            priority=30,
            reason="缺少足够的来源、目标或规模信息，建议先向销售侧回收关键字段。",
        )
