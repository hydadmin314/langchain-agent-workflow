from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from dataclasses import replace
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = Path(__file__).resolve().parent
RUNTIME_VERSION = "sales-demo-single-v5"
MAX_DEMO_TURNS = 6
sys.path.insert(0, str(PROJECT_ROOT))

from agent.sales_recommendation_agent.intent_parser import (  # noqa: E402
    CustomerDemand,
    HeuristicDemandParser,
    OpenAICompatibleDemandParser,
    SalesRecommendationSettings,
    SalesRequirementWorkflow,
)
from agent.sales_recommendation_agent.intent_parser.parsers import extract_json_object  # noqa: E402
from agent.sales_recommendation_agent.recommender import (  # noqa: E402
    ClarificationPlan,
    ClarificationQuestionGenerator,
    ClarificationQuestionResult,
    RecommendationExplanationResult,
    RecommendationExplainer,
    RecommendationProductExplanation,
)
from agent.sales_recommendation_agent.recommender.explainer import (  # noqa: E402
    apply_explanation_safety_guards,
    build_explainer_payload,
    parse_explanation_result,
)
from agent.sales_recommendation_agent.recommender.readiness import (  # noqa: E402
    normalize_llm_clarification_result,
    parse_clarification_result,
)
from agent.sales_recommendation_agent.workflow import (  # noqa: E402
    SalesRecommendationWorkflow,
    SalesRecommendationWorkflowResult,
)
from prompts.sales_recommendation_agent_prompts import (  # noqa: E402
    SINGLE_CALL_SALES_SYSTEM_PROMPT,
    build_clarification_question_user_prompt,
    build_recommendation_explainer_user_prompt,
    build_single_call_sales_user_prompt,
)


FIELD_LABELS = {
    "primary_category": "业务分类",
    "secondary_categories": "辅助分类",
    "primary_goal": "核心目标",
    "usage_scene": "使用场景",
    "business_action": "办理动作",
    "site_count": "地点数量",
    "user_count": "人数/规模",
    "bandwidth_need": "带宽需求",
    "fixed_ip_required": "固定公网 IP",
    "fixed_ip_count": "固定 IP 数量",
    "voice_required": "语音/固定电话",
    "concurrent_calls": "并发通话",
    "overseas_access": "海外访问",
    "overseas_target": "海外目标",
    "server_or_idc_required": "服务器/IDC",
    "cloud_office_required": "云办公",
    "security_required": "安全防护",
    "industry_scene": "行业场景",
    "marketing_touch_required": "营销触达",
    "budget": "预算",
    "reliability_level": "稳定性要求",
    "carrier_preference": "运营商偏好",
    "region": "安装区域",
    "customer_type": "客户类型",
}

HIDDEN_DISPLAY_FIELDS = {"raw_keywords", "confidence", "missing_fields"}
NEGATED_FIXED_IP_PATTERN = re.compile(
    r"(?:不需要|不要|无需|不用).{0,8}(?:固定\s*(?:公网\s*)?IP|公网\s*IP)",
    re.I,
)
OVERSEAS_SIGNAL_PATTERN = re.compile(
    r"海外|国外|跨境|美国|日本|新加坡|香港|欧洲|Google|Salesforce|Microsoft\s*365|Office\s*365|AWS|SaaS",
    re.I,
)
DEMO_PROGRESSIVE_QUESTIONS = [
    (
        "primary_goal",
        "客户最主要想解决哪类问题？例如办公室上网、多地互联、海外访问、固定电话或业务办理。",
    ),
    (
        "usage_scene",
        "客户主要在什么地点使用，业务要连接或访问到哪里？例如办公室到分公司、门店上网或访问海外系统。",
    ),
    (
        "user_count",
        "大约有多少人、终端或站点使用？这会影响带宽和产品规格。",
    ),
    (
        "reliability_level",
        "当前最影响业务的问题是什么？例如速度慢、经常断线、视频卡顿或重要系统不稳定。",
    ),
    (
        "budget",
        "客户大致预算和期望开通时间是什么？如果还没确定，也可以说明更看重价格还是稳定性。",
    ),
]


class DemoHeuristicDemandParser(HeuristicDemandParser):
    """修正旧兼容字段对单次模式本地预解析的干扰。"""

    def parse(self, raw_text: str) -> CustomerDemand:
        demand = super().parse(raw_text)
        if not OVERSEAS_SIGNAL_PATTERN.search(raw_text):
            demand.overseas_target = ""
            if demand.overseas_access is not False:
                demand.overseas_access = None
        return demand


class StrictClarificationQuestionGenerator(ClarificationQuestionGenerator):
    """Web 测试模式下要求追问必须真实来自大模型。"""

    def generate(
        self,
        *,
        query: str,
        demand: Any,
        category_decision: Any,
        readiness_result: Any,
    ) -> ClarificationQuestionResult:
        if not readiness_result.clarification_plan.intents:
            return ClarificationQuestionResult(message="", questions=[], fields=[])

        payload = {
            "query": query,
            "customer_need": demand.model_dump(mode="json"),
            "category": category_decision.model_dump(mode="json"),
            "readiness": readiness_result.model_dump(mode="json"),
        }
        response_text = self._invoke_llm(
            build_clarification_question_user_prompt(payload)
        )
        result = parse_clarification_result(response_text)
        return normalize_llm_clarification_result(
            result=result,
            readiness_result=readiness_result,
        )


class StrictRetryRecommendationExplainer(RecommendationExplainer):
    """严格解析推荐说明，格式异常时追加约束后重试一次。"""

    def explain(
        self,
        *,
        query: str,
        demand: Any,
        category_decision: Any,
        comparison_result: Any,
    ) -> Any:
        payload = build_explainer_payload(
            query=query,
            demand=demand,
            category_decision=category_decision,
            comparison_result=comparison_result,
        )
        prompt = build_recommendation_explainer_user_prompt(payload)
        prompt += (
            "\n\n请精简输出：summary 不超过100字；每个推荐理由不超过80字；"
            "comparison_summary、risk_reminders、clarifying_questions 各最多3条；"
            "sales_talk 不超过150字；evidence_notes 最多3条。"
        )
        last_error: Exception | None = None
        for attempt in range(2):
            current_prompt = prompt
            if attempt:
                current_prompt += (
                    "\n\n上一次输出无法解析。请重新输出完整、严格合法的 JSON："
                    "所有字符串使用双引号，数组和对象元素之间必须有逗号，"
                    "不要输出代码块或 JSON 之外的任何文字。"
                )
            try:
                result = parse_explanation_result(self._invoke_llm(current_prompt))
                return apply_explanation_safety_guards(
                    result=result,
                    category_decision=category_decision,
                    comparison_result=comparison_result,
                )
            except Exception as exc:  # noqa: BLE001 - retry once, then expose error.
                last_error = exc
        raise RuntimeError(f"推荐说明两次解析均失败：{last_error}")

    def _build_request_kwargs(self, prompt: str) -> dict[str, Any]:
        request_kwargs = super()._build_request_kwargs(prompt)
        request_kwargs["max_tokens"] = 900
        return request_kwargs


class SingleCallSalesOutput(BaseModel):
    """Web Demo 单次模型调用的统一输出。"""

    customer_need_patch: dict[str, Any] = Field(default_factory=dict)
    action: Literal["clarify", "recommend", "no_candidate"] = "clarify"
    message: str = ""
    questions: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    explanation: RecommendationExplanationResult = Field(
        default_factory=RecommendationExplanationResult
    )


class DemoTurnPolicy(BaseModel):
    """Web Demo 的轮次收敛结果。"""

    action: Literal["clarify", "recommend", "no_candidate"]
    terminal: bool = False
    max_turns: int = MAX_DEMO_TURNS
    reason: str = ""


class SingleCallSalesLLM:
    """一次调用完成语义补全与追问/推荐表达。"""

    def __init__(self, settings: SalesRecommendationSettings) -> None:
        self.settings = settings

    def generate(
        self,
        *,
        user_text: str,
        result: SalesRecommendationWorkflowResult,
    ) -> SingleCallSalesOutput:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("openai package is not installed") from exc

        client_kwargs: dict[str, Any] = {"api_key": self.settings.llm_api_key}
        if self.settings.llm_base_url:
            client_kwargs["base_url"] = self.settings.llm_base_url

        payload = build_single_call_payload(user_text=user_text, result=result)
        request_kwargs: dict[str, Any] = {
            "model": self.settings.llm_model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SINGLE_CALL_SALES_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_single_call_sales_user_prompt(payload),
                },
            ],
            "temperature": self.settings.llm_temperature,
            "max_tokens": 850,
            "timeout": self.settings.llm_timeout_seconds,
            "stream": False,
        }
        extra_body: dict[str, Any] = {}
        if self.settings.llm_enable_thinking is not None:
            extra_body["enable_thinking"] = self.settings.llm_enable_thinking
        if self.settings.llm_enable_search is not None:
            extra_body["enable_search"] = self.settings.llm_enable_search
        if extra_body:
            request_kwargs["extra_body"] = extra_body

        try:
            response = OpenAI(**client_kwargs).chat.completions.create(**request_kwargs)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "模型服务返回的响应包不是合法 JSON，请重试本轮。"
            ) from exc
        choice = response.choices[0]
        content = choice.message.content or "{}"
        return parse_single_call_output(
            content,
            finish_reason=str(choice.finish_reason or ""),
        )


def parse_single_call_output(
    content: str,
    *,
    finish_reason: str = "",
) -> SingleCallSalesOutput:
    """解析单次模型输出，并本地修复常见 JSON 标点问题。"""

    try:
        return SingleCallSalesOutput.model_validate(extract_json_object(content))
    except (json.JSONDecodeError, ValueError) as original_error:
        repaired = repair_common_json_errors(content)
        try:
            return SingleCallSalesOutput.model_validate(json.loads(repaired))
        except (json.JSONDecodeError, ValueError) as repaired_error:
            reason = "模型输出达到长度上限" if finish_reason == "length" else "模型返回的 JSON 格式不完整"
            raise RuntimeError(
                f"{reason}，请重试本轮；已保留每轮一次模型调用。"
            ) from repaired_error


def repair_common_json_errors(content: str) -> str:
    """修复漏逗号、尾逗号和 Markdown 代码块，不改写字段内容。"""

    cleaned = content.strip().lstrip("\ufeff")
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.S | re.I)
    if fenced:
        cleaned = fenced.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        cleaned = cleaned[start : end + 1]

    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    cleaned = re.sub(
        r'([}\]"0-9el])(\s+)(?="[^"\r\n]+"\s*:)',
        r"\1,\2",
        cleaned,
    )

    for _ in range(6):
        try:
            json.loads(cleaned)
            return cleaned
        except json.JSONDecodeError as exc:
            if exc.msg != "Expecting ',' delimiter":
                break
            remainder = cleaned[exc.pos :]
            if not re.match(r'\s*["}\]]', remainder):
                break
            cleaned = cleaned[: exc.pos] + "," + cleaned[exc.pos :]
    return cleaned


class SalesDemoService:
    """Web 展示层适配器，不改销售推荐主工作流。"""

    def __init__(
        self,
        *,
        published_root: Path,
        top_k: int = 8,
        use_llm: bool = False,
        llm_mode: str = "single",
    ) -> None:
        settings = SalesRecommendationSettings.from_env()
        if use_llm and llm_mode == "double":
            if not settings.llm_api_key:
                raise RuntimeError("LLM 模式已启用，但没有配置可用的 API Key。")
            requirement_workflow = SalesRequirementWorkflow(
                settings=settings,
                parser=OpenAICompatibleDemandParser(settings),
            )
            clarification_generator = StrictClarificationQuestionGenerator(settings=settings)
        else:
            if use_llm and not settings.llm_api_key:
                raise RuntimeError("LLM 模式已启用，但没有配置可用的 API Key。")
            offline_settings = replace(settings, llm_api_key=None)
            requirement_workflow = SalesRequirementWorkflow(
                settings=offline_settings,
                parser=DemoHeuristicDemandParser(offline_settings),
            )
            clarification_generator = ClarificationQuestionGenerator(settings=offline_settings)

        self.workflow = SalesRecommendationWorkflow(
            published_root=str(published_root),
            top_k=top_k,
            skip_explainer=not (use_llm and llm_mode == "double"),
            requirement_workflow=requirement_workflow,
            clarification_generator=clarification_generator,
            explainer=(
                StrictRetryRecommendationExplainer(settings=settings)
                if use_llm and llm_mode == "double"
                else None
            ),
        )
        self.product_count = self.workflow.product_repository.load_result().product_count
        self.use_llm = use_llm
        self.llm_mode = llm_mode if use_llm else "offline"
        self.single_call_llm = (
            SingleCallSalesLLM(settings)
            if use_llm and llm_mode == "single"
            else None
        )
        self.runtime = {
            "version": RUNTIME_VERSION,
            "llmEnabled": use_llm,
            "mode": self.llm_mode,
            "model": settings.llm_model if use_llm else "",
            "strictParsing": use_llm,
            "modelCallsPerTurn": 1 if self.llm_mode == "single" else (2 if use_llm else 0),
            "maxTurns": MAX_DEMO_TURNS,
        }

    def run_turn(
        self,
        *,
        session_id: str,
        user_text: str,
    ) -> tuple[
        SalesRecommendationWorkflowResult,
        list[str],
        dict[str, float],
        DemoTurnPolicy,
    ]:
        started_at = time.perf_counter()
        workflow_text, guardrails = normalize_demo_user_text(user_text)
        previous_questions = list(
            self.workflow.memory_store.get_or_create(session_id).asked_questions
        )
        local_started_at = time.perf_counter()
        result = self.workflow.run(session_id=session_id, user_text=workflow_text)
        result, turn_policy = apply_demo_turn_policy(
            workflow=self.workflow,
            result=result,
            max_turns=MAX_DEMO_TURNS,
        )
        local_seconds = time.perf_counter() - local_started_at
        model_seconds = 0.0
        if self.single_call_llm is not None:
            model_started_at = time.perf_counter()
            unified = self.single_call_llm.generate(
                user_text=user_text,
                result=result,
            )
            model_seconds = time.perf_counter() - model_started_at
            apply_single_call_output(
                workflow=self.workflow,
                result=result,
                output=unified,
                user_text=user_text,
            )
        if result.status == "ask_clarification":
            remove_repeated_questions(
                result=result,
                previous_questions=previous_questions,
            )
            self.workflow.memory_store.save(result.state)
        if (
            self.use_llm
            and self.llm_mode == "double"
            and result.status == "recommended"
            and result.comparison
            and result.comparison.products
            and result.explanation is None
        ):
            details = "；".join(result.errors) or "推荐说明未返回结构化结果"
            raise RuntimeError(f"大模型推荐说明生成失败：{details}")
        result.user_text = user_text
        apply_demo_guardrails(result=result, guardrails=guardrails)
        timings = {
            "local": round(local_seconds, 2),
            "model": round(model_seconds, 2),
            "total": round(time.perf_counter() - started_at, 2),
        }
        return result, [item["message"] for item in guardrails], timings, turn_policy


def apply_demo_turn_policy(
    *,
    workflow: SalesRecommendationWorkflow,
    result: SalesRecommendationWorkflowResult,
    max_turns: int,
) -> tuple[SalesRecommendationWorkflowResult, DemoTurnPolicy]:
    """保证信息充分立即推荐，信息模糊时最多追问到第六轮。"""

    if result.status == "recommended":
        return result, DemoTurnPolicy(
            action="recommend",
            terminal=True,
            max_turns=max_turns,
            reason="已满足最低推荐条件，立即输出推荐。",
        )
    if result.status == "no_candidate":
        return result, DemoTurnPolicy(
            action="no_candidate",
            terminal=True,
            max_turns=max_turns,
            reason="已完成需求判断，但当前产品库没有合适候选。",
        )
    if result.status != "ask_clarification" or result.state.turn_count < max_turns:
        return result, DemoTurnPolicy(
            action="clarify",
            terminal=False,
            max_turns=max_turns,
            reason=result.readiness.reason if result.readiness else "继续补充关键需求。",
        )

    forced_result = force_recommendation_at_turn_limit(
        workflow=workflow,
        result=result,
        max_turns=max_turns,
    )
    action: Literal["recommend", "no_candidate"] = (
        "recommend" if forced_result.status == "recommended" else "no_candidate"
    )
    return forced_result, DemoTurnPolicy(
        action=action,
        terminal=True,
        max_turns=max_turns,
        reason=f"已达到最多 {max_turns} 轮，基于当前信息和明确假设输出最优结果。",
    )


def force_recommendation_at_turn_limit(
    *,
    workflow: SalesRecommendationWorkflow,
    result: SalesRecommendationWorkflowResult,
    max_turns: int,
) -> SalesRecommendationWorkflowResult:
    """撤回本轮追问记录，复用主工作流现有推荐阶段生成最终结果。"""

    state = result.state
    if state.turns and state.turns[-1].status == "ask_clarification":
        state.turns.pop()
        state.turn_count = max(0, state.turn_count - 1)

    current_questions = list(result.clarification.questions if result.clarification else [])
    for question in current_questions:
        for index in range(len(state.asked_questions) - 1, -1, -1):
            if state.asked_questions[index] == question:
                state.asked_questions.pop(index)
                break

    readiness = result.readiness.model_copy(deep=True) if result.readiness else None
    if readiness is not None:
        pending_labels = [
            format_condition(item) for item in readiness.missing_conditions
        ]
        limit_assumption = (
            f"已达到最多 {max_turns} 轮，"
            + (
                f"以下信息尚未确认：{'、'.join(pending_labels)}；"
                if pending_labels
                else ""
            )
            + "当前推荐按已有信息生成，最终办理前需人工复核。"
        )
        readiness.decision = "ready_with_assumptions"
        readiness.reason = f"已达到最多 {max_turns} 轮，进入带假设推荐。"
        readiness.assumptions = dedupe(
            [*readiness.assumptions, limit_assumption]
        )
        readiness.clarification_plan = ClarificationPlan()
        state.readiness = readiness.model_dump(mode="json")
        state.assumptions = list(readiness.assumptions)

    forced = workflow._run_recommendation_stage(
        state=state,
        user_text=result.user_text,
        intent_result=result.intent,
        merge_result=result.merge_result,
        readiness=readiness,
    )
    forced.clarification = None
    return forced


def remove_repeated_questions(
    *,
    result: SalesRecommendationWorkflowResult,
    previous_questions: list[str],
) -> None:
    """过滤历史已问问题，避免模糊回答时反复追问同一句。"""

    clarification = result.clarification
    if clarification is None:
        return
    previous = {normalize_question_text(item) for item in previous_questions}
    kept_questions: list[str] = []
    kept_fields: list[str] = []
    for index, question in enumerate(clarification.questions):
        normalized = normalize_question_text(question)
        if not normalized or normalized in previous:
            continue
        kept_questions.append(question)
        if index < len(clarification.fields):
            kept_fields.append(clarification.fields[index])
        if len(kept_questions) >= 2:
            break
    if not kept_questions:
        start_index = min(
            max(result.state.turn_count - 1, 0),
            len(DEMO_PROGRESSIVE_QUESTIONS) - 1,
        )
        ordered_fallbacks = [
            *DEMO_PROGRESSIVE_QUESTIONS[start_index:],
            *DEMO_PROGRESSIVE_QUESTIONS[:start_index],
        ]
        for field_name, question in ordered_fallbacks:
            if normalize_question_text(question) in previous:
                continue
            kept_questions = [question]
            kept_fields = [field_name]
            break
    clarification.questions = kept_questions
    clarification.fields = kept_fields
    if kept_questions:
        clarification.message = (
            clarification.message
            or "好的，我再确认一个会直接影响方案选择的信息。"
        )
    else:
        clarification.message = (
            "前面的关键信息仍未明确，请尽量补充一个会影响方案选择的具体条件。"
        )
    result.state.asked_questions = dedupe_questions(
        [*previous_questions, *kept_questions]
    )


def dedupe_questions(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = normalize_question_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(value)
    return result


def normalize_question_text(value: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:]+", "", str(value or "")).lower()


def build_single_call_payload(
    *,
    user_text: str,
    result: SalesRecommendationWorkflowResult,
) -> dict[str, Any]:
    return {
        "user_text": user_text,
        "program_status": result.status,
        "turn_count": result.state.turn_count,
        "customer_need_draft": result.customer_need.model_dump(mode="json"),
        "category": result.category_decision.model_dump(mode="json"),
        "readiness": (
            result.readiness.model_dump(mode="json")
            if result.readiness is not None
            else {}
        ),
        "candidates": [
            compact_single_call_candidate(product)
            for product in (result.comparison.products if result.comparison else [])
        ],
        "comparison_dimensions": [
            dimension.model_dump(mode="json")
            for dimension in (
                result.comparison.comparison_dimensions[:5]
                if result.comparison
                else []
            )
        ],
        "global_questions": (
            result.comparison.global_questions[:3] if result.comparison else []
        ),
        "global_warnings": (
            result.comparison.global_warnings[:3] if result.comparison else []
        ),
        "recent_turns": [
            {
                "user_text": turn.user_text,
                "status": turn.status,
            }
            for turn in result.state.turns[-4:]
        ],
    }


def compact_single_call_candidate(product: Any) -> dict[str, Any]:
    package = product.package_summary
    constraints = product.constraint_summary
    return {
        "rank": product.rank,
        "document_id": product.document_id,
        "product_name": product.product_name,
        "product_family": product.product_family,
        "carrier": product.carrier,
        "final_score": product.final_score,
        "matched_strengths": product.matched_strengths[:3],
        "risk_warnings": product.risk_warnings[:3],
        "missing_info": product.missing_info[:3],
        "package": {
            "package_count": package.package_count,
            "price_range": package.price_range,
            "speeds": package.speeds[:6],
        },
        "important_risks": constraints.important_risks[:3],
    }


def apply_single_call_output(
    *,
    workflow: SalesRecommendationWorkflow,
    result: SalesRecommendationWorkflowResult,
    output: SingleCallSalesOutput,
    user_text: str,
) -> None:
    expected_action = {
        "ask_clarification": "clarify",
        "recommended": "recommend",
        "no_candidate": "no_candidate",
    }.get(result.status)
    if output.action != expected_action:
        raise RuntimeError(
            f"单次模型动作与程序决策不一致：程序={result.status}，模型={output.action}"
        )

    incoming_need = validate_customer_need_patch(output.customer_need_patch)
    semantic_merge = workflow.demand_merger.merge(
        existing=result.customer_need,
        incoming=incoming_need,
        user_text=user_text,
    )
    refined_need = semantic_merge.customer_need
    promoted_fields = promote_detailed_semantics(
        existing=result.customer_need,
        incoming=incoming_need,
        merged=refined_need,
        user_text=user_text,
    )
    if promoted_fields:
        semantic_merge.changed_fields = dedupe(
            [*semantic_merge.changed_fields, *promoted_fields]
        )
        semantic_merge.answered_fields = dedupe(
            [*semantic_merge.answered_fields, *promoted_fields]
        )
        semantic_merge.conflicts = [
            conflict
            for conflict in semantic_merge.conflicts
            if conflict.field not in promoted_fields
        ]

    # 分类与产品排序由程序负责，模型只能补全业务语义字段。
    refined_need.primary_category = result.category_decision.primary_category_name
    refined_need.secondary_categories = list(result.customer_need.secondary_categories)
    result.customer_need = refined_need
    result.state.customer_need = refined_need

    if result.merge_result is not None:
        result.merge_result.customer_need = refined_need
        result.merge_result.changed_fields = dedupe(
            [*result.merge_result.changed_fields, *semantic_merge.changed_fields]
        )
        result.merge_result.answered_fields = dedupe(
            [*result.merge_result.answered_fields, *semantic_merge.answered_fields]
        )
        result.merge_result.conflicts.extend(semantic_merge.conflicts)
    for field_name in semantic_merge.answered_fields:
        if field_name not in result.state.answered_fields:
            result.state.answered_fields.append(field_name)

    if result.status == "ask_clarification":
        clarification = ClarificationQuestionResult(
            message=output.message,
            questions=output.questions,
            fields=output.fields,
        )
        if result.readiness is not None:
            clarification = normalize_llm_clarification_result(
                result=clarification,
                readiness_result=result.readiness,
            )
        result.clarification = clarification
        result.assistant_message = clarification.message
    elif result.status == "recommended" and result.comparison is not None:
        explanation = enforce_program_ranking(
            explanation=output.explanation,
            result=result,
        )
        result.explanation = apply_explanation_safety_guards(
            result=explanation,
            category_decision=result.category_decision,
            comparison_result=result.comparison,
        )
        result.assistant_message = (
            result.explanation.summary or result.explanation.sales_talk
        )
    elif result.status == "no_candidate":
        result.assistant_message = output.message or result.assistant_message

    if result.state.turns:
        result.state.turns[-1].assistant_message = result.assistant_message
        result.state.turns[-1].status = result.status
    workflow.memory_store.save(result.state)


def validate_customer_need_patch(payload: dict[str, Any]) -> CustomerDemand:
    """忽略未知字段，并把模型常见的类型漂移归一化。"""

    allowed_fields = set(CustomerDemand.model_fields)
    list_fields = {"secondary_categories", "raw_keywords", "missing_fields"}
    boolean_fields = {
        "fixed_ip_required",
        "voice_required",
        "overseas_access",
        "server_or_idc_required",
        "cloud_office_required",
        "security_required",
        "marketing_touch_required",
    }
    cleaned: dict[str, Any] = {}
    for field_name, value in payload.items():
        if field_name not in allowed_fields:
            continue
        if value is None or value == "" or value == []:
            continue
        if field_name in list_fields:
            values = value if isinstance(value, list) else [value]
            cleaned[field_name] = [
                str(item).strip()
                for item in values
                if item is not None and str(item).strip()
            ]
            continue
        if field_name in boolean_fields:
            normalized_boolean = normalize_patch_boolean(value)
            if normalized_boolean is not None:
                cleaned[field_name] = normalized_boolean
            continue
        if field_name == "confidence":
            try:
                cleaned[field_name] = max(0.0, min(float(value), 1.0))
            except (TypeError, ValueError):
                continue
            continue
        cleaned[field_name] = str(value).strip()
    return CustomerDemand.model_validate(cleaned)


def normalize_patch_boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "需要", "是", "有"}:
        return True
    if normalized in {"false", "0", "no", "不需要", "否", "无"}:
        return False
    return None


def promote_detailed_semantics(
    *,
    existing: CustomerDemand,
    incoming: CustomerDemand,
    merged: CustomerDemand,
    user_text: str,
) -> list[str]:
    """用有原话证据的完整描述替换本地解析产生的短标签。"""

    field_keywords = {
        "primary_goal": (
            "访问",
            "上网",
            "互联",
            "组网",
            "提高",
            "提升",
            "保障",
            "稳定",
            "办理",
            "开通",
            "查询",
            "固定",
            "语音",
            "中继",
            "监控",
            "宽带",
            "专线",
        ),
        "usage_scene": (
            "办公室",
            "门店",
            "总部",
            "分支",
            "子公司",
            "呼叫中心",
            "服务器",
            "IDC",
            "SaaS",
            "上网",
            "互联",
        ),
    }
    promoted: list[str] = []
    for field_name, keywords in field_keywords.items():
        old_value = str(getattr(existing, field_name, "") or "").strip()
        new_value = str(getattr(incoming, field_name, "") or "").strip()
        if not is_grounded_semantic_upgrade(
            old_value=old_value,
            new_value=new_value,
            user_text=user_text,
            keywords=keywords,
        ):
            continue
        setattr(merged, field_name, new_value)
        promoted.append(field_name)
    return promoted


def is_grounded_semantic_upgrade(
    *,
    old_value: str,
    new_value: str,
    user_text: str,
    keywords: tuple[str, ...],
) -> bool:
    if len(new_value) < 4 or len(new_value) > 80:
        return False
    if old_value and len(new_value) < len(old_value) + 3:
        return False

    evidence = [keyword for keyword in keywords if keyword in user_text]
    if not evidence or not any(keyword in new_value for keyword in evidence):
        return False

    old_is_weak = len(old_value) <= 4 or (old_value and old_value in new_value)
    return not old_value or old_is_weak


def enforce_program_ranking(
    *,
    explanation: RecommendationExplanationResult,
    result: SalesRecommendationWorkflowResult,
) -> RecommendationExplanationResult:
    products = result.comparison.products if result.comparison else []
    if not products:
        return explanation

    top = products[0]
    explanation.recommended_product.document_id = top.document_id
    explanation.recommended_product.product_name = top.product_name

    model_reasons = {
        item.document_id: item.reason
        for item in explanation.alternative_products
        if item.document_id
    }
    explanation.alternative_products = [
        RecommendationProductExplanation(
            document_id=product.document_id,
            product_name=product.product_name,
            reason=model_reasons.get(product.document_id, ""),
        )
        for product in products[1:3]
    ]
    return explanation


class SalesDemoRequestHandler(SimpleHTTPRequestHandler):
    service: SalesDemoService

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_ROOT), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API.
        if self.path.rstrip("/") == "/api/sales-demo/status":
            self._write_json(
                HTTPStatus.OK,
                {
                    "status": "ready",
                    "productCount": self.service.product_count,
                    "runtime": self.service.runtime,
                },
            )
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
        if self.path.rstrip("/") != "/api/sales-demo/turn":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self._read_json_body()
            user_text = str(payload.get("user_text") or payload.get("query") or "").strip()
            if not user_text:
                raise ValueError("user_text 不能为空")

            session_id = str(payload.get("session_id") or uuid4().hex).strip()
            result, guardrails, timings, turn_policy = self.service.run_turn(
                session_id=session_id,
                user_text=user_text,
            )
            self._write_json(
                HTTPStatus.OK,
                build_api_response(
                    result,
                    guardrails=guardrails,
                    clarification_generator=self.service.workflow.clarification_generator,
                    runtime=self.service.runtime,
                    timings=timings,
                    turn_policy=turn_policy,
                ),
            )
        except ValueError as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": str(exc), "code": "invalid_request", "retryable": False},
            )
        except Exception as exc:  # noqa: BLE001 - demo API should expose readable failures.
            error_message = str(exc)
            is_model_format_error = isinstance(exc, RuntimeError) and any(
                marker in error_message
                for marker in ("JSON", "模型返回", "模型输出", "响应包")
            )
            self._write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "error": error_message or type(exc).__name__,
                    "code": (
                        "model_response_invalid"
                        if is_model_format_error
                        else "sales_demo_failed"
                    ),
                    "retryable": True,
                    "runtime": self.service.runtime,
                },
            )

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length)
        payload = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return payload

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def build_api_response(
    result: SalesRecommendationWorkflowResult,
    *,
    guardrails: list[str] | None = None,
    clarification_generator: ClarificationQuestionGenerator | None = None,
    runtime: dict[str, Any] | None = None,
    timings: dict[str, float] | None = None,
    turn_policy: DemoTurnPolicy | None = None,
) -> dict[str, Any]:
    demand = result.customer_need
    category = result.category_decision
    readiness = result.readiness
    ranking = build_ranking(result)
    interaction_id, interaction_name = interaction_category(result)
    recommendation = build_recommendation(result=result, ranking=ranking)
    top_score = max((item["finalScore"] for item in ranking), default=0)
    score_scale = max(100, int(math.ceil(top_score / 10.0) * 10))
    optional_clarification = build_optional_clarification(
        result,
        clarification_generator=clarification_generator,
    )

    missing_conditions = list(readiness.missing_conditions if readiness else [])
    missing_facts = dedupe(
        [
            *(FIELD_LABELS.get(item, item) for item in demand.missing_fields),
            *(format_condition(item) for item in missing_conditions),
        ]
    )
    current_conflicts = result.merge_result.conflicts if result.merge_result else []
    policy = turn_policy or DemoTurnPolicy(
        action=(
            "recommend"
            if result.status == "recommended"
            else "no_candidate"
            if result.status == "no_candidate"
            else "clarify"
        ),
        terminal=result.status in {"recommended", "no_candidate"},
    )
    return {
        "source": "backend",
        "runtime": build_runtime_status(runtime=runtime, result=result),
        "elapsedSeconds": (timings or {}).get("total"),
        "timings": dict(timings or {}),
        "sessionId": result.session_id,
        "status": result.status,
        "assistantMessage": result.assistant_message,
        "round": result.state.turn_count,
        "maxTurns": policy.max_turns,
        "action": policy.action,
        "terminal": policy.terminal,
        "terminationReason": policy.reason,
        "shouldRecommend": policy.terminal,
        "scene": {
            "id": scene_id(interaction_id),
            "primaryCategory": category_label(category.primary_category_id, category.primary_category_name),
            "interactionCategory": category_label(interaction_id, interaction_name),
            "candidateCount": result.score.scored_count if result.score else 0,
            "confidence": category.confidence,
        },
        "primaryCategory": category_label(category.primary_category_id, category.primary_category_name),
        "secondaryCategories": list(demand.secondary_categories),
        "interactionCategory": category_label(interaction_id, interaction_name),
        "candidateCount": result.score.scored_count if result.score else 0,
        "confidence": category.confidence,
        "customerNeed": demand.model_dump(mode="json"),
        "knownFacts": build_known_facts(demand),
        "missingFacts": missing_facts,
        "readiness": serialize_readiness(readiness),
        "clarification": serialize_clarification(result.clarification),
        "optionalClarification": serialize_clarification(optional_clarification),
        "changedFields": [
            {"field": item, "label": FIELD_LABELS.get(item, item)}
            for item in (result.merge_result.changed_fields if result.merge_result else [])
            if item not in HIDDEN_DISPLAY_FIELDS
        ],
        "answeredFields": [
            {"field": item, "label": FIELD_LABELS.get(item, item)}
            for item in result.state.answered_fields
            if item not in HIDDEN_DISPLAY_FIELDS
        ],
        "conflicts": [
            {
                "field": item.field,
                "label": FIELD_LABELS.get(item.field, item.field),
                "oldValue": format_value(item.old_value),
                "newValue": format_value(item.new_value),
            }
            for item in current_conflicts
        ],
        "guardrails": list(guardrails or []),
        "pipelineStats": {
            "publishedProducts": result.product_load.product_count if result.product_load else 0,
            "loadErrors": result.product_load.error_count if result.product_load else 0,
            "retrieved": result.retrieval.matched_count if result.retrieval else 0,
            "kept": result.filter_result.kept_count if result.filter_result else 0,
            "removed": result.filter_result.removed_count if result.filter_result else 0,
            "scored": result.score.scored_count if result.score else 0,
        },
        "scoreScale": score_scale,
        "ranking": ranking,
        "comparisonDimensions": [
            item.model_dump(mode="json")
            for item in (result.comparison.comparison_dimensions if result.comparison else [])
        ],
        "globalWarnings": list(result.comparison.global_warnings if result.comparison else []),
        "recommendation": recommendation,
        "errors": list(result.errors),
    }


def serialize_readiness(readiness: Any) -> dict[str, Any]:
    if readiness is None:
        return {
            "decision": "ask_clarification",
            "reason": "尚未完成最低推荐条件判断。",
            "missingConditions": [],
            "assumptions": [],
            "productLockHints": [],
            "clarificationPlan": [],
        }
    return {
        "decision": readiness.decision,
        "reason": readiness.reason,
        "missingConditions": [
            {"field": item, "label": format_condition(item)}
            for item in readiness.missing_conditions
        ],
        "assumptions": list(readiness.assumptions),
        "productLockHints": list(readiness.product_lock_hints),
        "clarificationPlan": [
            {
                "field": item.field,
                "label": FIELD_LABELS.get(item.field, item.field),
                "priority": item.priority,
                "intent": item.intent,
                "why": item.why,
                "options": list(item.example_options),
                "termExplanations": [
                    term.model_dump(mode="json") for term in item.term_explanations
                ],
            }
            for item in readiness.clarification_plan.intents
        ],
    }


def build_runtime_status(
    *,
    runtime: dict[str, Any] | None,
    result: SalesRecommendationWorkflowResult,
) -> dict[str, Any]:
    status = dict(runtime or {})
    if status.get("mode") == "single":
        completed_stages = ["本地召回与排序", "单次模型理解与表达"]
    else:
        completed_stages = ["需求解析"]
        if result.status == "ask_clarification" and result.clarification:
            completed_stages.append("智能追问")
        if result.status == "recommended" and result.explanation:
            completed_stages.append("推荐说明")
    status["completedStages"] = completed_stages
    return status


def serialize_clarification(clarification: Any) -> dict[str, Any]:
    if clarification is None:
        return {"message": "", "questions": [], "fields": []}
    return {
        "message": clarification.message,
        "questions": list(clarification.questions),
        "fields": list(clarification.fields),
    }


def build_optional_clarification(
    result: SalesRecommendationWorkflowResult,
    *,
    clarification_generator: ClarificationQuestionGenerator | None,
) -> Any:
    readiness = result.readiness
    if (
        readiness is None
        or readiness.decision != "ready_with_assumptions"
        or not readiness.clarification_plan.intents
        or clarification_generator is None
    ):
        return None
    return clarification_generator.generate(
        query=result.user_text,
        demand=result.customer_need,
        category_decision=result.category_decision,
        readiness_result=readiness,
    )


def build_known_facts(demand: Any) -> dict[str, Any]:
    return {
        "goal": demand.primary_goal,
        "scene": demand.usage_scene,
        "sites": demand.site_count,
        "users": demand.user_count,
        "bandwidth": demand.bandwidth_need,
        "budget": demand.budget,
        "action": "" if demand.business_action == "未知" else demand.business_action,
        "access": " / ".join(item for item in (demand.region, demand.overseas_target) if item),
        "reliability": demand.reliability_level,
        "fixedIp": bool_label(demand.fixed_ip_required),
        "voice": bool_label(demand.voice_required),
        "overseas": bool_label(demand.overseas_access),
    }


def build_ranking(result: SalesRecommendationWorkflowResult) -> list[dict[str, Any]]:
    if result.comparison is None or result.score is None:
        return []
    scored_by_document_id = {
        item.filtered_candidate.candidate.product.document_id: item
        for item in result.score.scored_candidates
    }
    ranking: list[dict[str, Any]] = []
    for compared in result.comparison.products:
        scored = scored_by_document_id.get(compared.document_id)
        package_summary = compared.package_summary.model_dump(mode="json")
        ranking.append(
            {
                "rank": compared.rank,
                "documentId": compared.document_id,
                "productName": display_product_name(compared.product_name, compared.product_family),
                "sourceDocumentName": compared.product_name,
                "productFamily": compared.product_family,
                "carrier": compared.carrier,
                "region": compared.region,
                "categoryPath": compared.category_path,
                "finalScore": compared.final_score,
                "retrievalScore": compared.retrieval_score,
                "scoreReasons": [
                    reason.model_dump(mode="json")
                    for reason in (scored.score_reasons if scored else [])
                ],
                "riskPenalties": [
                    reason.model_dump(mode="json")
                    for reason in (scored.risk_penalties if scored else [])
                ],
                "matchedStrengths": list(compared.matched_strengths),
                "riskWarnings": list(compared.risk_warnings),
                "missingInfo": list(compared.missing_info),
                "packageSummary": package_summary,
                "feeSummary": compared.fee_summary.model_dump(mode="json"),
                "optionalPackageSummary": compared.optional_package_summary.model_dump(mode="json"),
                "constraintSummary": compared.constraint_summary.model_dump(mode="json"),
                "source": compared.source,
            }
        )
    clarify_duplicate_product_names(ranking)
    return ranking


def clarify_duplicate_product_names(ranking: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in ranking:
        grouped.setdefault(item["productName"], []).append(item)

    for product_name, variants in grouped.items():
        if len(variants) < 2:
            continue
        for index, item in enumerate(variants, start=1):
            package_count = item["packageSummary"].get("package_count") or 0
            qualifier = f"{package_count}档套餐资料" if package_count else f"资料版本{index}"
            item["productName"] = f"{product_name}（{qualifier}）"


def build_recommendation(
    *,
    result: SalesRecommendationWorkflowResult,
    ranking: list[dict[str, Any]],
) -> dict[str, Any]:
    if result.status == "ask_clarification":
        return {
            "status": "clarify",
            "title": "继续了解客户需求",
            "summary": result.assistant_message,
            "answerText": "",
        }
    if result.status == "no_candidate" or not ranking:
        return {
            "status": "no_candidate",
            "title": "暂未找到合适候选",
            "summary": result.assistant_message,
            "answerText": result.assistant_message,
        }

    top = ranking[0]
    alternatives = ranking[1:3]
    explanation = result.explanation
    prices = top["packageSummary"].get("price_range") or "资料未给出明确价格"
    speeds = "、".join(top["packageSummary"].get("speeds") or []) or "待确认"
    strengths = top["matchedStrengths"][:3] or ["当前候选在程序排序中综合得分最高。"]
    model_risks = list(explanation.risk_reminders) if explanation else []
    risks = [
        compact_display_text(item, max_chars=120)
        for item in dedupe(
            [
                *model_risks,
                *top["missingInfo"],
                *(result.readiness.assumptions if result.readiness else []),
                *top["constraintSummary"].get("important_risks", []),
                *top["riskWarnings"],
                *(result.comparison.global_warnings if result.comparison else []),
            ]
        )[:3]
    ]
    if not risks:
        risks = ["资费有效期、资源覆盖和开通条件仍需人工确认。"]

    summary = explanation.summary if explanation and explanation.summary else result.assistant_message
    sales_talk = explanation.sales_talk if explanation and explanation.sales_talk else ""
    answer_sections = [
        f"【推荐结论】\n主推“{top['productName']}”，综合得分 {top['finalScore']}。{summary}",
        (
            "【客户需求理解】\n"
            f"- 核心目标：{result.customer_need.primary_goal or '待确认'}\n"
            f"- 使用场景：{result.customer_need.usage_scene or '待确认'}\n"
            f"- 业务分类：{result.category_decision.primary_category_name}"
        ),
        (
            "【主推方案】\n"
            f"- 产品/方案：{top['productName']}\n"
            f"- 套餐速度：{speeds}\n"
            f"- 价格参考：{prices}\n"
            f"- 推荐依据：{'；'.join(strengths)}"
        ),
        "【备选方案】\n"
        + (
            "\n".join(
                f"- {item['productName']}：综合得分 {item['finalScore']}，可作为备选核实。"
                for item in alternatives
            )
            if alternatives
            else "- 暂无明确备选。"
        ),
        "【风险、假设与人工确认】\n" + "\n".join(f"- {item}" for item in risks),
        (
            "【下一步动作】\n"
            "1. 核实客户地址的资源覆盖和开通条件。\n"
            "2. 对照预算确认套餐、计费周期和协议期。\n"
            "3. 对缺失价格或限制条款进行人工复核后再对外报价。"
        ),
    ]
    if sales_talk:
        answer_sections.append(f"【建议沟通话术】\n{sales_talk}")
    return {
        "status": "ready",
        "title": f"主推 {top['productName']}",
        "summary": summary,
        "primaryProduct": top,
        "alternativeProducts": alternatives,
        "risks": risks,
        "answerText": "\n\n".join(answer_sections),
    }


def interaction_category(result: SalesRecommendationWorkflowResult) -> tuple[str, str]:
    for match in result.category_decision.category_matches:
        if match.category_id == "5" and "门店_商铺_小微经营" in result.customer_need.secondary_categories:
            return match.category_id, match.category_name
    return (
        result.category_decision.primary_category_id,
        result.category_decision.primary_category_name,
    )


def display_product_name(product_name: str, product_family: str) -> str:
    raw_name = str(product_name or "").strip()
    family = str(product_family or "").strip()
    looks_like_document = bool(
        re.search(r"\.(?:pdf|docx?|xlsx?)$", raw_name, flags=re.IGNORECASE)
        or any(keyword in raw_name for keyword in ("申请单", "登记表", "业务表单"))
    )
    if looks_like_document and family:
        family_leaf = family.replace("\\", "/").split("/")[-1].strip()
        family_leaf = re.sub(r"^\d+(?:\.\d+)*\s*", "", family_leaf)
        if family_leaf:
            return family_leaf
    cleaned = re.sub(r"\.(?:pdf|docx?|xlsx?)$", "", raw_name, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\d+\s*", "", cleaned).strip(" 【】-_")
    return cleaned or family or "未命名产品"


def category_label(category_id: str, category_name: str) -> str:
    if category_id and category_name:
        return f"{category_id}_{category_name}"
    return category_name or category_id or "待澄清"


def scene_id(category_id: str) -> str:
    return {
        "1": "pricing",
        "2": "fixed_ip",
        "3": "networking",
        "4": "overseas",
        "5": "store",
        "6": "voice",
        "13": "service_process",
    }.get(category_id, "general")


def format_condition(condition: str) -> str:
    return " 或 ".join(FIELD_LABELS.get(item.strip(), item.strip()) for item in condition.split("/"))


def bool_label(value: bool | None) -> str:
    if value is True:
        return "需要"
    if value is False:
        return "不需要"
    return "待确认"


def format_value(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return bool_label(value)
    if isinstance(value, list):
        return "、".join(str(item) for item in value) or "未填写"
    return str(value or "未填写")


def normalize_demo_user_text(user_text: str) -> tuple[str, list[dict[str, str]]]:
    guardrails: list[dict[str, str]] = []
    normalized = user_text
    if NEGATED_FIXED_IP_PATTERN.search(user_text):
        normalized = NEGATED_FIXED_IP_PATTERN.sub("普通互联网接入", normalized)
        guardrails.append(
            {
                "code": "fixed_ip_explicitly_not_required",
                "message": "已识别“无需固定公网 IP”，分类时不把否定词中的专业名词当作购买诉求。",
            }
        )
    return normalized.strip() or user_text, guardrails


def apply_demo_guardrails(
    *,
    result: SalesRecommendationWorkflowResult,
    guardrails: list[dict[str, str]],
) -> None:
    codes = {item["code"] for item in guardrails}
    if "fixed_ip_explicitly_not_required" not in codes:
        return

    result.customer_need.fixed_ip_required = False
    result.state.customer_need.fixed_ip_required = False
    if "fixed_ip_required" not in result.state.answered_fields:
        result.state.answered_fields.append("fixed_ip_required")
    if result.merge_result and "fixed_ip_required" not in result.merge_result.changed_fields:
        result.merge_result.changed_fields.append("fixed_ip_required")
    if result.readiness:
        result.readiness.assumptions = [
            item
            for item in result.readiness.assumptions
            if "固定公网 IP" not in item and "固定 IP" not in item
        ]
        result.state.assumptions = list(result.readiness.assumptions)


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def compact_display_text(value: str, *, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip("，。；; ") + "…"


def main() -> None:
    parser = argparse.ArgumentParser(description="销售推荐 Demo 页面与统一工作流 API 服务。")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--published-root",
        default=str(PROJECT_ROOT / "data" / "product_doc_agent" / "published"),
    )
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument(
        "--llm-mode",
        choices=("single", "double"),
        default="single",
        help="single=每轮只调用一次模型；double=需求解析和推荐说明分别调用。",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--use-llm",
        "--use-llm-parser",
        dest="use_llm",
        action="store_true",
        default=True,
        help="启用项目配置的大模型进行需求解析、追问和推荐说明。",
    )
    mode_group.add_argument(
        "--offline",
        dest="use_llm",
        action="store_false",
        help="只使用本地规则，适合无网络预览。",
    )
    args = parser.parse_args()

    SalesDemoRequestHandler.service = SalesDemoService(
        published_root=Path(args.published_root),
        top_k=args.top_k,
        use_llm=args.use_llm,
        llm_mode=args.llm_mode,
    )
    server = ThreadingHTTPServer((args.host, args.port), SalesDemoRequestHandler)
    print(f"Sales Demo: http://{args.host}:{args.port}")
    print(f"Published products: {SalesDemoRequestHandler.service.product_count}")
    print(f"Runtime version: {RUNTIME_VERSION}")
    if SalesDemoRequestHandler.service.runtime["llmEnabled"]:
        print(
            "Runtime: "
            f"{SalesDemoRequestHandler.service.runtime['mode']} LLM "
            f"({SalesDemoRequestHandler.service.runtime['model']})"
        )
    else:
        print("Runtime: offline rules")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
