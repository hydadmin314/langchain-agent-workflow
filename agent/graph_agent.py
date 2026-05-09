from langgraph.graph import StateGraph, START, END
from agent.base_agent import BaseAgent
from workflow.state import AgentState
from workflow.nodes import agent_think_node, tool_node, route_tools
from utils.logger import logger

class GraphAgent(BaseAgent):
    def __init__(self):
        self.graph = self.build_agent()

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

    def run(self, query: str) -> str:
        res = self.graph.invoke({"messages": [("user", query)]})
        answer = res["messages"][-1].content
        logger.info("GraphAgent 回答完成")
        return answer