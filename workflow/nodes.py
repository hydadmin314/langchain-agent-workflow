from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END
from langgraph.prebuilt import ToolNode
from config.llm_config import get_llm
from tools.tool_list import ALL_TOOLS
from workflow.state import AgentState

llm = get_llm()
llm_with_tools = llm.bind_tools(ALL_TOOLS)
tool_node = ToolNode(tools=ALL_TOOLS)
SYSTEM_PROMPT = (
    "你是企业产品与报价助手。涉及产品目录、型号、品牌、资费、报价、最低销售价、调试费、"
    "计费周期、折扣规则、套餐和分类清单时，优先调用可用工具查询后再回答。"
)

def agent_think_node(state: AgentState) -> AgentState:
    messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}

def route_tools(state: AgentState):
    messages = state["messages"]
    last_msg = messages[-1]
    if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
        return "tools"
    return END
