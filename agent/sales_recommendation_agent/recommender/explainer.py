from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from agent.sales_recommendation_agent.intent_parser.config import SalesRecommendationSettings
from agent.sales_recommendation_agent.intent_parser.models import CustomerDemand, DemandCategoryDecision
from agent.sales_recommendation_agent.intent_parser.parsers import extract_json_object
from agent.sales_recommendation_agent.recommender.models import (
    ComparedProduct,
    ComparisonResult,
    RecommendationExplanationResult,
    RecommendationProductExplanation,
)
from agent.sales_recommendation_agent.recommender.readiness import ReadinessResult
from prompts.sales_recommendation_agent_prompts import (
    RECOMMENDATION_EXPLAINER_SYSTEM_PROMPT,
    build_recommendation_explainer_user_prompt,
)


class RecommendationExplanationError(RuntimeError):
    """推荐说明生成失败。"""


class RecommendationExplainer:
    """基于真实 LLM 的推荐说明生成器。

    Explainer 是表达层：它只读取 Comparator 输出，不重新召回、不重新排序、不做价格计算。
    """

    def __init__(
        self,
        settings: SalesRecommendationSettings | None = None,
        *,
        client: Any | None = None,
    ):
        self.settings = settings or SalesRecommendationSettings.from_env()
        # client 主要用于测试注入；生产默认使用 OpenAI-compatible SDK。
        self.client = client

    def explain(
        self,
        *,
        query: str,
        demand: CustomerDemand,
        category_decision: DemandCategoryDecision,
        comparison_result: ComparisonResult,
        readiness_result: ReadinessResult | None = None,
    ) -> RecommendationExplanationResult:
        """生成销售可读推荐说明。"""

        payload = build_explainer_payload(
            query=query,
            demand=demand,
            category_decision=category_decision,
            comparison_result=comparison_result,
            readiness_result=readiness_result,
        )
        prompt = build_recommendation_explainer_user_prompt(payload)
        response_text = self._invoke_llm(prompt)
        try:
            result = parse_explanation_result(response_text)
        except RecommendationExplanationError:
            # 部分 OpenAI-compatible 模型即使指定 json_object，也可能偶发输出少逗号、
            # 半截 JSON 或夹杂解释文本。推荐说明属于表达层，不能因为格式问题中断主流程；
            # 这里退回到程序化说明，并保留 readiness 中的追问计划。
            result = build_fallback_explanation(
                query=query,
                demand=demand,
                category_decision=category_decision,
                comparison_result=comparison_result,
                readiness_result=readiness_result,
            )
        return apply_explanation_safety_guards(
            result=result,
            demand=demand,
            category_decision=category_decision,
            comparison_result=comparison_result,
        )

    def _invoke_llm(self, prompt: str) -> str:
        """调用 OpenAI-compatible Chat Completions 接口。"""

        if not self.settings.llm_api_key and self.client is None:
            raise RecommendationExplanationError("LLM API key is not configured")

        client = self.client or self._build_client()
        response = client.chat.completions.create(**self._build_request_kwargs(prompt))
        return response.choices[0].message.content or "{}"

    def _build_client(self) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - 本项目环境通常已安装 openai。
            raise RecommendationExplanationError("openai package is not installed") from exc

        client_kwargs: dict[str, Any] = {"api_key": self.settings.llm_api_key}
        if self.settings.llm_base_url:
            client_kwargs["base_url"] = self.settings.llm_base_url
        return OpenAI(**client_kwargs)

    def _build_request_kwargs(self, prompt: str) -> dict[str, Any]:
        request_kwargs: dict[str, Any] = {
            "model": self.settings.llm_model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": RECOMMENDATION_EXPLAINER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.settings.llm_temperature,
            "max_tokens": max(self.settings.llm_max_tokens, 900),
            "timeout": self.settings.llm_timeout_seconds,
            "stream": False,
        }

        # DashScope/Qwen 兼容接口支持这些开关；如果环境变量置空，则不会传入。
        extra_body: dict[str, Any] = {}
        if self.settings.llm_enable_thinking is not None:
            extra_body["enable_thinking"] = self.settings.llm_enable_thinking
        if self.settings.llm_enable_search is not None:
            extra_body["enable_search"] = self.settings.llm_enable_search
        if extra_body:
            request_kwargs["extra_body"] = extra_body

        return request_kwargs


def build_explainer_payload(
    *,
    query: str,
    demand: CustomerDemand,
    category_decision: DemandCategoryDecision,
    comparison_result: ComparisonResult,
    readiness_result: ReadinessResult | None = None,
) -> dict[str, Any]:
    """把完整对象压缩成 LLM 需要看的推荐说明上下文。"""

    payload = {
        "query": query,
        "demand": {
            "primary_category": demand.primary_category,
            "secondary_categories": demand.secondary_categories,
            "primary_goal": demand.primary_goal,
            "usage_scene": demand.usage_scene,
            "business_action": demand.business_action,
            "site_count": demand.site_count,
            "user_count": demand.user_count,
            "bandwidth_need": demand.bandwidth_need,
            "fixed_ip_required": demand.fixed_ip_required,
            "fixed_ip_count": demand.fixed_ip_count,
            "voice_required": demand.voice_required,
            "concurrent_calls": demand.concurrent_calls,
            "overseas_access": demand.overseas_access,
            "overseas_target": demand.overseas_target,
            "server_or_idc_required": demand.server_or_idc_required,
            "cloud_office_required": demand.cloud_office_required,
            "security_required": demand.security_required,
            "industry_scene": demand.industry_scene,
            "marketing_touch_required": demand.marketing_touch_required,
            "budget": demand.budget,
            "reliability_level": demand.reliability_level,
            "carrier_preference": demand.carrier_preference,
            "region": demand.region,
            "customer_type": demand.customer_type,
            "raw_keywords": demand.raw_keywords,
            "missing_fields": demand.missing_fields,
        },
        "category": {
            "primary_category_id": category_decision.primary_category_id,
            "primary_category_name": category_decision.primary_category_name,
            "recommendation_mode": category_decision.recommendation_mode,
            "confidence": category_decision.confidence,
            "reason": category_decision.reason,
            "clarify_questions": category_decision.clarify_questions,
            "notes": category_decision.notes,
        },
        "comparison": {
            "recommendation_mode": comparison_result.recommendation_mode,
            "global_questions": comparison_result.global_questions,
            "global_warnings": comparison_result.global_warnings,
            "dimensions": [
                dimension.model_dump(mode="json")
                for dimension in comparison_result.comparison_dimensions
            ],
            "products": [
                build_product_payload(product)
                for product in comparison_result.products
            ],
        },
    }
    if readiness_result is not None:
        payload["readiness"] = {
            "decision": readiness_result.decision,
            "assumptions": readiness_result.assumptions,
            "clarification_intents": [
                intent.model_dump(mode="json")
                for intent in readiness_result.clarification_plan.intents
            ],
        }
    return payload


def build_product_payload(product: ComparedProduct) -> dict[str, Any]:
    """保留销售解释真正需要的候选产品字段，控制 prompt 长度。"""

    return {
        "rank": product.rank,
        "document_id": product.document_id,
        "product_name": product.product_name,
        "product_family": product.product_family,
        "carrier": product.carrier,
        "region": product.region,
        "category_path": product.category_path,
        "final_score": product.final_score,
        "retrieval_score": product.retrieval_score,
        "matched_strengths": product.matched_strengths[:6],
        "risk_warnings": product.risk_warnings[:6],
        "missing_info": product.missing_info[:6],
        "package_summary": product.package_summary.model_dump(mode="json"),
        "fee_summary": product.fee_summary.model_dump(mode="json"),
        "optional_package_summary": product.optional_package_summary.model_dump(mode="json"),
        "constraint_summary": product.constraint_summary.model_dump(mode="json"),
        "material_summary": product.material_summary.model_dump(mode="json"),
        "source": product.source,
    }


def parse_explanation_result(content: str) -> RecommendationExplanationResult:
    """解析并校验 LLM 输出。"""

    try:
        payload = extract_json_object(content)
        return RecommendationExplanationResult.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise RecommendationExplanationError(f"Failed to parse recommendation explanation: {exc}") from exc


def build_fallback_explanation(
    *,
    query: str,
    demand: CustomerDemand,
    category_decision: DemandCategoryDecision,
    comparison_result: ComparisonResult,
    readiness_result: ReadinessResult | None = None,
) -> RecommendationExplanationResult:
    """LLM 推荐说明 JSON 解析失败时的程序化兜底。

    兜底只使用程序已经排序好的候选，不新增产品、不改排序、不编造价格。
    它的目标是保证交互稳定：即使模型输出格式坏了，销售侧仍能看到推荐方向和追问问题。
    """

    if not comparison_result.products:
        return RecommendationExplanationResult(
            summary="当前没有可推荐候选产品，需要先补充产品数据或继续确认需求。",
            clarifying_questions=build_readiness_questions(readiness_result),
        )

    top_product = comparison_result.products[0]
    top_product_label = format_product_label(top_product)
    alternative_products = [
        RecommendationProductExplanation(
            document_id=product.document_id,
            product_name=product.product_name,
            reason=f"产品定位：{format_product_location(product)}。作为备选候选，可结合费用、带宽、限制和办理材料继续比较。",
        )
        for product in comparison_result.products[1:3]
    ]
    clarifying_questions = build_readiness_questions(readiness_result)
    risk_reminders = list(comparison_result.global_warnings)
    if demand.overseas_access is True:
        risk_reminders.append("客户提到海外/跨境访问诉求，需要进一步确认资源、合规和实际访问效果。")
    if readiness_result and readiness_result.assumptions:
        risk_reminders.extend(readiness_result.assumptions)

    summary = (
        f"根据当前需求“{query}”，已召回并排序候选产品。"
        f"当前首推候选方向为：{top_product_label}。"
    )
    if demand.bandwidth_need:
        summary += f"客户已提到带宽需求：{demand.bandwidth_need}。"
    elif any(intent.field == "bandwidth_need" for intent in (readiness_result.clarification_plan.intents if readiness_result else [])):
        summary += "客户尚未明确带宽，建议后续按人数、业务系统重要性和预算确认档位。"

    return RecommendationExplanationResult(
        summary=summary,
        recommended_product=RecommendationProductExplanation(
            document_id=top_product.document_id,
            product_name=top_product.product_name,
            reason=(
                f"产品定位：{format_product_location(top_product)}。"
                "该候选由程序排序为 Top1，可作为首选方向继续核对带宽、价格、协议期和办理材料。"
            ),
        ),
        alternative_products=alternative_products,
        comparison_summary=[
            f"{product.product_name}：综合得分 {product.final_score}，召回得分 {product.retrieval_score}。"
            for product in comparison_result.products[:3]
        ],
        risk_reminders=dedupe_texts(risk_reminders),
        clarifying_questions=clarifying_questions,
        sales_talk=summary,
        evidence_notes=[
            "本说明为 LLM 输出格式异常后的程序兜底说明，只解释程序已排序候选，不改变推荐排序。"
        ],
    )


def format_product_label(product: ComparedProduct) -> str:
    """生成带产品目录定位的候选名称。"""

    return f"{product.product_name} [{product.category_path}]" if product.category_path else product.product_name


def format_product_location(product: ComparedProduct) -> str:
    """生成用于推荐说明的产品定位描述。"""

    parts = [
        product.carrier,
        product.region,
        product.product_family,
        product.category_path,
        product.document_type,
    ]
    return " / ".join(part for part in parts if part) or "资料中未提取到明确目录定位"


def build_readiness_questions(readiness_result: ReadinessResult | None) -> list[str]:
    """把 readiness 的追问计划转成兜底问题。"""

    if readiness_result is None:
        return []

    questions: list[str] = []
    for intent in readiness_result.clarification_plan.intents[:3]:
        if intent.field == "bandwidth_need":
            questions.append(
                "客户暂时不确定带宽也没关系，可以先确认使用人数、访问的业务系统、是否有视频会议/大文件传输，"
                "再在 50M、100M、200M、500M 或 1G 等档位里估一个合适区间。"
            )
        elif intent.example_options:
            questions.append(f"{intent.intent}：可选方向包括 {', '.join(intent.example_options[:4])}。")
        elif intent.intent:
            questions.append(intent.intent)
    return dedupe_texts(questions)


def apply_explanation_safety_guards(
    *,
    result: RecommendationExplanationResult,
    demand: CustomerDemand,
    category_decision: DemandCategoryDecision,
    comparison_result: ComparisonResult,
) -> RecommendationExplanationResult:
    """对 LLM 输出做轻量安全收口，避免销售话术出现过度承诺。

    这里不改变推荐结果，只修正表达边界，并把程序侧已经知道的风险补进提醒列表。
    """

    result.summary = soften_overcommitment(result.summary)
    result.recommended_product.reason = soften_overcommitment(result.recommended_product.reason)
    for item in result.alternative_products:
        item.reason = soften_overcommitment(item.reason)
    result.comparison_summary = [soften_overcommitment(item) for item in result.comparison_summary]
    result.sales_talk = soften_overcommitment(result.sales_talk)

    for warning in comparison_result.global_warnings:
        append_unique(result.risk_reminders, warning)
    for note in category_decision.notes:
        append_unique(result.risk_reminders, note)

    if demand.overseas_access is True:
        append_unique(
            result.risk_reminders,
            "客户提到海外/跨境访问诉求，涉及资源、合规和实际访问效果确认，不能直接承诺访问效果。",
        )
    if any(product.risk_warnings for product in comparison_result.products):
        for product in comparison_result.products:
            for warning in product.risk_warnings:
                append_unique(result.risk_reminders, warning)

    return result


def soften_overcommitment(text: str) -> str:
    """把容易被销售误读为承诺的表达降级成候选建议。"""

    replacements = {
        "能够满足": "可作为候选方向匹配",
        "可以满足": "可作为候选方向匹配",
        "确保": "有助于",
        "保证": "有助于",
        "承诺": "说明",
        "完全符合预算": "存在预算匹配可能",
        "符合您的预算限制": "存在与预算匹配的可能",
    }
    result = text
    for source, target in replacements.items():
        result = result.replace(source, target)
    return result


def append_unique(values: list[str], value: str) -> None:
    """向列表追加去重文本。"""

    normalized = value.strip()
    if normalized and normalized not in values:
        values.append(normalized)


def dedupe_texts(values: list[str]) -> list[str]:
    """去除空文本和重复文本，保持原始顺序。"""

    result: list[str] = []
    for value in values:
        append_unique(result, str(value))
    return result
