from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent.sales_recommendation_agent.intent_parser import SalesRequirementWorkflow
from agent.sales_recommendation_agent.intent_parser.models import (
    CustomerDemand,
    DemandCategoryDecision,
    DemandCategoryMatch,
    RequirementAnalysisResult,
)
from agent.sales_recommendation_agent.intent_parser.parsers import (
    has_domestic_networking_signal,
    has_explicit_overseas_signal,
    has_negated_overseas_signal,
)
from agent.sales_recommendation_agent.product_repository import ProductLoadResult, ProductRepository
from agent.sales_recommendation_agent.recommender import (
    CandidateComparator,
    CandidateFilterResult,
    CandidateRetrievalResult,
    CandidateRetriever,
    CandidateRuleFilter,
    CandidateScoreResult,
    CandidateScorer,
    ClarificationQuestionGenerator,
    ClarificationQuestionResult,
    ComparisonResult,
    RecommendationExplainer,
    RecommendationExplanationResult,
    RecommendationReadinessEvaluator,
    ReadinessResult,
)
from memory.sales_recommendation_agent_memory import (
    ConversationState,
    CustomerDemandMerger,
    DemandMergeResult,
    InMemoryConversationStore,
)


WorkflowStatus = Literal["ask_clarification", "recommended", "no_candidate", "failed"]


class SalesRecommendationWorkflowResult(BaseModel):
    """销售推荐总流程的统一返回对象。

    前端或测试脚本只需要看 status 和 assistant_message；
    调试时可以继续展开 intent、retrieval、score、comparison 等结构化结果。
    """

    session_id: str
    status: WorkflowStatus
    assistant_message: str = ""
    user_text: str = ""
    customer_need: CustomerDemand
    category_decision: DemandCategoryDecision
    intent: RequirementAnalysisResult | None = None
    merge_result: DemandMergeResult | None = None
    readiness: ReadinessResult | None = None
    clarification: ClarificationQuestionResult | None = None
    product_load: ProductLoadResult | None = None
    retrieval: CandidateRetrievalResult | None = None
    filter_result: CandidateFilterResult | None = None
    score: CandidateScoreResult | None = None
    comparison: ComparisonResult | None = None
    explanation: RecommendationExplanationResult | None = None
    state: ConversationState
    errors: list[str] = Field(default_factory=list)


class SalesRecommendationWorkflow:
    """销售推荐 Agent 的总入口。

    它只负责编排，不把召回、过滤、打分、对比、LLM 说明等细节写在这里。
    这样后续前端只要调用 run(session_id, user_text)，不用关心内部有多少模块。
    """

    def __init__(
        self,
        *,
        published_root: str = "data/product_doc_agent/published",
        top_k: int = 8,
        skip_explainer: bool = False,
        requirement_workflow: SalesRequirementWorkflow | None = None,
        memory_store: InMemoryConversationStore | None = None,
        demand_merger: CustomerDemandMerger | None = None,
        readiness_evaluator: RecommendationReadinessEvaluator | None = None,
        clarification_generator: ClarificationQuestionGenerator | None = None,
        product_repository: ProductRepository | None = None,
        retriever: CandidateRetriever | None = None,
        rule_filter: CandidateRuleFilter | None = None,
        scorer: CandidateScorer | None = None,
        comparator: CandidateComparator | None = None,
        explainer: RecommendationExplainer | None = None,
    ) -> None:
        self.published_root = published_root
        self.top_k = top_k
        self.skip_explainer = skip_explainer
        self.requirement_workflow = requirement_workflow or SalesRequirementWorkflow()
        self.memory_store = memory_store or InMemoryConversationStore()
        self.demand_merger = demand_merger or CustomerDemandMerger()
        self.readiness_evaluator = readiness_evaluator or RecommendationReadinessEvaluator()
        self.clarification_generator = clarification_generator or ClarificationQuestionGenerator()
        self.product_repository = product_repository or ProductRepository(published_root)
        self.retriever = retriever or CandidateRetriever(default_top_k=top_k)
        self.rule_filter = rule_filter or CandidateRuleFilter()
        self.scorer = scorer or CandidateScorer()
        self.comparator = comparator or CandidateComparator()
        self.explainer = explainer or RecommendationExplainer()

    def run(self, *, session_id: str, user_text: str) -> SalesRecommendationWorkflowResult:
        """执行一轮销售推荐对话。

        多轮记忆通过 session_id 串起来：同一个 session_id 下，用户后续补充的信息会合并到历史需求表。
        """

        state = self.memory_store.get_or_create(session_id)
        intent_result = self.requirement_workflow.analyze(user_text)
        # 大模型负责语义理解，但一些明确的否定、人数、海外目标等信息可以由程序稳定兜底。
        # 这样可以避免“不要固定 IP”被模型误识别成“需要固定 IP”这类高风险错误进入上下文记忆。
        self._apply_deterministic_need_corrections(
            demand=intent_result.structured_data,
            user_text=user_text,
        )
        merge_result = self.demand_merger.merge(
            existing=state.customer_need,
            incoming=intent_result.structured_data,
            user_text=user_text,
        )
        merged_need = merge_result.customer_need
        category_decision = self._classify_merged_need(
            demand=merged_need,
            user_text=user_text,
        )
        # 分类器可能会根据规则回填部分字段，分类后再做一次轻量修正，确保显式事实不被覆盖。
        self._apply_deterministic_need_corrections(
            demand=merged_need,
            user_text=user_text,
        )
        self._apply_category_priority_corrections(
            demand=merged_need,
            category_decision=category_decision,
            user_text=user_text,
        )
        self._sync_state_after_merge(
            state=state,
            demand=merged_need,
            category_decision=category_decision,
            merge_result=merge_result,
        )

        readiness = self.readiness_evaluator.evaluate(
            demand=merged_need,
            category_decision=category_decision,
        )
        state.readiness = readiness.model_dump(mode="json")
        state.assumptions = readiness.assumptions

        if readiness.decision == "ask_clarification":
            clarification = self.clarification_generator.generate(
                query=user_text,
                demand=merged_need,
                category_decision=category_decision,
                readiness_result=readiness,
            )
            assistant_message = clarification.message
            state.asked_questions.extend(clarification.questions)
            state.append_turn(
                user_text=user_text,
                assistant_message=assistant_message,
                status="ask_clarification",
            )
            self.memory_store.save(state)
            return SalesRecommendationWorkflowResult(
                session_id=state.session_id,
                status="ask_clarification",
                assistant_message=assistant_message,
                user_text=user_text,
                customer_need=merged_need,
                category_decision=category_decision,
                intent=intent_result,
                merge_result=merge_result,
                readiness=readiness,
                clarification=clarification,
                state=state,
            )

        return self._run_recommendation_stage(
            state=state,
            user_text=user_text,
            intent_result=intent_result,
            merge_result=merge_result,
            readiness=readiness,
        )

    def _apply_deterministic_need_corrections(
        self,
        *,
        demand: CustomerDemand,
        user_text: str,
    ) -> None:
        """对本轮大模型解析结果做确定性兜底修正。

        这里不做复杂业务判断，只处理文本里明确出现的事实：
        - “不需要固定 IP”这类否定表达；
        - “10人/20个员工/30坐席”这类人数规模；
        - “美国 SaaS/海外系统”这类海外访问信号。
        """

        if re.search(r"(不需要|不要|无需|不用).{0,8}(固定\s*IP|公网\s*IP|公网地址)", user_text, re.I):
            demand.fixed_ip_required = False
            if demand.primary_goal in {"固定IP", "固定 IP", "公网IP", "公网 IP"}:
                demand.primary_goal = ""
        elif re.search(r"(需要|要|必须).{0,8}(固定\s*IP|公网\s*IP|公网地址)", user_text, re.I):
            demand.fixed_ip_required = True

        user_count_match = re.search(r"(\d+)\s*(人|个员工|名员工|坐席|终端|账号)", user_text)
        if user_count_match:
            demand.user_count = f"{user_count_match.group(1)}{user_count_match.group(2)}"

        if has_negated_overseas_signal(user_text):
            demand.overseas_access = False
            demand.overseas_target = ""
        elif has_explicit_overseas_signal(user_text):
            demand.overseas_access = True
            if not demand.overseas_target:
                target_match = re.search(
                    r"(海外|国外|境外|跨境|国际|出海|外贸|美国|日本|新加坡|香港|"
                    r"US|USA|United States|overseas|foreign|international|cross[-\s]?border)"
                    r"[^，。；,;]{0,20}",
                    user_text,
                    re.I,
                )
                if target_match:
                    demand.overseas_target = target_match.group(0).strip()

    def _apply_category_priority_corrections(
        self,
        *,
        demand: CustomerDemand,
        category_decision: DemandCategoryDecision,
        user_text: str,
    ) -> None:
        """对合并后的分类结果做强场景优先级修正。

        目前最典型的是海外访问：客户明确说“访问美国 SaaS 很慢”时，
        主分类应该优先是“海外访问与跨境加速”，而不是被办公宽带或固定 IP 泛化词压过去。
        """

        if category_decision.primary_category_id == "13":
            return

        combined_text = " ".join(
            [
                user_text or "",
                demand.primary_goal or "",
                demand.usage_scene or "",
                demand.site_count or "",
                demand.fixed_ip_count or "",
                *demand.raw_keywords,
            ]
        )
        has_overseas = has_overseas_access_signal(user_text=user_text, demand=demand)
        has_domestic_networking = has_domestic_networking_signal(combined_text)

        if has_overseas:
            demand.overseas_access = True
            category_decision.category_matches = promote_category_match(
                matches=category_decision.category_matches,
                category_id="4",
                category_name="海外访问与跨境加速",
                score=96,
                reason="命中海外访问强场景信号，提升为主分类。",
                matched_keywords=["海外访问"],
            )
            primary = category_decision.category_matches[0]
            category_decision.primary_category_id = primary.category_id
            category_decision.primary_category_name = primary.category_name
            category_decision.reason = primary.reason
            demand.primary_category = primary.category_name
            demand.secondary_categories = [
                item.category_name
                for item in category_decision.category_matches
                if item.category_id != primary.category_id
            ][:2]
            return

        if has_domestic_networking:
            category_decision.category_matches = promote_category_match(
                matches=category_decision.category_matches,
                category_id="3",
                category_name="国内组网与点对点专线",
                score=94,
                reason="命中国内异地/总部分支互联场景，提升为主分类。",
                matched_keywords=["国内组网"],
            )
            primary = category_decision.category_matches[0]
            category_decision.primary_category_id = primary.category_id
            category_decision.primary_category_name = primary.category_name
            category_decision.reason = primary.reason
            demand.primary_category = primary.category_name
            demand.secondary_categories = [
                item.category_name
                for item in category_decision.category_matches
                if item.category_id != primary.category_id
            ][:2]
            return

        # 明确固定公网 IP / 公网地址 / 服务器对外访问时，优先进入固定 IP 专线类。
        if demand.requires_fixed_ip or re.search(r"(固定\s*IP|公网\s*IP|公网地址|固定公网|服务器对外)", combined_text, re.I):
            category_decision.category_matches = promote_category_match(
                matches=category_decision.category_matches,
                category_id="2",
                category_name="固定IP_高带宽_互联网专线",
                score=94,
                reason="命中固定公网 IP 或服务器对外访问场景，提升为主分类。",
                matched_keywords=["固定公网IP"],
            )
            primary = category_decision.category_matches[0]
            category_decision.primary_category_id = primary.category_id
            category_decision.primary_category_name = primary.category_name
            category_decision.reason = primary.reason
            demand.primary_category = primary.category_name
            demand.secondary_categories = [
                item.category_name
                for item in category_decision.category_matches
                if item.category_id != primary.category_id
            ][:2]
            return

        # 普通办公宽带、门店小微这类泛场景交给 LLM 分类和 taxonomy 得分处理。
        # 这里不再做关键词提权，避免 workflow 逐渐变成第二套规则分类器。

    def _classify_merged_need(
        self,
        *,
        demand: CustomerDemand,
        user_text: str,
    ) -> DemandCategoryDecision:
        """对合并后的需求重新做 13 类分类。

        单轮解析得到的分类只代表“本轮输入”；多轮场景必须基于合并后的 customer_need 重新判断。
        """

        classifier = getattr(self.requirement_workflow, "classifier", None)
        if classifier is None:
            return DemandCategoryDecision(
                primary_category_name=demand.primary_category,
                category_matches=[],
                recommendation_mode="clarify",
            )

        category_decision = classifier.classify(demand=demand, raw_text=user_text)
        demand.primary_category = category_decision.primary_category_name
        demand.secondary_categories = [
            item.category_name
            for item in category_decision.category_matches
            if item.category_id != category_decision.primary_category_id
        ][:2]
        return category_decision

    def _sync_state_after_merge(
        self,
        *,
        state: ConversationState,
        demand: CustomerDemand,
        category_decision: DemandCategoryDecision,
        merge_result: DemandMergeResult,
    ) -> None:
        """把本轮合并结果写回会话状态。"""

        state.customer_need = demand
        state.category_decision = category_decision
        state.answered_fields.extend(
            field_name
            for field_name in merge_result.answered_fields
            if field_name not in state.answered_fields
        )
        state.conflicts.extend(merge_result.conflicts)

    def _run_recommendation_stage(
        self,
        *,
        state: ConversationState,
        user_text: str,
        intent_result: RequirementAnalysisResult,
        merge_result: DemandMergeResult,
        readiness: ReadinessResult,
    ) -> SalesRecommendationWorkflowResult:
        """执行产品召回、过滤、打分、对比和推荐说明生成。"""

        product_result = self.product_repository.load_result()
        retrieval_result = self.retriever.retrieve(
            demand=state.customer_need,
            category_decision=state.category_decision,
            products=product_result.products,
            top_k=self.top_k,
        )
        filter_result = self.rule_filter.apply(
            demand=state.customer_need,
            category_decision=state.category_decision,
            retrieval_result=retrieval_result,
        )
        score_result = self.scorer.score(
            demand=state.customer_need,
            category_decision=state.category_decision,
            filter_result=filter_result,
            top_k=self.top_k,
        )
        comparison_result = self.comparator.compare(
            demand=state.customer_need,
            category_decision=state.category_decision,
            score_result=score_result,
            top_n=min(self.top_k, 3),
        )

        explanation_result: RecommendationExplanationResult | None = None
        errors: list[str] = []
        if comparison_result.products and not self.skip_explainer:
            try:
                explanation_result = self.explainer.explain(
                    query=user_text,
                    demand=state.customer_need,
                    category_decision=state.category_decision,
                    comparison_result=comparison_result,
                    readiness_result=readiness,
                )
            except Exception as exc:
                errors.append(f"推荐说明生成失败：{exc}")

        if not comparison_result.products:
            status: WorkflowStatus = "no_candidate"
            assistant_message = "当前已完成需求理解，但 published 产品库里暂时没有召回到合适候选，需要补充产品数据或调整分类规则。"
        elif explanation_result:
            status = "recommended"
            assistant_message = explanation_result.summary or explanation_result.sales_talk
        else:
            status = "recommended"
            assistant_message = build_programmatic_recommendation_message(comparison_result)

        state.append_turn(
            user_text=user_text,
            assistant_message=assistant_message,
            status=status,
        )
        self.memory_store.save(state)
        return SalesRecommendationWorkflowResult(
            session_id=state.session_id,
            status=status,
            assistant_message=assistant_message,
            user_text=user_text,
            customer_need=state.customer_need,
            category_decision=state.category_decision,
            intent=intent_result,
            merge_result=merge_result,
            readiness=readiness,
            product_load=product_result,
            retrieval=retrieval_result,
            filter_result=filter_result,
            score=score_result,
            comparison=comparison_result,
            explanation=explanation_result,
            state=state,
            errors=errors,
        )


def build_programmatic_recommendation_message(comparison_result: ComparisonResult) -> str:
    """没有启用 LLM Explainer 时的程序兜底回复。"""

    if not comparison_result.products:
        return "当前没有可推荐候选产品。"

    top_product = comparison_result.products[0]
    name = top_product.product_name or top_product.document_id
    return (
        f"已根据当前需求召回并排序候选产品，优先建议查看：{name}。"
        "后续可结合价格、协议期、可选包和办理材料做人工确认。"
    )


def has_overseas_access_signal(*, user_text: str, demand: CustomerDemand) -> bool:
    """只在出现明确境外/跨境信号时才认为是海外访问。

    这里不能把 overseas_target 非空直接当成海外信号，因为上游模型偶尔会把
    “国内多点组网”“总部访问分支”误写进 overseas_target。最终判断统一走
    has_explicit_overseas_signal，避免国内异地访问被错误提升到海外访问类。
    """

    return has_explicit_overseas_signal(
        " ".join(
            [
                user_text or "",
                demand.overseas_target or "",
                demand.primary_goal or "",
                demand.usage_scene or "",
                demand.primary_category or "",
                *demand.secondary_categories,
                demand.region or "",
                *demand.raw_keywords,
            ]
        )
    )


def promote_category_match(
    *,
    matches: list[DemandCategoryMatch],
    category_id: str,
    category_name: str,
    score: float,
    reason: str,
    matched_keywords: list[str] | None = None,
) -> list[DemandCategoryMatch]:
    """把指定分类提升到 matches 首位，保留其它候选分类作为辅助分类。"""

    updated: list[DemandCategoryMatch] = []
    found = False
    for match in matches:
        if match.category_id == category_id:
            found = True
            updated.append(
                DemandCategoryMatch(
                    category_id=match.category_id,
                    category_name=match.category_name,
                    score=max(match.score, score),
                    matched_keywords=match.matched_keywords,
                    reason=reason,
                )
            )
        else:
            updated.append(match)

    if not found:
        updated.append(
            DemandCategoryMatch(
                category_id=category_id,
                category_name=category_name,
                score=score,
                matched_keywords=matched_keywords or [category_name],
                reason=reason,
            )
        )

    updated.sort(key=lambda item: item.score, reverse=True)
    return updated[:3]
