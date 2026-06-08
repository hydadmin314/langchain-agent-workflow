from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.sales_recommendation_agent.intent_parser import (  # noqa: E402
    HeuristicDemandParser,
    SalesRecommendationSettings,
    SalesRequirementWorkflow,
)
from agent.sales_recommendation_agent.product_repository import ProductRepository  # noqa: E402
from agent.sales_recommendation_agent.recommender import (  # noqa: E402
    CandidateComparator,
    CandidateRetriever,
    CandidateRuleFilter,
    CandidateScorer,
)


@dataclass
class PipelineResult:
    intent: Any
    product_load: Any
    retrieval: Any
    filter: Any
    score: Any
    comparison: Any


class SalesDemoService:
    def __init__(self, *, published_root: Path, top_k: int = 8, use_llm_parser: bool = False) -> None:
        self.top_k = top_k
        self.use_llm_parser = use_llm_parser
        self.product_load = ProductRepository(published_root).load_result()
        if use_llm_parser:
            self.workflow = SalesRequirementWorkflow()
        else:
            settings = SalesRecommendationSettings.from_env()
            self.workflow = SalesRequirementWorkflow(
                settings=settings,
                parser=HeuristicDemandParser(settings),
            )

    def analyze(self, query: str) -> PipelineResult:
        analysis_query = query if self.use_llm_parser else normalize_heuristic_query(query)
        intent = self.workflow.analyze(analysis_query)
        retrieval = CandidateRetriever(default_top_k=self.top_k).retrieve(
            demand=intent.structured_data,
            category_decision=intent.category_decision,
            products=self.product_load.products,
            top_k=self.top_k,
        )
        filtered = CandidateRuleFilter().apply(
            demand=intent.structured_data,
            category_decision=intent.category_decision,
            retrieval_result=retrieval,
        )
        score = CandidateScorer().score(
            demand=intent.structured_data,
            category_decision=intent.category_decision,
            filter_result=filtered,
            top_k=self.top_k,
        )
        comparison = CandidateComparator().compare(
            demand=intent.structured_data,
            category_decision=intent.category_decision,
            score_result=score,
            top_n=min(self.top_k, 3),
        )
        return PipelineResult(
            intent=intent,
            product_load=self.product_load,
            retrieval=retrieval,
            filter=filtered,
            score=score,
            comparison=comparison,
        )


def normalize_heuristic_query(query: str) -> str:
    normalized = query
    negative_fixed_ip_patterns = (
        r"(?:不需要|无需|不要求|没有)\s*(?:固定)?公网\s*IP(?:需求)?",
        r"(?:不需要|无需|不要求|没有)\s*固定\s*IP(?:需求)?",
    )
    for pattern in negative_fixed_ip_patterns:
        normalized = re.sub(pattern, "无需特殊地址要求", normalized, flags=re.IGNORECASE)

    negative_overseas_patterns = (
        r"(?:不涉及|没有|无需|不需要)\s*(?:海外|境外|跨境)(?:访问|业务|需求)?",
        r"不访问\s*(?:海外|境外)",
    )
    for pattern in negative_overseas_patterns:
        normalized = re.sub(pattern, "仅国内业务", normalized, flags=re.IGNORECASE)
    return normalized


class SalesDemoRequestHandler(SimpleHTTPRequestHandler):
    service: SalesDemoService

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_ROOT), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the background demo server independent from a terminal output stream.
        return

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
        if self.path.rstrip("/") != "/api/sales-demo/turn":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self._read_json_body()
            query = str(payload.get("query") or "").strip()
            if not query:
                raise ValueError("query 不能为空")
            result = self.service.analyze(query)
            response = build_api_response(
                query=query,
                result=result,
                round_no=_positive_int(payload.get("round"), 1),
                max_rounds=_positive_int(payload.get("max_rounds"), 6),
                min_rounds=_positive_int(payload.get("min_rounds_before_answer"), 6),
            )
            self._write_json(HTTPStatus.OK, response)
        except ValueError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - return a readable demo error.
            self._write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"{type(exc).__name__}: {exc}"},
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
    *,
    query: str,
    result: PipelineResult,
    round_no: int,
    max_rounds: int,
    min_rounds: int,
) -> dict[str, Any]:
    demand = result.intent.structured_data
    category = result.intent.category_decision
    interaction_category_id, interaction_category_name = interaction_category(result)
    ranking = build_ranking(result)
    recommendation = build_recommendation(query=query, result=result, ranking=ranking)
    top_score = max((item["finalScore"] for item in ranking), default=0)
    score_scale = max(100, int(math.ceil(top_score / 10.0) * 10))
    return {
        "source": "backend",
        "round": round_no,
        "shouldRecommend": bool(ranking) and round_no >= min(min_rounds, max_rounds),
        "scene": {
            "id": scene_id(interaction_category_id),
            "primaryCategory": category_label(category.primary_category_id, category.primary_category_name),
            "interactionCategory": category_label(interaction_category_id, interaction_category_name),
            "candidateCount": result.score.scored_count,
            "confidence": category.confidence,
        },
        "primaryCategory": category_label(category.primary_category_id, category.primary_category_name),
        "interactionCategory": category_label(interaction_category_id, interaction_category_name),
        "candidateCount": result.score.scored_count,
        "knownFacts": {
            "users": demand.user_count or "",
            "budget": demand.budget or "",
            "period": demand.duration or "",
            "access": " → ".join(
                item for item in (demand.access_source, demand.target_region) if item
            ),
        },
        "missingFacts": list(demand.missing_fields),
        "confidence": category.confidence,
        "pipelineStats": {
            "publishedProducts": result.product_load.product_count,
            "loadErrors": result.product_load.error_count,
            "retrieved": result.retrieval.matched_count,
            "kept": result.filter.kept_count,
            "removed": result.filter.removed_count,
            "scored": result.score.scored_count,
        },
        "scoreScale": score_scale,
        "ranking": ranking,
        "comparisonDimensions": [
            item.model_dump(mode="json") for item in result.comparison.comparison_dimensions
        ],
        "globalWarnings": list(result.comparison.global_warnings),
        "recommendation": recommendation,
    }


def build_ranking(result: PipelineResult) -> list[dict[str, Any]]:
    scored_by_document_id = {
        item.filtered_candidate.candidate.product.document_id: item
        for item in result.score.scored_candidates
    }
    ranking: list[dict[str, Any]] = []
    for compared in result.comparison.products:
        scored = scored_by_document_id.get(compared.document_id)
        display_name = display_product_name(compared.product_name, compared.product_family)
        ranking.append(
            {
                "rank": compared.rank,
                "documentId": compared.document_id,
                "productName": display_name,
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
                "packageSummary": compared.package_summary.model_dump(mode="json"),
                "feeSummary": compared.fee_summary.model_dump(mode="json"),
                "optionalPackageSummary": compared.optional_package_summary.model_dump(mode="json"),
                "constraintSummary": compared.constraint_summary.model_dump(mode="json"),
                "source": compared.source,
            }
        )
    return ranking


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


def build_recommendation(
    *,
    query: str,
    result: PipelineResult,
    ranking: list[dict[str, Any]],
) -> dict[str, Any]:
    if not ranking:
        return {
            "status": "clarify",
            "title": "当前信息不足，暂不推荐具体产品",
            "summary": "需要继续补充客户业务目标，或检查 published 产品数据。",
            "answerText": "当前没有通过过滤和打分的候选产品，建议继续澄清需求。",
        }

    top = ranking[0]
    alternatives = ranking[1:3]
    prices = top["packageSummary"].get("price_range") or "资料未给出明确价格"
    speeds = "、".join(top["packageSummary"].get("speeds") or []) or "待确认"
    strengths = top["matchedStrengths"][:3] or ["当前候选在程序排序中综合得分最高。"]
    risks = dedupe(
        [
            *top["riskWarnings"],
            *top["constraintSummary"].get("important_risks", []),
            *top["missingInfo"],
            *result.comparison.global_warnings,
        ]
    )[:6]
    if not risks:
        risks = ["资费有效期、资源覆盖和开通条件仍需人工确认。"]

    answer_sections = [
        f"【推荐结论】\n主推“{top['productName']}”，综合得分 {top['finalScore']}。",
        (
            "【客户需求理解】\n"
            f"- 当前累计需求：{query}\n"
            f"- 业务分类：{result.intent.category_decision.primary_category_name}"
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
        "【风险与人工确认】\n" + "\n".join(f"- {item}" for item in risks),
        (
            "【下一步动作】\n"
            "1. 核实客户地址的资源覆盖和开通条件。\n"
            "2. 对照预算确认套餐、计费周期和协议期。\n"
            "3. 对缺失价格或限制条款进行人工复核后再对外报价。"
        ),
    ]
    return {
        "status": "ready",
        "title": f"主推 {top['productName']}",
        "summary": f"综合得分 {top['finalScore']}，当前后端排序第 1。",
        "primaryProduct": top,
        "alternativeProducts": alternatives,
        "risks": risks,
        "answerText": "\n\n".join(answer_sections),
    }


def interaction_category(result: PipelineResult) -> tuple[str, str]:
    category = result.intent.category_decision
    demand_text = " ".join(
        [
            *(result.intent.structured_data.raw_keywords or []),
            *(result.intent.structured_data.category_candidate_keywords or []),
            category.primary_category_name or "",
        ]
    )
    for match in category.category_matches:
        if match.category_id == "5" and any(
            keyword in demand_text
            for keyword in ("餐饮", "门店", "商铺", "收银", "外卖", "监控", "小微")
        ):
            return match.category_id, match.category_name
    return category.primary_category_id, category.primary_category_name


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


def category_label(category_id: str, category_name: str) -> str:
    if category_id and category_name:
        return f"{category_id}_{category_name}"
    return category_name or category_id or "clarify_required"


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _positive_int(value: Any, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def main() -> None:
    parser = argparse.ArgumentParser(description="销售推荐 Demo 静态页面与轻量 API 服务。")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--published-root",
        default=str(PROJECT_ROOT / "data" / "product_doc_agent" / "published"),
    )
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument(
        "--use-llm-parser",
        action="store_true",
        help="需求解析使用项目配置的大模型；默认使用本地规则以保证演示响应稳定。",
    )
    args = parser.parse_args()

    SalesDemoRequestHandler.service = SalesDemoService(
        published_root=Path(args.published_root),
        top_k=args.top_k,
        use_llm_parser=args.use_llm_parser,
    )
    server = ThreadingHTTPServer((args.host, args.port), SalesDemoRequestHandler)
    print(f"Sales Demo: http://{args.host}:{args.port}")
    print(f"Published products: {SalesDemoRequestHandler.service.product_load.product_count}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
