from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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


DEFAULT_QUERY = "客户上海办公室10人访问美国 SaaS 很慢，预算5000左右"


def main() -> None:
    parser = argparse.ArgumentParser(description="运行销售推荐 Agent 当前已开发到 LLM Recommendation Explainer 的完整流程。")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="销售输入的客户原始需求。")
    parser.add_argument("--published-root", default="data/product_doc_agent/published", help="已审核发布的产品 JSON 目录。")
    parser.add_argument("--top-k", type=int, default=8, help="展示召回候选数量。")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON，便于调试字段。")
    parser.add_argument("--skip-explainer", action="store_true", help="跳过真实 LLM 推荐说明，只跑程序召回、过滤、排序和对比。")
    args = parser.parse_args()

    # 当前完整流程：
    # 1. Intent Parser 抽取客户结构化需求，并输出分类候选关键词
    # 2. Demand Category Classifier 按 13 类产品需求分类体系确定主分类
    # 3. Product Repository 读取 published 产品主数据
    # 4. Candidate Retriever 按主分类和候选分类召回产品
    # 5. Rule Filter 做确定性过滤和风险打标
    # 6. Scorer 对保留候选做稳定、可解释的程序排序
    # 7. Comparator 对 Top N 候选做结构化对比
    # 8. LLM Recommendation Explainer 读取对比结果，生成销售可读推荐说明
    intent_result = SalesRequirementWorkflow().analyze(args.query)
    product_result = ProductRepository(args.published_root).load_result()
    retrieval_result = CandidateRetriever(default_top_k=args.top_k).retrieve(
        demand=intent_result.structured_data,
        category_decision=intent_result.category_decision,
        products=product_result.products,
        top_k=args.top_k,
    )
    # Rule Filter 只做确定性过滤和风险打标，后续 Scorer 会基于保留下来的候选再排序。
    filter_result = CandidateRuleFilter().apply(
        demand=intent_result.structured_data,
        category_decision=intent_result.category_decision,
        retrieval_result=retrieval_result,
    )
    score_result = CandidateScorer().score(
        demand=intent_result.structured_data,
        category_decision=intent_result.category_decision,
        filter_result=filter_result,
        top_k=args.top_k,
    )
    comparison_result = CandidateComparator().compare(
        demand=intent_result.structured_data,
        category_decision=intent_result.category_decision,
        score_result=score_result,
        top_n=min(args.top_k, 3),
    )
    explanation_result = None
    if not args.skip_explainer:
        explanation_result = RecommendationExplainer().explain(
            query=args.query,
            demand=intent_result.structured_data,
            category_decision=intent_result.category_decision,
            comparison_result=comparison_result,
        )

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
                    "filter": filter_result.model_dump(mode="json"),
                    "score": score_result.model_dump(mode="json"),
                    "comparison": comparison_result.model_dump(mode="json"),
                    "explanation": explanation_result.model_dump(mode="json") if explanation_result else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    print("客户需求：")
    print(args.query)
    print()

    print("1. Intent Parser 结构化结果：")
    structured_data = intent_result.structured_data
    print(f"- primary_category: {structured_data.primary_category}")
    print(f"- secondary_categories: {structured_data.secondary_categories}")
    print(f"- primary_goal: {structured_data.primary_goal}")
    print(f"- usage_scene: {structured_data.usage_scene}")
    print(f"- business_action: {structured_data.business_action}")
    print(f"- site_count: {structured_data.site_count}")
    print(f"- user_count: {structured_data.user_count}")
    print(f"- bandwidth_need: {structured_data.bandwidth_need}")
    print(f"- fixed_ip_required: {structured_data.fixed_ip_required}")
    print(f"- voice_required: {structured_data.voice_required}")
    print(f"- overseas_access: {structured_data.overseas_access}")
    print(f"- overseas_target: {structured_data.overseas_target}")
    print(f"- budget: {structured_data.budget}")
    print(f"- reliability_level: {structured_data.reliability_level}")
    print(f"- carrier_preference: {structured_data.carrier_preference}")
    print(f"- region: {structured_data.region}")
    print(f"- customer_type: {structured_data.customer_type}")
    print(f"- raw_keywords: {structured_data.raw_keywords}")
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
        print("5. 需要追问的信息：")
        for question in retrieval_result.clarify_questions:
            print(f"- {question}")
        print()

    print(f"6. Candidate Retriever 召回结果 Top {len(retrieval_result.candidates)}：")
    if not retrieval_result.candidates:
        print("- 当前没有召回候选产品。")
        return

    print(
        f"- Rule Filter: 保留 {filter_result.kept_count} 个，"
        f"移除 {filter_result.removed_count} 个"
    )
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
        if candidate.warnings:
            print("- warnings:")
            for warning in candidate.warnings[:5]:
                print(f"  - {warning}")
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

    if filter_result.removed_candidates:
        print("\nRule Filter 移除的候选：")
        for filtered in filter_result.removed_candidates[:5]:
            product = filtered.candidate.product
            display_name = product.product_name or product.title or product.filename or product.document_id
            reason = filtered.filter_reasons[0] if filtered.filter_reasons else None
            if reason:
                print(f"- {display_name}: {reason.code} - {reason.message}")
            else:
                print(f"- {display_name}")

    print("\n7. Comparator 结构化对比摘要：")
    if not comparison_result.products:
        print("- 当前没有可对比的候选产品。")
    else:
        for item in comparison_result.products:
            print(f"\n[{item.rank}] {item.product_name}")
            print(f"- final_score: {item.final_score}")
            print(f"- package_count: {item.package_summary.package_count}")
            print(f"- price_range: {item.package_summary.price_range or '未提取'}")
            print(f"- fee_rule_count: {item.fee_summary.fee_rule_count}")
            print(f"- optional_package_count: {item.optional_package_summary.optional_package_count}")
            print(f"- constraint_count: {item.constraint_summary.constraint_count}")
            if item.risk_warnings:
                print(f"- risks: {item.risk_warnings[:3]}")
            if item.missing_info:
                print(f"- missing_info: {item.missing_info[:3]}")

        print("\n对比维度：")
        for dimension in comparison_result.comparison_dimensions:
            print(f"- {dimension.dimension}: {dimension.summary}")

    print("\n8. LLM Recommendation Explainer 推荐说明：")
    if explanation_result is None:
        print("- 已通过 --skip-explainer 跳过真实 LLM 推荐说明。")
    else:
        print(f"- summary: {explanation_result.summary}")
        print(
            "- recommended_product: "
            f"{explanation_result.recommended_product.product_name} "
            f"({explanation_result.recommended_product.document_id})"
        )
        print(f"  reason: {explanation_result.recommended_product.reason}")
        if explanation_result.alternative_products:
            print("- alternative_products:")
            for item in explanation_result.alternative_products:
                print(f"  - {item.product_name} ({item.document_id}): {item.reason}")
        if explanation_result.comparison_summary:
            print("- comparison_summary:")
            for item in explanation_result.comparison_summary:
                print(f"  - {item}")
        if explanation_result.risk_reminders:
            print("- risk_reminders:")
            for item in explanation_result.risk_reminders:
                print(f"  - {item}")
        if explanation_result.clarifying_questions:
            print("- clarifying_questions:")
            for item in explanation_result.clarifying_questions:
                print(f"  - {item}")
        print(f"- sales_talk: {explanation_result.sales_talk}")


if __name__ == "__main__":
    main()
