from __future__ import annotations

import re
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
    """把产品 JSON 归一化为推荐链路统一使用的 ProductCandidate。

    当前产品数据存在两类结构：
    1. 旧结构：document_info/base_package/optional_packages/fee_and_term_rules 位于顶层。
    2. 新结构：application_form_info + supplementary_info。

    新结构下，supplementary_info 是已经整理过的产品资料，优先级最高；
    application_form_info 更接近原始申请表，只在补充资料缺失时兜底使用。
    """

    def normalize(self, payload: dict[str, Any], *, json_path: str | Path = "") -> ProductCandidate:
        context = _ProductSourceContext(payload)

        packages = self._normalize_packages(context)
        optional_packages = self._normalize_optional_packages(context)
        fee_rules = self._normalize_fee_rules(context)
        constraints = self._normalize_constraints(context)

        title = self._resolve_title(context)
        product_name = _first_text(context.supplementary_product_intro.get("product_name"), context.document_info.get("product_name"), title)
        product_family = _first_text(
            context.supplementary_product_intro.get("product_family"),
            context.document_info.get("product_family"),
            context.category_path,
        )

        candidate = ProductCandidate(
            document_id=_first_text(
                context.document_info.get("document_id"),
                context.extraction_meta.get("document_id"),
                Path(json_path).stem if json_path else "",
            ),
            title=title,
            product_name=product_name,
            product_family=product_family,
            carrier=_first_text(context.document_info.get("carrier"), context.path_meta.carrier),
            region=_first_text(context.document_info.get("region"), context.path_meta.region),
            document_type=_text(context.document_info.get("document_type")),
            document_status=_text(context.document_info.get("document_status") or "active"),
            filename=_first_text(context.document_info.get("filename"), context.path_meta.filename),
            source_path=_first_text(context.document_info.get("source_path"), context.document_info.get("source_file"), context.path_meta.source_path),
            source_file_type=_first_text(context.document_info.get("source_file_type"), context.path_meta.source_file_type),
            published_json_path=path_to_text(json_path) if json_path else "",
            category_path=context.category_path,
            category_levels=context.category_levels,
            packages=packages,
            optional_packages=optional_packages,
            fee_rules=fee_rules,
            constraints=constraints,
            keywords=self._build_keywords(
                context=context,
                title=title,
                product_name=product_name,
                product_family=product_family,
                packages=packages,
                optional_packages=optional_packages,
                fee_rules=fee_rules,
            ),
            raw=payload,
        )
        return candidate

    def _resolve_title(self, context: "_ProductSourceContext") -> str:
        """优先使用补充资料里的产品名，缺失时再从申请表和路径兜底。"""

        return _first_text(
            context.supplementary_product_intro.get("title"),
            context.supplementary_product_intro.get("product_name"),
            context.document_info.get("title"),
            context.document_info.get("product_name"),
            context.path_meta.product_name_from_path,
            context.path_meta.filename,
        )

    def _normalize_packages(self, context: "_ProductSourceContext") -> list[PackageCandidate]:
        """优先从 supplementary_info.pricing_info.base_package_prices 读取基础套餐价格。"""

        supplementary_items = _as_list(context.supplementary_pricing_info.get("base_package_prices"))
        fallback_base_package = _as_dict(context.application_form_info.get("base_package")) or _as_dict(context.payload.get("base_package"))
        fallback_items = _as_list(fallback_base_package.get("packages"))
        fallback_pricing_items = _as_list(context.application_pricing_info.get("base_package_prices"))

        source_items = supplementary_items or fallback_pricing_items or fallback_items
        included_items = _first_non_empty(
            context.supplementary_pricing_info.get("included_items"),
            context.application_pricing_info.get("included_items"),
            fallback_base_package.get("included_items"),
        )

        packages: list[PackageCandidate] = []
        for item in source_items:
            if isinstance(item, dict):
                packages.append(self._normalize_package(item, included_items=included_items))
        return packages

    def _normalize_package(self, item: dict[str, Any], *, included_items: Any) -> PackageCandidate:
        """兼容旧 schema 的 package_name 与新价格表的 name 字段。"""

        raw_text = _text(item.get("raw_text"))
        return PackageCandidate(
            package_name=_first_text(item.get("package_name"), item.get("name")),
            package_code=_text(item.get("package_code")),
            speed=_text(item.get("speed")),
            upstream_speed=_text(item.get("upstream_speed")),
            downstream_speed=_text(item.get("downstream_speed")),
            bandwidth_unit=_text(item.get("bandwidth_unit")),
            has_voice=_optional_bool(item.get("has_voice")),
            price=_optional_float(item.get("price")),
            currency=_text(item.get("currency")),
            billing_period=_first_text(item.get("billing_period"), item.get("period")),
            contract_period=_text(item.get("contract_period")),
            quantity_limit=_text(item.get("quantity_limit")),
            applicable_conditions=_text_list(_first_non_empty(item.get("applicable_conditions"), item.get("conditions"))),
            included_items=[entry for entry in _as_list(included_items) if isinstance(entry, dict)],
            source_evidence=_first_text(item.get("source_evidence"), raw_text, item.get("description")),
            source_location=_as_dict(item.get("source_location")),
            confidence=_confidence(item.get("confidence"), default=0.8 if raw_text else 0.0),
            raw=item,
        )

    def _normalize_optional_packages(self, context: "_ProductSourceContext") -> list[OptionalPackageCandidate]:
        """优先读取补充资费中的 addon_prices，缺失时兼容旧 optional_packages。"""

        source_items = (
            _as_list(context.supplementary_pricing_info.get("addon_prices"))
            or _as_list(context.application_pricing_info.get("addon_prices"))
            or _as_list(context.payload.get("optional_packages"))
        )

        optional_packages: list[OptionalPackageCandidate] = []
        for item in source_items:
            if isinstance(item, dict):
                optional_packages.append(self._normalize_optional_package(item))
        return optional_packages

    def _normalize_optional_package(self, item: dict[str, Any]) -> OptionalPackageCandidate:
        raw_text = _text(item.get("raw_text"))
        amount = item.get("amount")
        unit = _text(item.get("unit"))
        fee_summary = _first_text(
            item.get("fee_summary"),
            f"{amount}{unit}" if amount not in (None, "") and unit else "",
            item.get("description"),
        )
        return OptionalPackageCandidate(
            name=_first_text(item.get("name"), item.get("package_name")),
            package_type=_first_text(item.get("package_type"), item.get("category")),
            category=_text(item.get("category")),
            description=_text(item.get("description")),
            fee_summary=fee_summary,
            price_items=[entry for entry in _as_list(item.get("price_items")) if isinstance(entry, dict)],
            options=_as_list(item.get("options")),
            required_with=_text_list(item.get("required_with")),
            incompatible_with=_text_list(item.get("incompatible_with")),
            applicable_conditions=_text_list(_first_non_empty(item.get("applicable_conditions"), item.get("conditions"))),
            source_evidence=_first_text(item.get("source_evidence"), raw_text),
            source_location=_as_dict(item.get("source_location")),
            confidence=_confidence(item.get("confidence"), default=0.8 if raw_text else 0.0),
            raw=item,
        )

    def _normalize_fee_rules(self, context: "_ProductSourceContext") -> list[PriceRuleCandidate]:
        """汇总一次性费用、规则、折扣政策等费用事实。"""

        pricing_info = context.supplementary_pricing_info if context.has_supplementary_pricing else context.application_pricing_info
        old_fee_rules = _as_list(context.payload.get("fee_and_term_rules"))
        source_groups = [
            ("one_time_fee", _as_list(pricing_info.get("one_time_fees"))),
            ("fee_rule", _as_list(pricing_info.get("fee_and_term_rules"))),
            ("discount_policy", _as_list(pricing_info.get("discount_policy"))),
            ("fee_rule", old_fee_rules),
        ]

        fee_rules: list[PriceRuleCandidate] = []
        for default_rule_type, items in source_groups:
            for item in items:
                if isinstance(item, dict):
                    fee_rules.append(self._normalize_fee_rule(item, default_rule_type=default_rule_type))
        return fee_rules

    def _normalize_fee_rule(self, item: dict[str, Any], *, default_rule_type: str = "") -> PriceRuleCandidate:
        raw_text = _text(item.get("raw_text"))
        return PriceRuleCandidate(
            rule_type=_first_text(item.get("rule_type"), item.get("category"), default_rule_type),
            name=_text(item.get("name")),
            description=_first_text(item.get("description"), item.get("formula")),
            amount=_optional_float(_first_non_empty(item.get("amount"), item.get("discounted_price"), item.get("standard_price"))),
            currency=_text(item.get("currency")),
            billing_period=_first_text(item.get("billing_period"), item.get("period")),
            contract_period=_text(item.get("contract_period")),
            conditions=_text_list(item.get("conditions")),
            applies_to=_text_list(_first_non_empty(item.get("applies_to"), item.get("applicable_to"))),
            source_evidence=_first_text(item.get("source_evidence"), raw_text),
            source_location=_as_dict(item.get("source_location")),
            confidence=_confidence(item.get("confidence"), default=0.8 if raw_text else 0.0),
            raw=item,
        )

    def _normalize_constraints(self, context: "_ProductSourceContext") -> list[ConstraintCandidate]:
        constraints: list[ConstraintCandidate] = []

        source_payload = context.application_form_info if context.is_new_shape else context.payload
        for item in _as_list(source_payload.get("eligibility_and_constraints")):
            if isinstance(item, dict):
                constraints.append(self._normalize_eligibility_constraint(item))

        for item in _as_list(source_payload.get("agreement_rules")):
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
        context: "_ProductSourceContext",
        title: str,
        product_name: str,
        product_family: str,
        packages: list[PackageCandidate],
        optional_packages: list[OptionalPackageCandidate],
        fee_rules: list[PriceRuleCandidate],
    ) -> list[str]:
        """生成召回关键词，覆盖产品名、路径分类、补充关键词和套餐/资费名称。"""

        values: list[str] = [
            title,
            product_name,
            product_family,
            context.path_meta.carrier,
            context.path_meta.region,
            context.path_meta.filename,
            context.category_path,
            *context.category_levels,
            *_split_keywords(context.supplementary_keywords.get("raw_keywords")),
            _text(context.supplementary_product_intro.get("full_description")),
            _text(context.supplementary_product_intro.get("application_scenarios")),
        ]
        values.extend(package.package_name for package in packages)
        values.extend(package.speed for package in packages)
        values.extend(optional.name for optional in optional_packages)
        values.extend(optional.category for optional in optional_packages)
        values.extend(rule.name for rule in fee_rules)
        values.extend(rule.description for rule in fee_rules)

        seen: set[str] = set()
        keywords: list[str] = []
        for value in values:
            normalized = value.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                keywords.append(normalized)
        return keywords


class _ProductSourceContext:
    """封装新旧 JSON 结构的读取入口，避免 normalize() 里到处判断路径。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.application_form_info = _as_dict(payload.get("application_form_info"))
        self.supplementary_info = _as_dict(payload.get("supplementary_info"))
        self.extraction_meta = _as_dict(payload.get("extraction_meta"))
        self.is_new_shape = bool(self.application_form_info or self.supplementary_info)

        self.document_info = self._resolve_document_info()
        self.supplementary_product_intro = _as_dict(self.supplementary_info.get("product_intro"))
        self.supplementary_keywords = _as_dict(self.supplementary_info.get("product_keywords"))
        self.supplementary_pricing_info = _as_dict(self.supplementary_info.get("pricing_info"))
        self.application_pricing_info = _as_dict(self.application_form_info.get("pricing_info"))
        self.has_supplementary_pricing = bool(self.supplementary_pricing_info)

        source_path = _first_text(
            self.document_info.get("source_path"),
            self.document_info.get("source_file"),
            self.supplementary_pricing_info.get("source_file"),
            self.application_pricing_info.get("source_file"),
        )
        self.path_meta = _PathMeta.from_source_path(source_path)

        raw_category = _as_dict(self.extraction_meta.get("raw_category"))
        self.category_levels = _text_list(raw_category.get("category_levels")) or self.path_meta.category_levels
        self.category_path = _first_text(raw_category.get("category_path"), "/".join(self.category_levels))

    def _resolve_document_info(self) -> dict[str, Any]:
        if self.is_new_shape:
            return _as_dict(self.application_form_info.get("document_info"))
        return _as_dict(self.payload.get("document_info"))


class _PathMeta:
    """从原始文件路径中提取分类、运营商、文件名等推荐侧需要的元数据。"""

    def __init__(
        self,
        *,
        source_path: str = "",
        category_levels: list[str] | None = None,
        carrier: str = "",
        region: str = "",
        filename: str = "",
        source_file_type: str = "",
        product_name_from_path: str = "",
    ) -> None:
        self.source_path = source_path
        self.category_levels = category_levels or []
        self.carrier = carrier
        self.region = region
        self.filename = filename
        self.source_file_type = source_file_type
        self.product_name_from_path = product_name_from_path

    @classmethod
    def from_source_path(cls, source_path: str) -> "_PathMeta":
        text = _text(source_path)
        if not text:
            return cls()

        path = Path(text)
        parts = [part for part in path.parts if part and part not in {path.anchor}]
        category_levels = _extract_category_levels(parts)
        filename = path.name if path.suffix else ""
        source_file_type = path.suffix.lstrip(".").lower()
        product_name_from_path = _first_text(
            category_levels[-1] if category_levels else "",
            path.stem if path.suffix else path.name,
        )
        return cls(
            source_path=text,
            category_levels=category_levels,
            carrier=_extract_carrier(parts),
            region=_extract_region(parts),
            filename=filename,
            source_file_type=source_file_type,
            product_name_from_path=product_name_from_path,
        )


def _extract_category_levels(parts: list[str]) -> list[str]:
    """从类似 E:/GitHub/产品/组网/电信/本地国内MPLS-VPN/xxx.docx 中提取业务分类路径。"""

    # “产品/产品数据”比 data/raw/published 更接近真实业务分类，优先从这里截取。
    for marker in ("产品", "产品数据", "raw", "published", "data"):
        if marker in parts:
            index = len(parts) - 1 - list(reversed(parts)).index(marker)
            if index + 1 < len(parts):
                levels = parts[index + 1 :]
                if levels and "." in levels[-1]:
                    levels = levels[:-1]
                return [level for level in levels if level]
    return []


def _extract_carrier(parts: list[str]) -> str:
    joined = " ".join(parts)
    if "电信" in joined:
        return "电信"
    if "联通" in joined:
        return "联通"
    if "移动" in joined:
        return "移动"
    return ""


def _extract_region(parts: list[str]) -> str:
    for part in parts:
        if part in {"上海", "北京", "广东", "江苏", "浙江"}:
            return part
    return ""


def _split_keywords(value: Any) -> list[str]:
    text = _text(value)
    if not text:
        return []
    return [item.strip() for item in re.split(r"[\s,，、;；|/]+", text) if item.strip()]


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


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


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


def _confidence(value: Any, *, default: float = 0.0) -> float:
    number = _optional_float(value)
    if number is None:
        number = default
    return max(0.0, min(1.0, number))
