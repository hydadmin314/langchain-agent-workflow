from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
    """结构化后的客户需求，供后续场景路由、产品召回和推荐排序使用。"""

    access_source: str | None = Field(default=None, description="访问来源，例如上海办公室、深圳分公司。")
    source_scope: RegionScope = Field(default=RegionScope.unknown, description="访问来源区域范围。")
    target_region: str | None = Field(default=None, description="访问目标，例如美国 SaaS、总部机房。")
    target_scope: RegionScope = Field(default=RegionScope.unknown, description="访问目标区域范围。")
    user_count: int | None = Field(default=None, ge=1, description="预计使用人数。")
    bandwidth_est_mbps: int = Field(default=0, ge=0, description="估算或明确提出的带宽，单位 Mbps。")
    duration: str | None = Field(default=None, description="试用期、合同期或客户希望的使用周期。")
    budget: float | None = Field(default=None, ge=0, description="客户预算，默认按人民币理解。")
    requires_fixed_ip: bool = Field(default=False, description="是否明确需要固定 IP、公网 IP 或专线资源。")
    scenario_type: ScenarioType = Field(default=ScenarioType.unknown, description="解析得到的业务场景。")
    raw_keywords: list[str] = Field(default_factory=list, description="从原始需求中保留的关键触发词。")
    confidence: float = Field(default=0.6, ge=0, le=1, description="需求解析置信度。")
    missing_fields: list[str] = Field(default_factory=list, description="后续销售需要追问的关键字段。")

    @field_validator("bandwidth_est_mbps")
    @classmethod
    def normalize_bandwidth(cls, value: int) -> int:
        """带宽进入后续模块前统一为非负整数。"""

        return max(0, int(value))


class RouteDecision(BaseModel):
    """场景路由结果，暂时只负责把需求送到合适的产品推荐分支。"""

    route: Literal[
        "flow_overseas_access",
        "flow_domestic_networking",
        "flow_dedicated_ip_bandwidth",
        "flow_clarify_requirements",
    ]
    action: str
    matched_rules: list[str] = Field(default_factory=list)
    priority: int = Field(ge=0, le=100)
    reason: str


class RequirementAnalysisResult(BaseModel):
    """需求提取阶段的完整返回结果。"""

    request_id: str | None = None
    structured_data: CustomerDemand
    decision: RouteDecision
