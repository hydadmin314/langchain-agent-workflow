from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any


def llm_enabled_by_env() -> bool:
    return os.getenv("PRODUCT_DOC_AGENT_ENABLE_LLM", "false").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class LLMCallResult:
    text: str
    raw: Any


class LLMClient:
    def __init__(self, temperature: float = 0.1) -> None:
        from config.llm_config import get_llm

        self.llm = get_llm(temperature=temperature)

    def complete(self, prompt: str) -> LLMCallResult:
        response = self.llm.invoke(prompt)
        return LLMCallResult(text=str(getattr(response, "content", response)), raw=response)

    def complete_json(self, prompt: str) -> dict[str, Any]:
        return parse_json_object(self.complete(prompt).text)


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            raise
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("LLM response must be a JSON object")
    return payload
