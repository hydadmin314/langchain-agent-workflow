from __future__ import annotations

import json
import math
import re
from typing import Any, Protocol

from pydantic import ValidationError

from agent.sales_recommendation_agent.intent_parser.config import SalesRecommendationSettings
from agent.sales_recommendation_agent.intent_parser.models import CustomerDemand, RegionScope, ScenarioType
from agent.sales_recommendation_agent.intent_parser.taxonomy import DEMAND_CATEGORY_RULES
from prompts.sales_recommendation_agent_prompts import (
    DEMAND_PARSER_SYSTEM_PROMPT,
    build_demand_parser_user_prompt,
)


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
                {"role": "system", "content": DEMAND_PARSER_SYSTEM_PROMPT},
                {"role": "user", "content": build_demand_parser_user_prompt(raw_text)},
            ],
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "timeout": self.settings.llm_timeout_seconds,
            "stream": False,
        }

        # DashScope/Qwen 兼容接口支持这些开关；其他兼容服务必要时可通过环境变量置空。
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
        requires_fixed_ip = contains_any(text, ["固定IP", "固定 IP", "公网IP", "公网 IP", "公网地址"])
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
        budget_text = extract_budget_text(text)
        bandwidth_text = extract_bandwidth_text(text)
        category_candidate_keywords = extract_category_candidate_keywords(text)
        raw_keywords = extract_keywords(text, category_candidate_keywords=category_candidate_keywords)

        missing_fields = [
            name
            for name, value in {
                "usage_scene": access_source,
                "primary_goal": infer_primary_goal(text, category_candidate_keywords),
                "user_count": user_count,
                "business_action": infer_business_action(text),
                "budget": budget_text,
            }.items()
            if is_missing_value(value)
        ]

        confidence = 0.55
        if user_count:
            confidence += 0.1
        if target_scope != RegionScope.unknown:
            confidence += 0.1
        if category_candidate_keywords:
            confidence += 0.15
        elif scenario_type != ScenarioType.unknown:
            confidence += 0.1
        if explicit_bandwidth:
            confidence += 0.05

        return CustomerDemand(
            primary_goal=infer_primary_goal(text, category_candidate_keywords) or target_region or "",
            usage_scene=access_source or "",
            business_action=infer_business_action(text) or "未知",
            site_count=extract_site_count_text(text),
            user_count=f"{user_count}人" if user_count else "",
            bandwidth_need=bandwidth_text,
            fixed_ip_required=True if requires_fixed_ip else infer_negative_requirement(text, ("固定IP", "公网IP", "公网地址")),
            fixed_ip_count=extract_fixed_ip_count_text(text),
            voice_required=infer_positive_or_negative(text, positive=("语音", "固定电话", "固话", "云中继", "商云通", "30B+D"), negative=("不带语音", "不要语音")),
            concurrent_calls=extract_concurrent_calls_text(text),
            overseas_access=True if target_scope == RegionScope.overseas else infer_negative_requirement(text, ("海外", "国外", "跨境", "国际")),
            overseas_target=target_region or "",
            server_or_idc_required=infer_positive_or_negative(text, positive=("服务器", "IDC", "机房", "云主机", "托管"), negative=()),
            cloud_office_required=infer_positive_or_negative(text, positive=("云电脑", "云桌面", "云盘", "远程办公", "视频会议"), negative=()),
            security_required=infer_positive_or_negative(text, positive=("安全", "防护", "运维", "代维", "托管"), negative=()),
            industry_scene=infer_industry_scene(text),
            marketing_touch_required=infer_positive_or_negative(text, positive=("400", "来电名片", "挂机短信", "云录音", "营销触达"), negative=()),
            budget=budget_text or (str(int(budget)) if budget is not None else ""),
            reliability_level=infer_reliability_level(text),
            carrier_preference=infer_carrier_preference(text),
            region=infer_region(text) or access_source or "",
            customer_type=infer_customer_type(text),
            raw_keywords=raw_keywords,
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
                demand = self.primary.parse(raw_text)
                # LLM 偶尔会漏掉人数、预算、区域等显性字段；用本地规则做补漏，不覆盖模型已明确抽出的值。
                return merge_customer_demand(demand, self.fallback.parse(raw_text))
            except (ParserError, ValidationError, json.JSONDecodeError, Exception):
                pass
        return self.fallback.parse(raw_text)


def parse_customer_demand_json(content: str) -> CustomerDemand:
    payload = extract_json_object(content)
    return CustomerDemand.model_validate(payload)


def merge_customer_demand(primary: CustomerDemand, fallback: CustomerDemand) -> CustomerDemand:
    """把 LLM 结果和本地规则结果合并。

    LLM 负责语义理解；本地规则负责补齐客户原话里非常明确的人数、预算、区域、带宽等字段。
    """

    merged = primary.model_copy(deep=True)
    text_fields = [
        "primary_goal",
        "usage_scene",
        "business_action",
        "site_count",
        "user_count",
        "bandwidth_need",
        "overseas_target",
        "industry_scene",
        "budget",
        "reliability_level",
        "carrier_preference",
        "region",
        "customer_type",
    ]
    for field_name in text_fields:
        if not getattr(merged, field_name):
            setattr(merged, field_name, getattr(fallback, field_name))

    bool_fields = [
        "fixed_ip_required",
        "voice_required",
        "overseas_access",
        "server_or_idc_required",
        "cloud_office_required",
        "security_required",
        "marketing_touch_required",
    ]
    for field_name in bool_fields:
        if getattr(merged, field_name) is None:
            setattr(merged, field_name, getattr(fallback, field_name))

    if merged.fixed_ip_count is None:
        merged.fixed_ip_count = fallback.fixed_ip_count
    if merged.concurrent_calls is None:
        merged.concurrent_calls = fallback.concurrent_calls
    if not merged.raw_keywords:
        merged.raw_keywords = fallback.raw_keywords

    merged.missing_fields = [
        field_name
        for field_name in _dedupe([*primary.missing_fields, *fallback.missing_fields])
        if is_customer_need_field_missing(merged, field_name)
    ]
    merged.confidence = max(primary.confidence, fallback.confidence)
    return merged


def is_missing_value(value: Any) -> bool:
    """判断解析出的字段是否仍然缺失。"""

    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == "" or value == "未知"
    if isinstance(value, list):
        return not value
    return False


def is_customer_need_field_missing(demand: CustomerDemand, field_name: str) -> bool:
    """判断字段合并后是否仍为空，用于刷新 missing_fields。"""

    if not hasattr(demand, field_name):
        return False
    value = getattr(demand, field_name)
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == "" or value == "未知"
    if isinstance(value, list):
        return not value
    return False


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


def contains_any(text: str, keywords: list[str] | tuple[str, ...]) -> bool:
    normalized = text.lower()
    return any(keyword.lower() in normalized for keyword in keywords)


def extract_user_count(text: str) -> int | None:
    patterns = [
        r"(?:大概|约|差不多|预计)?\s*(\d+)\s*(?:个)?(?:人|用户|员工|座席|坐席|终端)",
        r"team\s*of\s*(\d+)",
        r"(\d+)\s*users?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return int(match.group(1))

    chinese_match = re.search(r"([零一二两三四五六七八九十百千万]+)\s*(?:个)?(?:人|用户|员工|座席|坐席|终端)", text)
    if chinese_match:
        return chinese_number_to_int(chinese_match.group(1))
    return None


def extract_bandwidth(text: str) -> int | None:
    # “5G套餐/5G融合/5G手机卡”是移动通信制式，不是 5000M 带宽。
    mobile_5g_context = re.search(r"5G[^，。；,;]{0,8}(?:套餐|融合|手机|移动|流量|卡)", text, re.I)
    pattern = r"(\d+(?:\.\d+)?)\s*(G|Gbps|M|Mbps|兆|千兆)"
    for match in re.finditer(pattern, text, re.I):
        if mobile_5g_context and match.group(0).lower().startswith("5g"):
            continue
        value = float(match.group(1))
        unit = match.group(2).lower()
        if unit in {"g", "gbps", "千兆"}:
            value *= 1000
        return math.ceil(value)
    return None


def extract_bandwidth_text(text: str) -> str:
    """保留客户原始带宽表达，供前端和 LLM 解释使用。"""

    mobile_5g_context = re.search(r"5G[^，。；,;]{0,8}(?:套餐|融合|手机|移动|流量|卡)", text, re.I)
    pattern = r"(\d+(?:\.\d+)?)\s*(G|Gbps|M|Mbps|兆|千兆)"
    for match in re.finditer(pattern, text, re.I):
        if mobile_5g_context and match.group(0).lower().startswith("5g"):
            continue
        return match.group(0)
    if "不确定" in text and contains_any(text, ("带宽", "速率")):
        return "客户不确定"
    return ""


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


def extract_budget_text(text: str) -> str:
    """保留预算原文，金额标准化由 CustomerDemand.budget_amount 负责。"""

    match = re.search(r"(?:预算|费用|价格|报价|控制在)[^，。；,;]{0,16}", text)
    if match:
        return match.group(0)
    match = re.search(r"\d+(?:\.\d+)?\s*(?:万|千|k|K|元|块)(?:左右|以内|以下|以上)?", text)
    return match.group(0) if match else ""


def extract_site_count_text(text: str) -> str:
    """抽取使用地点数量原文。"""

    if contains_any(text, ("总部", "分支", "分公司")):
        match = re.search(r"总部\s*[+加和与、]?\s*(\d+)\s*(?:个)?(?:分支|分公司|门店)", text)
        return match.group(0) if match else "总部+分支"
    match = re.search(r"(\d+)\s*(?:个)?(?:门店|网点|办公室|站点|点位)", text)
    if match:
        return match.group(0)
    if contains_any(text, ("多点", "多地")):
        return "多点"
    if contains_any(text, ("单点", "一个办公室", "一家门店")):
        return "单点"
    return ""


def extract_fixed_ip_count_text(text: str) -> str | None:
    """抽取固定公网 IP 数量原文。"""

    match = re.search(r"(\d+)\s*(?:个)?(?:固定)?(?:公网)?IP", text, re.I)
    return match.group(0) if match else None


def extract_concurrent_calls_text(text: str) -> str | None:
    """抽取语音并发、通道、线数或 30B+D 等表达。"""

    match = re.search(r"(\d+)\s*(?:路|线|通道|并发|坐席)", text)
    if match:
        return match.group(0)
    match = re.search(r"\d+B\s*\+\s*D", text, re.I)
    return match.group(0) if match else None


def infer_business_action(text: str) -> str | None:
    """从销售话术中识别办理动作。"""

    action_keywords = (
        ("拆机", ("拆机", "销户", "注销")),
        ("撤单", ("撤单",)),
        ("续约", ("续约", "续签")),
        ("改套餐", ("改套餐", "改资费", "换套餐")),
        ("变更", ("变更", "改业务")),
        ("移机", ("移机",)),
        ("过户", ("过户",)),
        ("新装", ("新装", "新办", "开通", "新办公室", "新门店")),
    )
    for action, keywords in action_keywords:
        if contains_any(text, keywords):
            return action
    return None


def infer_primary_goal(text: str, category_candidate_keywords: list[str]) -> str | None:
    """归纳客户的核心目标，优先使用原文中的强业务词。"""

    goal_keywords = (
        "海外访问",
        "海外SaaS",
        "海外 SaaS",
        "固定IP",
        "公网IP",
        "云中继",
        "30B+D",
        "云电脑",
        "远程办公",
        "IDC",
        "服务器托管",
        "办公上网",
        "办公上网",
        "组网",
        "固定电话",
        "5G",
        "流量套餐",
        "门店WiFi",
    )
    for keyword in goal_keywords:
        if keyword in text:
            return keyword
    if contains_any(text, ("美国", "香港", "新加坡", "海外", "国外", "跨境")) and contains_any(text, ("访问", "SaaS", "系统", "网站")):
        return "海外访问"
    for keyword in category_candidate_keywords:
        if keyword:
            return keyword
    return None


def infer_positive_or_negative(
    text: str,
    *,
    positive: tuple[str, ...],
    negative: tuple[str, ...],
) -> bool | None:
    """抽取三态需求：明确需要、明确不需要、未说明。"""

    if negative and contains_any(text, negative):
        return False
    if positive and contains_any(text, positive):
        return True
    return None


def infer_negative_requirement(text: str, keywords: tuple[str, ...]) -> bool | None:
    """只在客户明确否定时返回 false，否则保持未知。"""

    for keyword in keywords:
        if f"不需要{keyword}" in text or f"不要{keyword}" in text:
            return False
    return None


def infer_reliability_level(text: str) -> str:
    """抽取稳定性或时延要求。"""

    if contains_any(text, ("极高", "高保障", "专线保障")):
        return "极高"
    if contains_any(text, ("较高", "稳定", "低延迟", "低时延")):
        return "较高"
    if contains_any(text, ("普通", "一般")):
        return "普通"
    return ""


def infer_carrier_preference(text: str) -> str:
    """抽取运营商偏好。"""

    for carrier in ("电信", "联通", "移动"):
        if carrier in text:
            return carrier
    if "无偏好" in text or "都可以" in text:
        return "无偏好"
    return ""


def infer_region(text: str) -> str:
    """抽取客户或安装区域。"""

    regions = ["上海", "北京", "深圳", "广州", "杭州", "成都", "武汉", "南京", "外地", "跨省", "多地"]
    matched = [region for region in regions if region in text]
    return "、".join(matched)


def infer_customer_type(text: str) -> str:
    """抽取客户类型。"""

    for keyword in ("上海公司", "外地公司", "门店", "集团", "存量客户", "个体工商户", "小微企业"):
        if keyword in text:
            return keyword
    return ""


def infer_industry_scene(text: str) -> str:
    """抽取行业或专项场景。"""

    for keyword in ("视频监控", "电梯", "物业", "园区", "地图", "图像", "物联", "收银", "酒店"):
        if keyword in text:
            return keyword
    return ""


def infer_source_scope(text: str) -> RegionScope:
    if contains_any(text, ["美国办公室", "海外办公室", "新加坡办公室", "香港办公室", "海外分公司"]):
        return RegionScope.overseas
    if contains_any(text, ["上海", "北京", "深圳", "广州", "杭州", "国内", "中国", "办公室", "分公司", "总部", "门店"]):
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
            "海外SaaS",
            "海外 SaaS",
        ],
    ):
        return RegionScope.overseas
    if contains_any(text, ["国内组网", "内网互通", "多点组网", "总部", "分公司", "同城", "点对点"]):
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
    if contains_any(text, ["企业官网", "服务器对外", "备案"]):
        return "企业公网访问"
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
    if "门店" in text:
        return "门店"
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
    # scenario_type 只保留为兼容早期字段，不再作为推荐主分类。
    if requires_fixed_ip or bandwidth_est_mbps > high_bandwidth_threshold_mbps:
        return ScenarioType.dedicated_ip_or_high_bandwidth
    if target_scope == RegionScope.overseas and source_scope in {RegionScope.domestic, RegionScope.unknown}:
        return ScenarioType.overseas_access
    if target_scope == RegionScope.domestic or contains_any(text, ["组网", "互通", "分公司", "点对点"]):
        return ScenarioType.domestic_networking
    if contains_any(text, ["试用", "先试", "测试", "POC", "poc"]):
        return ScenarioType.trial_or_poc
    return ScenarioType.unknown


def extract_category_candidate_keywords(text: str) -> list[str]:
    """从 13 类 taxonomy 中提取客户需求侧命中的候选关键词。"""

    matched: list[str] = []
    normalized = text.lower()
    for rule in DEMAND_CATEGORY_RULES:
        for keyword in rule.demand_keywords:
            if keyword.lower() in normalized:
                matched.append(keyword)
    return _dedupe(matched)


def extract_keywords(text: str, *, category_candidate_keywords: list[str]) -> list[str]:
    candidates = [
        "固定IP",
        "公网IP",
        "专线",
        "海外",
        "美国",
        "国内组网",
        "内网互通",
        "试用",
        "预算",
        "IDC",
        "云电脑",
        "商云通",
        "云中继",
        "400电话",
    ]
    keywords = [item for item in candidates if item in text]
    keywords.extend(category_candidate_keywords)
    user_count = extract_user_count(text)
    if user_count:
        keywords.append(f"{user_count}人")
    bandwidth = extract_bandwidth(text)
    if bandwidth:
        keywords.append(f"{bandwidth}M")
    return _dedupe(keywords)


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


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
