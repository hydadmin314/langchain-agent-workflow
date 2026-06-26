from __future__ import annotations

import re
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
    """基于上网/组网六类销售套餐分类体系的确定性分类器。

    LLM 只负责抽取需求事实和正/负向信号，最终分类由这套 taxonomy 规则完成，
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
                    primary_domain=rule.primary_domain,
                    score=score,
                    matched_keywords=matched_keywords,
                    reason=f"命中需求分类关键词：{', '.join(matched_keywords)}",
                )
            )

        matches = self._apply_priority_corrections(
            matches=matches,
            demand=demand,
            raw_text=raw_text,
            demand_text=demand_text,
        )
        matches.sort(key=lambda item: item.score, reverse=True)
        if not matches:
            return DemandCategoryDecision(
                primary_category_id="",
                primary_category_name="",
                primary_domain="",
                category_matches=[],
                recommendation_mode="clarify",
                confidence=0.0,
                clarify_questions=self._build_generic_clarify_questions(demand),
                reason="未命中上网/组网六类销售套餐分类关键词，需要先澄清客户需求。",
            )

        primary = matches[0]
        rule = self.rules_by_id.get(primary.category_id) or DEMAND_CATEGORY_RULES_BY_ID[primary.category_id]
        clarify_questions = self._build_clarify_questions(demand=demand, rule=rule)
        confidence = min(0.95, 0.45 + primary.score / 60)
        return DemandCategoryDecision(
            primary_category_id=primary.category_id,
            primary_category_name=primary.category_name,
            primary_domain=rule.primary_domain,
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
            demand.primary_goal,
            demand.usage_scene,
            demand.business_action,
            demand.site_count,
            demand.user_count,
            demand.bandwidth_need,
            demand.fixed_ip_count or "",
            demand.concurrent_calls or "",
            demand.overseas_target,
            demand.industry_scene,
            demand.budget,
            demand.reliability_level,
            demand.carrier_preference,
            demand.region,
            demand.customer_type,
            demand.scenario_type.value,
            *demand.raw_keywords,
            *demand.positive_signals,
            *demand.negative_signals,
            "固定IP" if demand.requires_fixed_ip else "",
            f"{demand.bandwidth_est_mbps}M" if demand.bandwidth_est_mbps else "",
        ]
        return " ".join(value for value in values if value)

    def _apply_priority_corrections(
        self,
        *,
        matches: list[DemandCategoryMatch],
        demand: CustomerDemand,
        raw_text: str,
        demand_text: str,
    ) -> list[DemandCategoryMatch]:
        """分类器内的高置信纠偏。

        这些逻辑属于“理解用户需求”，因此统一放在 intent_parser 中，
        不再散落到推荐总 workflow 里做补丁式修正。
        """

        if demand.fixed_ip_required is False or has_negated_fixed_ip_signal(demand_text):
            return promote_category_match(
                matches=matches,
                rule=self.rules_by_id[select_non_fixed_internet_category_id(raw_text or demand_text)],
                score=120,
                reason="用户明确表示不需要固定公网 IP，纠正为非固定 IP 上网类主分类。",
                matched_keywords=["不需要固定IP"],
            )

        if has_domestic_site_access_signal(demand_text):
            return promote_category_match(
                matches=matches,
                rule=self.rules_by_id[select_network_category_id(raw_text or demand_text)],
                score=120,
                reason="命中国内异地/总部与分支互联场景，提升为组网类主分类。",
                matched_keywords=["国内组网"],
            )

        if demand.requires_fixed_ip or has_positive_fixed_ip_signal(demand_text):
            return promote_category_match(
                matches=matches,
                rule=self.rules_by_id["internet_fixed_ip"],
                score=120,
                reason="命中固定公网 IP 或服务器对外访问场景，提升为上网固定 IP 类主分类。",
                matched_keywords=["固定公网IP"],
            )

        return matches

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
            "usage_scene": "客户从哪里访问或使用业务？例如上海办公室、门店、总部或分公司。",
            "primary_goal": "客户最核心想解决什么问题？例如沿街店铺上网、办公上网、固定IP、点对点互联或智能组网。",
            "user_count": "预计多少人、多少终端、多少号码或多少坐席使用？",
            "business_action": "客户是新装、变更、移机、过户、改套餐、拆机、撤单还是续约？",
            "budget": "客户大致预算是多少？",
        }
        questions = [questions_by_field[field] for field in demand.missing_fields if field in questions_by_field]
        return questions or ["当前需求信息不足，需要补充客户场景、规模、周期和预算。"]

    def _scenario_boost(self, *, demand: CustomerDemand, category_id: str) -> float:
        """用早期兼容字段做轻量加权，避免泛化关键词压过强场景关键词。"""

        if demand.scenario_type.value == "domestic_networking" and category_id in {
            "network_point_to_point",
            "network_point_to_multipoint",
            "network_smart",
        }:
            return 8
        if demand.requires_fixed_ip and category_id == "internet_fixed_ip":
            return 8
        return 0


def _matched_keywords(text: str, keywords: Iterable[str]) -> list[str]:
    normalized = text.lower()
    matched: list[str] = []
    for keyword in keywords:
        if keyword and keyword.lower() in normalized:
            matched.append(keyword)
    return _dedupe(matched)


def promote_category_match(
    *,
    matches: list[DemandCategoryMatch],
    rule: DemandCategoryRule,
    score: float,
    reason: str,
    matched_keywords: list[str],
) -> list[DemandCategoryMatch]:
    """把指定分类提升到候选首位，保留其它候选作为辅助分类。"""

    updated: list[DemandCategoryMatch] = []
    found = False
    for match in matches:
        if match.category_id == rule.category_id:
            found = True
            updated.append(
                DemandCategoryMatch(
                    category_id=match.category_id,
                    category_name=match.category_name,
                    primary_domain=match.primary_domain,
                    score=max(match.score, score),
                    matched_keywords=_dedupe([*match.matched_keywords, *matched_keywords]),
                    reason=reason,
                )
            )
        else:
            updated.append(match)

    if not found:
        updated.append(
            DemandCategoryMatch(
                category_id=rule.category_id,
                category_name=rule.category_name,
                primary_domain=rule.primary_domain,
                score=score,
                matched_keywords=matched_keywords,
                reason=reason,
            )
        )
    updated.sort(key=lambda item: item.score, reverse=True)
    return updated[:3]


def has_negated_fixed_ip_signal(text: str) -> bool:
    """识别固定 IP 否定表达。"""

    return bool(re.search(r"(不需要|不要|无须|无需|不用|不办).{0,8}(固定\s*IP|公网\s*IP|公网地址)", text, re.I))


def has_positive_fixed_ip_signal(text: str) -> bool:
    """识别固定 IP 正向表达，否定表达优先级由上游先处理。"""

    return bool(re.search(r"(固定\s*IP|公网\s*IP|公网地址|固定公网|服务器对外)", text, re.I))


def has_domestic_site_access_signal(text: str) -> bool:
    """识别“总部访问分支/子公司”这类国内组网表达。

    这里不枚举城市，只看组织关系和访问/互联动作。
    """

    return bool(
        re.search(r"(总部|总公司|办公室).{0,20}(访问|连接|连通|互联|组网).{0,20}(分支|分公司|子公司|门店)", text)
        or re.search(r"(分支|分公司|子公司|门店).{0,20}(访问|连接|连通|互联|组网).{0,20}(总部|总公司|办公室)", text)
    )


def select_network_category_id(text: str) -> str:
    """在三类组网子类中做高置信分流。"""

    if re.search(r"(SD-?WAN|智能组网|设备组网|已有宽带|互联网组网|VPN|虚拟专网)", text, re.I):
        return "network_smart"
    if re.search(r"(点对多|多点|多个分支|多分支|多门店|多站点|星型|爪形|总部\+?多个)", text, re.I):
        return "network_point_to_multipoint"
    return "network_point_to_point"


def select_non_fixed_internet_category_id(text: str) -> str:
    """固定 IP 被明确否定时，在上网类里选择更合适的非固定 IP 分类。"""

    if re.search(r"(沿街|店铺|商铺|门店|餐饮|零售|收银|开店|旺铺)", text):
        return "internet_store_street"
    return "internet_office_dynamic_ip"


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
