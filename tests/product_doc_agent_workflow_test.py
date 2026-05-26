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


def main() -> None:
    workflow = ProductDocAgentWorkflow(max_context_chars=12000, enable_self_check=True)
    try:
        result = workflow.run(r"data/raw/1 【20260115起】100M智云上海专线基础版688套餐申请登记表 不带语音.docx")
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
