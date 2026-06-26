from __future__ import annotations

from collections.abc import Iterable

from agent.sales_recommendation_agent.intent_parser.models import CustomerDemand, DemandCategoryDecision
from agent.sales_recommendation_agent.product_repository.models import (
    ConstraintCandidate,
    OptionalPackageCandidate,
    PackageCandidate,
    PriceRuleCandidate,
    ProductCandidate,
)
from agent.sales_recommendation_agent.recommender.models import (
    ComparedProduct,
    ComparisonDimension,
    ComparisonResult,
    ConstraintComparisonSummary,
    FeeComparisonSummary,
    MaterialComparisonSummary,
    OptionalPackageComparisonSummary,
    PackageComparisonSummary,
    CandidateScoreResult,
    ScoredCandidate,
)


class CandidateComparator:
    """候选产品结构化对比器。

    Comparator 不重新排序、不调用大模型、不做报价计算，只把 Scorer Top N 结果整理成对比表。
    """

    def compare(
        self,
        *,
        demand: CustomerDemand,
        category_decision: DemandCategoryDecision,
        score_result: CandidateScoreResult,
        top_n: int = 3,
    ) -> ComparisonResult:
        scored_items = score_result.scored_candidates[:top_n]
        compared_products = [
            self._compare_one(rank=index, scored_candidate=item)
            for index, item in enumerate(scored_items, start=1)
        ]

        return ComparisonResult(
            primary_category_id=score_result.primary_category_id,
            primary_category_name=score_result.primary_category_name,
            recommendation_mode=score_result.recommendation_mode,
            compared_count=len(compared_products),
            products=compared_products,
            comparison_dimensions=self._build_dimensions(
                compared_products=compared_products,
                demand=demand,
                category_decision=category_decision,
            ),
            global_questions=score_result.clarify_questions,
            global_warnings=score_result.global_warnings,
        )

    def _compare_one(self, *, rank: int, scored_candidate: ScoredCandidate) -> ComparedProduct:
        filtered = scored_candidate.filtered_candidate
        candidate = filtered.candidate
        product = candidate.product

        risk_warnings = [
            reason.message
            for reason in filtered.filter_reasons
            if reason.severity in {"warning", "error"}
        ]
        # risk_penalties 是 Scorer 对 filter_reasons 的扣分表达，Comparator 只展示业务风险，
        # 避免同一个问题在对比表中重复出现两次。

        missing_info = self._build_missing_info(product)

        return ComparedProduct(
            rank=rank,
            document_id=product.document_id,
            product_name=display_product_name(product),
            product_family=product.product_family,
            carrier=product.carrier,
            region=product.region,
            document_type=product.document_type,
            category_path=product.category_path,
            final_score=scored_candidate.final_score,
            retrieval_score=candidate.retrieval_score,
            recommendation_status="candidate",
            matched_strengths=self._build_strengths(scored_candidate),
            risk_warnings=dedupe(risk_warnings),
            missing_info=missing_info,
            package_summary=build_package_summary(product.packages),
            fee_summary=build_fee_summary(product.fee_rules, product.packages),
            optional_package_summary=build_optional_package_summary(product.optional_packages),
            constraint_summary=build_constraint_summary(product.constraints),
            material_summary=build_material_summary(product),
            source={
                "document_id": product.document_id,
                "published_json_path": product.published_json_path,
                "source_path": product.source_path,
                "category_path": product.category_path,
            },
        )

    def _build_strengths(self, scored_candidate: ScoredCandidate) -> list[str]:
        """把打分原因转成更适合展示的优势摘要。"""

        strengths: list[str] = []
        for reason in scored_candidate.score_reasons:
            if reason.code == "base_retrieval_score":
                continue
            text = reason.message
            if reason.evidence:
                text = f"{text}（证据：{reason.evidence}）"
            strengths.append(text)
        return dedupe(strengths)

    def _build_missing_info(self, product: ProductCandidate) -> list[str]:
        """记录当前对比无法覆盖的关键信息，避免前端或 LLM 误以为没有要求。"""

        missing: list[str] = []
        if not product.packages:
            missing.append("缺少基础套餐信息")
        if not product.fee_rules and not any(package.price is not None for package in product.packages):
            missing.append("缺少费用规则或套餐价格")
        if "application_materials" not in product.raw:
            missing.append("application_materials 尚未进入 ProductCandidate，暂无法对比办理材料")
        return missing

    def _build_dimensions(
        self,
        *,
        compared_products: list[ComparedProduct],
        demand: CustomerDemand,
        category_decision: DemandCategoryDecision,
    ) -> list[ComparisonDimension]:
        """生成跨产品对比摘要，给业务前端和后续 LLM Explainer 使用。"""

        if not compared_products:
            return [
                ComparisonDimension(
                    dimension="候选",
                    summary="当前没有可对比的候选产品，需要先补充客户需求或产品数据。",
                )
            ]

        dimensions = [
            self._summarize_package_dimension(compared_products),
            self._summarize_fee_dimension(compared_products, demand),
            self._summarize_optional_package_dimension(compared_products),
            self._summarize_risk_dimension(compared_products),
            self._summarize_material_dimension(compared_products),
        ]

        return dimensions

    def _summarize_package_dimension(self, products: list[ComparedProduct]) -> ComparisonDimension:
        with_packages = [item.product_name for item in products if item.package_summary.package_count > 0]
        if not with_packages:
            summary = "当前 Top 候选均缺少基础套餐摘要，套餐能力需要人工补充确认。"
        else:
            summary = "有基础套餐摘要的候选：" + "、".join(with_packages[:5])
        return ComparisonDimension(dimension="套餐能力", summary=summary)

    def _summarize_fee_dimension(
        self,
        products: list[ComparedProduct],
        demand: CustomerDemand,
    ) -> ComparisonDimension:
        with_fee = [item.product_name for item in products if has_fee_evidence(item)]
        without_fee = [item.product_name for item in products if not has_fee_evidence(item)]
        parts: list[str] = []
        if with_fee:
            parts.append("有费用证据的候选：" + "、".join(with_fee[:5]))
        if without_fee:
            parts.append("缺少费用证据的候选：" + "、".join(without_fee[:5]))
        if demand.budget_amount is not None and without_fee:
            parts.append("客户已给出预算，缺少费用证据的候选不适合直接报价。")
        return ComparisonDimension(dimension="费用", summary="；".join(parts) if parts else "当前候选费用信息不足。")

    def _summarize_optional_package_dimension(self, products: list[ComparedProduct]) -> ComparisonDimension:
        with_optional = [
            item.product_name
            for item in products
            if item.optional_package_summary.optional_package_count > 0
        ]
        if with_optional:
            summary = "包含可选包/权益包的候选：" + "、".join(with_optional[:5])
        else:
            summary = "当前 Top 候选未整理出可选包/权益包摘要。"
        return ComparisonDimension(dimension="可选包", summary=summary)

    def _summarize_risk_dimension(self, products: list[ComparedProduct]) -> ComparisonDimension:
        risky = [item.product_name for item in products if item.risk_warnings or item.constraint_summary.blocks_recommendation]
        if risky:
            summary = "存在风险提醒的候选：" + "、".join(risky[:5])
        else:
            summary = "当前 Top 候选没有 Rule Filter 输出的明确风险提醒。"
        return ComparisonDimension(dimension="风险", summary=summary)

    def _summarize_material_dimension(self, products: list[ComparedProduct]) -> ComparisonDimension:
        if any(item.material_summary.available for item in products):
            summary = "部分候选已具备办理材料摘要，可用于比较材料复杂度。"
        else:
            summary = "当前 ProductCandidate 尚未映射办理材料，暂无法做材料复杂度对比。"
        return ComparisonDimension(dimension="办理材料", summary=summary)


def build_package_summary(packages: list[PackageCandidate]) -> PackageComparisonSummary:
    """汇总基础套餐能力。"""

    prices = [package.price for package in packages if package.price is not None]
    return PackageComparisonSummary(
        package_count=len(packages),
        package_names=dedupe(package.package_name for package in packages if package.package_name)[:8],
        speeds=dedupe(
            value
            for package in packages
            for value in (package.speed, package.upstream_speed, package.downstream_speed)
            if value
        )[:8],
        price_values=sorted(set(prices)),
        price_range=format_price_range(prices),
        billing_periods=dedupe(package.billing_period for package in packages if package.billing_period),
        contract_periods=dedupe(package.contract_period for package in packages if package.contract_period),
        has_voice_values=dedupe(format_has_voice(package.has_voice) for package in packages if package.has_voice is not None),
    )


def build_fee_summary(
    fee_rules: list[PriceRuleCandidate],
    packages: list[PackageCandidate],
) -> FeeComparisonSummary:
    """汇总费用规则，不做精确报价。"""

    all_fee_text = " ".join(
        value
        for rule in fee_rules
        for value in (rule.rule_type, rule.name, rule.description, rule.source_evidence)
        if value
    )
    package_prices = [package.price for package in packages if package.price is not None]
    rule_amounts = [rule.amount for rule in fee_rules if rule.amount is not None]
    return FeeComparisonSummary(
        fee_rule_count=len(fee_rules),
        has_monthly_fee=contains_any(all_fee_text, ("月费", "月租", "月基本费", "元/月")) or bool(package_prices),
        has_one_time_fee=contains_any(all_fee_text, ("一次性", "手续费", "调测费")),
        has_installation_fee=contains_any(all_fee_text, ("安装", "调测")),
        has_deposit=contains_any(all_fee_text, ("押金", "保证金")),
        has_penalty_rule=contains_any(all_fee_text, ("违约", "提前终止", "退出")),
        amount_values=sorted(set(package_prices + rule_amounts)),
        fee_rule_names=dedupe(rule.name or rule.rule_type for rule in fee_rules if rule.name or rule.rule_type)[:8],
    )


def build_optional_package_summary(optional_packages: list[OptionalPackageCandidate]) -> OptionalPackageComparisonSummary:
    """汇总可选包、权益包和增值包。"""

    paid: list[str] = []
    free: list[str] = []
    for item in optional_packages:
        name = item.name or item.category or item.package_type
        if not name:
            continue
        text = " ".join([item.package_type, item.category, item.fee_summary, item.source_evidence])
        if contains_any(text, ("0元", "免费", "赠送")):
            free.append(name)
        elif item.price_items or contains_any(text, ("元", "收费", "月费", "资费")):
            paid.append(name)

    return OptionalPackageComparisonSummary(
        optional_package_count=len(optional_packages),
        names=dedupe(item.name or item.category or item.package_type for item in optional_packages if item.name or item.category or item.package_type)[:10],
        paid_package_names=dedupe(paid)[:8],
        free_package_names=dedupe(free)[:8],
        categories=dedupe(item.category for item in optional_packages if item.category)[:8],
        has_incompatibility=any(item.incompatible_with for item in optional_packages),
    )


def build_constraint_summary(constraints: list[ConstraintCandidate]) -> ConstraintComparisonSummary:
    """汇总限制和风险规则。"""

    blocking = [item for item in constraints if item.blocks_recommendation]
    important = [
        item.description or item.result or item.condition or item.name
        for item in constraints
        if item.blocks_recommendation or contains_any(
            " ".join([item.constraint_type, item.name, item.description, item.result, item.condition]),
            ("不得", "不能", "禁止", "违约", "终止", "限制", "风险"),
        )
    ]
    return ConstraintComparisonSummary(
        constraint_count=len(constraints),
        blocks_recommendation=bool(blocking),
        important_risks=dedupe(important)[:8],
    )


def build_material_summary(product: ProductCandidate) -> MaterialComparisonSummary:
    """办理材料暂未进入 ProductCandidate，先给出明确缺失说明。"""

    materials = product.raw.get("application_materials")
    if not isinstance(materials, list):
        return MaterialComparisonSummary(
            available=False,
            missing_reason="application_materials 尚未进入 ProductCandidate，暂无法对比办理材料",
        )

    names = [
        item.get("material_name", "")
        for item in materials
        if isinstance(item, dict) and item.get("material_name")
    ]
    return MaterialComparisonSummary(
        available=True,
        material_count=len(materials),
        material_names=dedupe(names)[:10],
    )


def has_fee_evidence(product: ComparedProduct) -> bool:
    """判断对比对象是否有可用于预算判断的费用证据。"""

    return bool(
        product.package_summary.price_values
        or product.fee_summary.amount_values
        or product.fee_summary.fee_rule_count
    )


def display_product_name(product: ProductCandidate) -> str:
    """统一产品展示名，避免前端出现空名称。"""

    return product.product_name or product.title or product.filename or product.document_id


def format_price_range(prices: list[float]) -> str:
    """把套餐价格列表格式化成简单区间。"""

    if not prices:
        return ""
    values = sorted(set(prices))
    if len(values) == 1:
        return f"{values[0]:g}"
    return f"{values[0]:g}-{values[-1]:g}"


def format_has_voice(value: bool | None) -> str:
    if value is True:
        return "带语音"
    if value is False:
        return "不带语音"
    return "未知"


def contains_any(text: str, keywords: Iterable[str]) -> bool:
    normalized = text.lower()
    return any(keyword.lower() in normalized for keyword in keywords if keyword)


def dedupe(values: Iterable[str]) -> list[str]:
    """保持顺序去重，并去掉空字符串。"""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
