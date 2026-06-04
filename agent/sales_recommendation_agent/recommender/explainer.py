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
)
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
    ) -> RecommendationExplanationResult:
        """生成销售可读推荐说明。"""

        payload = build_explainer_payload(
            query=query,
            demand=demand,
            category_decision=category_decision,
            comparison_result=comparison_result,
        )
        prompt = build_recommendation_explainer_user_prompt(payload)
        response_text = self._invoke_llm(prompt)
        result = parse_explanation_result(response_text)
        return apply_explanation_safety_guards(
            result=result,
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
) -> dict[str, Any]:
    """把完整对象压缩成 LLM 需要看的推荐说明上下文。"""

    return {
        "query": query,
        "demand": {
            "access_source": demand.access_source,
            "target_region": demand.target_region,
            "user_count": demand.user_count,
            "bandwidth_est_mbps": demand.bandwidth_est_mbps,
            "budget": demand.budget,
            "requires_fixed_ip": demand.requires_fixed_ip,
            "scenario_type": demand.scenario_type.value,
            "raw_keywords": demand.raw_keywords,
            "category_candidate_keywords": demand.category_candidate_keywords,
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


def apply_explanation_safety_guards(
    *,
    result: RecommendationExplanationResult,
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

    if category_decision.primary_category_id == "4":
        append_unique(
            result.risk_reminders,
            "海外访问与跨境加速涉及资源、合规和实际访问效果确认，不能直接承诺访问效果。",
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
