from __future__ import annotations

from pydantic import BaseModel, Field

from agent.sales_recommendation_agent.intent_parser.models import DemandCategoryMatch
from agent.sales_recommendation_agent.product_repository.models import ProductCandidate


class RetrievedCandidate(BaseModel):
    """召回阶段命中的产品候选。

    retrieval_score 只表示“相关性召回分”，不是最终推荐排序分。
    """

    product: ProductCandidate
    retrieval_score: float
    demand_categories: list[DemandCategoryMatch] = Field(default_factory=list)
    matched_reasons: list[str] = Field(default_factory=list)
    matched_keywords: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CandidateRetrievalResult(BaseModel):
    """Candidate Retriever 的完整输出。"""

    primary_category_id: str = ""
    primary_category_name: str = ""
    recommendation_mode: str = "clarify"
    total_products: int
    matched_count: int
    demand_categories: list[DemandCategoryMatch] = Field(default_factory=list)
    candidates: list[RetrievedCandidate] = Field(default_factory=list)
    clarify_questions: list[str] = Field(default_factory=list)


class FilterReason(BaseModel):
    """Rule Filter 对单条候选产品给出的过滤或风险原因。

    code 用于程序识别，message 用于前端或日志展示，evidence 尽量保留触发判断的文本依据。
    """

    code: str
    message: str
    severity: str = "warning"
    evidence: str = ""


class FilteredCandidate(BaseModel):
    """经过 Rule Filter 处理后的候选产品。

    decision=keep 表示继续进入后续排序；decision=remove 表示当前候选不再参与推荐。
    """

    candidate: RetrievedCandidate
    decision: str
    filter_reasons: list[FilterReason] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)


class CandidateFilterResult(BaseModel):
    """Rule Filter 的完整输出。

    本层只做确定性过滤和风险打标，不负责最终排序，也不生成销售话术。
    """

    primary_category_id: str = ""
    primary_category_name: str = ""
    recommendation_mode: str = "clarify"
    total_candidates: int = 0
    kept_count: int = 0
    removed_count: int = 0
    kept_candidates: list[FilteredCandidate] = Field(default_factory=list)
    removed_candidates: list[FilteredCandidate] = Field(default_factory=list)
    global_warnings: list[str] = Field(default_factory=list)
    clarify_questions: list[str] = Field(default_factory=list)


class ScoreReason(BaseModel):
    """Scorer 给候选产品加分或扣分的解释。

    score_delta 为正数表示加分，为负数表示扣分；后续前端可以直接展示排序依据。
    """

    code: str
    score_delta: float
    message: str
    evidence: str = ""


class ScoredCandidate(BaseModel):
    """经过 Scorer 排序后的候选产品。"""

    filtered_candidate: FilteredCandidate
    final_score: float
    score_reasons: list[ScoreReason] = Field(default_factory=list)
    risk_penalties: list[ScoreReason] = Field(default_factory=list)


class CandidateScoreResult(BaseModel):
    """Scorer 的完整输出。

    排序只基于程序规则，保证稳定、可解释；大模型后续只读取 Top N 结果做表达。
    """

    primary_category_id: str = ""
    primary_category_name: str = ""
    recommendation_mode: str = "clarify"
    total_candidates: int = 0
    scored_count: int = 0
    scored_candidates: list[ScoredCandidate] = Field(default_factory=list)
    clarify_questions: list[str] = Field(default_factory=list)
    global_warnings: list[str] = Field(default_factory=list)


class PackageComparisonSummary(BaseModel):
    """基础套餐对比摘要。

    这里不展开所有套餐明细，只提炼销售最常用的名称、速率、价格、周期和协议期。
    """

    package_count: int = 0
    package_names: list[str] = Field(default_factory=list)
    speeds: list[str] = Field(default_factory=list)
    price_values: list[float] = Field(default_factory=list)
    price_range: str = ""
    billing_periods: list[str] = Field(default_factory=list)
    contract_periods: list[str] = Field(default_factory=list)
    has_voice_values: list[str] = Field(default_factory=list)


class FeeComparisonSummary(BaseModel):
    """费用规则对比摘要。

    第一版只判断费用类型是否存在，不做折扣和总价核算。
    """

    fee_rule_count: int = 0
    has_monthly_fee: bool = False
    has_one_time_fee: bool = False
    has_installation_fee: bool = False
    has_deposit: bool = False
    has_penalty_rule: bool = False
    amount_values: list[float] = Field(default_factory=list)
    fee_rule_names: list[str] = Field(default_factory=list)


class OptionalPackageComparisonSummary(BaseModel):
    """可选包、权益包和增值包对比摘要。"""

    optional_package_count: int = 0
    names: list[str] = Field(default_factory=list)
    paid_package_names: list[str] = Field(default_factory=list)
    free_package_names: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    has_incompatibility: bool = False


class ConstraintComparisonSummary(BaseModel):
    """限制、准入和协议风险摘要。"""

    constraint_count: int = 0
    blocks_recommendation: bool = False
    important_risks: list[str] = Field(default_factory=list)


class MaterialComparisonSummary(BaseModel):
    """办理材料摘要。

    当前 ProductCandidate 尚未映射 application_materials，因此第一版先预留字段。
    """

    available: bool = False
    material_count: int = 0
    material_names: list[str] = Field(default_factory=list)
    missing_reason: str = ""


class ComparedProduct(BaseModel):
    """Comparator 输出中的单个产品对比对象。"""

    rank: int
    document_id: str = ""
    product_name: str = ""
    product_family: str = ""
    carrier: str = ""
    region: str = ""
    document_type: str = ""
    category_path: str = ""
    final_score: float = 0.0
    retrieval_score: float = 0.0
    recommendation_status: str = "candidate"
    matched_strengths: list[str] = Field(default_factory=list)
    risk_warnings: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    package_summary: PackageComparisonSummary = Field(default_factory=PackageComparisonSummary)
    fee_summary: FeeComparisonSummary = Field(default_factory=FeeComparisonSummary)
    optional_package_summary: OptionalPackageComparisonSummary = Field(default_factory=OptionalPackageComparisonSummary)
    constraint_summary: ConstraintComparisonSummary = Field(default_factory=ConstraintComparisonSummary)
    material_summary: MaterialComparisonSummary = Field(default_factory=MaterialComparisonSummary)
    source: dict[str, str] = Field(default_factory=dict)


class ComparisonDimension(BaseModel):
    """跨产品维度对比摘要。

    这部分给后续 LLM Explainer 使用，让它能基于结构化差异生成销售说明。
    """

    dimension: str
    summary: str


class ComparisonResult(BaseModel):
    """Comparator 的完整输出。"""

    primary_category_id: str = ""
    primary_category_name: str = ""
    recommendation_mode: str = "clarify"
    compared_count: int = 0
    products: list[ComparedProduct] = Field(default_factory=list)
    comparison_dimensions: list[ComparisonDimension] = Field(default_factory=list)
    global_questions: list[str] = Field(default_factory=list)
    global_warnings: list[str] = Field(default_factory=list)


class RecommendationProductExplanation(BaseModel):
    """LLM 对单个候选产品生成的推荐说明。

    document_id 用来和候选产品主数据做稳定关联，reason 只能解释已有对比结果，不能新增产品能力。
    """

    document_id: str = ""
    product_name: str = ""
    reason: str = ""


class RecommendationExplanationResult(BaseModel):
    """LLM Recommendation Explainer 的结构化输出。

    该结果面向销售阅读，但仍保持 JSON 结构，方便后续前端分区展示。
    """

    summary: str = ""
    recommended_product: RecommendationProductExplanation = Field(default_factory=RecommendationProductExplanation)
    alternative_products: list[RecommendationProductExplanation] = Field(default_factory=list)
    comparison_summary: list[str] = Field(default_factory=list)
    risk_reminders: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    sales_talk: str = ""
    evidence_notes: list[str] = Field(default_factory=list)
