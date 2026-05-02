import os
from dotenv import load_dotenv

load_dotenv()

# LLM
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
LLM_MODEL = os.getenv("LLM_MODEL")

# Agent
AGENT_MAX_ITERATIONS = int(os.getenv("AGENT_MAX_ITERATIONS", 5))
AGENT_VERBOSE = os.getenv("AGENT_VERBOSE", "True") == "True"