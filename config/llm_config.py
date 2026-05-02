from langchain_openai import ChatOpenAI
from config.settings import OPENAI_API_KEY, OPENAI_BASE_URL, LLM_MODEL

def get_llm(temperature: float = 0.1) -> ChatOpenAI:
    return ChatOpenAI(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        model=LLM_MODEL,
        temperature=temperature
    )