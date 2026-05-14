import json
from typing import Any

from langchain.tools import tool

from tools.sales_tools.requirement_parser import parse_requirement_payload
from tools.sales_tools.rule_engine import classify_requirement_payload


UNSUPPORTED_CLAIMS = [
    "SLA 或可用性承诺",
    "故障响应时效",
    "免费测试或试用权益",
    "开通、部署、交付周期",
    "免费巡检或健康报告",
    "赠品、赠送设备或赠送固定 IP",
    "IP 扩容价格或扩容权益",
    "调试费减免、初装费减免",
    "锁价、续约保护或长期价格承诺",
    "具体协议优化能力，如 TCP/UDP/TLS 加速",
    "实测延迟、丢包率、RTT 降低比例",
]


def build_rag_context_response(query: str) -> dict[str, Any]:
    parsed = parse_requirement_payload(query)
    rule_result = classify_requirement_payload(parsed)
    module_key = rule_result.get("primary_module")

    return {
        "raw_query": query,
        "parsed_requirements": parsed,
        "module_key": module_key,
        "rag_stage": "technical_sales_context",
        "knowledge_base_configured": False,
        "message": "RAG 技术知识库暂未配置，当前不能补充产品数据和工具结果之外的技术参数、优势或销售话术依据。",
        "technical_claims": [],
        "sales_talking_points": [],
        "source_refs": [],
        "risk_notes": [
            "最终回答只能使用 Intent Parser、Rule Engine、产品核算、Compare、Calculator 已输出的字段和结论。",
            "技术优势、交付承诺、服务权益、SLA、免费测试、巡检、IP 赠送、调试费减免等内容，当前均未获得 RAG 依据。",
            "如果销售需要这些内容，应先补充知识库资料或由销售/交付侧确认。",
        ],
        "unsupported_claims": UNSUPPORTED_CLAIMS,
        "generator_policy": {
            "allow_extra_technical_claims": False,
            "allow_extra_sales_benefits": False,
            "fallback_sentence": "产品数据未配置，需销售确认。",
        },
    }


def render_rag_context_response(response: dict[str, Any]) -> str:
    lines = ["RAG：技术参数、优势与销售话术依据"]
    lines.append(response["message"])
    module_key = response.get("module_key")
    if module_key:
        lines.append(f"命中产品模块：{module_key}")

    lines.append("当前可引用技术依据：无")
    lines.append("当前可引用销售话术依据：无")
    lines.append("Generator 约束：不得补充工具结果之外的技术优势、服务权益或交付承诺。")

    if response.get("unsupported_claims"):
        lines.append("未获依据的内容：")
        for claim in response["unsupported_claims"]:
            lines.append(f"- {claim}：产品数据未配置，需销售确认。")

    return "\n".join(lines)


@tool
def retrieve_sales_context(query: str) -> str:
    """
    RAG 层：用于补充技术参数、产品优势和销售话术依据。
    输入必须是客户原始需求。当前知识库尚未配置，因此本工具只返回生成约束：
    没有 RAG 依据时，不允许补充 SLA、巡检、免费测试、开通周期、IP 赠送、调试费减免等承诺。
    """
    response = build_rag_context_response(query)
    return render_rag_context_response(response)


@tool
def retrieve_sales_context_json(query: str) -> str:
    """
    返回 RAG 层结构化 JSON，便于调试 Generator 可引用依据与禁止补充项。
    """
    response = build_rag_context_response(query)
    return json.dumps(response, ensure_ascii=False, indent=2)

