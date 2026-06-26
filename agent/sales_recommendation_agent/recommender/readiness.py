from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from agent.sales_recommendation_agent.intent_parser.config import SalesRecommendationSettings
from agent.sales_recommendation_agent.intent_parser.models import CustomerDemand, DemandCategoryDecision
from agent.sales_recommendation_agent.intent_parser.parsers import extract_json_object
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
    """构建新六类产品需求的最低推荐条件。

    这份配置是后续业务讨论最容易调整的地方。开发上尽量把规则集中在这里，
    避免散落到 Retriever、Scorer 或 Prompt 中。
    """

    fixed_ip_explanation = TermExplanation(
        term="固定公网 IP",
        explanation="固定不变的公网地址，常用于服务器对外访问、远程连接或固定出口。",
    )
    bandwidth_explanation = TermExplanation(
        term="带宽",
        explanation="可以理解为网络通道大小。客户不确定时，可先按人数、应用类型和是否有视频会议/大文件/服务器访问来估算。",
    )

    return {
        "上网-沿街店铺": ReadinessRule(
            required_any=[["usage_scene", "industry_scene", "primary_goal", "raw_keywords"]],
            assumable={
                "bandwidth_need": "客户未明确带宽，暂按沿街店铺、小商铺常见收银、Wi-Fi、监控场景做初步推荐，后续需确认 50M/100M 等档位。",
                "carrier_preference": "客户未明确运营商偏好，暂按无偏好处理。",
                "fixed_ip_required": "暂按不需要固定公网 IP 处理。",
            },
            clarification_intents={
                "usage_scene": ClarificationIntent(
                    field="usage_scene",
                    priority="high",
                    intent="确认是否属于沿街店铺或小商铺场景",
                    why="沿街店铺通常更关注低成本、快速开通、收银/Wi-Fi/监控等基础联网能力。",
                    example_options=["沿街店铺", "小商铺", "餐饮零售", "单店办公"],
                ),
                "bandwidth_need": ClarificationIntent(
                    field="bandwidth_need",
                    priority="medium",
                    intent="确认门店带宽或让客户按场景估算",
                    why="带宽会影响套餐档位；客户不确定时，可按收银、访客 Wi-Fi、监控数量来建议。",
                    example_options=["50M", "100M", "200M", "客户不确定，按门店规模建议"],
                    term_explanations=[bandwidth_explanation],
                ),
                "carrier_preference": ClarificationIntent(
                    field="carrier_preference",
                    priority="low",
                    intent="确认是否有运营商偏好",
                    why="运营商不是产品需求分类，但会影响候选产品筛选。",
                    example_options=["电信", "联通", "移动", "无偏好"],
                ),
            },
            product_lock_hints=["旺铺宽带", "智联", "开店宝", "沿街店铺宽带"],
        ),
        "上网-中小企业动态IP办公": ReadinessRule(
            required_any=[["usage_scene", "user_count"]],
            assumable={
                "bandwidth_need": "客户未明确带宽，暂按人数和办公场景做初步推荐，推荐结果需提示后续确认 100M/200M/500M 等档位。",
                "fixed_ip_required": "暂按不需要固定公网 IP 处理。",
                "carrier_preference": "客户未明确运营商偏好，暂按无偏好处理。",
            },
            clarification_intents={
                "usage_scene": ClarificationIntent(
                    field="usage_scene",
                    priority="high",
                    intent="确认办公上网使用场景",
                    why="需要判断是中小企业办公动态 IP，还是固定 IP、组网等更高阶需求。",
                    example_options=["办公室员工上网", "普通办公宽带", "多人日常上网"],
                ),
                "user_count": ClarificationIntent(
                    field="user_count",
                    priority="high",
                    intent="确认使用规模",
                    why="人数会影响套餐档位、带宽规模和后续预算匹配。",
                    example_options=["10人以内", "10-50人", "50人以上"],
                ),
                "bandwidth_need": ClarificationIntent(
                    field="bandwidth_need",
                    priority="medium",
                    intent="确认期望带宽或让客户按场景估算",
                    why="带宽会影响套餐档位、价格和体验；如果客户不知道，可以按人数和应用来给区间建议。",
                    example_options=["50M", "100M", "200M", "客户不确定，按人数建议"],
                    term_explanations=[bandwidth_explanation],
                ),
                "fixed_ip_required": ClarificationIntent(
                    field="fixed_ip_required",
                    priority="medium",
                    intent="确认是否需要固定公网 IP",
                    why="如果需要固定公网 IP，候选产品可能转向 IPMAN、精品专线等方向。",
                    example_options=["需要", "不需要", "暂不确定"],
                    term_explanations=[fixed_ip_explanation],
                ),
                "carrier_preference": ClarificationIntent(
                    field="carrier_preference",
                    priority="low",
                    intent="确认是否有运营商偏好",
                    why="运营商偏好用于候选过滤，不作为需求主分类。",
                    example_options=["电信", "联通", "移动", "无偏好"],
                ),
            },
            product_lock_hints=["智云上海专线", "智享", "智云", "商务宽带", "企业宽带"],
        ),
        "上网-固定IP上网": ReadinessRule(
            required_any=[["fixed_ip_required", "server_or_idc_required", "primary_goal"]],
            assumable={
                "bandwidth_need": "客户未明确带宽，暂按固定公网 IP/服务器访问方向做初步推荐，需后续确认 100M/200M/500M/1G 等档位。",
                "carrier_preference": "客户未明确运营商偏好，暂按无偏好处理。",
            },
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
                    intent="确认专线带宽规模",
                    why="带宽会影响套餐档位、价格区间和资源确认；如果客户不知道，可先按服务器访问量、并发和预算给建议。",
                    example_options=["100M", "200M", "500M", "1G", "客户不确定，按服务器/并发估算"],
                    term_explanations=[bandwidth_explanation],
                ),
                "carrier_preference": ClarificationIntent(
                    field="carrier_preference",
                    priority="low",
                    intent="确认是否有运营商偏好",
                    why="固定 IP 产品通常会受运营商资源影响，偏好可用于候选筛选。",
                    example_options=["电信", "联通", "移动", "无偏好"],
                ),
            },
            product_lock_hints=["IPMAN", "BGP&IPMAN", "精品专线", "智光", "沃专线"],
        ),
        "组网-点对点": ReadinessRule(
            required_any=[["site_count", "usage_scene"]],
            assumable={
                "bandwidth_need": "客户未明确组网带宽，暂按两点互联和业务重要性做初步推荐，需后续确认每条线路的大致带宽。",
                "server_or_idc_required": "暂按不涉及服务器或 IDC 场景处理。",
                "carrier_preference": "客户未明确运营商偏好，暂按无偏好处理。",
            },
            clarification_intents={
                "site_count": ClarificationIntent(
                    field="site_count",
                    priority="high",
                    intent="确认是否为两个地点互联",
                    why="点对点适合两端固定地点互联，站点数量会影响是否转向点对多或智能组网。",
                    example_options=["A点到B点", "总部+一个分支", "两地机房互联"],
                ),
                "usage_scene": ClarificationIntent(
                    field="usage_scene",
                    priority="high",
                    intent="确认组网用途",
                    why="需要判断是纯内网互联、互联网访问，还是重要业务传输。",
                    example_options=["纯内网互联", "多门店互联", "重要业务传输"],
                ),
                "bandwidth_need": ClarificationIntent(
                    field="bandwidth_need",
                    priority="medium",
                    intent="确认每个站点的大致带宽",
                    why="点对点或多点组网的带宽会影响专线类型、价格和资源确认；客户不确定时，可按业务系统重要性和并发人数估算。",
                    example_options=["每点50M", "每点100M", "每点200M以上", "客户不确定，按业务系统估算"],
                    term_explanations=[bandwidth_explanation],
                ),
                "carrier_preference": ClarificationIntent(
                    field="carrier_preference",
                    priority="low",
                    intent="确认是否有运营商偏好",
                    why="运营商资源会影响本地专线、MSTP、IPRAN、OTN 等候选范围。",
                    example_options=["电信", "联通", "移动", "无偏好"],
                ),
            },
            product_lock_hints=["IPRAN", "MSTP", "OTN", "以太专线", "本地专线"],
        ),
        "组网-点对多": ReadinessRule(
            required_any=[["site_count", "usage_scene", "primary_goal"]],
            assumable={
                "bandwidth_need": "客户未明确多点组网带宽，暂按站点数和业务重要性做初步推荐，需后续确认中心点和分支点带宽。",
                "carrier_preference": "客户未明确运营商偏好，暂按无偏好处理。",
            },
            clarification_intents={
                "site_count": ClarificationIntent(
                    field="site_count",
                    priority="high",
                    intent="确认多点数量和拓扑",
                    why="点对多要明确中心点、分支数量和是否星型/爪形结构，才能选择 MPLS-VPN、IPRAN 等方案。",
                    example_options=["总部+多个分支", "多个门店互联", "中心点+多分支"],
                ),
                "bandwidth_need": ClarificationIntent(
                    field="bandwidth_need",
                    priority="medium",
                    intent="确认中心点和分支点带宽",
                    why="点对多组网的带宽通常要分别看总部和分支，客户不确定时可按业务系统和人数估算。",
                    example_options=["中心100M/分支50M", "每点100M", "客户不确定，按站点规模建议"],
                    term_explanations=[bandwidth_explanation],
                ),
                "carrier_preference": ClarificationIntent(
                    field="carrier_preference",
                    priority="low",
                    intent="确认是否有运营商偏好",
                    why="运营商资源会影响多点组网覆盖和价格。",
                    example_options=["电信", "联通", "移动", "无偏好"],
                ),
            },
            product_lock_hints=["MPLS-VPN", "IPRAN", "OTN", "MSTP", "多点组网"],
        ),
        "组网-智能组网": ReadinessRule(
            required_any=[["site_count", "usage_scene", "primary_goal"]],
            assumable={
                "bandwidth_need": "客户未明确智能组网带宽，暂按已有互联网线路和设备组网方向做初步推荐，需后续确认每个站点带宽。",
                "carrier_preference": "客户未明确运营商偏好，暂按无偏好处理。",
            },
            clarification_intents={
                "site_count": ClarificationIntent(
                    field="site_count",
                    priority="high",
                    intent="确认需要接入的站点数量",
                    why="智能组网通常用于多站点，是否已有宽带、是否需要设备接入会影响方案。",
                    example_options=["多个门店", "总部+分支", "已有宽带上叠加组网"],
                ),
                "bandwidth_need": ClarificationIntent(
                    field="bandwidth_need",
                    priority="medium",
                    intent="确认各站点的上网带宽",
                    why="SD-WAN 或智能组网常叠加在现有宽带上，带宽会影响体验和设备选型。",
                    example_options=["每点50M", "每点100M", "已有宽带不确定", "客户不确定，按门店规模建议"],
                    term_explanations=[bandwidth_explanation],
                ),
                "carrier_preference": ClarificationIntent(
                    field="carrier_preference",
                    priority="low",
                    intent="确认是否有运营商偏好",
                    why="智能组网可结合已有线路，运营商偏好用于资源和候选筛选。",
                    example_options=["电信", "联通", "移动", "无偏好", "已有运营商线路"],
                ),
            },
            product_lock_hints=["SD-WAN", "MSTP-VPN", "智能组网", "设备组网"],
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
                            why="当前无法稳定判断六类产品需求主分类。",
                            example_options=["沿街店铺上网", "中小企业办公上网", "固定 IP 上网", "点对点组网", "点对多组网", "智能组网"],
                        )
                    ]
                ),
                reason="当前无法匹配到明确的六类主分类，需要先确认客户主要需求。",
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
