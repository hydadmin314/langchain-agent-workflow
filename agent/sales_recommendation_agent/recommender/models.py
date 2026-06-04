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
