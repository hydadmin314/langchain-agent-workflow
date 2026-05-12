import json
from typing import Any

from langchain.tools import tool

from tools.product_tool import normalize_text
from tools.requirement_parser import parse_requirement_payload


NETWORK_MODULES = {
    "sd_wan": {
        "module_name": "SD-WAN 核算模块",
        "category": "SD-WAN",
        "scene": "国内多点组网",
    },
    "intl_route_optimization": {
        "module_name": "国际路由优化 核算模块",
        "category": "国际路由优化",
        "scene": "访问海外应用",
    },
    "isp_private_line": {
        "module_name": "ISP 专线 核算模块",
        "category": "ISP",
        "scene": "固定 IP / 大带宽 / 互联网专线",
    },
}


def _add_score(
    bucket: dict[str, dict[str, Any]],
    module_key: str,
    points: int,
    reason: str,
    evidence: str | None = None,
) -> None:
    item = bucket[module_key]
    item["score"] += points
    item["reasons"].append(reason)
    if evidence:
        item["evidence"].append(evidence)


def _confidence(score: int) -> str:
    if score >= 8:
        return "high"
    if score >= 4:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def classify_requirement_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    query_norm = normalize_text(parsed.get("raw_query") or parsed.get("normalized_query"))
    network = parsed.get("network_requirements", {})
    specs = parsed.get("spec_requirements", {})
    categories = set(parsed.get("target_categories") or parsed.get("explicit_categories") or [])
    scenarios = set(parsed.get("scenarios") or [])

    buckets: dict[str, dict[str, Any]] = {
        key: {
            "module_key": key,
            "module_name": config["module_name"],
            "category": config["category"],
            "scene": config["scene"],
            "score": 0,
            "confidence": "none",
            "reasons": [],
            "evidence": [],
        }
        for key, config in NETWORK_MODULES.items()
    }

    if "SD-WAN" in categories or "组网互联" in scenarios:
        _add_score(buckets, "sd_wan", 6, "命中 SD-WAN / 组网类诉求")
    if specs.get("site_count") or specs.get("branch_count"):
        _add_score(buckets, "sd_wan", 5, "识别到多站点、分支或门店数量")
    if any(term in query_norm for term in ("组网", "多分支", "多门店", "总部", "分公司", "互联")):
        _add_score(buckets, "sd_wan", 4, "文本包含国内多点互联关键词")

    if "国际路由优化" in categories or "国际访问优化" in scenarios:
        _add_score(buckets, "intl_route_optimization", 6, "命中国际路由优化场景")
    if network.get("cross_border_access"):
        _add_score(buckets, "intl_route_optimization", 7, "识别到跨境访问链路")
    if network.get("has_latency_or_speed_issue"):
        _add_score(buckets, "intl_route_optimization", 4, "客户反馈访问慢、延迟高或链路不稳定")
    if any(term in query_norm for term in ("海外", "国外", "美国", "日本", "新加坡", "跨境", "出海", "游戏服务器")):
        _add_score(buckets, "intl_route_optimization", 4, "文本包含海外应用或出海关键词")

    if "ISP" in categories or "互联网接入" in scenarios:
        _add_score(buckets, "isp_private_line", 6, "命中 ISP / 互联网接入场景")
    isp_flags = [
        network.get("requires_public_ip"),
        network.get("requires_fixed_ip"),
        network.get("requires_dedicated_line"),
        network.get("requires_large_bandwidth"),
    ]
    if any(isp_flags):
        _add_score(buckets, "isp_private_line", 6, "识别到固定 IP、公网 IP、专线或大带宽诉求")
    if any(term in query_norm for term in ("固定ip", "公网ip", "专线", "大带宽", "宽带", "互联网接入", "isp")):
        _add_score(buckets, "isp_private_line", 4, "文本包含 ISP 专线关键词")

    for module in buckets.values():
        module["confidence"] = _confidence(module["score"])

    candidates = sorted(
        (module for module in buckets.values() if module["score"] > 0),
        key=lambda item: (-item["score"], item["module_key"]),
    )

    primary = candidates[0] if candidates else None
    return {
        "primary_scene": primary["scene"] if primary else None,
        "primary_module": primary["module_key"] if primary else None,
        "routed_modules": candidates,
        "module_categories": [item["category"] for item in candidates],
    }


def classify_requirement(query: str) -> dict[str, Any]:
    parsed = parse_requirement_payload(query)
    return {
        "raw_query": query,
        "parsed_requirements": parsed,
        "rule_engine": classify_requirement_payload(parsed),
    }


@tool
def classify_sales_scene(query: str) -> str:
    """
    Rule Engine：场景分类器。
    用于把销售输入分类到 SD-WAN、国际路由优化、ISP 专线等核算模块，
    并返回命中原因、置信度和后续是否需要进入 calculator。
    """
    return json.dumps(classify_requirement(query), ensure_ascii=False, indent=2)
