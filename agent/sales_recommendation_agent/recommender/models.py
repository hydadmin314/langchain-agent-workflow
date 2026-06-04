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
