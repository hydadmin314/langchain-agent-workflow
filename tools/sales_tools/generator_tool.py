import json
import re
from datetime import date
from typing import Any

from langchain.tools import tool

from tools.sales_tools.calc_tool import build_calculator_response
from tools.sales_tools.compare_tool import build_comparison_response
from tools.sales_tools.product_tool import display_text
from tools.sales_tools.rag_tool import build_rag_context_response
from tools.sales_tools.requirement_parser import parse_requirement_payload
from tools.sales_tools.rule_engine import classify_requirement_payload


DERIVED_QUERY_MARKERS = (
    "候选方案",
    "推荐核算方案",
    "低带宽备选方案",
    "扩容备选方案",
    "候选明细",
    "标价口径",
    "pricing_input",
)


def _number(value: Any, default: float = 0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_money(value: Any) -> str:
    if value is None:
        return "-"
    amount = _number(value)
    if amount.is_integer():
        return f"{int(amount)} 元"
    return f"{amount:.2f} 元"


def _join(values: list[Any] | None, default: str = "-") -> str:
    cleaned = [display_text(value, "").strip() for value in values or [] if display_text(value, "").strip()]
    return "、".join(cleaned) if cleaned else default


def _source_label(source: dict[str, Any] | None) -> str:
    if not source:
        return ""
    parts = []
    if source.get("product_id"):
        parts.append(str(source["product_id"]))
    if source.get("source_product_no") is not None:
        parts.append(f"source_product_no {source['source_product_no']}")
    if source.get("spec_text"):
        parts.append(display_text(source["spec_text"]))
    if source.get("sale_price") is not None:
        parts.append(_format_money(source["sale_price"]))
    return "，".join(parts)


def _looks_like_derived_tool_output(query: str) -> bool:
    if not query:
        return False
    has_marker = any(marker in query for marker in DERIVED_QUERY_MARKERS)
    has_product_id = bool(re.search(r"(intl_route_optimization|sd_wan|isp)-\d+", query))
    return has_marker and has_product_id


def _budget_line(item: dict[str, Any]) -> str:
    if item.get("budget_upper") is None:
        return "未提供预算上限"
    if item.get("budget_fit"):
        return f"预算内，余量 {_format_money(item.get('budget_delta'))}"
    return f"超预算 {_format_money(abs(item.get('budget_delta') or 0))}"


def _tax_text() -> str:
    return "待财务确认"


def _module_accepts_contract_mode(module_name: str | None) -> bool:
    return display_text(module_name, "") == "SD-WAN"


MODULE_SCENARIO_LABELS = {
    "SD-WAN": "组网互联",
    "国际路由优化": "国际访问优化",
    "ISP 专线": "互联网接入",
    "ISP": "互联网接入",
}


def _scenario_summary(
    parsed: dict[str, Any],
    module_name: str | None,
    rule_result: dict[str, Any] | None = None,
) -> str:
    module = display_text(module_name, "")
    if module in MODULE_SCENARIO_LABELS:
        return MODULE_SCENARIO_LABELS[module]

    primary_module = display_text((rule_result or {}).get("primary_module"), "")
    module_by_key = {
        "sd_wan": "组网互联",
        "intl_route_optimization": "国际访问优化",
        "isp_private_line": "互联网接入",
    }
    if primary_module in module_by_key:
        return module_by_key[primary_module]

    return _join(parsed.get("scenarios"))


def _contract_modes_for_module(parsed: dict[str, Any], module_name: str | None) -> list[Any]:
    if not _module_accepts_contract_mode(module_name):
        return []
    return parsed.get("contract_modes") or []


def _requirement_summary(
    parsed: dict[str, Any],
    module_name: str | None,
    rule_result: dict[str, Any] | None = None,
) -> list[str]:
    specs = parsed.get("spec_requirements") or {}
    budget = parsed.get("budget") or {}
    contract_modes = _contract_modes_for_module(parsed, module_name)
    parts = [
        f"- 场景：{_scenario_summary(parsed, module_name, rule_result)}",
        f"- 产品模块：{display_text(module_name, '-')}",
        f"- 计费周期：{_join(parsed.get('billing_cycles'))}",
        f"- 签约方式：{_join(contract_modes)}",
    ]
    if specs.get("user_count"):
        parts.append(f"- 使用人数：{specs['user_count']} 人")
    if specs.get("bandwidth_mbps"):
        parts.append(f"- 明确带宽：{max(specs['bandwidth_mbps'])}M")
    if budget.get("upper_cny") is not None:
        parts.append(f"- 预算：{_format_money(budget['upper_cny'])}以内")
    return parts


def _compare_item_by_id(compare_response: dict[str, Any], product_id: str | None) -> dict[str, Any] | None:
    if not product_id:
        return None
    return next(
        (item for item in compare_response.get("compared_items", []) if item.get("product_id") == product_id),
        None,
    )


def _alternative_pricing_results(calculator: dict[str, Any], recommended: dict[str, Any] | None) -> list[dict[str, Any]]:
    recommended_id = recommended.get("product_id") if recommended else None
    recommended_cycle = recommended.get("billing_cycle") if recommended else None
    alternatives = []
    for item in calculator.get("pricing_results", []):
        if item.get("product_id") == recommended_id and item.get("billing_cycle") == recommended_cycle:
            continue
        alternatives.append(item)
    return alternatives


def _parse_rank_token(value: str | None) -> int | None:
    if not value:
        return None
    token = value.strip()
    if token.isdigit():
        return int(token)
    digits = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    if token in digits:
        return digits[token]
    if len(token) == 2 and token[0] == "十" and token[1] in digits:
        return 10 + digits[token[1]]
    if len(token) == 2 and token[1] == "十" and token[0] in digits:
        return digits[token[0]] * 10
    if len(token) == 3 and token[1] == "十" and token[0] in digits and token[2] in digits:
        return digits[token[0]] * 10 + digits[token[2]]
    return None


def parse_package_selection(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    patterns = (
        r"(?:选择|选|用|按|就用)\s*(?:候选|候补|备选)?\s*(?:里面|里|中的|中)?\s*(?:第)?\s*([一二两三四五六七八九十\d]+)\s*(?:个|项|套)?\s*(?:套餐|方案)",
        r"(?:选择|选|用|按|就用)\s*(?:候选|候补|备选)?\s*(?:里面|里|中的|中)?\s*(?:套餐|方案)\s*([一二两三四五六七八九十\d]+)",
        r"(?:选择|选|用|按|就用)?\s*(?:第)?\s*([一二两三四五六七八九十\d]+)\s*个\s*(?:候选|候补|备选)",
        r"(?:选择|选|用|按|就用)\s*(?:第)?\s*([一二两三四五六七八九十\d]+)\s*(?:个|项|套)?\s*(?:套餐|方案)",
        r"(?:选择|选|用|按|就用)\s*(?:套餐|方案)\s*([一二两三四五六七八九十\d]+)",
        r"第\s*([一二两三四五六七八九十\d]+)\s*(?:个|项|套)?\s*(?:套餐|方案)",
        r"(?:套餐|方案)\s*([一二两三四五六七八九十\d]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        rank = _parse_rank_token(match.group(1))
        if not rank:
            continue
        scope = (
            "alternative"
            if re.search(
                r"(?:第)?\s*[一二两三四五六七八九十\d]+\s*个\s*(?:候选|候补|备选)|(?:候选|候补|备选)(?:里|里面|中的|中)\s*第\s*[一二两三四五六七八九十\d]+\s*个",
                text,
            )
            else "overall"
        )
        return {"rank": rank, "scope": scope, "raw_text": match.group(0)}
    return None


def _ranked_pricing_results(response: dict[str, Any], scope: str = "overall") -> list[dict[str, Any]]:
    recommended = response.get("recommended_pricing")
    alternatives = _alternative_pricing_results(response.get("calculator", {}), recommended)
    if scope == "alternative":
        return alternatives
    return ([recommended] if recommended else []) + alternatives


def apply_package_selection(response: dict[str, Any], selection_text: str | None) -> dict[str, Any]:
    selection = parse_package_selection(selection_text)
    if not selection:
        return response
    ranked = _ranked_pricing_results(response, selection["scope"])
    index = selection["rank"] - 1
    if index < 0 or index >= len(ranked):
        selected_response = dict(response)
        selected_response["selection_error"] = (
            f"未找到{selection['raw_text']}对应的候选套餐；当前可选数量为 {len(ranked)}。"
        )
        return selected_response

    selected = ranked[index]
    selected_response = dict(response)
    selected_response["recommended_pricing"] = selected
    selected_response["recommended_compare"] = _compare_item_by_id(response.get("compare", {}), selected.get("product_id")) or {}
    selected_response["selected_package"] = {
        "rank": selection["rank"],
        "scope": selection["scope"],
        "raw_text": selection["raw_text"],
        "product_id": selected.get("product_id"),
        "billing_cycle": selected.get("billing_cycle"),
    }
    selected_response["message"] = f"已按用户选择的{selection['raw_text']}生成。"
    return selected_response


def build_quote_proposal_response(query: str) -> dict[str, Any]:
    parsed = parse_requirement_payload(query)
    rule_result = classify_requirement_payload(parsed)
    compare_response = build_comparison_response(query)
    calculator_response = build_calculator_response(query)
    rag_response = build_rag_context_response(query)

    pricing_results = calculator_response.get("pricing_results", [])
    recommended_pricing = pricing_results[0] if pricing_results else None
    recommended_compare = _compare_item_by_id(
        compare_response,
        recommended_pricing.get("product_id") if recommended_pricing else None,
    )

    return {
        "raw_query": query,
        "parsed_requirements": parsed,
        "rule_engine": rule_result,
        "compare": compare_response,
        "calculator": calculator_response,
        "rag": rag_response,
        "recommended_pricing": recommended_pricing,
        "recommended_compare": recommended_compare,
        "message": "已生成方案书/报价说明。" if recommended_pricing else "未找到可生成报价说明的计价结果。",
    }


def render_quote_proposal_response(response: dict[str, Any]) -> str:
    parsed = response["parsed_requirements"]
    rule_result = response.get("rule_engine") or {}
    calculator = response["calculator"]
    compare = response["compare"]
    rag = response["rag"]
    recommended = response.get("recommended_pricing")
    recommended_compare = response.get("recommended_compare") or {}

    lines = ["方案书 / 报价说明", response["message"]]
    lines.append("")
    lines.append("一、客户需求摘要")
    lines.extend(_requirement_summary(parsed, calculator.get("module_name"), rule_result))

    if response.get("selection_error"):
        lines.append("")
        lines.append(response["selection_error"])
        return "\n".join(lines)

    if not recommended:
        lines.append("")
        lines.append("当前未生成可报价方案，请补充客户场景、产品线或预算信息后重试。")
        return "\n".join(lines)

    lines.append("")
    lines.append("二、推荐结论")
    lines.append(
        f"1. 推荐方案：{display_text(recommended.get('product_name'), display_text(recommended.get('product_id')))}"
        f"（{recommended.get('product_id')}）"
    )
    if recommended_compare.get("spec_text"):
        lines.append(f"- 规格：{display_text(recommended_compare.get('spec_text'))}")
    lines.append(
        f"- 计费：{display_text(recommended.get('billing_cycle'))}，数量 {recommended.get('quantity')}，"
        f"单价 {_format_money(recommended.get('unit_price'))}/{display_text(recommended.get('unit'))}"
    )
    lines.append(
        f"- 应付合计：{_format_money(recommended.get('payable_total'))}"
        f"（{_budget_line(recommended)}）"
    )
    lines.append(f"- 计价规则：{recommended.get('discount_reason')}")
    if recommended_compare.get("advantages"):
        lines.append("- 推荐依据：" + "；".join(display_text(value) for value in recommended_compare["advantages"]))
    if recommended_compare.get("tradeoffs"):
        lines.append("- 主要取舍：" + "；".join(display_text(value) for value in recommended_compare["tradeoffs"]))

    lines.append("")
    lines.append("三、候补方案报价对比")
    alternatives = _alternative_pricing_results(calculator, recommended)
    if not alternatives:
        lines.append("暂无其他候选方案。")
    for index, item in enumerate(alternatives, start=2):
        compare_item = _compare_item_by_id(compare, item.get("product_id")) or {}
        spec = display_text(compare_item.get("spec_text"), "-")
        lines.append(
            f"{index}. {display_text(item.get('product_name'), display_text(item.get('product_id')))}"
            f"（{item.get('product_id')}）：{spec}，"
            f"{display_text(item.get('billing_cycle'))}，数量 {item.get('quantity')}，"
            f"标价小计 {_format_money(item.get('original_subtotal'))}，"
            f"折扣 {_format_money(item.get('discount_amount'))}，"
            f"初装/调试费 {_format_money(item.get('setup_total'))}，"
            f"应付 {_format_money(item.get('payable_total'))}，{_budget_line(item)}。"
        )

    lines.append("")
    lines.append("四、费用来源与边界")
    price_source = _source_label(recommended.get("price_source"))
    setup_source = _source_label(recommended.get("setup_fee_source"))
    if price_source:
        lines.append(f"- 套餐价来源：产品表 {price_source}")
    if setup_source:
        lines.append(f"- 初装/调试费来源：产品表 {setup_source}")
    elif recommended.get("setup_total"):
        lines.append("- 初装/调试费来源：未携带来源，请回查产品模块输出。")
    lines.append(f"- 折扣/价格规则：{recommended.get('discount_reason')}")
    lines.append("- 技术参数、服务权益和销售话术依据：RAG 层当前未配置知识库，不能补充产品数据和工具结果之外的承诺。")

    lines.append("")
    lines.append("五、需销售确认")
    follow_up = compare.get("follow_up_questions") or parsed.get("follow_up_questions") or []
    for question in follow_up:
        lines.append(f"- {question}")
    unsupported = rag.get("unsupported_claims") or []
    if unsupported:
        lines.append("- SLA、开通周期、免费测试、巡检、赠品、IP 赠送、调试费减免、锁价等：产品数据未配置，需销售确认。")

    return "\n".join(lines)


def _quote_line_items(recommended: dict[str, Any], compare_item: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    spec_text = display_text((compare_item or {}).get("spec_text"), display_text(recommended.get("billing_cycle")))
    items = [
        {
            "product_or_service": display_text(recommended.get("product_name"), display_text(recommended.get("product_id"))),
            "brand": "待填写",
            "spec_model": f"{spec_text}，{display_text(recommended.get('product_id'))}",
            "unit": display_text(recommended.get("unit"), "项"),
            "quantity": recommended.get("quantity"),
            "unit_price": recommended.get("discounted_unit_price"),
            "amount": recommended.get("discounted_subtotal"),
            "note": recommended.get("discount_reason"),
        }
    ]
    if _number(recommended.get("setup_total")) > 0:
        items.append(
            {
                "product_or_service": "初装/调试费",
                "brand": "待填写",
                "spec_model": display_text(recommended.get("product_id")),
                "unit": "项",
                "quantity": recommended.get("quantity"),
                "unit_price": recommended.get("setup_fee"),
                "amount": recommended.get("setup_total"),
                "note": "按产品表初装/调试费来源核算",
            }
        )
    return items


def render_quote_sheet_response(response: dict[str, Any]) -> str:
    parsed = response["parsed_requirements"]
    calculator = response["calculator"]
    rag = response["rag"]
    recommended = response.get("recommended_pricing")
    recommended_compare = response.get("recommended_compare") or {}

    lines = ["报价单模板数据"]
    lines.append("以下字段按 data/raw/报价单模板.xlsx 的结构整理；空缺项请销售填写。")
    lines.append("")
    lines.append("一、报价单基础信息")
    lines.append(f"- 报价单编号：待填写")
    lines.append(f"- 报价日期：{date.today().isoformat()}")
    lines.append("- 报价单位：待填写")
    lines.append("- 客户名称：待填写")
    lines.append("- 报价方联系人 / 固定电话 / 手机 / E-mail：待填写")
    lines.append("- 客户方联系人 / 固定电话 / 手机 / E-mail：待填写")
    lines.append("- 报价方地址：上海市杨浦区国霞路259号绿地新江湾大厦1号楼323室")
    lines.append("- 客户地址：待填写")
    lines.append("- 币种：中国，人民币")

    if response.get("selection_error"):
        lines.append("")
        lines.append(response["selection_error"])
        return "\n".join(lines)

    if not recommended:
        lines.append("")
        lines.append("当前未找到可报价明细，请补充客户场景、产品线或预算信息后重试。")
        return "\n".join(lines)

    lines.append("")
    lines.append("二、报价明细")
    lines.append("| 序号 | 产品或服务 | 品牌 | 规格型号 | 单位 | 数量 | 单价(不含税) | 金额(不含税) | 税率 | 税额 | 价税合计 |")
    lines.append("|---|---|---|---|---|---:|---:|---:|---|---|---|")
    for index, item in enumerate(_quote_line_items(recommended, recommended_compare), start=1):
        lines.append(
            f"| {index} | {item['product_or_service']} | {item['brand']} | {item['spec_model']} | "
            f"{item['unit']} | {item['quantity']} | {_format_money(item['unit_price'])} | "
            f"{_format_money(item['amount'])} | {_tax_text()} | {_tax_text()} | {_tax_text()} |"
        )

    lines.append("")
    lines.append("三、合计")
    lines.append(f"- 金额小计（按产品表价格口径）：{_format_money(recommended.get('payable_total'))}")
    lines.append("- 税率、税额、价税合计：产品数据未配置，需财务确认。")
    if recommended.get("budget_upper") is not None:
        lines.append(f"- 预算匹配：{_budget_line(recommended)}")

    lines.append("")
    lines.append("四、费用来源")
    price_source = _source_label(recommended.get("price_source"))
    setup_source = _source_label(recommended.get("setup_fee_source"))
    if price_source:
        lines.append(f"- 套餐价来源：产品表 {price_source}")
    if setup_source:
        lines.append(f"- 初装/调试费来源：产品表 {setup_source}")
    lines.append(f"- 折扣/价格规则：{recommended.get('discount_reason')}")

    lines.append("")
    lines.append("五、服务条款（模板原文）")
    lines.append("- 此报价单按客户需求报价，有效期为15天；")
    lines.append("- 如客户补充需求重新拟定报价单，此报价单作废；")
    lines.append("- 此报价单内硬件设备从发货之日起质保一年；")
    lines.append("- 客户确认此报价后签订订单或合同，按订单或合同约定方式付款；")
    lines.append("- 客户付款后发货，遇节假日顺延，发票随货提供。")

    if rag.get("unsupported_claims"):
        lines.append("")
        lines.append("六、需销售确认")
        lines.append("- SLA、开通周期、免费测试、巡检、赠品、IP 赠送、调试费减免、锁价等：产品数据未配置，需销售确认。")
        lines.append("- 当前 RAG 知识库未配置，不能补充产品数据和工具结果之外的技术优势或服务承诺。")

    lines.append("")
    lines.append("原始需求备注")
    lines.append(f"- {response['raw_query']}")
    return "\n".join(lines)


def render_recommendation_response(response: dict[str, Any]) -> str:
    parsed = response["parsed_requirements"]
    rule_result = response.get("rule_engine") or {}
    calculator = response["calculator"]
    compare = response["compare"]
    rag = response["rag"]
    recommended = response.get("recommended_pricing")
    recommended_compare = response.get("recommended_compare") or {}

    lines = ["推荐与报价核算结果"]
    lines.append("")
    lines.append("客户需求摘要")
    lines.extend(_requirement_summary(parsed, calculator.get("module_name"), rule_result))

    if response.get("selection_error"):
        lines.append("")
        lines.append(response["selection_error"])
        return "\n".join(lines)

    if not recommended:
        lines.append("")
        lines.append("当前未找到可报价方案，请补充客户场景、产品线或预算信息。")
        return "\n".join(lines)

    lines.append("")
    lines.append("推荐方案")
    lines.append(
        f"1. {display_text(recommended.get('product_name'), display_text(recommended.get('product_id')))}"
        f"（{recommended.get('product_id')}）"
    )
    if recommended_compare.get("spec_text"):
        lines.append(f"- 规格：{display_text(recommended_compare.get('spec_text'))}")
    lines.append(
        f"- 计费：{display_text(recommended.get('billing_cycle'))}，数量 {recommended.get('quantity')}，"
        f"单价 {_format_money(recommended.get('unit_price'))}/{display_text(recommended.get('unit'))}"
    )
    lines.append(f"- 应付合计：{_format_money(recommended.get('payable_total'))}（{_budget_line(recommended)}）")
    lines.append(f"- 计价规则：{recommended.get('discount_reason')}")
    if recommended_compare.get("advantages"):
        lines.append("- 推荐依据：" + "；".join(display_text(value) for value in recommended_compare["advantages"]))
    if recommended_compare.get("tradeoffs"):
        lines.append("- 主要取舍：" + "；".join(display_text(value) for value in recommended_compare["tradeoffs"]))

    lines.append("")
    lines.append("候补方案对比")
    alternatives = _alternative_pricing_results(calculator, recommended)
    if not alternatives:
        lines.append("暂无其他候选方案。")
    for index, item in enumerate(alternatives, start=2):
        compare_item = _compare_item_by_id(compare, item.get("product_id")) or {}
        spec = display_text(compare_item.get("spec_text"), "-")
        lines.append(
            f"{index}. {display_text(item.get('product_name'), display_text(item.get('product_id')))}"
            f"（{item.get('product_id')}）：{spec}，应付 {_format_money(item.get('payable_total'))}，"
            f"{_budget_line(item)}。"
        )

    lines.append("")
    lines.append("边界说明")
    price_source = _source_label(recommended.get("price_source"))
    setup_source = _source_label(recommended.get("setup_fee_source"))
    if price_source:
        lines.append(f"- 套餐价来源：产品表 {price_source}")
    if setup_source:
        lines.append(f"- 初装/调试费来源：产品表 {setup_source}")
    lines.append("- RAG 层当前未配置知识库，不补充产品数据和工具结果之外的技术优势或服务承诺。")

    follow_up = compare.get("follow_up_questions") or parsed.get("follow_up_questions") or []
    unsupported = rag.get("unsupported_claims") or []
    if follow_up or unsupported:
        lines.append("")
        lines.append("需销售确认")
        for question in follow_up:
            lines.append(f"- {question}")
        if unsupported:
            lines.append("- SLA、开通周期、免费测试、巡检、赠品、IP 赠送、调试费减免、锁价等：产品数据未配置，需销售确认。")

    return "\n".join(lines)


@tool
def generate_quote_proposal(query: str) -> str:
    """
    Generator：生成方案书 / 报价说明。
    输入必须是客户原始需求。本工具会按 Intent Parser -> Rule Engine -> 产品核算模块 -> Compare -> Calculator -> RAG -> Generator
    的顺序编排，并只使用前面各层已有的结构化结果生成最终说明。
    """
    if _looks_like_derived_tool_output(query):
        return (
            "Generator 输入错误：请传入客户原始需求，不要传入候选方案摘要或其他工具输出。"
            "例如传入“客户需要固定IP和100M大带宽，上海办公，预算2万以内”。"
        )
    response = build_quote_proposal_response(query)
    return render_quote_proposal_response(response)


@tool
def generate_quote_sheet(query: str) -> str:
    """
    Generator：按 data/raw/报价单模板.xlsx 的字段结构生成报价单模板数据。
    输入必须是客户原始需求。本工具只生成销售可填写的报价单字段和报价明细，不生成方案书话术。
    """
    if _looks_like_derived_tool_output(query):
        return (
            "Generator 输入错误：请传入客户原始需求，不要传入候选方案摘要或其他工具输出。"
            "例如传入“有个做游戏的客户，服务器在美国，上海办公室访问很慢，大概10个人用，想先试一个月，预算1万元以内”。"
        )
    response = build_quote_proposal_response(query)
    return render_quote_sheet_response(response)


@tool
def generate_quote_proposal_json(query: str) -> str:
    """
    返回 Generator 层结构化 JSON，便于调试完整销售流程结果。
    """
    response = build_quote_proposal_response(query)
    return json.dumps(response, ensure_ascii=False, indent=2)

