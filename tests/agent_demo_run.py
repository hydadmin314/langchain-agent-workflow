from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.sales_recommendation_agent.intent_parser import SalesRequirementWorkflow
from agent.sales_recommendation_agent.product_repository import ProductRepository
from agent.sales_recommendation_agent.recommender import (
    CandidateComparator,
    CandidateRetriever,
    CandidateRuleFilter,
    CandidateScorer,
    RecommendationExplainer,
)
from config.llm_config import get_llm
from langchain_core.messages import HumanMessage
from prompts.sales_recommendation_agent_prompts import (
    build_interaction_state_prompt,
    build_sales_recommendation_answer_prompt,
)


DEFAULT_QUERY = "餐饮门店5个人用，主要收银、外卖平台、监控和日常上网，预算有限，想要稳定宽带"


CATEGORY_QUESTION_TEMPLATES: dict[str, list[str]] = {
    "1": [
        "客户主要是日常办公上网、访问内部系统，还是访问外部 SaaS？",
        "大约多少人或终端使用，是否有视频会议、上传文件等高带宽场景？",
        "期望带宽、预算范围和月付/年付偏好是什么？",
        "官网或服务器是否需要长期通过同一个固定的网络地址供外部访问？另外，是否需要无线覆盖或安全防护？",
        "客户希望什么时候开通，是否需要先试用或临时方案？",
    ],
    "2": [
        "官网或服务器主要承载什么业务？是否需要长期通过同一个固定的网络地址供外部访问？",
        "这些对外业务预计有多少访问量，对上传速度和下载速度分别有什么要求？",
        "是否已经有域名和备案？是否需要基础防火墙或防攻击能力？",
        "预算范围、协议期和期望开通时间是什么？",
    ],
    "3": [
        "需要互联的站点有几个，分别在哪些城市或地址？",
        "是点对点互联，还是总部、分公司、仓库等多点组网？",
        "每个站点大约多少人或终端使用，核心系统是什么？",
        "是否有时延、带宽、备份线路或安全隔离要求？",
        "预算范围、开通周期和月付/年付偏好是什么？",
    ],
    "4": [
        "海外 SaaS 或服务器具体在哪个国家/地区，主要访问什么应用？",
        "访问来源有几个地点，各有多少人或终端？",
        "当前慢的表现是什么，例如登录慢、下载慢、视频会议卡顿？",
        "是否有官网或服务器需要长期使用同一个网络地址供外部访问？另外，是否涉及备案或其他合规要求？",
        "预算范围、试用周期和期望开通时间是什么？",
    ],
    "5": [
        "这是单门店还是多门店，门店大致地址或所在区域在哪里？",
        "收银和外卖平台是否必须持续在线，断网会不会直接影响营业？",
        "监控是本地存储还是云端上传，大概有多少路摄像头？",
        "店内是否需要无线网络覆盖？员工和顾客是否要使用不同的网络？主线路故障时是否需要一条备用网络？",
        "预算有限是大概多少金额，偏向年付、月付还是先试用？",
        "下月开通前是否需要上门勘查、资源覆盖确认或临时过渡方案？",
    ],
    "6": [
        "这是新建呼叫中心，还是已有总机/PBX/号码需要改造或迁移？",
        "30 个坐席中，最忙时预计有多少人会同时通话？是否所有坐席都需要拨打外部电话？",
        "是否需要保留原号码、统一总机号码或分机号？外部客户是否需要直接拨到具体坐席？",
        "客户来电后是否需要按键语音导航？另外是否需要录音、来电转接、黑白名单或外呼频次控制？",
        "是否有外呼合规、实名材料、授权盖章或行业监管要求？",
        "预算范围、月付/年付偏好、期望开通时间是什么？",
        "海外 SaaS 是呼叫中心系统本身，还是另一个独立的网络访问需求？",
    ],
    "13": [
        "客户要办理新装、变更、续约、拆机、移机还是过户？",
        "原业务号码、合同编号或客户名称是否已经明确？",
        "是否已经准备营业执照、经办人证件、授权书和盖章材料？",
        "期望办理完成时间是什么，是否有停机或不中断要求？",
    ],
}


@dataclass
class PipelineResult:
    intent: Any
    product_load: Any
    retrieval: Any
    filter: Any
    score: Any
    comparison: Any
    explanation: Any | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description="运行销售推荐 Agent demo，支持最多 6 轮交互测试。")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="销售输入的客户原始需求。")
    parser.add_argument("--published-root", default="data/product_doc_agent/published", help="已发布产品 JSON 目录。")
    parser.add_argument("--top-k", type=int, default=8, help="展示和召回候选数量。")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON，便于调试字段。")
    parser.add_argument("--skip-explainer", action="store_true", help="跳过新版 RecommendationExplainer。")
    parser.add_argument("--answer", action="store_true", help="调用销售推荐提示词生成最终答案。")
    parser.add_argument("--print-answer-prompt", action="store_true", help="只打印最终答案 prompt，不调用 LLM。")
    parser.add_argument("--interactive", action="store_true", help="启用最多 6 轮 demo 交互状态机。")
    parser.add_argument("--max-rounds", type=int, default=6, help="交互最大轮数。")
    parser.add_argument("--min-rounds-before-answer", type=int, default=1, help="至少交互几轮后才允许最终回答。")
    parser.add_argument("--llm-temperature", type=float, default=0.1, help="交互判断和最终答案的 LLM temperature。")
    args = parser.parse_args()

    if args.interactive:
        run_interactive_demo(args)
        return

    result = run_pipeline(
        args.query,
        args.published_root,
        args.top_k,
        run_explainer=not args.skip_explainer,
    )

    if args.json:
        print(json.dumps(build_json_payload(args.query, result), ensure_ascii=False, indent=2))
        return

    print_debug_report(args.query, result)

    if args.print_answer_prompt:
        print_sales_answer_prompt(args.query, result)
    if args.answer:
        print_sales_answer(args.query, result, temperature=args.llm_temperature)


def run_pipeline(query: str, published_root: str, top_k: int, *, run_explainer: bool = False) -> PipelineResult:
    intent_result = SalesRequirementWorkflow().analyze(query)
    product_result = ProductRepository(published_root).load_result()
    retrieval_result = CandidateRetriever(default_top_k=top_k).retrieve(
        demand=intent_result.structured_data,
        category_decision=intent_result.category_decision,
        products=product_result.products,
        top_k=top_k,
    )
    filter_result = CandidateRuleFilter().apply(
        demand=intent_result.structured_data,
        category_decision=intent_result.category_decision,
        retrieval_result=retrieval_result,
    )
    score_result = CandidateScorer().score(
        demand=intent_result.structured_data,
        category_decision=intent_result.category_decision,
        filter_result=filter_result,
        top_k=top_k,
    )
    comparison_result = CandidateComparator().compare(
        demand=intent_result.structured_data,
        category_decision=intent_result.category_decision,
        score_result=score_result,
        top_n=min(top_k, 3),
    )
    explanation_result = None
    if run_explainer:
        explanation_result = RecommendationExplainer().explain(
            query=query,
            demand=intent_result.structured_data,
            category_decision=intent_result.category_decision,
            comparison_result=comparison_result,
        )
    return PipelineResult(
        intent=intent_result,
        product_load=product_result,
        retrieval=retrieval_result,
        filter=filter_result,
        score=score_result,
        comparison=comparison_result,
        explanation=explanation_result,
    )


def build_json_payload(query: str, result: PipelineResult) -> dict[str, Any]:
    return {
        "query": query,
        "intent": result.intent.model_dump(mode="json"),
        "product_load": {
            "product_count": result.product_load.product_count,
            "error_count": result.product_load.error_count,
            "errors": [error.model_dump(mode="json") for error in result.product_load.errors],
        },
        "retrieval": result.retrieval.model_dump(mode="json"),
        "filter": result.filter.model_dump(mode="json"),
        "score": result.score.model_dump(mode="json"),
        "comparison": result.comparison.model_dump(mode="json"),
        "explanation": result.explanation.model_dump(mode="json") if result.explanation else None,
    }


def print_debug_report(query: str, result: PipelineResult) -> None:
    intent_result = result.intent
    retrieval_result = result.retrieval
    filter_result = result.filter
    score_result = result.score
    comparison_result = result.comparison
    explanation_result = result.explanation

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
    print(f"- product_count: {result.product_load.product_count}")
    print(f"- error_count: {result.product_load.error_count}")
    if result.product_load.errors:
        print("- errors:")
        for error in result.product_load.errors[:5]:
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
        print("- 未命中明确需求分类。")
    print()

    if retrieval_result.clarify_questions:
        title = "5. 后续确认信息：" if retrieval_result.candidates else "5. 需要追问的信息："
        print(title)
        for question in retrieval_result.clarify_questions[:8]:
            print(f"- {question}")
        print()

    print(f"6. Candidate Retriever 召回结果 Top {len(retrieval_result.candidates)}：")
    if not retrieval_result.candidates:
        print("- 当前没有召回候选产品。")
        return

    print(f"- Rule Filter: 保留 {filter_result.kept_count} 个，移除 {filter_result.removed_count} 个")
    if filter_result.global_warnings:
        for warning in filter_result.global_warnings:
            print(f"  warning: {warning}")

    for index, scored in enumerate(score_result.scored_candidates, start=1):
        filtered = scored.filtered_candidate
        candidate = filtered.candidate
        product = candidate.product
        display_name = product.product_name or product.title or product.filename or product.document_id
        print(f"\n[{index}] {display_name}")
        print(f"- document_id: {product.document_id}")
        print(f"- category_path: {product.category_path}")
        print(f"- final_score: {scored.final_score}")
        print(f"- retrieval_score: {candidate.retrieval_score}")
        print(f"- packages: {len(product.packages)}")
        print(f"- optional_packages: {len(product.optional_packages)}")
        print(f"- fee_rules: {len(product.fee_rules)}")
        print(f"- constraints: {len(product.constraints)}")
        print(f"- matched_keywords: {candidate.matched_keywords[:10]}")
        if candidate.matched_reasons:
            print("- matched_reasons:")
            for reason in candidate.matched_reasons[:5]:
                print(f"  - {reason}")
        if filtered.filter_reasons:
            print("- filter_reasons:")
            for reason in filtered.filter_reasons[:5]:
                print(f"  - {reason.severity} | {reason.code}: {reason.message}")
        if scored.score_reasons:
            print("- score_reasons:")
            for reason in scored.score_reasons[:5]:
                print(f"  - {reason.score_delta:+.1f} | {reason.code}: {reason.message}")
        if scored.risk_penalties:
            print("- risk_penalties:")
            for penalty in scored.risk_penalties[:5]:
                print(f"  - {penalty.score_delta:+.1f} | {penalty.code}: {penalty.message}")

    print("\n7. Comparator 结构化对比摘要：")
    if not comparison_result.products:
        print("- 当前没有可对比的候选产品。")
    else:
        for item in comparison_result.products:
            print(f"\n[{item.rank}] {item.product_name}")
            print(f"- final_score: {item.final_score}")
            print(f"- package_count: {item.package_summary.package_count}")
            print(f"- price_range: {item.package_summary.price_range or '未提取'}")
            print(f"- optional_package_count: {item.optional_package_summary.optional_package_count}")
            print(f"- constraint_count: {item.constraint_summary.constraint_count}")
            if item.risk_warnings:
                print(f"- risks: {item.risk_warnings[:3]}")
            if item.missing_info:
                print(f"- missing_info: {item.missing_info[:3]}")

    print("\n8. LLM Recommendation Explainer 推荐说明：")
    if explanation_result is None:
        print("- 未运行新版 Explainer；如需测试可去掉 --skip-explainer。")
    else:
        print(f"- summary: {explanation_result.summary}")
        print(
            "- recommended_product: "
            f"{explanation_result.recommended_product.product_name} "
            f"({explanation_result.recommended_product.document_id})"
        )
        print(f"  reason: {explanation_result.recommended_product.reason}")
        if explanation_result.risk_reminders:
            print("- risk_reminders:")
            for item in explanation_result.risk_reminders:
                print(f"  - {item}")
        print(f"- sales_talk: {explanation_result.sales_talk}")


def run_interactive_demo(args: argparse.Namespace) -> None:
    query = args.query
    history: list[dict[str, str]] = []

    print("销售推荐 Demo 交互模式")
    print(f"- 最多轮数：{args.max_rounds}")
    print(f"- 最少回答前轮数：{args.min_rounds_before_answer}")
    print(f"- 初始需求：{query}")
    print()

    for round_no in range(1, args.max_rounds + 1):
        result = run_pipeline(query, args.published_root, args.top_k, run_explainer=False)
        state = decide_interaction_state(
            query,
            result,
            history=history,
            round_no=round_no,
            max_rounds=args.max_rounds,
            min_rounds_before_answer=args.min_rounds_before_answer,
            temperature=args.llm_temperature,
        )

        category_decision = result.intent.category_decision
        print(f"====== 第 {round_no} 轮 ======")
        print(f"当前累计需求：{query}")
        print(f"业务分类：{category_decision.primary_category_id}_{category_decision.primary_category_name}")
        display_category_id, display_category_name = display_interaction_category(result)
        if display_category_id != category_decision.primary_category_id:
            print(f"交互场景：{display_category_id}_{display_category_name}")
        print(f"候选产品数：{result.retrieval.matched_count}")
        print(f"过滤后候选数：{result.score.scored_count}")
        print(f"状态判断：{'可以推荐' if state.get('should_recommend') else '需要追问'}")
        if state.get("reason"):
            print(f"判断原因：{state['reason']}")
        print()

        if state.get("should_recommend") or round_no == args.max_rounds:
            print_debug_report(query, result)
            print_sales_answer(
                query,
                result,
                temperature=args.llm_temperature,
                conversation_history=format_history(history),
            )
            return

        question_items = select_question_items(state, result, history)
        if not question_items:
            if round_no < args.min_rounds_before_answer:
                question_items = build_question_items(build_confirmation_questions(result, history))
            else:
                print("没有可继续追问的问题，直接生成当前推荐答案。")
                print_sales_answer(
                    query,
                    result,
                    temperature=args.llm_temperature,
                    conversation_history=format_history(history),
                )
                return

        acknowledgement = state.get("acknowledgement", "")
        transition = state.get("transition", "")
        if acknowledgement:
            print(acknowledgement)
        if transition:
            print(transition)
        for index, item in enumerate(question_items, start=1):
            print(f"{index}. {item['text']}")
            if item.get("helper"):
                print(f"   说明：{item['helper']}")
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
                "assistant": format_turn_message(acknowledgement, transition, question_items),
                "user": user_reply,
            }
        )
        query = merge_query(query, user_reply)
        print()


def decide_interaction_state(
    query: str,
    result: PipelineResult,
    *,
    history: list[dict[str, str]],
    round_no: int,
    max_rounds: int,
    min_rounds_before_answer: int,
    temperature: float,
) -> dict[str, Any]:
    prompt = build_interaction_state_prompt(
        query,
        intent_result=compact_intent_result(result.intent),
        retrieval_result=compact_pipeline_context(result),
        conversation_history=format_history(history),
        round_no=round_no,
        max_rounds=max_rounds,
    )
    try:
        response = get_llm(temperature=temperature).invoke([HumanMessage(content=prompt)])
        payload = parse_json_object(response.content)
        if isinstance(payload, dict):
            return normalize_interaction_state(
                payload,
                result,
                history=history,
                round_no=round_no,
                max_rounds=max_rounds,
                min_rounds_before_answer=min_rounds_before_answer,
            )
    except Exception as exc:  # noqa: BLE001 - demo needs a local fallback.
        print(f"交互状态 LLM 判断失败，使用本地兜底：{type(exc).__name__}: {exc}")

    return normalize_interaction_state(
        fallback_interaction_state(result),
        result,
        history=history,
        round_no=round_no,
        max_rounds=max_rounds,
        min_rounds_before_answer=min_rounds_before_answer,
    )


def normalize_interaction_state(
    state: dict[str, Any],
    result: PipelineResult,
    *,
    history: list[dict[str, str]],
    round_no: int,
    max_rounds: int,
    min_rounds_before_answer: int,
) -> dict[str, Any]:
    if round_no >= max_rounds:
        state["should_recommend"] = True
        state["questions"] = []
        state["reason"] = append_reason(state.get("reason"), "已到最大轮数，输出当前最优推荐。")
        return enrich_interaction_state(state, result, history)

    if round_no < min_rounds_before_answer:
        state["should_recommend"] = False
        state["questions"] = select_missing_fact_questions(result, history) or build_confirmation_questions(result, history)
        state["transition"] = ""
        state["reason"] = append_reason(state.get("reason"), "未达到最少交互轮数，继续补齐关键需求。")
        return enrich_interaction_state(state, result, history)

    if not can_recommend(result):
        state["should_recommend"] = False
        state["questions"] = state.get("questions") or select_missing_fact_questions(result, history)
        return enrich_interaction_state(state, result, history)

    if should_continue_collecting(result, history):
        state["should_recommend"] = False
        state["questions"] = select_missing_fact_questions(result, history)
        state["transition"] = ""
        state["reason"] = append_reason(
            state.get("reason"),
            "已有候选产品，但仍缺少影响套餐和报价的关键信息，先继续追问。",
        )
        return enrich_interaction_state(state, result, history)

    state["should_recommend"] = True
    state["questions"] = []
    state["reason"] = append_reason(
        state.get("reason"),
        "业务分类、候选产品和排序结果已具备，进入最终推荐。",
    )
    return enrich_interaction_state(state, result, history)


def can_recommend(result: PipelineResult) -> bool:
    category = result.intent.category_decision
    display_category_id, display_category_name = display_interaction_category(result)
    return bool(
        category.primary_category_id
        and category.recommendation_mode != "clarify"
        and result.score.scored_candidates
    )


def should_continue_collecting(result: PipelineResult, history: list[dict[str, str]]) -> bool:
    if len(history) >= 2:
        return False
    missing = set(result.intent.structured_data.missing_fields)
    important_missing = missing.intersection({"user_count", "budget", "duration"})
    if len(important_missing) >= 2:
        return True
    if "user_count" in important_missing and not has_any_text(history, ("人", "终端", "门店", "坐席", "号码")):
        return True
    return False


def fallback_interaction_state(result: PipelineResult) -> dict[str, Any]:
    can_answer = can_recommend(result)
    return {
        "should_recommend": can_answer,
        "business_tag": result.intent.category_decision.primary_category_name,
        "need_summary": "",
        "known_facts": [],
        "missing_facts": result.intent.structured_data.missing_fields,
        "acknowledgement": "",
        "transition": "",
        "questions": [] if can_answer else select_missing_fact_questions(result, []),
        "reason": "本地兜底：有明确分类和打分候选时可推荐，否则继续追问。",
    }


def enrich_interaction_state(
    state: dict[str, Any],
    result: PipelineResult,
    history: list[dict[str, str]],
) -> dict[str, Any]:
    if state.get("should_recommend"):
        return state
    question_items = build_question_items(state.get("questions", []))
    state["questions"] = question_items
    state["acknowledgement"] = str(state.get("acknowledgement") or build_local_acknowledgement(history, result))
    state["transition"] = str(state.get("transition") or build_local_transition(question_items, result))
    return state


def select_question_items(
    state: dict[str, Any],
    result: PipelineResult,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    items = build_question_items(state.get("questions", []))
    if not items:
        items = build_question_items(select_missing_fact_questions(result, history))
    return items[:2]


def build_question_items(questions: Any) -> list[dict[str, str]]:
    if not isinstance(questions, list):
        return []
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for question in questions:
        if isinstance(question, dict):
            text = str(question.get("text") or "").strip()
            helper = str(question.get("helper") or "").strip()
        else:
            text = str(question or "").strip()
            helper = ""
        if not text or text in seen:
            continue
        seen.add(text)
        items.append({"text": text, "helper": helper or helper_for_question(text)})
    return items


def build_local_acknowledgement(
    history: list[dict[str, str]],
    result: PipelineResult,
) -> str:
    if not history:
        return "好的，我先了解一下您的实际使用场景，这样后面的方案会更贴合。"
    reply = history[-1].get("user", "").strip()
    if contains_any(reply, ("年付", "月付", "预算", "元", "万")):
        return "好的，我理解您已经补充了预算和计费偏好，我们可以进一步缩小合适方案的范围。"
    if contains_any(reply, ("断网", "不中断", "持续在线", "稳定", "备份", "备用")):
        return "好的，我理解您比较看重业务持续可用，网络中断会对实际经营或办公造成影响。"
    if contains_any(reply, ("监控", "摄像头", "云端上传", "Wi-Fi", "无线")):
        return "好的，我理解除了日常上网，监控和无线网络也是这次方案需要一起考虑的部分。"
    if contains_any(reply, ("坐席", "并发", "外呼", "总机", "号码")):
        return "好的，我理解您对坐席通话规模和号码能力已经有了比较明确的要求。"
    if contains_any(reply, ("总部", "分公司", "站点", "上海", "新疆", "地址")):
        return "好的，我理解您已经补充了主要使用地点和业务连接范围。"
    category_name = display_interaction_category(result)[1] or "当前业务"
    return f"好的，我理解您补充的信息会影响“{category_name}”方案的选择，我已经记录下来了。"


def build_local_transition(
    question_items: list[dict[str, str]],
    result: PipelineResult,
) -> str:
    question_text = " ".join(item["text"] for item in question_items)
    if contains_any(question_text, ("预算", "年付", "月付", "费用")):
        topic = "预算和计费方式"
    elif contains_any(question_text, ("开通", "什么时候", "周期", "试用")):
        topic = "开通安排"
    elif contains_any(question_text, ("监控", "无线", "Wi-Fi", "备用网络")):
        topic = "门店内的其他网络使用情况"
    elif contains_any(question_text, ("坐席", "号码", "语音导航", "外呼")):
        topic = "电话和坐席的具体使用方式"
    elif contains_any(question_text, ("地址", "地点", "站点", "门店")):
        topic = "实际使用地点和覆盖范围"
    elif contains_any(question_text, ("固定", "官网", "服务器", "外部访问")):
        topic = "官网或服务器对外访问的方式"
    else:
        topic = "方案必须满足的实际使用要求"
    return f"接下来，我想再了解一下{topic}。"


def helper_for_question(question: str) -> str:
    if contains_any(question, ("固定公网 IP", "固定公网IP", "固定的网络地址")):
        return "固定公网 IP 可以理解为一个长期不变化、可供外部访问的互联网地址，常用于官网、服务器或远程访问。"
    if contains_any(question, ("备用网络", "备线")):
        return "备用网络是主线路故障时用于临时接替的网络，可以减少收银、外卖或办公中断。"
    if contains_any(question, ("上下行对称", "上下行带宽")):
        return "上行是上传数据的速度，下行是下载数据的速度；上下行对称表示两者速度相同。"
    if contains_any(question, ("语音导航", "IVR")):
        return "语音导航就是客户打电话后听到的“按1、按2”菜单。"
    if contains_any(question, ("直接拨到具体坐席", "DID")):
        return "这类号码可以让外部客户绕过总机，直接拨到指定坐席。"
    if contains_any(question, ("DDoS", "恶意访问")):
        return "DDoS 防护用于降低服务器被大量恶意访问冲垮的风险。"
    if contains_any(question, ("PBX",)):
        return "PBX 可以理解为企业内部管理分机和外线电话的总机系统。"
    return ""


def format_turn_message(
    acknowledgement: str,
    transition: str,
    question_items: list[dict[str, str]],
) -> str:
    parts = [item for item in (acknowledgement, transition) if item]
    for item in question_items:
        parts.append(item["text"])
        if item.get("helper"):
            parts.append(f"说明：{item['helper']}")
    return "\n".join(parts)


def select_missing_fact_questions(result: PipelineResult, history: list[dict[str, str]]) -> list[str]:
    asked_text = format_history(history)
    structured = result.intent.structured_data
    questions: list[str] = []
    category_id = effective_interaction_category_id(result)

    questions.extend(select_category_questions(category_id, asked_text))

    # If a business category is already clear, keep follow-up questions close to
    # that category. Generic source/target questions are only used before the
    # category is known, otherwise they can pull the conversation off track.
    if category_id:
        if "budget" in structured.missing_fields and not contains_any(asked_text, ("预算", "费用")):
            questions.append("预算范围、月付/年付偏好、期望开通时间是什么？")
        return [question for question in dedupe_questions(questions) if question and question not in asked_text][:1]

    if "access_source" in structured.missing_fields:
        questions.append("客户从哪里访问或使用业务？例如上海办公室、门店、总部或分公司。")
    if "target_region" in structured.missing_fields:
        questions.append("客户要访问哪里或办理什么业务？例如国内总部、海外 SaaS、美国服务器、固定电话或 IDC。")

    if "user_count" in structured.missing_fields and not contains_any(asked_text, ("多少人", "终端", "门店", "坐席", "号码")):
        questions.append("预计多少人、多少终端、多少门店或多少坐席使用？")
    if "budget" in structured.missing_fields and not contains_any(asked_text, ("预算", "费用")):
        questions.append("客户大致预算是多少，偏向月付、年付还是先试用？")
    if "duration" in structured.missing_fields and not contains_any(asked_text, ("试用", "月付", "年付", "签约")):
        questions.append("客户希望试用、月付、年付，还是签约半年或一年以上？")
    if not questions:
        questions.extend(result.intent.category_decision.clarify_questions[:2])
    if not questions:
        questions.append("客户目前最想解决的实际问题是什么？例如访问更快、多个地点互联、降低成本，或者办理业务变更。")
    return [
        question
        for question in dedupe_questions(questions)
        if question and question not in asked_text and not is_known_fact_question(question, structured)
    ][:1]


def select_category_questions(category_id: str, asked_text: str) -> list[str]:
    template = CATEGORY_QUESTION_TEMPLATES.get(category_id, [])
    return [question for question in template if question and question not in asked_text][:1]


def build_confirmation_questions(result: PipelineResult, history: list[dict[str, str]]) -> list[str]:
    asked_text = format_history(history)
    category_id = effective_interaction_category_id(result)
    template = CATEGORY_QUESTION_TEMPLATES.get(category_id, [])
    remaining = [question for question in template if question not in asked_text]
    if remaining:
        return remaining[:2]
    return [
        "您这边有没有一些比较明确的、方案必须满足的要求？例如业务不能中断、必须按期上线，或者需要更高的安全保障。",
        "除了目前讨论的主要需求，是否还有电话、云服务、其他网络地点或办理材料需要一起考虑？",
    ][:1]


def build_answer_prompt(query: str, result: PipelineResult, *, conversation_history: str = "") -> str:
    return build_sales_recommendation_answer_prompt(
        query,
        intent_result=compact_intent_result(result.intent),
        retrieval_result=compact_pipeline_context(result),
        conversation_history=conversation_history,
    )


def print_sales_answer_prompt(query: str, result: PipelineResult) -> None:
    print()
    print("9. 销售推荐智能体 Prompt：")
    print(build_answer_prompt(query, result))


def print_sales_answer(
    query: str,
    result: PipelineResult,
    *,
    temperature: float,
    conversation_history: str = "",
) -> None:
    print()
    print("9. 销售推荐智能体最终答案：")
    prompt = build_answer_prompt(query, result, conversation_history=conversation_history)
    try:
        response = get_llm(temperature=temperature).invoke([HumanMessage(content=prompt)])
    except Exception as exc:  # noqa: BLE001 - show demo config problems directly.
        print(f"- 生成失败：{type(exc).__name__}: {exc}")
        print("- 已使用本地兜底答案，便于 demo 继续验证流程。")
        print()
        print(build_local_sales_answer(query, result))
        return
    print(response.content)


def build_local_sales_answer(query: str, result: PipelineResult) -> str:
    display_category_id, display_category_name = display_interaction_category(result)
    if not result.score.scored_candidates:
        questions = result.retrieval.clarify_questions or result.intent.category_decision.clarify_questions
        question_text = "\n".join(f"{index}. {question}" for index, question in enumerate(questions[:3], start=1))
        return "\n".join(
            [
                "【推荐结论】",
                "当前信息不足，暂不强行推荐产品，需要先补充客户业务场景。",
                "",
                "【客户需求理解】",
                f"- 当前需求：{query}",
                "- 现状：未命中明确产品分类或没有可用候选产品。",
                "",
                "【下一步动作】",
                question_text or "1. 请补充客户访问来源、访问目标、人数/终端数、预算和使用周期。",
            ]
        )

    ordered_candidates = ordered_scored_candidates_for_demo(result)
    top = ordered_candidates[0]
    product = top.filtered_candidate.candidate.product
    alternatives = ordered_candidates[1:3]
    display_name = product.product_name or product.title or product.filename or product.document_id
    package_text = summarize_packages(product.packages)
    risk_text = summarize_risks(top.filtered_candidate.filter_reasons, result.comparison.global_warnings)
    alternative_text = summarize_alternatives(alternatives)

    return "\n".join(
        [
            "【推荐结论】",
            f"主推“{display_name}”，当前程序打分最高，可作为客户需求的优先推荐方向。",
            "",
            "【客户需求理解】",
            f"- 客户场景：{query}",
            f"- 业务分类：{display_category_id}_{display_category_name}",
            f"- 已知约束：人数={result.intent.structured_data.user_count}，预算={result.intent.structured_data.budget}，周期={result.intent.structured_data.duration or '待确认'}",
            "",
            "【主推方案】",
            f"- 产品/方案：{display_name}",
            f"- 推荐理由：候选召回分 {top.filtered_candidate.candidate.retrieval_score}，最终分 {top.final_score}，命中关键词 {top.filtered_candidate.candidate.matched_keywords[:6]}。",
            f"- 套餐/价格参考：{package_text}",
            "- 适用边界：以上仅基于 published 产品 JSON 和程序打分，资费、资源、开通条件需人工确认。",
            "",
            "【备选方案】",
            alternative_text,
            "",
            "【风险与人工确认】",
            risk_text,
            "",
            "【下一步动作】",
            "1. 与客户确认人数/终端规模、预算、试用或签约周期。",
            "2. 核实主推产品在客户地址和目标区域的资源、资费与开通条件。",
            "3. 将确认后的字段回填 demo，再跑一次交互推荐。",
        ]
    )


def summarize_packages(packages: list[Any]) -> str:
    if not packages:
        return "候选资料未给出明确套餐/价格，需要人工确认。"
    parts: list[str] = []
    for package in packages[:3]:
        name = package.package_name or package.speed or "套餐"
        price = f"{package.price}{package.currency or '元'}" if package.price is not None else "价格待确认"
        period = f"/{package.billing_period}" if package.billing_period else ""
        parts.append(f"{name} {price}{period}")
    return "；".join(parts)


def summarize_risks(filter_reasons: list[Any], global_warnings: list[str]) -> str:
    risks = [reason.message for reason in filter_reasons if getattr(reason, "severity", "") in {"warning", "error"}]
    risks.extend(global_warnings)
    if not risks:
        return "- 当前程序未发现明确过滤风险，但价格、资源、协议期仍需人工确认。"
    return "\n".join(f"- {risk}" for risk in risks[:5])


def summarize_alternatives(alternatives: list[Any]) -> str:
    if not alternatives:
        return "- 暂无明确备选，建议先核实主推方案资源和资费。"
    lines: list[str] = []
    for item in alternatives:
        product = item.filtered_candidate.candidate.product
        display_name = product.product_name or product.title or product.filename or product.document_id
        lines.append(f"- {display_name}：最终分 {item.final_score}，可作为备选核实。")
    return "\n".join(lines)


def compact_intent_result(intent_result: Any) -> dict[str, Any]:
    data = intent_result.model_dump(mode="json")
    structured_data = data.get("structured_data", {})
    category_decision = data.get("category_decision", {})
    return {
        "structured_data": {
            "access_source": structured_data.get("access_source"),
            "source_scope": structured_data.get("source_scope"),
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


def compact_pipeline_context(result: PipelineResult, *, max_candidates: int = 5, max_packages: int = 4) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for scored in ordered_scored_candidates_for_demo(result)[:max_candidates]:
        filtered = scored.filtered_candidate
        candidate = filtered.candidate
        product = candidate.product
        candidates.append(
            {
                "document_id": product.document_id,
                "product_name": product.product_name or product.title or product.filename,
                "product_family": product.product_family,
                "category_path": product.category_path,
                "final_score": scored.final_score,
                "retrieval_score": candidate.retrieval_score,
                "matched_keywords": candidate.matched_keywords[:10],
                "matched_reasons": candidate.matched_reasons[:5],
                "filter_reasons": [reason.model_dump(mode="json") for reason in filtered.filter_reasons[:4]],
                "score_reasons": [reason.model_dump(mode="json") for reason in scored.score_reasons[:5]],
                "risk_penalties": [reason.model_dump(mode="json") for reason in scored.risk_penalties[:4]],
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
                "fee_rules": [
                    {
                        "name": rule.name,
                        "amount": rule.amount,
                        "billing_period": rule.billing_period,
                        "description": rule.description,
                        "source_evidence": rule.source_evidence,
                    }
                    for rule in product.fee_rules[:max_packages]
                ],
            }
        )

    return {
        "primary_category_id": result.retrieval.primary_category_id,
        "primary_category_name": result.retrieval.primary_category_name,
        "interaction_category": {
            "category_id": display_interaction_category(result)[0],
            "category_name": display_interaction_category(result)[1],
            "note": "Demo 交互层用于追问和最终答案组织；当门店/餐饮/收银/外卖/监控与办公宽带同时命中时，优先按门店小微经营处理。",
        },
        "recommendation_mode": result.retrieval.recommendation_mode,
        "total_products": result.retrieval.total_products,
        "retrieved_count": result.retrieval.matched_count,
        "kept_count": result.filter.kept_count,
        "scored_count": result.score.scored_count,
        "demand_categories": [item.model_dump(mode="json") for item in result.retrieval.demand_categories],
        "clarify_questions": result.retrieval.clarify_questions,
        "global_warnings": result.score.global_warnings,
        "comparison_dimensions": [item.model_dump(mode="json") for item in result.comparison.comparison_dimensions],
        "compared_products": [item.model_dump(mode="json") for item in result.comparison.products[:3]],
        "candidates": candidates,
    }


def ordered_scored_candidates_for_demo(result: PipelineResult) -> list[Any]:
    items = list(result.score.scored_candidates)
    category_id = effective_interaction_category_id(result)
    if category_id == "6":
        return sorted(items, key=lambda item: (voice_direct_priority(item), item.final_score), reverse=True)
    if category_id == "5":
        return sorted(items, key=lambda item: (store_demo_sort_score(item), item.final_score), reverse=True)
    return items


def effective_interaction_category_id(result: PipelineResult) -> str:
    category = result.intent.category_decision
    demand_text = build_demand_scene_text(result)
    matched_category_ids = {item.category_id for item in category.category_matches}
    if "5" in matched_category_ids and contains_any(demand_text, STORE_SCENE_KEYWORDS):
        return "5"
    return category.primary_category_id


def display_interaction_category(result: PipelineResult) -> tuple[str, str]:
    category_id = effective_interaction_category_id(result)
    if category_id == result.intent.category_decision.primary_category_id:
        return category_id, result.intent.category_decision.primary_category_name
    for match in result.intent.category_decision.category_matches:
        if match.category_id == category_id:
            return match.category_id, match.category_name
    return category_id, result.intent.category_decision.primary_category_name


STORE_SCENE_KEYWORDS = (
    "餐饮",
    "门店",
    "商铺",
    "收银",
    "外卖",
    "监控",
    "小微",
    "营业",
    "店内",
    "顾客 Wi-Fi",
    "顾客Wi-Fi",
)


def build_demand_scene_text(result: PipelineResult) -> str:
    structured = result.intent.structured_data
    category = result.intent.category_decision
    values = [
        structured.access_source or "",
        structured.target_region or "",
        structured.duration or "",
        *structured.raw_keywords,
        *structured.category_candidate_keywords,
        category.primary_category_name or "",
        category.reason or "",
    ]
    for match in category.category_matches:
        values.extend(match.matched_keywords)
        values.append(match.category_name)
    return " ".join(value for value in values if value)


def voice_direct_priority(scored: Any) -> int:
    product = scored.filtered_candidate.candidate.product
    text = " ".join(
        [
            product.product_name or "",
            product.title or "",
            product.product_family or "",
            product.category_path or "",
            product.filename or "",
        ]
    )
    if contains_any(text, ("云中继", "商继通", "30B+D", "DID", "中继线")):
        return 3
    if contains_any(text, ("商云通", "固话", "语音")):
        return 2
    if contains_any(text, ("精品专线", "商务专线", "宽带")):
        return 1
    return 0


def store_direct_priority(scored: Any) -> int:
    product = scored.filtered_candidate.candidate.product
    text = " ".join(
        [
            product.product_name or "",
            product.title or "",
            product.product_family or "",
            product.category_path or "",
            product.filename or "",
        ]
    )
    if contains_any(text, ("小微", "旺铺", "开店宝", "门店", "商铺", "商企云宽", "沃商务企微宽带")):
        return 3
    if contains_any(text, ("商务专线", "智云随选", "云宽")):
        return 2
    if contains_any(text, ("精品专线", "智能专线", "互联网专线")):
        return 1
    return 0


def store_demo_sort_score(scored: Any) -> float:
    product = scored.filtered_candidate.candidate.product
    has_package_price = any(package.price is not None for package in product.packages)
    has_packages = bool(product.packages)
    has_fee_rules = bool(product.fee_rules)
    score = store_direct_priority(scored) * 10.0
    if has_package_price:
        score += 25.0
    elif has_packages or has_fee_rules:
        score += 8.0
    if product.optional_packages:
        score += 2.0
    return score


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


def append_reason(original: Any, addition: str) -> str:
    original_text = str(original or "").strip()
    if not original_text:
        return addition
    if addition in original_text:
        return original_text
    return f"{original_text}；{addition}"


def contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def has_any_text(history: list[dict[str, str]], keywords: tuple[str, ...]) -> bool:
    text = format_history(history)
    return contains_any(text, keywords)


def is_known_fact_question(question: str, structured_data: Any) -> bool:
    if structured_data.access_source and contains_any(question, ("办公地点", "从哪里访问", "哪里访问", "来源")):
        return True
    if structured_data.target_region and contains_any(question, ("访问哪里", "办理什么业务", "目标")):
        return True
    if structured_data.user_count and contains_any(question, ("多少人", "终端", "门店", "坐席", "号码")):
        return True
    if structured_data.budget is not None and contains_any(question, ("预算", "费用")):
        return True
    if structured_data.duration and contains_any(question, ("试用", "月付", "年付", "签约")):
        return True
    return False


def dedupe_questions(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


if __name__ == "__main__":
    main()
