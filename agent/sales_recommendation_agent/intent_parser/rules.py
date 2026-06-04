from __future__ import annotations

from typing import Iterable

from agent.sales_recommendation_agent.intent_parser.models import (
    CustomerDemand,
    DemandCategoryDecision,
    DemandCategoryMatch,
)
from agent.sales_recommendation_agent.intent_parser.taxonomy import (
    DEMAND_CATEGORY_RULES,
    DEMAND_CATEGORY_RULES_BY_ID,
    DemandCategoryRule,
)


class DemandCategoryClassifier:
    """基于 13 类产品需求分类体系的确定性分类器。

    LLM 只负责给出 category_candidate_keywords，最终分类由这套 taxonomy 规则完成，
    这样后续新增产品类时可以稳定扩展，不依赖模型自由发挥分类名。
    """

    def __init__(self, category_rules: Iterable[DemandCategoryRule] = DEMAND_CATEGORY_RULES):
        self.category_rules = tuple(category_rules)
        self.rules_by_id = {rule.category_id: rule for rule in self.category_rules}

    def classify(self, *, demand: CustomerDemand, raw_text: str = "") -> DemandCategoryDecision:
        demand_text = self._build_demand_text(demand, raw_text=raw_text)
        matches: list[DemandCategoryMatch] = []

        for rule in self.category_rules:
            matched_keywords = _matched_keywords(demand_text, rule.demand_keywords)
            if not matched_keywords:
                continue

            # LLM/规则抽出的候选关键词命中，说明客户需求明确指向该类；按命中数量给基础分。
            score = 8 + 6 * len(matched_keywords)
            score += self._scenario_boost(demand=demand, category_id=rule.category_id)
            matches.append(
                DemandCategoryMatch(
                    category_id=rule.category_id,
                    category_name=rule.category_name,
                    score=score,
                    matched_keywords=matched_keywords,
                    reason=f"命中需求分类关键词：{', '.join(matched_keywords)}",
                )
            )

        # “办理流程/材料/变更/拆机”等诉求不是新销售推荐，优先进入第 13 类。
        # 这里做成通用流程词优先级，不绑定具体运营商或产品名称，避免后续新增产品时反复补规则。
        matches = self._apply_priority_overrides(matches=matches, demand_text=demand_text)
        matches.sort(key=lambda item: item.score, reverse=True)
        if not matches:
            return DemandCategoryDecision(
                primary_category_id="",
                primary_category_name="",
                category_matches=[],
                recommendation_mode="clarify",
                confidence=0.0,
                clarify_questions=self._build_generic_clarify_questions(demand),
                reason="未命中 13 类产品需求分类关键词，需要先澄清客户需求。",
            )

        primary = matches[0]
        rule = self.rules_by_id.get(primary.category_id) or DEMAND_CATEGORY_RULES_BY_ID[primary.category_id]
        clarify_questions = self._build_clarify_questions(demand=demand, rule=rule)
        confidence = min(0.95, 0.45 + primary.score / 60)
        return DemandCategoryDecision(
            primary_category_id=primary.category_id,
            primary_category_name=primary.category_name,
            category_matches=matches[:3],
            recommendation_mode=rule.recommendation_mode,
            confidence=round(confidence, 2),
            clarify_questions=clarify_questions,
            notes=list(rule.notes),
            reason=primary.reason,
        )

    def _build_demand_text(self, demand: CustomerDemand, *, raw_text: str = "") -> str:
        values = [
            raw_text,
            demand.access_source or "",
            demand.target_region or "",
            demand.duration or "",
            demand.scenario_type.value,
            *demand.raw_keywords,
            *demand.category_candidate_keywords,
            "固定IP" if demand.requires_fixed_ip else "",
            f"{demand.bandwidth_est_mbps}M" if demand.bandwidth_est_mbps else "",
        ]
        return " ".join(value for value in values if value)

    def _build_clarify_questions(self, *, demand: CustomerDemand, rule: DemandCategoryRule) -> list[str]:
        questions = list(rule.clarify_questions)

        # 缺失字段仍然提示销售追问，但不阻断产品召回。
        generic = self._build_generic_clarify_questions(demand)
        for question in generic:
            if question not in questions:
                questions.append(question)
        return questions

    def _build_generic_clarify_questions(self, demand: CustomerDemand) -> list[str]:
        questions_by_field = {
            "access_source": "客户从哪里访问或使用业务？例如上海办公室、门店、总部或分公司。",
            "target_region": "客户要访问哪里或办理什么业务？例如国内总部、海外 SaaS、美国服务器、固定电话或 IDC。",
            "user_count": "预计多少人、多少终端、多少号码或多少坐席使用？",
            "duration": "客户希望试用、月付、年付还是签约几年？",
            "budget": "客户大致预算是多少？",
        }
        questions = [questions_by_field[field] for field in demand.missing_fields if field in questions_by_field]
        return questions or ["当前需求信息不足，需要补充客户场景、规模、周期和预算。"]

    def _scenario_boost(self, *, demand: CustomerDemand, category_id: str) -> float:
        """用早期兼容字段做轻量加权，避免泛化关键词压过强场景关键词。"""

        if demand.scenario_type.value == "overseas_access" and category_id == "4":
            return 8
        if demand.scenario_type.value == "domestic_networking" and category_id == "3":
            return 8
        if demand.requires_fixed_ip and category_id == "2":
            return 8
        return 0

    def _apply_priority_overrides(
        self,
        *,
        matches: list[DemandCategoryMatch],
        demand_text: str,
    ) -> list[DemandCategoryMatch]:
        process_keywords = _matched_keywords(demand_text, SERVICE_PROCESS_STRONG_KEYWORDS)
        if not process_keywords:
            return matches

        category_id = "13"
        rule = self.rules_by_id.get(category_id) or DEMAND_CATEGORY_RULES_BY_ID[category_id]
        process_score = 80 + 4 * len(process_keywords)
        reason = f"命中办理流程/材料类强关键词：{', '.join(process_keywords)}"

        updated: list[DemandCategoryMatch] = []
        has_process_match = False
        for match in matches:
            if match.category_id != category_id:
                updated.append(match)
                continue

            has_process_match = True
            merged_keywords = _dedupe([*match.matched_keywords, *process_keywords])
            updated.append(
                DemandCategoryMatch(
                    category_id=match.category_id,
                    category_name=match.category_name,
                    score=max(match.score, process_score),
                    matched_keywords=merged_keywords,
                    reason=reason,
                )
            )

        if not has_process_match:
            updated.append(
                DemandCategoryMatch(
                    category_id=rule.category_id,
                    category_name=rule.category_name,
                    score=process_score,
                    matched_keywords=process_keywords,
                    reason=reason,
                )
            )

        return updated


SERVICE_PROCESS_STRONG_KEYWORDS = (
    "办理流程",
    "办理手续",
    "办理材料",
    "申请材料",
    "需要哪些材料",
    "材料",
    "手续",
    "流程",
    "担保",
    "授权",
    "盖章",
    "拆机",
    "撤单",
    "续约",
    "变更",
    "移机",
    "过户",
    "更名",
    "退费",
    "停机",
    "销户",
)


def _matched_keywords(text: str, keywords: Iterable[str]) -> list[str]:
    normalized = text.lower()
    matched: list[str] = []
    for keyword in keywords:
        if keyword and keyword.lower() in normalized:
            matched.append(keyword)
    return _dedupe(matched)


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
