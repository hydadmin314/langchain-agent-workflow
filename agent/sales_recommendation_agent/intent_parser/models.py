from __future__ import annotations

from enum import Enum
import re

from pydantic import BaseModel, Field, field_validator, model_validator


class RegionScope(str, Enum):
    """客户访问来源或目标区域范围。"""

    domestic = "domestic"
    overseas = "overseas"
    unknown = "unknown"


class ScenarioType(str, Enum):
    """销售需求的一层业务场景分类。"""

    overseas_access = "overseas_access"
    domestic_networking = "domestic_networking"
    dedicated_ip_or_high_bandwidth = "dedicated_ip_or_high_bandwidth"
    trial_or_poc = "trial_or_poc"
    unknown = "unknown"


class DemandAnalysisRequest(BaseModel):
    """销售原始需求输入。"""

    text: str = Field(min_length=1, description="销售侧输入的客户原始需求。")
    channel: str | None = Field(default=None, description="需求来源渠道，例如 CRM、企微、电话纪要。")
    request_id: str | None = Field(default=None, description="外部系统传入的追踪 ID。")


class CustomerDemand(BaseModel):
    """结构化后的客户需求字段表。

    这版字段面向销售推荐：保留业务可读文本字段，后续程序需要数值时通过兼容属性解析。
    """

    primary_category: str = Field(default="", description="13类中的主分类名称。")
    secondary_categories: list[str] = Field(default_factory=list, description="辅助分类，最多2个，用于交叉需求。")
    primary_goal: str = Field(default="", description="客户原始目标，例如办公上网、固定IP、云中继。")
    usage_scene: str = Field(default="", description="使用场景，例如办公室、门店、总部+分支。")
    business_action: str = Field(default="未知", description="办理动作，例如新装、变更、拆机、续约、未知。")
    site_count: str = Field(default="", description="使用地点数量，例如单点、多点、3个门店。")
    user_count: str = Field(default="", description="使用规模，例如10人、30坐席、20张手机卡。")
    bandwidth_need: str = Field(default="", description="带宽需求，例如100M、1G、客户不确定。")

    fixed_ip_required: bool | None = Field(default=None, description="是否需要固定公网IP。")
    fixed_ip_count: str | None = Field(default=None, description="需要的固定公网IP数量。")

    voice_required: bool | None = Field(default=None, description="是否需要语音/固定电话能力。")
    concurrent_calls: str | None = Field(default=None, description="语音并发数量或通道数。")

    overseas_access: bool | None = Field(default=None, description="是否有海外访问需求。")
    overseas_target: str = Field(default="", description="海外访问目标。")

    server_or_idc_required: bool | None = Field(default=None, description="是否涉及服务器、云主机、IDC或机房托管。")
    cloud_office_required: bool | None = Field(default=None, description="是否需要云办公能力。")
    security_required: bool | None = Field(default=None, description="是否需要安全防护、运维代维、网络托管。")
    industry_scene: str = Field(default="", description="行业场景。")
    marketing_touch_required: bool | None = Field(default=None, description="是否需要营销触达能力。")

    budget: str = Field(default="", description="预算，例如低、中、高、明确金额、未知。")
    reliability_level: str = Field(default="", description="稳定性要求。")
    carrier_preference: str = Field(default="", description="运营商偏好。")
    region: str = Field(default="", description="客户或安装区域。")
    customer_type: str = Field(default="", description="客户类型。")

    raw_keywords: list[str] = Field(default_factory=list, description="从原始需求中保留的关键触发词。")
    confidence: float = Field(default=0.6, ge=0, le=1, description="需求解析置信度。")
    missing_fields: list[str] = Field(default_factory=list, description="后续销售需要追问的关键字段。")

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_fields(cls, value):
        """兼容旧版字段，避免历史测试或外部调用一次性全部失效。"""

        if not isinstance(value, dict):
            return value
        if "bandwidth_need" in value or "primary_goal" in value:
            return value

        migrated = dict(value)
        access_source = value.get("access_source") or ""
        target_region = value.get("target_region") or ""
        user_count = value.get("user_count")
        bandwidth = value.get("bandwidth_est_mbps")
        budget = value.get("budget")

        migrated.setdefault("usage_scene", access_source)
        migrated.setdefault("region", access_source)
        migrated.setdefault("overseas_target", target_region)
        if user_count is not None and not isinstance(user_count, str):
            migrated["user_count"] = f"{user_count}人"
        else:
            migrated.setdefault("user_count", user_count or "")
        migrated.setdefault("bandwidth_need", f"{bandwidth}M" if bandwidth else "")
        if budget is not None and not isinstance(budget, str):
            migrated["budget"] = str(budget)
        else:
            migrated.setdefault("budget", budget or "")
        migrated.setdefault("fixed_ip_required", value.get("requires_fixed_ip"))
        migrated.setdefault("overseas_access", value.get("target_scope") == "overseas")
        migrated.setdefault("primary_goal", target_region or "")
        return migrated

    @field_validator("confidence")
    @classmethod
    def normalize_confidence(cls, value: float) -> float:
        """置信度统一收敛在 0 到 1。"""

        return max(0.0, min(float(value), 1.0))

    @property
    def access_source(self) -> str | None:
        """兼容旧字段：访问来源。"""

        return self.usage_scene or self.region or None

    @property
    def source_scope(self) -> RegionScope:
        """兼容旧字段：访问来源区域范围。"""

        if any(keyword in self.region for keyword in ("海外", "国外", "美国", "香港", "新加坡", "日本")):
            return RegionScope.overseas
        if self.region:
            return RegionScope.domestic
        return RegionScope.unknown

    @property
    def target_region(self) -> str | None:
        """兼容旧字段：访问目标。"""

        return self.overseas_target or self.primary_goal or None

    @property
    def target_scope(self) -> RegionScope:
        """兼容旧字段：访问目标区域范围。"""

        if self.overseas_access is True or self.overseas_target:
            return RegionScope.overseas
        if self.primary_goal or self.usage_scene:
            return RegionScope.domestic
        return RegionScope.unknown

    @property
    def user_count_value(self) -> int | None:
        """从 user_count 文本中解析规模数值，供排序和容量匹配使用。"""

        return _first_int(self.user_count)

    @property
    def bandwidth_est_mbps(self) -> int:
        """兼容旧字段：带宽统一转 Mbps。"""

        text = self.bandwidth_need or ""
        match = re.search(r"(\d+(?:\.\d+)?)\s*(G|Gbps|M|Mbps|兆|千兆)", text, re.I)
        if not match:
            return self.user_count_value or 0
        value = float(match.group(1))
        unit = match.group(2).lower()
        if unit in {"g", "gbps", "千兆"}:
            value *= 1000
        return max(0, int(value))

    @property
    def budget_amount(self) -> float | None:
        """从 budget 文本中解析预算金额，默认人民币。"""

        text = self.budget or ""
        match = re.search(r"(\d+(?:\.\d+)?)\s*(万|千|k|K|元|块)?", text)
        if not match:
            return None
        value = float(match.group(1))
        unit = match.group(2) or "元"
        if unit == "万":
            value *= 10000
        elif unit in {"千", "k", "K"}:
            value *= 1000
        return value

    @property
    def requires_fixed_ip(self) -> bool:
        """兼容旧字段：是否明确需要固定 IP。"""

        return self.fixed_ip_required is True

    @property
    def scenario_type(self) -> ScenarioType:
        """兼容旧字段：粗场景只作为辅助，不作为最终 13 类分类。"""

        if self.overseas_access is True:
            return ScenarioType.overseas_access
        if self.fixed_ip_required is True or self.bandwidth_est_mbps >= 100:
            return ScenarioType.dedicated_ip_or_high_bandwidth
        if any(keyword in self.primary_goal + self.usage_scene for keyword in ("组网", "互通", "总部", "分支")):
            return ScenarioType.domestic_networking
        return ScenarioType.unknown

    @property
    def category_candidate_keywords(self) -> list[str]:
        """兼容旧字段：把新版字段汇总成分类候选关键词。"""

        values = [
            self.primary_category,
            *self.secondary_categories,
            self.primary_goal,
            self.usage_scene,
            self.business_action,
            self.bandwidth_need,
            self.fixed_ip_count or "",
            self.concurrent_calls or "",
            self.overseas_target,
            self.industry_scene,
            self.reliability_level,
            self.carrier_preference,
            self.region,
            self.customer_type,
            *self.raw_keywords,
        ]
        if self.fixed_ip_required is True:
            values.append("固定IP")
        if self.voice_required is True:
            values.append("语音")
        if self.overseas_access is True:
            values.append("海外访问")
        if self.server_or_idc_required is True:
            values.append("IDC")
        if self.cloud_office_required is True:
            values.append("云办公")
        if self.security_required is True:
            values.append("安全防护")
        if self.marketing_touch_required is True:
            values.append("营销触达")
        return _dedupe(values)


def _first_int(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


class DemandCategoryMatch(BaseModel):
    """13 类产品需求分类体系中的命中结果。"""

    category_id: str
    category_name: str
    score: float = 0.0
    matched_keywords: list[str] = Field(default_factory=list)
    reason: str = ""


class DemandCategoryDecision(BaseModel):
    """需求分类结果。

    推荐主流程只依赖 13 类产品需求分类，不再依赖早期 4 类粗路由。
    """

    primary_category_id: str = ""
    primary_category_name: str = ""
    category_matches: list[DemandCategoryMatch] = Field(default_factory=list)
    recommendation_mode: str = "clarify"
    confidence: float = 0.0
    clarify_questions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    reason: str = ""


class RequirementAnalysisResult(BaseModel):
    """需求提取阶段的完整返回结果。"""

    request_id: str | None = None
    structured_data: CustomerDemand
    category_decision: DemandCategoryDecision
