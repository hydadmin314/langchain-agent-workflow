from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class PackageCandidate(BaseModel):
    """基础套餐候选项。

    一份产品文档里可能包含多个套餐档位，因此这里是产品候选下的列表元素。
    """

    package_name: str = ""
    package_code: str = ""
    speed: str = ""
    upstream_speed: str = ""
    downstream_speed: str = ""
    bandwidth_unit: str = ""
    has_voice: bool | None = None
    price: float | None = None
    currency: str = ""
    billing_period: str = ""
    contract_period: str = ""
    quantity_limit: str = ""
    applicable_conditions: list[str] = Field(default_factory=list)
    included_items: list[dict[str, Any]] = Field(default_factory=list)
    source_evidence: str = ""
    source_location: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    raw: dict[str, Any] = Field(default_factory=dict)


class OptionalPackageCandidate(BaseModel):
    """可选包、权益包或增值包候选项。"""

    name: str = ""
    package_type: str = ""
    category: str = ""
    description: str = ""
    fee_summary: str = ""
    price_items: list[dict[str, Any]] = Field(default_factory=list)
    options: list[Any] = Field(default_factory=list)
    required_with: list[str] = Field(default_factory=list)
    incompatible_with: list[str] = Field(default_factory=list)
    applicable_conditions: list[str] = Field(default_factory=list)
    source_evidence: str = ""
    source_location: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    raw: dict[str, Any] = Field(default_factory=dict)


class PriceRuleCandidate(BaseModel):
    """费用和期限规则。

    这里不做报价计算，只保留后续 Calculator 或推荐规则需要使用的事实。
    """

    rule_type: str = ""
    name: str = ""
    description: str = ""
    amount: float | None = None
    currency: str = ""
    billing_period: str = ""
    contract_period: str = ""
    conditions: list[str] = Field(default_factory=list)
    applies_to: list[str] = Field(default_factory=list)
    source_evidence: str = ""
    source_location: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    raw: dict[str, Any] = Field(default_factory=dict)


class ConstraintCandidate(BaseModel):
    """准入、限制、协议和售后类规则。"""

    constraint_type: str = ""
    name: str = ""
    description: str = ""
    condition: str = ""
    result: str = ""
    blocks_recommendation: bool = False
    applies_to: list[str] = Field(default_factory=list)
    rule_source: str = ""
    source_evidence: str = ""
    source_location: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    raw: dict[str, Any] = Field(default_factory=dict)


class ProductCandidate(BaseModel):
    """推荐侧统一产品候选对象。

    Product Repository 的核心输出就是这个对象列表。推荐、对比和解释模块都基于它继续工作。
    """

    document_id: str = ""
    title: str = ""
    product_name: str = ""
    product_family: str = ""
    carrier: str = ""
    region: str = ""
    document_type: str = ""
    document_status: str = ""
    filename: str = ""
    source_path: str = ""
    source_file_type: str = ""
    published_json_path: str = ""
    category_path: str = ""
    category_levels: list[str] = Field(default_factory=list)
    packages: list[PackageCandidate] = Field(default_factory=list)
    optional_packages: list[OptionalPackageCandidate] = Field(default_factory=list)
    fee_rules: list[PriceRuleCandidate] = Field(default_factory=list)
    constraints: list[ConstraintCandidate] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        """产品是否处于可推荐状态的基础判断。

        更复杂的推荐阻断逻辑放到 recommender/rule_filter.py，这里只判断文档状态。
        """

        return self.document_status.lower() not in {"inactive", "disabled", "stopped"}


class ProductLoadError(BaseModel):
    """单个 JSON 加载失败时的错误记录。"""

    path: str
    error_type: str
    message: str


class ProductLoadResult(BaseModel):
    """批量加载结果，保留错误列表便于后续排查数据质量。"""

    products: list[ProductCandidate] = Field(default_factory=list)
    errors: list[ProductLoadError] = Field(default_factory=list)

    @property
    def product_count(self) -> int:
        return len(self.products)

    @property
    def error_count(self) -> int:
        return len(self.errors)


def path_to_text(path: str | Path) -> str:
    """统一路径输出，避免模型对象里混入 Path 类型导致 JSON 序列化不稳定。"""

    return str(Path(path))
