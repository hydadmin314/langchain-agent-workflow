from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from config.llm_config import get_llm
from prompts.product_doc_agent_prompts import build_module_prompt, build_self_check_prompt
from utils.logger import logger


EXTRACTION_MODULES = [
    "document_info",
    "parties_and_application",
    "base_package",
    "optional_packages",
    "fee_and_term_rules",
    "agreement_rules",
    "application_materials",
    "eligibility_and_constraints",
    "supplemental_rules",
]


class LLMExtractionError(RuntimeError):
    pass


class ProductDocumentLLMExtractor:
    """Call the LLM module-by-module and parse strict JSON responses."""

    def __init__(self, llm: BaseChatModel | None = None, *, max_context_chars: int = 60000) -> None:
        self.llm = llm or get_llm(temperature=0)
        self.max_context_chars = max_context_chars

    def extract_modules(
        self,
        document_context: str | dict[str, str],
        modules: Sequence[str] | None = None,
        *,
        continue_on_error: bool = False,
    ) -> dict[str, Any]:
        module_names = list(modules or EXTRACTION_MODULES)
        results: dict[str, Any] = {}
        module_errors: list[dict[str, str]] = []
        for module_name in module_names:
            context = document_context.get(module_name, "") if isinstance(document_context, dict) else document_context
            prompt = build_module_prompt(module_name, context[: self.max_context_chars])
            logger.info(f"开始抽取模块: {module_name}, prompt_chars={len(prompt)}")
            try:
                results[module_name] = self._invoke_json(prompt, expected_module=module_name)
            except LLMExtractionError as exc:
                if not continue_on_error:
                    raise
                logger.warning(str(exc))
                results[module_name] = empty_module_output(module_name)
                module_errors.append({"module": module_name, "error": str(exc)})
        if module_errors:
            results["__module_errors__"] = module_errors
        return results

    def self_check(self, product_document: dict[str, Any]) -> dict[str, Any]:
        prompt = build_self_check_prompt(json.dumps(product_document, ensure_ascii=False, indent=2))
        result = self._invoke_json(prompt, expected_module="self_check")
        if not isinstance(result, dict):
            raise LLMExtractionError("self_check response must be a JSON object")
        return result

    def _invoke_json(self, prompt: str, *, expected_module: str) -> Any:
        started_at = time.perf_counter()
        try:
            response = self.llm.invoke([HumanMessage(content=prompt)])
        except Exception as exc:
            elapsed = time.perf_counter() - started_at
            raise LLMExtractionError(
                f"{expected_module}: LLM request failed after {elapsed:.1f}s; "
                f"prompt_chars={len(prompt)}; original_error={exc.__class__.__name__}: {exc}"
            ) from exc
        content = getattr(response, "content", response)
        if isinstance(content, list):
            content = "\n".join(str(item) for item in content)
        if not isinstance(content, str):
            raise LLMExtractionError(f"{expected_module}: LLM response is not text")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            candidate = extract_json_from_text(content)
            if candidate is None:
                raise LLMExtractionError(f"{expected_module}: response is not valid JSON")
            return candidate


def extract_json_from_text(text: str) -> Any | None:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    starts = [index for index, char in enumerate(text) if char in "[{"]
    for start in starts:
        for end in range(len(text), start, -1):
            fragment = text[start:end].strip()
            if not fragment or fragment[-1] not in "]}":
                continue
            try:
                return json.loads(fragment)
            except json.JSONDecodeError:
                continue
    return None


def empty_module_output(module_name: str) -> Any:
    if module_name in {"document_info", "parties_and_application", "base_package"}:
        return {}
    return []
