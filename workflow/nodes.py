from langchain_core.messages import AIMessage
from langgraph.graph import END
from langgraph.prebuilt import ToolNode
from config.llm_config import get_llm
from tools.tool_list import ALL_TOOLS
from workflow.state import AgentState

llm = get_llm()
llm_with_tools = llm.bind_tools(ALL_TOOLS)
tool_node = ToolNode(tools=ALL_TOOLS)

def agent_think_node(state: AgentState) -> AgentState:
    messages = state["messages"]
    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}

def route_tools(state: AgentState):
    messages = state["messages"]
    last_msg = messages[-1]
    if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
        return "tools"
    return END