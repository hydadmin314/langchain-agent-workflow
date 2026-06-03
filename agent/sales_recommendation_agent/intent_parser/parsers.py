from __future__ import annotations

import json
import math
import re
from typing import Any, Protocol

from pydantic import ValidationError

from agent.sales_recommendation_agent.intent_parser.config import SalesRecommendationSettings
from agent.sales_recommendation_agent.intent_parser.models import CustomerDemand, RegionScope, ScenarioType


SYSTEM_PROMPT = """你负责把销售侧口语化客户需求提取为严格 JSON，不要输出 Markdown 或解释。

只允许输出这些字段：
{
  "access_source": "",
  "source_scope": "domestic/overseas/unknown",
  "target_region": "",
  "target_scope": "domestic/overseas/unknown",
  "user_count": null,
  "bandwidth_est_mbps": 0,
  "duration": "",
  "budget": null,
  "requires_fixed_ip": false,
  "scenario_type": "overseas_access/domestic_networking/dedicated_ip_or_high_bandwidth/trial_or_poc/unknown",
  "raw_keywords": [],
  "confidence": 0.0,
  "missing_fields": []
}

抽取规则：
1. 未明确带宽但提供人数时，按每人 1 Mbps 估算 bandwidth_est_mbps。
2. 出现固定 IP、公网 IP、专线、独享、大带宽时，requires_fixed_ip=true 或 scenario_type=dedicated_ip_or_high_bandwidth。
3. 国内办公室访问海外应用、海外机房、海外 SaaS 时，scenario_type=overseas_access。
4. 多地办公室、总部分公司、内网互通、组网时，scenario_type=domestic_networking。
5. 试用、POC、先试一个月等信息写入 duration，同时可标记 scenario_type=trial_or_poc，除非更强的网络场景已经明确。
6. 缺少来源、目标、人数、周期、预算等关键信息时，把字段名写入 missing_fields。
"""


class DemandParser(Protocol):
    """需求解析器协议，方便后续替换为更强的 LLM 或规则实现。"""

    def parse(self, raw_text: str) -> CustomerDemand:
        ...


class ParserError(RuntimeError):
    """LLM 解析失败时抛出，外层会自动降级到本地规则。"""


class OpenAICompatibleDemandParser:
    """OpenAI-compatible 大模型解析器。"""

    def __init__(self, settings: SalesRecommendationSettings):
        self.settings = settings

    def parse(self, raw_text: str) -> CustomerDemand:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ParserError("openai package is not installed") from exc

        if not self.settings.llm_api_key:
            raise ParserError("OPENAI_API_KEY is not configured")

        client_kwargs: dict[str, Any] = {"api_key": self.settings.llm_api_key}
        if self.settings.llm_base_url:
            client_kwargs["base_url"] = self.settings.llm_base_url

        response = OpenAI(**client_kwargs).chat.completions.create(
            **self.build_request_kwargs(raw_text)
        )
        content = response.choices[0].message.content or "{}"
        return parse_customer_demand_json(content)

    def build_request_kwargs(self, raw_text: str) -> dict[str, Any]:
        request_kwargs: dict[str, Any] = {
            "model": self.settings.llm_model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"请抽取以下客户需求，只输出 JSON：\n{raw_text}"},
            ],
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "timeout": self.settings.llm_timeout_seconds,
            "stream": False,
        }

        # DashScope/Qwen 兼容接口支持这些开关；其他兼容服务会忽略或报错，必要时可通过环境变量置空。
        extra_body: dict[str, Any] = {}
        if self.settings.llm_enable_thinking is not None:
            extra_body["enable_thinking"] = self.settings.llm_enable_thinking
        if self.settings.llm_enable_search is not None:
            extra_body["enable_search"] = self.settings.llm_enable_search
        if extra_body:
            request_kwargs["extra_body"] = extra_body

        return request_kwargs


class HeuristicDemandParser:
    """本地启发式解析器，用于无 API、API 超时或测试场景。"""

    def __init__(self, settings: SalesRecommendationSettings):
        self.settings = settings

    def parse(self, raw_text: str) -> CustomerDemand:
        text = raw_text.strip()
        user_count = extract_user_count(text)
        explicit_bandwidth = extract_bandwidth(text)
        bandwidth_est = explicit_bandwidth
        if bandwidth_est is None and user_count:
            bandwidth_est = math.ceil(user_count * self.settings.per_user_bandwidth_mbps)

        source_scope = infer_source_scope(text)
        target_scope = infer_target_scope(text)
        requires_fixed_ip = contains_any(text, ["固定IP", "固定 IP", "公网IP", "公网 IP", "专线", "独享"])
        scenario_type = infer_scenario_type(
            source_scope=source_scope,
            target_scope=target_scope,
            bandwidth_est_mbps=bandwidth_est or 0,
            requires_fixed_ip=requires_fixed_ip,
            high_bandwidth_threshold_mbps=self.settings.high_bandwidth_threshold_mbps,
            text=text,
        )

        access_source = infer_access_source(text)
        target_region = infer_target_region(text, target_scope)
        duration = extract_duration(text)
        budget = extract_budget(text)
        missing_fields = [
            name
            for name, value in {
                "access_source": access_source,
                "target_region": target_region,
                "user_count": user_count,
                "duration": duration,
                "budget": budget,
            }.items()
            if value is None
        ]

        confidence = 0.55
        if user_count:
            confidence += 0.1
        if target_scope != RegionScope.unknown:
            confidence += 0.1
        if scenario_type != ScenarioType.unknown:
            confidence += 0.15
        if explicit_bandwidth:
            confidence += 0.05

        return CustomerDemand(
            access_source=access_source,
            source_scope=source_scope,
            target_region=target_region,
            target_scope=target_scope,
            user_count=user_count,
            bandwidth_est_mbps=bandwidth_est or 0,
            duration=duration,
            budget=budget,
            requires_fixed_ip=requires_fixed_ip,
            scenario_type=scenario_type,
            raw_keywords=extract_keywords(text),
            confidence=min(confidence, 0.95),
            missing_fields=missing_fields,
        )


class ResilientDemandParser:
    """优先走 LLM，失败后自动降级到本地规则。"""

    def __init__(self, settings: SalesRecommendationSettings):
        self.primary = OpenAICompatibleDemandParser(settings)
        self.fallback = HeuristicDemandParser(settings)

    def parse(self, raw_text: str) -> CustomerDemand:
        if self.primary.settings.llm_api_key:
            try:
                return self.primary.parse(raw_text)
            except (ParserError, ValidationError, json.JSONDecodeError, Exception):
                # 需求提取不能因为模型服务短暂失败而中断销售流程，先给出可用的本地解析结果。
                pass
        return self.fallback.parse(raw_text)


def parse_customer_demand_json(content: str) -> CustomerDemand:
    payload = extract_json_object(content)
    return CustomerDemand.model_validate(payload)


def extract_json_object(content: str) -> dict[str, Any]:
    """兼容模型输出 ```json ... ``` 或前后带解释文本的情况。"""

    cleaned = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.S)
    if fenced:
        cleaned = fenced.group(1)
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end >= start:
            cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def contains_any(text: str, keywords: list[str]) -> bool:
    normalized = text.lower()
    return any(keyword.lower() in normalized for keyword in keywords)


def extract_user_count(text: str) -> int | None:
    patterns = [
        r"(?:大概|约|差不多|预计)?\s*(\d+)\s*(?:个)?(?:人|用户|员工|座席)",
        r"team\s*of\s*(\d+)",
        r"(\d+)\s*users?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return int(match.group(1))

    chinese_match = re.search(r"([零一二两三四五六七八九十百千万]+)\s*(?:个)?(?:人|用户|员工|座席)", text)
    if chinese_match:
        return chinese_number_to_int(chinese_match.group(1))
    return None


def extract_bandwidth(text: str) -> int | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(G|Gbps|M|Mbps|兆|千兆)", text, re.I)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit in {"g", "gbps", "千兆"}:
        value *= 1000
    return math.ceil(value)


def extract_duration(text: str) -> str | None:
    trial_match = re.search(
        r"(试用|先试|测试|poc|POC)[^\d零一二两三四五六七八九十百千万]{0,8}"
        r"(\d+|[零一二两三四五六七八九十百千万]+)\s*(天|周|个月|月|年)",
        text,
    )
    if trial_match:
        return f"试用{normalize_number_text(trial_match.group(2))}{trial_match.group(3)}"

    match = re.search(r"(\d+|[零一二两三四五六七八九十百千万]+)\s*(天|周|个月|月|年)", text)
    if match:
        return f"{normalize_number_text(match.group(1))}{match.group(2)}"
    return None


def extract_budget(text: str) -> float | None:
    match = re.search(
        r"(?:预算|费用|价格|报价|控制在)[^\d]{0,8}(\d+(?:\.\d+)?)\s*(万|千|k|K|元|块)?",
        text,
    )
    if not match:
        match = re.search(r"(\d+(?:\.\d+)?)\s*(万|千|k|K|元|块)", text)
    if not match:
        return None

    value = float(match.group(1))
    unit = match.group(2) or "元"
    if unit == "万":
        value *= 10000
    elif unit in {"千", "k", "K"}:
        value *= 1000
    return value


def infer_source_scope(text: str) -> RegionScope:
    if contains_any(text, ["美国办公室", "海外办公室", "新加坡办公室", "香港办公室", "海外分公司"]):
        return RegionScope.overseas
    if contains_any(text, ["上海", "北京", "深圳", "广州", "杭州", "国内", "中国", "办公室", "分公司"]):
        return RegionScope.domestic
    return RegionScope.unknown


def infer_target_scope(text: str) -> RegionScope:
    if contains_any(
        text,
        [
            "美国",
            "海外",
            "国外",
            "新加坡",
            "日本",
            "欧洲",
            "香港",
            "Google",
            "Salesforce",
            "Microsoft 365",
            "Office 365",
            "AWS",
        ],
    ):
        return RegionScope.overseas
    if contains_any(text, ["国内组网", "内网互通", "多点组网", "总部", "分公司", "同城"]):
        return RegionScope.domestic
    return RegionScope.unknown


def infer_target_region(text: str, target_scope: RegionScope) -> str | None:
    for region in ["美国", "新加坡", "日本", "欧洲", "香港", "海外", "国外"]:
        if region in text:
            return region
    if contains_any(text, ["Google", "Salesforce", "Microsoft 365", "Office 365", "AWS", "SaaS"]):
        return "海外 SaaS/云服务"
    if target_scope == RegionScope.domestic:
        return "国内多点组网"
    return None


def infer_access_source(text: str) -> str | None:
    cities = ["上海", "北京", "深圳", "广州", "杭州", "成都", "武汉", "南京"]
    found = [city for city in cities if city in text]
    if found:
        if len(found) == 1:
            return f"{found[0]}办公室" if "办公室" in text or "办公" in text else found[0]
        return "、".join(found)
    if "国内" in text:
        return "国内办公环境"
    return None


def infer_scenario_type(
    *,
    source_scope: RegionScope,
    target_scope: RegionScope,
    bandwidth_est_mbps: int,
    requires_fixed_ip: bool,
    high_bandwidth_threshold_mbps: int,
    text: str,
) -> ScenarioType:
    if requires_fixed_ip or bandwidth_est_mbps > high_bandwidth_threshold_mbps:
        return ScenarioType.dedicated_ip_or_high_bandwidth
    if target_scope == RegionScope.overseas and source_scope in {RegionScope.domestic, RegionScope.unknown}:
        return ScenarioType.overseas_access
    if target_scope == RegionScope.domestic or contains_any(text, ["组网", "互通", "分公司"]):
        return ScenarioType.domestic_networking
    if contains_any(text, ["试用", "先试", "测试", "POC", "poc"]):
        return ScenarioType.trial_or_poc
    return ScenarioType.unknown


def extract_keywords(text: str) -> list[str]:
    candidates = ["固定IP", "公网IP", "专线", "海外", "美国", "国内组网", "内网互通", "试用", "预算"]
    keywords = [item for item in candidates if item in text]
    user_count = extract_user_count(text)
    if user_count:
        keywords.append(f"{user_count}人")
    bandwidth = extract_bandwidth(text)
    if bandwidth:
        keywords.append(f"{bandwidth}M")
    return keywords


def normalize_number_text(value: str) -> str:
    return str(chinese_number_to_int(value)) if not value.isdigit() else value


def chinese_number_to_int(value: str) -> int:
    digits = {
        "零": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if value.isdigit():
        return int(value)
    if "万" in value:
        left, _, right = value.partition("万")
        base = chinese_number_to_int(left) * 10000
        return base + (chinese_number_to_int(right) if right else 0)
    if "千" in value:
        left, _, right = value.partition("千")
        base = (chinese_number_to_int(left) if left else 1) * 1000
        return base + (chinese_number_to_int(right) if right else 0)
    if "百" in value:
        left, _, right = value.partition("百")
        base = (chinese_number_to_int(left) if left else 1) * 100
        return base + (chinese_number_to_int(right) if right else 0)
    if "十" in value:
        left, _, right = value.partition("十")
        base = (chinese_number_to_int(left) if left else 1) * 10
        return base + (digits.get(right, 0) if right else 0)
    return digits.get(value, 0)
