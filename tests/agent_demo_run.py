from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.sales_recommendation_agent.intent_parser import SalesRequirementWorkflow
from agent.sales_recommendation_agent.product_repository import ProductRepository
from agent.sales_recommendation_agent.recommender import CandidateRetriever
from config.llm_config import get_llm
from langchain_core.messages import HumanMessage
from prompts.sales_recommendation_agent_prompts import (
    build_interaction_state_prompt,
    build_sales_recommendation_answer_prompt,
)


DEFAULT_QUERY = "上海总部、深圳分公司、广州仓库三个点要内网互通，150人办公，先按半年看"


def main() -> None:
    parser = argparse.ArgumentParser(description="运行销售推荐 Agent 当前已开发到 CandidateRetriever 的完整流程。")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="销售输入的客户原始需求。")
    parser.add_argument("--published-root", default="data/product_doc_agent/published", help="已审核发布的产品 JSON 目录。")
    parser.add_argument("--top-k", type=int, default=8, help="展示召回候选数量。")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON，便于调试字段。")
    parser.add_argument("--answer", action="store_true", help="调用销售推荐提示词，生成最终推荐答案。")
    parser.add_argument("--print-answer-prompt", action="store_true", help="只打印最终推荐答案 prompt，不调用 LLM。")
    parser.add_argument("--interactive", action="store_true", help="启用最多 6 轮的 demo 轻量交互状态机。")
    parser.add_argument("--max-rounds", type=int, default=6, help="交互状态机最大轮数。")
    parser.add_argument("--llm-temperature", type=float, default=0.1, help="交互和最终答案 LLM temperature。")
    args = parser.parse_args()

    if args.interactive:
        run_interactive_demo(args)
        return

    # 当前完整流程：
    # 1. Intent Parser 抽取客户结构化需求，并输出分类候选关键词
    # 2. Demand Category Classifier 按 13 类产品需求分类体系确定主分类
    # 3. Product Repository 读取 published 产品主数据
    # 4. Candidate Retriever 按主分类和候选分类召回产品
    intent_result, product_result, retrieval_result = run_pipeline(args.query, args.published_root, args.top_k)

    if args.json:
        print(
            json.dumps(
                {
                    "query": args.query,
                    "intent": intent_result.model_dump(mode="json"),
                    "product_load": {
                        "product_count": product_result.product_count,
                        "error_count": product_result.error_count,
                        "errors": [error.model_dump(mode="json") for error in product_result.errors],
                    },
                    "retrieval": retrieval_result.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    print_debug_report(args.query, intent_result, product_result, retrieval_result)

    if args.print_answer_prompt:
        print_sales_answer_prompt(args.query, intent_result, retrieval_result)
    if args.answer:
        print_sales_answer(args.query, intent_result, retrieval_result, temperature=args.llm_temperature)


def run_pipeline(query: str, published_root: str, top_k: int) -> tuple[Any, Any, Any]:
    intent_result = SalesRequirementWorkflow().analyze(query)
    product_result = ProductRepository(published_root).load_result()
    retrieval_result = CandidateRetriever(default_top_k=top_k).retrieve(
        demand=intent_result.structured_data,
        category_decision=intent_result.category_decision,
        products=product_result.products,
        top_k=top_k,
    )
    return intent_result, product_result, retrieval_result


def print_debug_report(query: str, intent_result: Any, product_result: Any, retrieval_result: Any) -> None:
    print("客户需求：")
    print(query)
    print()

    print("1. Intent Parser 结构化结果：")
    structured_data = intent_result.structured_data
    print(f"- access_source: {structured_data.access_source}")
    print(f"- target_region: {structured_data.target_region}")
    print(f"- user_count: {structured_data.user_count}")
    print(f"- bandwidth_est_mbps: {structured_data.bandwidth_est_mbps}")
    print(f"- budget: {structured_data.budget}")
    print(f"- requires_fixed_ip: {structured_data.requires_fixed_ip}")
    print(f"- scenario_type: {structured_data.scenario_type.value}")
    print(f"- raw_keywords: {structured_data.raw_keywords}")
    print(f"- category_candidate_keywords: {structured_data.category_candidate_keywords}")
    print(f"- missing_fields: {structured_data.missing_fields}")
    print()

    print("2. Demand Category Classifier 结果：")
    category_decision = intent_result.category_decision
    print(f"- primary_category: {category_decision.primary_category_id}_{category_decision.primary_category_name}")
    print(f"- recommendation_mode: {category_decision.recommendation_mode}")
    print(f"- confidence: {category_decision.confidence}")
    print(f"- reason: {category_decision.reason}")
    if category_decision.notes:
        print(f"- notes: {category_decision.notes}")
    print()

    print("3. Product Repository 加载结果：")
    print(f"- product_count: {product_result.product_count}")
    print(f"- error_count: {product_result.error_count}")
    if product_result.errors:
        print("- errors:")
        for error in product_result.errors[:5]:
            print(f"  - {error.path}: {error.error_type} - {error.message}")
    print()

    print("4. 需求分类候选：")
    if retrieval_result.demand_categories:
        for category in retrieval_result.demand_categories:
            print(
                f"- {category.category_id}_{category.category_name} "
                f"score={category.score} keywords={category.matched_keywords}"
            )
            if category.reason:
                print(f"  reason: {category.reason}")
    else:
        print("- 未命中明确需求分类")
    print()

    if retrieval_result.clarify_questions:
        title = "5. 后续确认信息：" if retrieval_result.candidates else "5. 需要追问的信息："
        print(title)
        for question in retrieval_result.clarify_questions:
            print(f"- {question}")
        print()

    print(f"6. Candidate Retriever 召回结果 Top {len(retrieval_result.candidates)}：")
    if not retrieval_result.candidates:
        print("- 当前没有召回候选产品。")
        return

    for index, candidate in enumerate(retrieval_result.candidates, start=1):
        product = candidate.product
        display_name = product.product_name or product.title or product.filename or product.document_id
        print(f"\n[{index}] {display_name}")
        print(f"- document_id: {product.document_id}")
        print(f"- category_path: {product.category_path}")
        print(f"- score: {candidate.retrieval_score}")
        print(f"- packages: {len(product.packages)}")
        print(f"- optional_packages: {len(product.optional_packages)}")
        print(f"- fee_rules: {len(product.fee_rules)}")
        print(f"- constraints: {len(product.constraints)}")
        print(f"- matched_keywords: {candidate.matched_keywords[:10]}")
        if candidate.matched_reasons:
            print("- matched_reasons:")
            for reason in candidate.matched_reasons[:5]:
                print(f"  - {reason}")
        if candidate.warnings:
            print("- warnings:")
            for warning in candidate.warnings[:5]:
                print(f"  - {warning}")


def run_interactive_demo(args: argparse.Namespace) -> None:
    query = args.query
    history: list[dict[str, str]] = []

    print("销售推荐 Demo 交互模式")
    print(f"- 最多轮数：{args.max_rounds}")
    print(f"- 初始需求：{query}")
    print()

    for round_no in range(1, args.max_rounds + 1):
        intent_result, product_result, retrieval_result = run_pipeline(query, args.published_root, args.top_k)
        state = decide_interaction_state(
            query,
            intent_result,
            retrieval_result,
            history=history,
            round_no=round_no,
            max_rounds=args.max_rounds,
            temperature=args.llm_temperature,
        )

        print(f"====== 第 {round_no} 轮 ======")
        print(f"当前累计需求：{query}")
        print(f"业务分类：{intent_result.category_decision.primary_category_id}_{intent_result.category_decision.primary_category_name}")
        print(f"候选产品数：{retrieval_result.matched_count}")
        print(f"状态判断：{'可以推荐' if state.get('should_recommend') else '需要追问'}")
        if state.get("reason"):
            print(f"判断原因：{state['reason']}")
        print()

        if state.get("should_recommend") or round_no == args.max_rounds:
            print_debug_report(query, intent_result, product_result, retrieval_result)
            print_sales_answer(
                query,
                intent_result,
                retrieval_result,
                temperature=args.llm_temperature,
                conversation_history=format_history(history),
            )
            return

        questions = state.get("questions") or retrieval_result.clarify_questions[:2]
        questions = [question for question in questions if question][:2]
        if not questions:
            print("没有可继续追问的问题，直接生成当前推荐答案。")
            print_sales_answer(
                query,
                intent_result,
                retrieval_result,
                temperature=args.llm_temperature,
                conversation_history=format_history(history),
            )
            return

        print("请补充以下信息：")
        for index, question in enumerate(questions, start=1):
            print(f"{index}. {question}")
        try:
            user_reply = input("销售/客户补充：").strip()
        except EOFError:
            print("未收到补充信息，结束交互。")
            return
        if not user_reply:
            print("未收到补充信息，结束交互。")
            return

        history.append(
            {
                "assistant": "；".join(questions),
                "user": user_reply,
            }
        )
        query = merge_query(query, user_reply)
        print()


def decide_interaction_state(
    query: str,
    intent_result: Any,
    retrieval_result: Any,
    *,
    history: list[dict[str, str]],
    round_no: int,
    max_rounds: int,
    temperature: float,
) -> dict[str, Any]:
    prompt = build_interaction_state_prompt(
        query,
        intent_result=compact_intent_result(intent_result),
        retrieval_result=compact_retrieval_result(retrieval_result),
        conversation_history=format_history(history),
        round_no=round_no,
        max_rounds=max_rounds,
    )
    try:
        response = get_llm(temperature=temperature).invoke([HumanMessage(content=prompt)])
        payload = parse_json_object(response.content)
        if isinstance(payload, dict):
            if should_force_recommend(intent_result, retrieval_result):
                payload["should_recommend"] = True
                payload["questions"] = []
                payload["reason"] = (
                    f"{payload.get('reason', '')}；demo 兜底：已有明确需求分类和候选产品，"
                    "风险项进入最终答案确认，不阻断推荐。"
                ).strip("；")
            return payload
    except Exception as exc:  # noqa: BLE001 - demo 交互需要可降级，避免模型问题阻塞演示。
        print(f"交互状态 LLM 判断失败，使用本地兜底：{type(exc).__name__}: {exc}")

    return fallback_interaction_state(intent_result, retrieval_result)


def should_force_recommend(intent_result: Any, retrieval_result: Any) -> bool:
    return bool(
        intent_result.category_decision.primary_category_id
        and intent_result.category_decision.recommendation_mode != "clarify"
        and retrieval_result.candidates
    )


def fallback_interaction_state(intent_result: Any, retrieval_result: Any) -> dict[str, Any]:
    can_recommend = bool(intent_result.category_decision.primary_category_id and retrieval_result.candidates)
    questions = []
    if not can_recommend:
        questions = retrieval_result.clarify_questions[:2] or intent_result.category_decision.clarify_questions[:2]
    return {
        "should_recommend": can_recommend,
        "business_tag": intent_result.category_decision.primary_category_name,
        "need_summary": "",
        "known_facts": [],
        "missing_facts": intent_result.structured_data.missing_fields,
        "questions": questions,
        "reason": "本地兜底：有明确分类且存在候选产品时直接推荐，否则追问。",
    }


def build_answer_prompt(query: str, intent_result: Any, retrieval_result: Any, *, conversation_history: str = "") -> str:
    return build_sales_recommendation_answer_prompt(
        query,
        intent_result=compact_intent_result(intent_result),
        retrieval_result=compact_retrieval_result(retrieval_result),
        conversation_history=conversation_history,
    )


def print_sales_answer_prompt(query: str, intent_result: Any, retrieval_result: Any) -> None:
    print()
    print("7. 销售推荐智能体 Prompt：")
    print(build_answer_prompt(query, intent_result, retrieval_result))


def print_sales_answer(
    query: str,
    intent_result: Any,
    retrieval_result: Any,
    *,
    temperature: float,
    conversation_history: str = "",
) -> None:
    print()
    print("7. 销售推荐智能体最终答案：")
    prompt = build_answer_prompt(query, intent_result, retrieval_result, conversation_history=conversation_history)
    try:
        response = get_llm(temperature=temperature).invoke([HumanMessage(content=prompt)])
    except Exception as exc:  # noqa: BLE001 - demo 需要把模型配置问题直接展示给使用者。
        print(f"- 生成失败：{type(exc).__name__}: {exc}")
        print("- 请检查 OPENAI_API_KEY、OPENAI_BASE_URL、LLM_MODEL、LLM_TIMEOUT 等环境变量。")
        return

    print(response.content)


def compact_intent_result(intent_result: Any) -> dict[str, Any]:
    data = intent_result.model_dump(mode="json")
    structured_data = data.get("structured_data", {})
    category_decision = data.get("category_decision", {})
    return {
        "structured_data": {
            "access_source": structured_data.get("access_source"),
            "target_region": structured_data.get("target_region"),
            "target_scope": structured_data.get("target_scope"),
            "user_count": structured_data.get("user_count"),
            "bandwidth_est_mbps": structured_data.get("bandwidth_est_mbps"),
            "duration": structured_data.get("duration"),
            "budget": structured_data.get("budget"),
            "requires_fixed_ip": structured_data.get("requires_fixed_ip"),
            "scenario_type": structured_data.get("scenario_type"),
            "raw_keywords": structured_data.get("raw_keywords", []),
            "category_candidate_keywords": structured_data.get("category_candidate_keywords", []),
            "missing_fields": structured_data.get("missing_fields", []),
        },
        "category_decision": {
            "primary_category_id": category_decision.get("primary_category_id"),
            "primary_category_name": category_decision.get("primary_category_name"),
            "recommendation_mode": category_decision.get("recommendation_mode"),
            "confidence": category_decision.get("confidence"),
            "clarify_questions": category_decision.get("clarify_questions", []),
            "notes": category_decision.get("notes", []),
            "reason": category_decision.get("reason"),
        },
    }


def compact_retrieval_result(retrieval_result: Any, *, max_candidates: int = 5, max_packages: int = 3) -> dict[str, Any]:
    candidates = []
    for item in retrieval_result.candidates[:max_candidates]:
        product = item.product
        candidates.append(
            {
                "document_id": product.document_id,
                "product_name": product.product_name or product.title or product.filename,
                "product_family": product.product_family,
                "category_path": product.category_path,
                "retrieval_score": item.retrieval_score,
                "matched_keywords": item.matched_keywords[:10],
                "matched_reasons": item.matched_reasons[:5],
                "warnings": item.warnings[:5],
                "packages": [
                    {
                        "package_name": package.package_name,
                        "speed": package.speed,
                        "price": package.price,
                        "currency": package.currency,
                        "billing_period": package.billing_period,
                        "contract_period": package.contract_period,
                        "source_evidence": package.source_evidence,
                    }
                    for package in product.packages[:max_packages]
                ],
                "optional_packages": [
                    {
                        "name": optional.name,
                        "fee_summary": optional.fee_summary,
                        "source_evidence": optional.source_evidence,
                    }
                    for optional in product.optional_packages[:max_packages]
                ],
            }
        )

    return {
        "primary_category_id": retrieval_result.primary_category_id,
        "primary_category_name": retrieval_result.primary_category_name,
        "recommendation_mode": retrieval_result.recommendation_mode,
        "total_products": retrieval_result.total_products,
        "matched_count": retrieval_result.matched_count,
        "demand_categories": [item.model_dump(mode="json") for item in retrieval_result.demand_categories],
        "clarify_questions": retrieval_result.clarify_questions,
        "candidates": candidates,
    }


def parse_json_object(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def merge_query(query: str, user_reply: str) -> str:
    return f"{query}\n补充信息：{user_reply}"


def format_history(history: list[dict[str, str]]) -> str:
    if not history:
        return ""
    lines: list[str] = []
    for index, item in enumerate(history, start=1):
        lines.append(f"第{index}轮追问：{item['assistant']}")
        lines.append(f"第{index}轮补充：{item['user']}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
