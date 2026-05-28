import os
from dotenv import load_dotenv

load_dotenv()

# LLM
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
LLM_MODEL = os.getenv("LLM_MODEL")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "1"))

# Product document extraction
PRODUCT_DOC_AGENT_RATE_LIMIT_MAX_ATTEMPTS = int(os.getenv("PRODUCT_DOC_AGENT_RATE_LIMIT_MAX_ATTEMPTS", "2"))
PRODUCT_DOC_AGENT_RATE_LIMIT_RETRY_SECONDS = float(os.getenv("PRODUCT_DOC_AGENT_RATE_LIMIT_RETRY_SECONDS", "8"))

# Agent
AGENT_MAX_ITERATIONS = int(os.getenv("AGENT_MAX_ITERATIONS", 5))
AGENT_VERBOSE = os.getenv("AGENT_VERBOSE", "True") == "True"
