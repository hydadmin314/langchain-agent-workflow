from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from agent.base_agent import BaseAgent
from workflow.state import AgentState
from workflow.nodes import agent_think_node, tool_node, route_tools
from utils.logger import logger


class GraphAgent(BaseAgent):
    def __init__(self):
        self.graph = self.build_agent()
        self.messages: list[BaseMessage] = []
        self.active_requirement: dict | None = None

    def build_agent(self):
        workflow = StateGraph(AgentState)
        workflow.add_node("agent", agent_think_node)
        workflow.add_node("tools", tool_node)

        workflow.add_edge(START, "agent")
        workflow.add_conditional_edges(
            "agent",
            route_tools,
            {"tools": "tools", "__end__": END}
        )
        workflow.add_edge("tools", "agent")
        return workflow.compile()

    def reset(self) -> None:
        self.messages = []
        self.active_requirement = None
        logger.info("GraphAgent 会话上下文已清空")

    def run(self, query: str) -> str:
        current_messages = [*self.messages, HumanMessage(content=query)]
        initial_state = {"messages": current_messages}
        if self.active_requirement:
            initial_state["active_requirement"] = self.active_requirement
        res = self.graph.invoke(initial_state)
        self.messages = list(res["messages"])
        self.active_requirement = res.get("active_requirement")
        answer = res["messages"][-1].content
        logger.info("GraphAgent 回答完成")
        return answer
