from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END
from langgraph.prebuilt import ToolNode
from config.llm_config import get_llm
from tools.generator_tool import build_quote_proposal_response, render_quote_proposal_response
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
    "Generator 是最终方案书/报价说明生成层；当用户请求套餐推荐、报价、方案书、报价说明或销售沟通输出时，优先直接调用 generate_quote_proposal。"
    "generate_quote_proposal 会按 Intent Parser、Rule Engine、产品核算、Compare、Calculator、RAG、Generator 完整流程编排；不要手动串联工具后再自行重新组织未验证事实。"
    "调用 generate_quote_proposal 时也必须使用客户原始需求。"
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


def _tool_names(state: AgentState) -> set[str]:
    return {
        message.name
        for message in state["messages"]
        if isinstance(message, ToolMessage) and message.name
    }


def agent_think_node(state: AgentState) -> AgentState:
    if state["messages"]:
        last_msg = state["messages"][-1]
        if isinstance(last_msg, ToolMessage) and last_msg.name == "generate_quote_proposal":
            return {"messages": [AIMessage(content=last_msg.content)]}
        if isinstance(last_msg, ToolMessage) and last_msg.name == "retrieve_sales_context":
            names = _tool_names(state)
            query = _latest_human_query(state)
            if query and "calculator" in names:
                response = build_quote_proposal_response(query)
                return {"messages": [AIMessage(content=render_quote_proposal_response(response))]}
    messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}

def route_tools(state: AgentState):
    messages = state["messages"]
    last_msg = messages[-1]
    if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
        return "tools"
    return END
