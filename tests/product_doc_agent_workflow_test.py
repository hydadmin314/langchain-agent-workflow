from __future__ import annotations

import os
import sys
from pathlib import Path

# The real workflow calls the configured LLM several times. Keep these defaults
# friendly for large docx inputs; explicit .env values still win.
os.environ.setdefault("LLM_TIMEOUT", "180")
os.environ.setdefault("LLM_MAX_RETRIES", "0")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.product_doc_agent import ProductDocAgentWorkflow
from config.settings import (
    LLM_MODEL,
    LLM_TIMEOUT,
    LLM_MAX_RETRIES,
    OPENAI_BASE_URL,
    OPENAI_API_KEY,
    PRODUCT_DOC_AGENT_RATE_LIMIT_MAX_ATTEMPTS,
    PRODUCT_DOC_AGENT_RATE_LIMIT_RETRY_SECONDS,
)

def print_llm_config() -> None:
    print("LLM_MODEL:", LLM_MODEL)
    print("OPENAI_BASE_URL:", OPENAI_BASE_URL)
    print("LLM_TIMEOUT:", LLM_TIMEOUT)
    print("LLM_MAX_RETRIES:", LLM_MAX_RETRIES)
    print("OPENAI_API_KEY configured:", bool(OPENAI_API_KEY))
    print("PRODUCT_DOC_AGENT_RATE_LIMIT_MAX_ATTEMPTS:", PRODUCT_DOC_AGENT_RATE_LIMIT_MAX_ATTEMPTS)
    print("PRODUCT_DOC_AGENT_RATE_LIMIT_RETRY_SECONDS:", PRODUCT_DOC_AGENT_RATE_LIMIT_RETRY_SECONDS)

def main() -> None:

    print_llm_config()
    
    workflow = ProductDocAgentWorkflow(
        max_context_chars=12000,
        enable_debug_markdown=True,
        enable_self_check=True,
        max_concurrency=6,
    )
    try:
        result = workflow.run(r"data/raw/联通/1.1 沃专线/1 【铅印版】中国联通互联网专线接入业务（沃专线类）新装申请表.pdf")
    except Exception as exc:
        print("运行失败：", exc)
        print("建议检查：")
        print("1. .env 里 LLM_TIMEOUT 是否至少为 180")
        print("2. OPENAI_BASE_URL / LLM_MODEL 是否可用")
        print("3. 如果仍然超时，可以把 max_context_chars 继续调小，例如 12000")
        raise

    print(result.document_id)
    print(result.review_path)
    print(result.validation_issue_count)


if __name__ == "__main__":
    main()
