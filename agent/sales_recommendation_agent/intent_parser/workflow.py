from __future__ import annotations

from agent.sales_recommendation_agent.intent_parser.config import SalesRecommendationSettings
from agent.sales_recommendation_agent.intent_parser.models import DemandAnalysisRequest, RequirementAnalysisResult
from agent.sales_recommendation_agent.intent_parser.parsers import DemandParser, ResilientDemandParser
from agent.sales_recommendation_agent.intent_parser.rules import DemandCategoryClassifier


class SalesRequirementWorkflow:
    """销售推荐 Agent 的需求提取入口。

    当前只负责：客户原始需求 -> 结构化需求 -> 上网/组网六类销售套餐分类。
    后续产品召回、过滤、排序、对比和报价不要放在这里，避免职责变重。
    """

    def __init__(
        self,
        *,
        settings: SalesRecommendationSettings | None = None,
        parser: DemandParser | None = None,
        classifier: DemandCategoryClassifier | None = None,
    ):
        self.settings = settings or SalesRecommendationSettings.from_env()
        self.parser = parser or ResilientDemandParser(self.settings)
        self.classifier = classifier or DemandCategoryClassifier()

    def analyze(self, request: DemandAnalysisRequest | str) -> RequirementAnalysisResult:
        if isinstance(request, str):
            request = DemandAnalysisRequest(text=request)

        structured_data = self.parser.parse(request.text)
        # 最终分类统一交给 DemandCategoryClassifier。
        # LLM 即使输出了分类字段，也只当作不可信中间信息，避免和 taxonomy 裁决冲突。
        structured_data.primary_domain = ""
        structured_data.primary_category = ""
        structured_data.secondary_categories = []
        category_decision = self.classifier.classify(
            demand=structured_data,
            raw_text=request.text,
        )
        # 分类器是六类销售套餐体系的最终裁决者，回填到需求字段表，方便后续前端和推荐模块直接展示。
        structured_data.primary_domain = category_decision.primary_domain
        structured_data.primary_category = category_decision.primary_category_name
        structured_data.secondary_categories = [
            item.category_name
            for item in category_decision.category_matches
            if item.category_id != category_decision.primary_category_id
        ][:2]
        return RequirementAnalysisResult(
            request_id=request.request_id,
            structured_data=structured_data,
            category_decision=category_decision,
        )

    def run(self, query: str) -> RequirementAnalysisResult:
        """兼容 Agent 常用的 run(query) 调用形式。"""

        return self.analyze(query)
