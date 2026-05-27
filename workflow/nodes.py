import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END
from langgraph.prebuilt import ToolNode
from config.llm_config import get_llm
from tools.sales_tools.generator_tool import (
    apply_package_selection,
    build_quote_proposal_response,
    render_quote_proposal_response,
    render_quote_sheet_response,
    render_recommendation_response,
)
from tools.sales_tools.requirement_parser import parse_requirement_payload
from tools.tool_list import ALL_TOOLS
from workflow.state import AgentState

llm = get_llm()
llm_with_tools = llm.bind_tools(ALL_TOOLS)
tool_node = ToolNode(tools=ALL_TOOLS)
SYSTEM_PROMPT = (
    "你是企业产品与报价助手，负责把客户需求转成可销售、可报价、可解释的套餐建议。"
    "涉及客户需求梳理、套餐推荐、套餐对比、适用场景判断时，必须优先调用需求解析工具，"
    "先把客户原始描述整理成结构化需求，再结合场景分类、推荐工具、套餐对比工具和产品工具回答。"
    "涉及产品目录、型号、品牌、资费、报价、最低销售价、调试费、计费周期、折扣规则、"
    "年付包、SLA、赠品、IP 扩容、巡检、锁价、增值服务等内容时，必须以工具结果或产品数据为准。"
    "如果产品数据没有提供，就明确说明“产品数据未配置，需销售确认”，严禁编造。"
    "做套餐对比时，必须基于候选产品之间的真实字段差异，例如价格、带宽、计费周期、产品类型、"
    "适用场景、限制条件、已配置权益等；不要把行业常见权益、经验判断或销售话术当成已承诺服务。"
    "产品核算模块只负责找出候选套餐、说明适配度和优劣；折扣、调试费、首期总价、预算匹配等最终价格结论，"
    "必须交给 Calculator 模块统一计算。"
    "技术参数、产品优势、销售话术依据、服务权益和交付承诺属于 RAG 层；报价或套餐推荐类回答在 Calculator 后、最终回答前必须调用 retrieve_sales_context。"
    "调用 retrieve_sales_context 时也必须使用客户原始需求。"
    "当 RAG 返回知识库未配置或没有依据时，不得补充任何额外技术优势或销售承诺，只能说明“产品数据未配置，需销售确认”。"
    "Generator 是最终输出层；只有当用户明确要求生成报价单、方案书、报价说明、正式报价或最终输出时，才进入 Generator。"
    "如果用户要报价单，必须使用 generate_quote_sheet 或按报价单模板字段输出；如果用户要方案书或报价说明，才使用 generate_quote_proposal。"
    "generate_quote_sheet 和 generate_quote_proposal 都会按 Intent Parser、Rule Engine、产品核算、Compare、Calculator、RAG、Generator 完整流程编排；不要手动串联工具后再自行重新组织未验证事实。"
    "调用 Generator 工具时也必须使用客户原始需求。"
    "拿到工具结果后，不要原样输出 JSON，要整理成客户可读、销售可用的自然语言结论。"
    "推荐套餐时，优先给出 2-4 个候选方案进行对比，再说明推荐哪一个、为什么推荐、其他方案的取舍。"
    "调用 Compare 或 Calculator 时，工具参数必须使用客户原始需求，不要传入其他工具输出、候选方案摘要、表格摘要或自己二次改写后的方案文本；"
    "如果是多轮追问，就结合会话上下文补全为客户原始需求口径后再调用。"
    "最终回答只能复述工具结果中出现的产品字段、价格、规则、优势和取舍；工具结果没有出现的交付周期、免费测试、SLA、巡检、赠品、IP 赠送、调试费减免、锁价等内容，一律回答“产品数据未配置，需销售确认”。"
)

def _latest_human_query(state: AgentState) -> str | None:
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return None


def _is_generator_request(text: str | None) -> bool:
    if not text:
        return False
    terms = (
        "生成报价单",
        "生成报价",
        "生成方案",
        "生成方案书",
        "生成报价说明",
        "正式报价",
        "报价单",
        "方案书",
        "报价说明",
        "最终输出",
        "出一份方案",
        "出方案",
        "出一份报价",
        "出报价",
    )
    return any(term in text for term in terms)


def _is_quote_sheet_request(text: str | None) -> bool:
    if not text:
        return False
    quote_sheet_terms = (
        "报价单",
        "正式报价",
        "生成报价",
        "出一份报价",
        "出报价",
    )
    proposal_terms = (
        "方案书",
        "报价说明",
    )
    return any(term in text for term in quote_sheet_terms) and not any(term in text for term in proposal_terms)


def _latest_business_query(state: AgentState) -> str | None:
    active_requirement = _updated_active_requirement(state)
    return _active_requirement_query(active_requirement) or _latest_human_query(state)


def _has_sales_modifier(text: str) -> bool:
    if re.search(r"\d+(?:\.\d+)?\s*(?:m|M|兆|宽带|带宽)", text):
        return True
    terms = (
        "预算",
        "一年",
        "一个月",
        "试用",
        "先试",
        "年付",
        "月付",
        "直签",
        "ict",
        "折扣",
        "优惠",
        "带宽",
        "宽带",
        "兆",
    )
    return any(term in text for term in terms)


def _has_standalone_scene(text: str) -> bool:
    parsed = parse_requirement_payload(text)
    return bool(
        parsed.get("scenarios")
        or parsed.get("target_categories")
        or parsed.get("explicit_categories")
        or parsed.get("model_keywords")
    )


def _is_business_requirement_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped or _is_generator_request(stripped):
        return False
    return _has_standalone_scene(stripped) or _has_sales_modifier(stripped)


def _starts_new_requirement(text: str) -> bool:
    terms = (
        "另一个客户",
        "另外一个客户",
        "新客户",
        "新需求",
        "重新",
        "换一个",
        "再来一个",
    )
    return any(term in text for term in terms)


def _is_context_follow_up(text: str, previous_query: str | None) -> bool:
    if not previous_query or _is_generator_request(text):
        return False
    stripped = text.strip()
    if not stripped or _starts_new_requirement(stripped) or not _has_sales_modifier(stripped):
        return False
    if len(stripped) <= 24:
        return True
    return not _has_standalone_scene(stripped)


def _merge_business_query(previous_query: str, follow_up: str) -> str:
    return f"{previous_query}；补充条件：{follow_up}"


def _resolved_business_query(state: AgentState) -> str | None:
    active_requirement = _updated_active_requirement(state)
    return _active_requirement_query(active_requirement)


def _active_requirement_query(active_requirement: dict[str, Any] | None) -> str | None:
    if not active_requirement:
        return None
    query = active_requirement.get("canonical_query")
    return str(query) if query else None


def _build_active_requirement(parts: list[str]) -> dict[str, Any]:
    canonical_query = parts[0] if parts else ""
    for part in parts[1:]:
        canonical_query = _merge_business_query(canonical_query, part)
    parsed = parse_requirement_payload(canonical_query) if canonical_query else {}
    return {
        "canonical_query": canonical_query,
        "parts": parts,
        "parsed": parsed,
        "summary": {
            "scenarios": parsed.get("scenarios", []),
            "target_categories": parsed.get("target_categories", []),
            "billing_cycles": parsed.get("billing_cycles", []),
            "contract_modes": parsed.get("contract_modes", []),
            "budget": parsed.get("budget"),
            "spec_requirements": parsed.get("spec_requirements", {}),
            "network_requirements": parsed.get("network_requirements", {}),
        },
    }


def _updated_active_requirement(state: AgentState) -> dict[str, Any] | None:
    existing = state.get("active_requirement")
    parts = list((existing or {}).get("parts") or [])
    last_msg = state["messages"][-1] if state.get("messages") else None
    if not isinstance(last_msg, HumanMessage):
        return existing

    content = str(last_msg.content).strip()
    if not _is_business_requirement_text(content):
        return existing

    previous_query = _active_requirement_query(existing)
    if _is_context_follow_up(content, previous_query):
        parts.append(content)
    else:
        parts = [content]
    return _build_active_requirement(parts)


def _tool_names(state: AgentState) -> set[str]:
    return {
        message.name
        for message in state["messages"]
        if isinstance(message, ToolMessage) and message.name
    }


def agent_think_node(state: AgentState) -> AgentState:
    active_requirement = _updated_active_requirement(state)
    active_query = _active_requirement_query(active_requirement)
    if state["messages"]:
        last_msg = state["messages"][-1]
        if isinstance(last_msg, HumanMessage) and _is_generator_request(str(last_msg.content)):
            query = active_query or _latest_human_query(state)
            if query:
                response = build_quote_proposal_response(query)
                response = apply_package_selection(response, str(last_msg.content))
                if _is_quote_sheet_request(str(last_msg.content)):
                    content = render_quote_sheet_response(response)
                else:
                    content = render_quote_proposal_response(response)
                return {"messages": [AIMessage(content=content)], "active_requirement": active_requirement}
        if isinstance(last_msg, HumanMessage):
            latest_query = str(last_msg.content)
            resolved_query = active_query
            if resolved_query and _is_business_requirement_text(latest_query):
                response = build_quote_proposal_response(resolved_query)
                return {
                    "messages": [AIMessage(content=render_recommendation_response(response))],
                    "active_requirement": active_requirement,
                }
        if isinstance(last_msg, ToolMessage) and last_msg.name in {"generate_quote_proposal", "generate_quote_sheet"}:
            return {"messages": [AIMessage(content=last_msg.content)], "active_requirement": active_requirement}
        if isinstance(last_msg, ToolMessage) and last_msg.name == "retrieve_sales_context":
            names = _tool_names(state)
            query = active_query or _latest_human_query(state)
            if query and "calculator" in names:
                response = build_quote_proposal_response(query)
                if _is_generator_request(query):
                    if _is_quote_sheet_request(query):
                        content = render_quote_sheet_response(response)
                    else:
                        content = render_quote_proposal_response(response)
                else:
                    content = render_recommendation_response(response)
                return {"messages": [AIMessage(content=content)], "active_requirement": active_requirement}
    messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
    response = llm_with_tools.invoke(messages)
    return {"messages": [response], "active_requirement": active_requirement}

def route_tools(state: AgentState):
    messages = state["messages"]
    last_msg = messages[-1]
    if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
        return "tools"
    return END
