from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.sales_recommendation_agent.intent_parser import SalesRequirementWorkflow
from agent.sales_recommendation_agent.product_repository import ProductRepository
from agent.sales_recommendation_agent.recommender import CandidateRetriever


DEFAULT_QUERY = "客户上海办公室10人访问美国 SaaS 很慢，预算5000左右"


def main() -> None:
    parser = argparse.ArgumentParser(description="运行销售推荐 Agent 当前已开发到 CandidateRetriever 的完整流程。")
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help="销售输入的客户原始需求。",
    )
    parser.add_argument(
        "--published-root",
        default="data/product_doc_agent/published",
        help="已审核发布的产品 JSON 目录。",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=8,
        help="展示召回候选数量。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出完整 JSON，便于调试字段。",
    )
    args = parser.parse_args()

    # 当前完整流程：
    # 1. Intent Parser 抽取客户结构化需求
    # 2. Scenario Router 判断需求场景
    # 3. Product Repository 读取 published 产品主数据
    # 4. Candidate Retriever 按产品需求分类体系召回候选产品
    intent_result = SalesRequirementWorkflow().analyze(args.query)
    product_result = ProductRepository(args.published_root).load_result()
    retrieval_result = CandidateRetriever(default_top_k=args.top_k).retrieve(
        demand=intent_result.structured_data,
        decision=intent_result.decision,
        products=product_result.products,
        raw_text=args.query,
        top_k=args.top_k,
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
    print(f"- access_source: {structured_data.access_source}")
    print(f"- target_region: {structured_data.target_region}")
    print(f"- user_count: {structured_data.user_count}")
    print(f"- bandwidth_est_mbps: {structured_data.bandwidth_est_mbps}")
    print(f"- budget: {structured_data.budget}")
    print(f"- requires_fixed_ip: {structured_data.requires_fixed_ip}")
    print(f"- scenario_type: {structured_data.scenario_type.value}")
    print(f"- missing_fields: {structured_data.missing_fields}")
    print()

    print("2. Scenario Router 结果：")
    decision = intent_result.decision
    print(f"- route: {decision.route}")
    print(f"- priority: {decision.priority}")
    print(f"- reason: {decision.reason}")
    print()

    print("3. Product Repository 加载结果：")
    print(f"- product_count: {product_result.product_count}")
    print(f"- error_count: {product_result.error_count}")
    if product_result.errors:
        print("- errors:")
        for error in product_result.errors[:5]:
            print(f"  - {error.path}: {error.error_type} - {error.message}")
    print()

    print("4. Candidate Retriever 需求分类：")
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
        print("5. 当前不推荐产品，需要追问：")
        for question in retrieval_result.clarify_questions:
            print(f"- {question}")
        return

    print(f"5. Candidate Retriever 召回结果 Top {len(retrieval_result.candidates)}：")
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


if __name__ == "__main__":
    main()
