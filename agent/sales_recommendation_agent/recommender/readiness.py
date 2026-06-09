from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from agent.sales_recommendation_agent.intent_parser.config import SalesRecommendationSettings
from agent.sales_recommendation_agent.intent_parser.models import CustomerDemand, DemandCategoryDecision
from agent.sales_recommendation_agent.intent_parser.parsers import extract_json_object, has_explicit_overseas_signal
from prompts.sales_recommendation_agent_prompts import (
    CLARIFICATION_QUESTION_SYSTEM_PROMPT,
    build_clarification_question_user_prompt,
)


ReadinessDecision = Literal["ready", "ready_with_assumptions", "ask_clarification"]


class TermExplanation(BaseModel):
    """专业术语解释。

    Evaluator 只负责告诉大模型“哪些词可能需要解释”，真正怎么说由 LLM 生成。
    """

    term: str = ""
    explanation: str = ""


class ClarificationIntent(BaseModel):
    """结构化追问意图。

    这里不是最终面向客户的话术，而是告诉 LLM：
    - 需要追问哪个字段
    - 为什么要问
    - 可以给客户哪些选项
    - 哪些专业词需要顺手解释
    """

    field: str = ""
    priority: str = "medium"
    intent: str = ""
    why: str = ""
    example_options: list[str] = Field(default_factory=list)
    term_explanations: list[TermExplanation] = Field(default_factory=list)


class ClarificationPlan(BaseModel):
    """给 LLM 追问生成器使用的计划。

    max_questions 用来控制追问数量，避免 Agent 一次问太多，变成问卷体验。
    """

    max_questions: int = 3
    tone: str = "销售顾问式，简洁、自然，不要像问卷。"
    audience: str = "sales_or_customer"
    intents: list[ClarificationIntent] = Field(default_factory=list)


class ReadinessResult(BaseModel):
    """最低推荐条件判断结果。

    decision 含义：
    - ready：最低条件已满足，直接进入推荐。
    - ready_with_assumptions：可以初步推荐，但要带假设说明。
    - ask_clarification：缺少最低条件，当前轮先追问，不进入推荐。
    """

    decision: ReadinessDecision
    missing_conditions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    clarification_plan: ClarificationPlan = Field(default_factory=ClarificationPlan)
    reason: str = ""
    product_lock_hints: list[str] = Field(default_factory=list)


class ClarificationQuestionResult(BaseModel):
    """LLM 生成的人性化追问结果。"""

    message: str = ""
    questions: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)


class ReadinessRule(BaseModel):
    """单个主分类的最低推荐条件配置。

    required_all：里面的字段必须全部有值。
    required_any：每个小组至少有一个字段有值。
    assumable：字段缺失时仍可先推荐，但必须在推荐结果中展示假设。
    clarification_intents：字段缺失时给 LLM 的追问意图。
    product_lock_hints：给后续 Product Lock / Retriever 的候选方向提示。
    """

    required_all: list[str] = Field(default_factory=list)
    required_any: list[list[str]] = Field(default_factory=list)
    assumable: dict[str, str] = Field(default_factory=dict)
    default_assumptions: list[str] = Field(default_factory=list)
    clarification_intents: dict[str, ClarificationIntent] = Field(default_factory=dict)
    product_lock_hints: list[str] = Field(default_factory=list)


def build_readiness_rules() -> dict[str, ReadinessRule]:
    """构建 13 类产品需求的最低推荐条件。

    这份配置是后续业务讨论最容易调整的地方。开发上尽量把规则集中在这里，
    避免散落到 Retriever、Scorer 或 Prompt 中。
    """

    fixed_ip_explanation = TermExplanation(
        term="固定公网 IP",
        explanation="固定不变的公网地址，常用于服务器对外访问、远程连接或固定出口。",
    )

    return {
        "企业上网与办公宽带": ReadinessRule(
            required_any=[["usage_scene", "user_count"]],
            assumable={
                "fixed_ip_required": "暂按不需要固定公网 IP 处理。",
                "voice_required": "暂按不需要语音/固定电话能力处理。",
            },
            clarification_intents={
                "usage_scene": ClarificationIntent(
                    field="usage_scene",
                    priority="high",
                    intent="确认办公上网使用场景",
                    why="需要判断是普通办公室上网、门店经营，还是其他网络场景。",
                    example_options=["办公室员工上网", "门店经营", "多点组网"],
                ),
                "user_count": ClarificationIntent(
                    field="user_count",
                    priority="high",
                    intent="确认使用规模",
                    why="人数会影响套餐档位、带宽规模和后续预算匹配。",
                    example_options=["10人以内", "10-50人", "50人以上"],
                ),
                "fixed_ip_required": ClarificationIntent(
                    field="fixed_ip_required",
                    priority="medium",
                    intent="确认是否需要固定公网 IP",
                    why="如果需要固定公网 IP，候选产品可能转向 IPMAN、精品专线等方向。",
                    example_options=["需要", "不需要", "暂不确定"],
                    term_explanations=[fixed_ip_explanation],
                ),
                "voice_required": ClarificationIntent(
                    field="voice_required",
                    priority="medium",
                    intent="确认是否需要语音/固定电话能力",
                    why="是否带语音会影响商务专线等套餐版本筛选。",
                    example_options=["需要", "不需要", "暂不确定"],
                ),
            },
            product_lock_hints=["商务专线", "精品专线", "智能专线"],
        ),
        "固定IP_高带宽_互联网专线": ReadinessRule(
            required_any=[["fixed_ip_required", "server_or_idc_required", "primary_goal"]],
            clarification_intents={
                "fixed_ip_required": ClarificationIntent(
                    field="fixed_ip_required",
                    priority="high",
                    intent="确认是否需要固定公网 IP",
                    why="固定公网 IP 是本分类的核心条件，不能默认假设。",
                    example_options=["服务器对外访问", "办公固定出口", "不确定"],
                    term_explanations=[fixed_ip_explanation],
                ),
                "server_or_idc_required": ClarificationIntent(
                    field="server_or_idc_required",
                    priority="high",
                    intent="确认是否涉及服务器、网站或 IDC",
                    why="服务器对外访问、网站上线和办公固定出口会影响 IPMAN、BGP&IPMAN、精品专线的选择。",
                    example_options=["服务器对外访问", "网站上线", "办公固定出口"],
                ),
                "bandwidth_need": ClarificationIntent(
                    field="bandwidth_need",
                    priority="medium",
                    intent="确认带宽规模",
                    why="带宽会影响套餐档位、价格区间和资源确认。",
                    example_options=["100M", "200M", "500M", "1G"],
                ),
            },
            product_lock_hints=["IPMAN", "BGP&IPMAN", "精品专线"],
        ),
        "国内组网与点对点专线": ReadinessRule(
            required_any=[["site_count", "usage_scene"]],
            assumable={"server_or_idc_required": "暂按不涉及服务器或 IDC 场景处理。"},
            clarification_intents={
                "site_count": ClarificationIntent(
                    field="site_count",
                    priority="high",
                    intent="确认点对点还是多点互联",
                    why="站点数量和组网形态会决定 MPLS-VPN、SD-WAN、MSTP、IPRAN、OTN 等候选方向。",
                    example_options=["点对点", "总部+分支", "多门店/多站点"],
                ),
                "usage_scene": ClarificationIntent(
                    field="usage_scene",
                    priority="high",
                    intent="确认组网用途",
                    why="需要判断是纯内网互联、互联网访问，还是重要业务传输。",
                    example_options=["纯内网互联", "多门店互联", "重要业务传输"],
                ),
            },
            product_lock_hints=["MPLS-VPN", "SD-WAN", "MSTP", "IPRAN", "OTN"],
        ),
        "海外访问与跨境加速": ReadinessRule(
            required_any=[["overseas_target", "primary_goal"], ["usage_scene", "site_count"]],
            assumable={
                "fixed_ip_required": "暂按不需要固定公网 IP 处理。",
                "site_count": "暂按单个办公地点访问处理。",
            },
            default_assumptions=["暂按不要求明确跨境专线合规承诺处理。"],
            clarification_intents={
                "overseas_target": ClarificationIntent(
                    field="overseas_target",
                    priority="high",
                    intent="确认海外访问目标",
                    why="访问海外 SaaS、自有海外服务器或普通海外网页，对候选产品方向影响很大。",
                    example_options=["海外 SaaS", "自有海外服务器", "外贸网站", "普通海外网页"],
                ),
                "primary_goal": ClarificationIntent(
                    field="primary_goal",
                    priority="high",
                    intent="确认客户主要想解决的问题",
                    why="需要确认是海外访问慢、服务器对外访问，还是普通办公上网。",
                    example_options=["海外访问慢", "服务器对外访问", "普通办公上网"],
                ),
                "usage_scene": ClarificationIntent(
                    field="usage_scene",
                    priority="high",
                    intent="确认是单点还是多地访问",
                    why="单办公室和多地访问会影响精品专线、智能专线、SD-WAN 等候选方向。",
                    example_options=["单个办公室", "多个门店/分支", "总部+分支"],
                ),
                "site_count": ClarificationIntent(
                    field="site_count",
                    priority="high",
                    intent="确认使用地点数量",
                    why="多地访问海外 SaaS 时，可能需要评估 SD-WAN 或组网方案。",
                    example_options=["单点", "多点", "总部+分支"],
                ),
                "fixed_ip_required": ClarificationIntent(
                    field="fixed_ip_required",
                    priority="high",
                    intent="确认是否需要固定公网 IP",
                    why="如果访问自有海外服务器且需要固定出口，候选产品可能转向 IPMAN 或 BGP&IPMAN。",
                    example_options=["需要", "不需要", "暂不确定"],
                    term_explanations=[fixed_ip_explanation],
                ),
            },
            product_lock_hints=["精品专线", "智能专线", "商务专线", "IPMAN", "BGP&IPMAN", "SD-WAN"],
        ),
        "门店_商铺_小微经营": ReadinessRule(
            required_any=[["usage_scene", "industry_scene"]],
            assumable={
                "voice_required": "暂按不需要语音处理。",
                "fixed_ip_required": "暂按不需要固定公网 IP 处理。",
            },
            clarification_intents={
                "usage_scene": ClarificationIntent(
                    field="usage_scene",
                    priority="high",
                    intent="确认门店或小微经营场景",
                    why="单门店、连锁门店、酒店公寓等场景会影响候选产品范围。",
                    example_options=["单门店", "连锁门店", "酒店/公寓", "小微线上业务"],
                ),
                "industry_scene": ClarificationIntent(
                    field="industry_scene",
                    priority="medium",
                    intent="确认是否有行业经营场景",
                    why="收银、WiFi、云宽、5G 融合等需求会影响旺铺宽带、开店宝等产品选择。",
                    example_options=["收银", "门店 WiFi", "5G 融合", "云宽"],
                ),
                "voice_required": ClarificationIntent(
                    field="voice_required",
                    priority="medium",
                    intent="确认是否需要语音/固定电话",
                    why="门店是否需要语音会影响是否筛选带语音版本或联动语音产品。",
                    example_options=["需要", "不需要", "暂不确定"],
                ),
                "fixed_ip_required": ClarificationIntent(
                    field="fixed_ip_required",
                    priority="medium",
                    intent="确认是否需要固定公网 IP",
                    why="如需固定公网 IP，候选产品可能需要联动专线或固定 IP 类产品。",
                    example_options=["需要", "不需要", "暂不确定"],
                    term_explanations=[fixed_ip_explanation],
                ),
            },
            product_lock_hints=["旺铺宽带", "开店宝", "小微上线", "小微在线", "沃商务企微宽带"],
        ),
        "固定电话_语音中继_呼叫业务": ReadinessRule(
            required_any=[["voice_required", "primary_goal", "concurrent_calls"]],
            assumable={"concurrent_calls": "暂按并发数量未明确处理，先按语音类候选方向评估。"},
            clarification_intents={
                "voice_required": ClarificationIntent(
                    field="voice_required",
                    priority="high",
                    intent="确认语音业务类型",
                    why="普通固定电话、多路并发、云中继会对应不同候选产品。",
                    example_options=["普通固定电话", "呼叫中心/多路并发", "云化语音"],
                ),
                "concurrent_calls": ClarificationIntent(
                    field="concurrent_calls",
                    priority="high",
                    intent="确认号码数或并发数",
                    why="并发数会影响直线、30B+D、DID、云中继等产品选择。",
                    example_options=["少量号码", "10路并发", "30B+D", "更多并发"],
                ),
            },
            product_lock_hints=["直线", "30B+D", "DID", "云中继", "商云通", "商继通"],
        ),
        "移动通信_流量_固移融合": ReadinessRule(
            required_any=[["user_count", "industry_scene", "primary_goal"]],
            assumable={"voice_required": "暂按主要关注流量和移动通信能力处理。"},
            clarification_intents={
                "user_count": ClarificationIntent(
                    field="user_count",
                    priority="high",
                    intent="确认卡数或使用规模",
                    why="员工手机卡、设备流量卡和批量办理材料都依赖数量规模。",
                    example_options=["员工手机卡数量", "设备卡数量", "不确定"],
                ),
                "primary_goal": ClarificationIntent(
                    field="primary_goal",
                    priority="high",
                    intent="确认移动通信需求类型",
                    why="需要判断是员工手机卡、行业流量卡，还是宽带手机融合。",
                    example_options=["员工手机卡", "设备/行业流量卡", "固移融合"],
                ),
            },
            product_lock_hints=["5G畅享", "行业大流量", "固移融合套餐"],
        ),
        "云资源_IDC_算力托管": ReadinessRule(
            required_any=[["server_or_idc_required", "primary_goal"]],
            clarification_intents={
                "server_or_idc_required": ClarificationIntent(
                    field="server_or_idc_required",
                    priority="high",
                    intent="确认云资源或 IDC 类型",
                    why="云主机、物理托管、IDC 审批和线路接入会走不同候选方向。",
                    example_options=["云服务器", "物理服务器托管", "IDC 机房", "算力资源"],
                ),
            },
            product_lock_hints=["天翼云主机", "IDC", "IPMAN", "MSTP", "MPLS-VPN", "OTN"],
        ),
        "云办公_协同_云电脑": ReadinessRule(
            required_any=[["cloud_office_required", "primary_goal"]],
            clarification_intents={
                "cloud_office_required": ClarificationIntent(
                    field="cloud_office_required",
                    priority="high",
                    intent="确认云办公能力类型",
                    why="云电脑、云盘、会议、文档协同对应不同产品方向。",
                    example_options=["云电脑/云桌面", "云盘", "会议", "文档协同"],
                ),
            },
            product_lock_hints=["天翼云电脑", "天翼企业云盘", "天翼云会议", "WPS云文档"],
        ),
        "安全防护_运维代维_托管": ReadinessRule(
            required_any=[["security_required", "primary_goal"]],
            clarification_intents={
                "security_required": ClarificationIntent(
                    field="security_required",
                    priority="high",
                    intent="确认安全或运维需求类型",
                    why="防攻击、代维、网络托管、智能组网对应不同候选产品。",
                    example_options=["防攻击", "日常运维", "上门维护", "网络托管/优化"],
                ),
            },
            product_lock_hints=["云堤", "智云护航", "本地代维", "智能组网", "云网托管"],
        ),
        "行业场景_物联_视频_电梯": ReadinessRule(
            required_any=[["industry_scene", "primary_goal"]],
            clarification_intents={
                "industry_scene": ClarificationIntent(
                    field="industry_scene",
                    priority="high",
                    intent="确认行业场景",
                    why="视频监控、电梯物联、地图图像等场景会锁定不同产品。",
                    example_options=["视频监控", "门店看护", "物业电梯", "园区物联"],
                ),
            },
            product_lock_hints=["天翼云眼", "智慧电梯", "天翼云图"],
        ),
        "营销触达_来电展示_短信录音": ReadinessRule(
            required_any=[["marketing_touch_required", "primary_goal"]],
            clarification_intents={
                "marketing_touch_required": ClarificationIntent(
                    field="marketing_touch_required",
                    priority="high",
                    intent="确认营销触达能力类型",
                    why="400、来电名片、挂机短信、云录音分别对应不同候选产品。",
                    example_options=["400热线", "来电名片", "通话录音", "挂机短信"],
                ),
            },
            product_lock_hints=["预付费400", "来电名片", "云录音", "音证宝", "挂机短信"],
        ),
        "办理变更_续约_拆机_撤单": ReadinessRule(
            required_all=["business_action"],
            required_any=[["primary_goal", "usage_scene"]],
            clarification_intents={
                "business_action": ClarificationIntent(
                    field="business_action",
                    priority="high",
                    intent="确认办理动作",
                    why="第 13 类不进入普通新销售推荐，需要先确定办理动作。",
                    example_options=["移机", "过户", "改套餐", "拆机", "撤单", "续约"],
                ),
                "primary_goal": ClarificationIntent(
                    field="primary_goal",
                    priority="high",
                    intent="确认已有产品或业务",
                    why="办理流程和材料清单依赖已有产品名称和合同状态。",
                    example_options=["已有产品名称", "合同是否已签", "是否涉及退款/押金"],
                ),
            },
            product_lock_hints=["办理流程模式", "材料清单", "规则说明"],
        ),
    }


READINESS_RULES = build_readiness_rules()


class RecommendationReadinessEvaluator:
    """推荐前最低条件判断器。

    本模块只做确定性判断：
    - 当前主分类有没有满足最低推荐条件
    - 缺什么关键条件
    - 哪些条件可以先用假设进入初步推荐

    它不调用大模型，也不负责生成最终销售话术。
    """

    def __init__(self, rules: dict[str, ReadinessRule] | None = None):
        self.rules = rules or READINESS_RULES

    def evaluate(
        self,
        *,
        demand: CustomerDemand,
        category_decision: DemandCategoryDecision,
    ) -> ReadinessResult:
        """根据主分类检查当前 customer_need 是否足够进入推荐。"""

        category_name = category_decision.primary_category_name or demand.primary_category
        rule = self.rules.get(category_name)
        if not rule:
            return ReadinessResult(
                decision="ask_clarification",
                missing_conditions=["primary_category"],
                clarification_plan=ClarificationPlan(
                    intents=[
                        ClarificationIntent(
                            field="primary_goal",
                            priority="high",
                            intent="确认客户主要想解决的问题",
                            why="当前无法稳定判断 13 类产品需求主分类。",
                            example_options=["办公室上网", "固定 IP", "多点组网", "海外访问", "语音", "办理变更"],
                        )
                    ]
                ),
                reason="当前无法匹配到明确的 13 类主分类，需要先确认客户主要需求。",
            )

        if category_decision.primary_category_id == "4" and not has_explicit_overseas_signal(
            " ".join(
                [
                    demand.primary_goal or "",
                    demand.usage_scene or "",
                    demand.overseas_target or "",
                    demand.region or "",
                    *demand.raw_keywords,
                ]
            )
        ):
            return ReadinessResult(
                decision="ask_clarification",
                missing_conditions=["overseas_target"],
                clarification_plan=ClarificationPlan(
                    intents=[
                        ClarificationIntent(
                            field="overseas_target",
                            priority="high",
                            intent="确认访问目标是否在境外",
                            why="当前主分类为海外访问，但需求字段里没有明确境外目标，可能是国内异地或总部分支访问。",
                            example_options=["国内异地系统", "海外 SaaS", "国外服务器", "还不确定"],
                        )
                    ]
                ),
                reason="海外访问类缺少明确境外目标，需先确认是国内异地访问还是海外/跨境访问。",
                product_lock_hints=rule.product_lock_hints,
            )

        missing_conditions = self._collect_missing_conditions(demand, rule)
        if missing_conditions:
            return ReadinessResult(
                decision="ask_clarification",
                missing_conditions=missing_conditions,
                clarification_plan=self._build_clarification_plan(
                    missing_conditions=missing_conditions,
                    rule=rule,
                ),
                reason=f"缺少进入「{category_name}」推荐的最低条件。",
                product_lock_hints=rule.product_lock_hints,
            )

        assumptions = self._collect_assumptions(demand, rule)
        decision: ReadinessDecision = "ready_with_assumptions" if assumptions else "ready"
        return ReadinessResult(
            decision=decision,
            assumptions=assumptions,
            clarification_plan=self._build_assumption_confirmation_plan(demand=demand, rule=rule),
            reason=(
                f"已满足「{category_name}」最低推荐条件，可带假设进入初步推荐。"
                if assumptions
                else f"已满足「{category_name}」最低推荐条件，可以进入产品推荐。"
            ),
            product_lock_hints=rule.product_lock_hints,
        )

    def _collect_missing_conditions(self, demand: CustomerDemand, rule: ReadinessRule) -> list[str]:
        """收集真正阻断推荐的最低条件缺口。"""

        missing: list[str] = []
        for field_name in rule.required_all:
            if is_customer_need_empty(demand, field_name):
                missing.append(field_name)

        for group in rule.required_any:
            if all(is_customer_need_empty(demand, field_name) for field_name in group):
                missing.append(" / ".join(group))
        return missing

    def _collect_assumptions(self, demand: CustomerDemand, rule: ReadinessRule) -> list[str]:
        """收集可带假设推荐的说明。

        注意：这里不写回 customer_need，只在 ReadinessResult 中展示假设。
        """

        assumptions = list(rule.default_assumptions)
        for field_name, assumption in rule.assumable.items():
            if is_customer_need_empty(demand, field_name):
                assumptions.append(assumption)
        return dedupe_texts(assumptions)

    def _build_clarification_plan(
        self,
        *,
        missing_conditions: list[str],
        rule: ReadinessRule,
    ) -> ClarificationPlan:
        """根据缺失条件构造 LLM 追问计划。"""

        intents: list[ClarificationIntent] = []
        for condition in missing_conditions:
            for field_name in split_condition_fields(condition):
                intent = rule.clarification_intents.get(field_name)
                if intent:
                    intents.append(intent)
        return ClarificationPlan(intents=dedupe_intents(intents)[:3])

    def _build_assumption_confirmation_plan(
        self,
        *,
        demand: CustomerDemand,
        rule: ReadinessRule,
    ) -> ClarificationPlan:
        """给 ready_with_assumptions 场景构造“可选确认”的追问计划。"""

        intents: list[ClarificationIntent] = []
        for field_name in rule.assumable:
            if is_customer_need_empty(demand, field_name):
                intent = rule.clarification_intents.get(field_name)
                if intent:
                    intents.append(intent)
        return ClarificationPlan(intents=dedupe_intents(intents)[:3])


class ClarificationQuestionGenerator:
    """把结构化追问计划转成自然语言。

    程序只判断“缺什么”，这里负责让大模型把追问说得像销售顾问。
    如果没有配置 LLM，则使用本地模板兜底，保证测试和离线场景能跑通。
    """

    def __init__(
        self,
        settings: SalesRecommendationSettings | None = None,
        *,
        client: Any | None = None,
    ):
        self.settings = settings or SalesRecommendationSettings.from_env()
        self.client = client

    def generate(
        self,
        *,
        query: str,
        demand: CustomerDemand,
        category_decision: DemandCategoryDecision,
        readiness_result: ReadinessResult,
    ) -> ClarificationQuestionResult:
        """生成自然语言追问。"""

        if not readiness_result.clarification_plan.intents:
            return ClarificationQuestionResult(message="", questions=[], fields=[])

        if not self.settings.llm_api_key and self.client is None:
            return build_fallback_clarification(readiness_result)

        payload = {
            "query": query,
            "customer_need": demand.model_dump(mode="json"),
            "category": category_decision.model_dump(mode="json"),
            "readiness": readiness_result.model_dump(mode="json"),
        }
        prompt = build_clarification_question_user_prompt(payload)
        try:
            response_text = self._invoke_llm(prompt)
            result = parse_clarification_result(response_text)
            return normalize_llm_clarification_result(result=result, readiness_result=readiness_result)
        except Exception:
            # 追问生成失败不应该中断推荐流程；使用本地模板兜底。
            return build_fallback_clarification(readiness_result)

    def _invoke_llm(self, prompt: str) -> str:
        """调用 OpenAI-compatible Chat Completions 接口。"""

        client = self.client or self._build_client()
        response = client.chat.completions.create(**self._build_request_kwargs(prompt))
        return response.choices[0].message.content or "{}"

    def _build_client(self) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("openai package is not installed") from exc

        client_kwargs: dict[str, Any] = {"api_key": self.settings.llm_api_key}
        if self.settings.llm_base_url:
            client_kwargs["base_url"] = self.settings.llm_base_url
        return OpenAI(**client_kwargs)

    def _build_request_kwargs(self, prompt: str) -> dict[str, Any]:
        request_kwargs: dict[str, Any] = {
            "model": self.settings.llm_model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": CLARIFICATION_QUESTION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.settings.llm_temperature,
            "max_tokens": min(max(self.settings.llm_max_tokens, 600), 1200),
            "timeout": self.settings.llm_timeout_seconds,
            "stream": False,
        }

        # 兼容 DashScope/Qwen 的可选开关；未配置时不传，避免影响其他兼容服务。
        extra_body: dict[str, Any] = {}
        if self.settings.llm_enable_thinking is not None:
            extra_body["enable_thinking"] = self.settings.llm_enable_thinking
        if self.settings.llm_enable_search is not None:
            extra_body["enable_search"] = self.settings.llm_enable_search
        if extra_body:
            request_kwargs["extra_body"] = extra_body
        return request_kwargs


def parse_clarification_result(content: str) -> ClarificationQuestionResult:
    """解析并校验 LLM 追问输出。"""

    try:
        payload = extract_json_object(content)
        return ClarificationQuestionResult.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise RuntimeError(f"Failed to parse clarification question: {exc}") from exc


def normalize_llm_clarification_result(
    *,
    result: ClarificationQuestionResult,
    readiness_result: ReadinessResult,
) -> ClarificationQuestionResult:
    """收敛 LLM 追问输出，避免模型自由发挥。

    真实 LLM 偶尔会出现两类问题：
    - 只说“我需要确认几个问题”，但没有给出具体问题。
    - fields 输出了 clarification_plan 之外的字段。

    这里做轻量校正：只保留计划内字段；如果问题为空或话术太泛，则回退到本地兜底追问。
    """

    allowed_fields = {
        intent.field
        for intent in readiness_result.clarification_plan.intents
        if intent.field
    }
    if not allowed_fields:
        return result

    result.fields = [field for field in result.fields if field in allowed_fields]
    if not result.fields:
        result.fields = [
            intent.field
            for intent in readiness_result.clarification_plan.intents[
                : readiness_result.clarification_plan.max_questions
            ]
            if intent.field
        ]

    if not result.questions:
        return build_fallback_clarification(readiness_result)

    # 如果模型问的问题数量超过计划内字段数量，通常意味着它开始自由扩展了。
    # 这类输出宁可回退到本地兜底，也不要让 Agent 问一堆无关问题。
    if len(result.questions) > len(result.fields):
        return build_fallback_clarification(readiness_result)

    # 如果 message 只是泛泛开头，没有包含任何问句，就把 questions 追加进去。
    if "？" not in result.message and "?" not in result.message:
        result.message = f"{result.message.rstrip('。')}：" + " ".join(result.questions)

    return result


def build_fallback_clarification(readiness_result: ReadinessResult) -> ClarificationQuestionResult:
    """离线兜底追问。

    真实对话中优先由 LLM 生成自然话术；这里用于测试、API 失败或未配置 API key 的场景。
    """

    questions: list[str] = []
    fields: list[str] = []
    for intent in readiness_result.clarification_plan.intents[: readiness_result.clarification_plan.max_questions]:
        fields.append(intent.field)
        options = f"可以按这些方向说：{'、'.join(intent.example_options)}。" if intent.example_options else ""
        explanations = " ".join(
            f"{item.term}简单说就是：{item.explanation}"
            for item in intent.term_explanations
            if item.term and item.explanation
        )
        question_core = build_human_fallback_question(intent)
        questions.append(f"{question_core}{options}{explanations}".strip())

    prefix = "为了更准确地锁定产品，我再确认几个关键信息："
    if readiness_result.decision == "ready_with_assumptions" and readiness_result.assumptions:
        readable_assumptions = "；".join(
            item.replace("暂按", "").replace("处理。", "").strip("。")
            for item in readiness_result.assumptions
        )
        prefix = f"我可以先按{readable_assumptions}来做初步推荐。为了后续更准确，我再确认："
    return ClarificationQuestionResult(message=prefix + " ".join(questions), questions=questions, fields=fields)


def build_human_fallback_question(intent: ClarificationIntent) -> str:
    """把结构化追问意图转成相对自然的兜底问法。"""

    if intent.field == "primary_goal":
        return "客户主要想解决哪类问题？"
    if intent.field == "usage_scene":
        return "客户主要在什么场景下使用？"
    if intent.field == "site_count":
        return "这是单个地点使用，还是多个地点都要用？"
    if intent.field == "fixed_ip_required":
        return "是否需要固定公网 IP？"
    if intent.field == "voice_required":
        return "是否需要语音或固定电话能力？"
    if intent.field == "concurrent_calls":
        return "大概需要多少路并发或多少个号码？"
    if intent.field == "overseas_target":
        return "主要访问的是海外网站、海外 SaaS，还是自有海外服务器？"
    if intent.field == "business_action":
        return "客户这次是新装、变更、拆机、撤单还是续约？"
    return f"{intent.intent}？"


def is_customer_need_empty(demand: CustomerDemand, field_name: str) -> bool:
    """判断 customer_need 字段是否为空。

    False 是明确回答“不需要”，不能当作空值；None 才表示暂不确定。
    """

    if not hasattr(demand, field_name):
        return True
    value = getattr(demand, field_name)
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        normalized = value.strip()
        return normalized == "" or normalized in {"未知", "不确定", "暂不确定"}
    if isinstance(value, list):
        return not value
    return False


def split_condition_fields(condition: str) -> list[str]:
    """把 'usage_scene / site_count' 这类缺口拆回字段名。"""

    return [item.strip() for item in condition.split("/") if item.strip()]


def dedupe_intents(intents: list[ClarificationIntent]) -> list[ClarificationIntent]:
    """按 field 去重，保留首次出现的高优先级追问意图。"""

    seen: set[str] = set()
    result: list[ClarificationIntent] = []
    for intent in intents:
        key = intent.field
        if key and key not in seen:
            seen.add(key)
            result.append(intent)
    return result


def dedupe_texts(values: list[str]) -> list[str]:
    """文本去重，避免假设和风险提醒重复展示。"""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
