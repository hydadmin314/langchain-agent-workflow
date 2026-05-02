from langchain.agents import create_openai_tools_agent, AgentExecutor
from config.llm_config import get_llm
from config.settings import AGENT_MAX_ITERATIONS, AGENT_VERBOSE
from tools.tool_list import ALL_TOOLS
from prompts.agent_prompt import get_agent_prompt
from memory.chat_memory import get_chat_memory
from agent.base_agent import BaseAgent

class NormalToolAgent(BaseAgent):
    def __init__(self):
        self.llm = get_llm()
        self.tools = ALL_TOOLS
        self.prompt = get_agent_prompt()
        self.memory = get_chat_memory()
        self.agent_executor = self.build_agent()

    def build_agent(self) -> AgentExecutor:
        agent = create_openai_tools_agent(self.llm, self.tools, self.prompt)
        executor = AgentExecutor(
            agent=agent,
            tools=self.tools,
            verbose=AGENT_VERBOSE,
            max_iterations=AGENT_MAX_ITERATIONS,
            memory=self.memory,
            handle_parsing_errors=True
        )
        return executor

    def run(self, query: str) -> str:
        res = self.agent_executor.invoke({"input": query})
        return res["output"]