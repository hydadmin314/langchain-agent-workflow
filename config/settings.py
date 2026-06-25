import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _env_or(name: str, fallback):
    value = os.getenv(name)
    return fallback if value in (None, "") else value


def _env_bool(name: str, fallback: bool) -> bool:
    value = os.getenv(name)
    if value in (None, ""):
        return fallback
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


# LLM
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
LLM_MODEL = os.getenv("LLM_MODEL")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "1"))

# Product document text extraction LLM. Falls back to the default LLM settings.
PRODUCT_DOC_TEXT_LLM_API_KEY = _env_or("PRODUCT_DOC_TEXT_LLM_API_KEY", OPENAI_API_KEY)
PRODUCT_DOC_TEXT_LLM_BASE_URL = _env_or("PRODUCT_DOC_TEXT_LLM_BASE_URL", OPENAI_BASE_URL)
PRODUCT_DOC_TEXT_LLM_MODEL = _env_or("PRODUCT_DOC_TEXT_LLM_MODEL", LLM_MODEL)
PRODUCT_DOC_TEXT_LLM_TIMEOUT = float(_env_or("PRODUCT_DOC_TEXT_LLM_TIMEOUT", LLM_TIMEOUT))
PRODUCT_DOC_TEXT_LLM_MAX_RETRIES = int(_env_or("PRODUCT_DOC_TEXT_LLM_MAX_RETRIES", LLM_MAX_RETRIES))
PRODUCT_DOC_TEXT_LLM_DISABLE_THINKING = _env_bool(
    "PRODUCT_DOC_TEXT_LLM_DISABLE_THINKING",
    "deepseek" in str(PRODUCT_DOC_TEXT_LLM_BASE_URL or "").lower(),
)

# Product document vision/OCR LLM. Used to turn image-only PDFs into Markdown.
PRODUCT_DOC_OCR_LLM_API_KEY = _env_or("PRODUCT_DOC_OCR_LLM_API_KEY", OPENAI_API_KEY)
PRODUCT_DOC_OCR_LLM_BASE_URL = _env_or("PRODUCT_DOC_OCR_LLM_BASE_URL", OPENAI_BASE_URL)
PRODUCT_DOC_OCR_LLM_MODEL = _env_or("PRODUCT_DOC_OCR_LLM_MODEL", LLM_MODEL)
PRODUCT_DOC_OCR_LLM_TIMEOUT = float(_env_or("PRODUCT_DOC_OCR_LLM_TIMEOUT", LLM_TIMEOUT))
PRODUCT_DOC_OCR_LLM_MAX_RETRIES = int(_env_or("PRODUCT_DOC_OCR_LLM_MAX_RETRIES", LLM_MAX_RETRIES))

# Product document extraction
PRODUCT_DOC_AGENT_RATE_LIMIT_MAX_ATTEMPTS = int(os.getenv("PRODUCT_DOC_AGENT_RATE_LIMIT_MAX_ATTEMPTS", "2"))
PRODUCT_DOC_AGENT_RATE_LIMIT_RETRY_SECONDS = float(os.getenv("PRODUCT_DOC_AGENT_RATE_LIMIT_RETRY_SECONDS", "8"))
PRODUCT_DOC_RAW_ROOT = Path(os.getenv("PRODUCT_DOC_RAW_ROOT", str(Path("data") / "raw")))
PRODUCT_DOC_AGENT_MODULE_CACHE_ENABLED = _env_bool("PRODUCT_DOC_AGENT_MODULE_CACHE_ENABLED", True)
PRODUCT_DOC_AGENT_MODULE_CACHE_DIR = Path(
    os.getenv(
        "PRODUCT_DOC_AGENT_MODULE_CACHE_DIR",
        str(Path("data") / "product_doc_agent" / "cache" / "module_outputs"),
    )
)

# Agent
AGENT_MAX_ITERATIONS = int(os.getenv("AGENT_MAX_ITERATIONS", 5))
AGENT_VERBOSE = os.getenv("AGENT_VERBOSE", "True") == "True"
