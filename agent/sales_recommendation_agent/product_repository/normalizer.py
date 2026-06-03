from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.sales_recommendation_agent.product_repository.models import (
    ConstraintCandidate,
    OptionalPackageCandidate,
    PackageCandidate,
    PriceRuleCandidate,
    ProductCandidate,
    path_to_text,
)


class ProductDocumentNormalizer:
    """把产品文档抽取 JSON 转成推荐侧统一候选对象。"""

    def normalize(self, payload: dict[str, Any], *, json_path: str | Path = "") -> ProductCandidate:
        document_info = _as_dict(payload.get("document_info"))
        extraction_meta = _as_dict(payload.get("extraction_meta"))
        category = _as_dict(extraction_meta.get("raw_category"))
        base_package = _as_dict(payload.get("base_package"))

        packages = [
            self._normalize_package(item, included_items=base_package.get("included_items"))
            for item in _as_list(base_package.get("packages"))
            if isinstance(item, dict)
        ]
        optional_packages = [
            self._normalize_optional_package(item)
            for item in _as_list(payload.get("optional_packages"))
            if isinstance(item, dict)
        ]
        fee_rules = [
            self._normalize_fee_rule(item)
            for item in _as_list(payload.get("fee_and_term_rules"))
            if isinstance(item, dict)
        ]
        constraints = self._normalize_constraints(payload)

        title = _first_text(
            document_info.get("title"),
            document_info.get("product_name"),
            document_info.get("filename"),
        )
        product_name = _first_text(document_info.get("product_name"), title)
        product_family = _first_text(document_info.get("product_family"), category.get("category_path"))

        candidate = ProductCandidate(
            document_id=_text(document_info.get("document_id")),
            title=title,
            product_name=product_name,
            product_family=product_family,
            carrier=_text(document_info.get("carrier")),
            region=_text(document_info.get("region")),
            document_type=_text(document_info.get("document_type")),
            document_status=_text(document_info.get("document_status") or "active"),
            filename=_text(document_info.get("filename")),
            source_path=_text(document_info.get("source_path")),
            source_file_type=_text(document_info.get("source_file_type")),
            published_json_path=path_to_text(json_path) if json_path else "",
            category_path=_text(category.get("category_path")),
            category_levels=_text_list(category.get("category_levels")),
            packages=packages,
            optional_packages=optional_packages,
            fee_rules=fee_rules,
            constraints=constraints,
            keywords=self._build_keywords(
                document_info=document_info,
                category=category,
                packages=packages,
                optional_packages=optional_packages,
            ),
            raw=payload,
        )
        return candidate

    def _normalize_package(
        self,
        item: dict[str, Any],
        *,
        included_items: Any,
    ) -> PackageCandidate:
        return PackageCandidate(
            package_name=_text(item.get("package_name")),
            package_code=_text(item.get("package_code")),
            speed=_text(item.get("speed")),
            upstream_speed=_text(item.get("upstream_speed")),
            downstream_speed=_text(item.get("downstream_speed")),
            bandwidth_unit=_text(item.get("bandwidth_unit")),
            has_voice=_optional_bool(item.get("has_voice")),
            price=_optional_float(item.get("price")),
            currency=_text(item.get("currency")),
            billing_period=_text(item.get("billing_period")),
            contract_period=_text(item.get("contract_period")),
            quantity_limit=_text(item.get("quantity_limit")),
            applicable_conditions=_text_list(item.get("applicable_conditions")),
            # included_items 是基础套餐层字段，推荐时通常要能从每个套餐候选上直接拿到。
            included_items=[entry for entry in _as_list(included_items) if isinstance(entry, dict)],
            source_evidence=_text(item.get("source_evidence")),
            source_location=_as_dict(item.get("source_location")),
            confidence=_confidence(item.get("confidence")),
            raw=item,
        )

    def _normalize_optional_package(self, item: dict[str, Any]) -> OptionalPackageCandidate:
        return OptionalPackageCandidate(
            name=_text(item.get("name")),
            package_type=_text(item.get("package_type")),
            category=_text(item.get("category")),
            description=_text(item.get("description")),
            fee_summary=_text(item.get("fee_summary")),
            price_items=[entry for entry in _as_list(item.get("price_items")) if isinstance(entry, dict)],
            options=_as_list(item.get("options")),
            required_with=_text_list(item.get("required_with")),
            incompatible_with=_text_list(item.get("incompatible_with")),
            applicable_conditions=_text_list(item.get("applicable_conditions")),
            source_evidence=_text(item.get("source_evidence")),
            source_location=_as_dict(item.get("source_location")),
            confidence=_confidence(item.get("confidence")),
            raw=item,
        )

    def _normalize_fee_rule(self, item: dict[str, Any]) -> PriceRuleCandidate:
        return PriceRuleCandidate(
            rule_type=_text(item.get("rule_type")),
            name=_text(item.get("name")),
            description=_text(item.get("description")),
            amount=_optional_float(item.get("amount")),
            currency=_text(item.get("currency")),
            billing_period=_text(item.get("billing_period")),
            contract_period=_text(item.get("contract_period")),
            conditions=_text_list(item.get("conditions")),
            applies_to=_text_list(item.get("applies_to")),
            source_evidence=_text(item.get("source_evidence")),
            source_location=_as_dict(item.get("source_location")),
            confidence=_confidence(item.get("confidence")),
            raw=item,
        )

    def _normalize_constraints(self, payload: dict[str, Any]) -> list[ConstraintCandidate]:
        constraints: list[ConstraintCandidate] = []

        for item in _as_list(payload.get("eligibility_and_constraints")):
            if isinstance(item, dict):
                constraints.append(self._normalize_eligibility_constraint(item))

        for item in _as_list(payload.get("agreement_rules")):
            if isinstance(item, dict):
                constraints.append(self._normalize_agreement_rule(item))

        return constraints

    def _normalize_eligibility_constraint(self, item: dict[str, Any]) -> ConstraintCandidate:
        return ConstraintCandidate(
            constraint_type=_text(item.get("constraint_type")),
            name=_text(item.get("name")),
            description=_text(item.get("description")),
            condition=_text(item.get("condition")),
            result=_text(item.get("result")),
            blocks_recommendation=bool(item.get("blocks_recommendation") is True),
            applies_to=_text_list(item.get("applies_to")),
            rule_source=_text(item.get("rule_source")),
            source_evidence=_text(item.get("source_evidence")),
            source_location=_as_dict(item.get("source_location")),
            confidence=_confidence(item.get("confidence")),
            raw=item,
        )

    def _normalize_agreement_rule(self, item: dict[str, Any]) -> ConstraintCandidate:
        title = _text(item.get("title"))
        description = _text(item.get("description"))
        return ConstraintCandidate(
            constraint_type=_text(item.get("rule_type") or "agreement_rule"),
            name=title,
            description=description,
            condition="；".join(_text_list(item.get("conditions"))),
            result=_text(item.get("consequence")),
            blocks_recommendation=False,
            applies_to=_text_list(item.get("applies_to")),
            rule_source="agreement_rules",
            source_evidence=_text(item.get("source_evidence")),
            source_location=_as_dict(item.get("source_location")),
            confidence=_confidence(item.get("confidence")),
            raw=item,
        )

    def _build_keywords(
        self,
        *,
        document_info: dict[str, Any],
        category: dict[str, Any],
        packages: list[PackageCandidate],
        optional_packages: list[OptionalPackageCandidate],
    ) -> list[str]:
        """生成轻量关键词，供后续候选召回使用。"""

        values: list[str] = [
            _text(document_info.get("title")),
            _text(document_info.get("product_name")),
            _text(document_info.get("product_family")),
            _text(document_info.get("carrier")),
            _text(document_info.get("filename")),
            _text(category.get("category_path")),
            *_text_list(category.get("category_levels")),
        ]
        values.extend(package.package_name for package in packages)
        values.extend(package.speed for package in packages)
        values.extend(optional.name for optional in optional_packages)
        values.extend(optional.category for optional in optional_packages)

        seen: set[str] = set()
        keywords: list[str] = []
        for value in values:
            normalized = value.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                keywords.append(normalized)
        return keywords


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _text_list(value: Any) -> list[str]:
    result: list[str] = []
    for item in _as_list(value):
        text = _text(item)
        if text:
            result.append(text)
    return result


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    normalized = _text(value).lower()
    if normalized in {"true", "1", "yes", "y", "是", "带", "带语音"}:
        return True
    if normalized in {"false", "0", "no", "n", "否", "不带", "不带语音"}:
        return False
    return None


def _confidence(value: Any) -> float:
    number = _optional_float(value)
    if number is None:
        return 0.0
    return max(0.0, min(1.0, number))
