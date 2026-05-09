from typing import Any
from langgraph.graph import MessagesState

class AgentState(MessagesState):
    input: str
    tool_result: Any