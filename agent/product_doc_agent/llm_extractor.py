from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from agent.product_doc_agent.schema_normalizer import normalize_to_module_schema
from agent.product_doc_agent.validator import validate_against_schema
from config.llm_config import get_llm
from prompts.product_doc_agent_prompts import (
    build_compact_module_prompt,
    build_module_prompt,
    build_module_repair_prompt,
    build_self_check_prompt,
)
from schema.schema import get_module_json_schema
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

COMPACT_FIRST_MODULES = {"parties_and_application"}


class LLMExtractionError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ProductDocumentLLMExtractor:
    """Call the LLM module-by-module and parse strict JSON responses."""

    def __init__(
        self,
        llm: BaseChatModel | None = None,
        *,
        max_context_chars: int = 60000,
        max_concurrency: int = 3,
    ) -> None:
        self.llm = llm or get_llm(temperature=0)
        self.max_context_chars = max_context_chars
        self.max_concurrency = max(1, max_concurrency)

    def extract_modules(
        self,
        document_context: str | dict[str, str],
        modules: Sequence[str] | None = None,
        *,
        continue_on_error: bool = False,
    ) -> dict[str, Any]:
        return run_async_from_sync(
            self.extract_modules_async(
                document_context,
                modules=modules,
                continue_on_error=continue_on_error,
            )
        )

    async def extract_modules_async(
        self,
        document_context: str | dict[str, str],
        modules: Sequence[str] | None = None,
        *,
        continue_on_error: bool = False,
        max_concurrency: int | None = None,
    ) -> dict[str, Any]:
        module_names = list(modules or EXTRACTION_MODULES)
        semaphore = asyncio.Semaphore(max(1, max_concurrency or self.max_concurrency))

        async def run_one(module_name: str) -> tuple[str, Any, dict[str, str] | None]:
            async with semaphore:
                return await self._extract_one_module_async(
                    module_name,
                    document_context,
                    continue_on_error=continue_on_error,
                )

        module_results = await asyncio.gather(*(run_one(module_name) for module_name in module_names))
        results: dict[str, Any] = {}
        module_errors: list[dict[str, str]] = []

        for module_name, result, error in module_results:
            results[module_name] = result
            if error is not None:
                module_errors.append(error)

        if module_errors:
            results["__module_errors__"] = module_errors
        return results

    async def _extract_one_module_async(
        self,
        module_name: str,
        document_context: str | dict[str, str],
        *,
        continue_on_error: bool,
    ) -> tuple[str, Any, dict[str, str] | None]:
        context = document_context.get(module_name, "") if isinstance(document_context, dict) else document_context
        prompt = build_primary_module_prompt(module_name, context, max_context_chars=self.max_context_chars)
        logger.info(f"start module extraction: {module_name}, prompt_chars={len(prompt)}")

        try:
            result = await self._ainvoke_json(prompt, expected_module=module_name)
            result = normalize_to_module_schema(module_name, result)
            result = await self._repair_if_needed_async(module_name, result)
            return module_name, result, None
        except LLMExtractionError as exc:
            retry_result = await self._retry_with_compact_prompt_async(module_name, context, exc)
            if retry_result is not None:
                return module_name, retry_result, None
            if not continue_on_error:
                raise
            logger.warning(str(exc))
            fallback = getattr(exc, "fallback_result", None)
            result = fallback if fallback is not None else empty_module_output(module_name)
            return module_name, result, {"module": module_name, "error": str(exc)}

    def _repair_if_needed(self, module_name: str, result: Any) -> Any:
        issues = validate_module_output(module_name, result)
        if not issues:
            return result

        issue_text = format_validation_issues(issues)
        logger.warning(f"{module_name}: module output violates schema, repairing; issue_count={len(issues)}")
        repair_prompt = build_module_repair_prompt(
            module_name,
            json.dumps(result, ensure_ascii=False, indent=2),
            issue_text,
        )
        repaired = self._invoke_json(repair_prompt, expected_module=f"{module_name}.repair")
        repaired = normalize_to_module_schema(module_name, repaired)
        remaining_issues = validate_module_output(module_name, repaired)
        if remaining_issues:
            error = LLMExtractionError(
                f"{module_name}: repaired output still violates schema; "
                f"issue_count={len(remaining_issues)}; first_issue={format_validation_issues(remaining_issues[:1])}"
            )
            error.fallback_result = result
            raise error
        return repaired

    async def _repair_if_needed_async(self, module_name: str, result: Any) -> Any:
        issues = validate_module_output(module_name, result)
        if not issues:
            return result

        issue_text = format_validation_issues(issues)
        logger.warning(f"{module_name}: module output violates schema, repairing; issue_count={len(issues)}")
        repair_prompt = build_module_repair_prompt(
            module_name,
            json.dumps(result, ensure_ascii=False, indent=2),
            issue_text,
        )
        repaired = await self._ainvoke_json(repair_prompt, expected_module=f"{module_name}.repair")
        repaired = normalize_to_module_schema(module_name, repaired)
        remaining_issues = validate_module_output(module_name, repaired)
        if remaining_issues:
            error = LLMExtractionError(
                f"{module_name}: repaired output still violates schema; "
                f"issue_count={len(remaining_issues)}; first_issue={format_validation_issues(remaining_issues[:1])}"
            )
            error.fallback_result = result
            raise error
        return repaired

    async def _retry_with_compact_prompt_async(
        self,
        module_name: str,
        context: str,
        error: LLMExtractionError,
    ) -> Any | None:
        if not error.retryable:
            return None

        compact_context = compact_context_for_retry(module_name, context)
        compact_prompt = build_compact_module_prompt(module_name, compact_context)
        logger.warning(
            f"{module_name}: retrying with compact prompt after request failure; "
            f"prompt_chars={len(compact_prompt)}"
        )
        try:
            result = await self._ainvoke_json(compact_prompt, expected_module=f"{module_name}.compact_retry")
            result = normalize_to_module_schema(module_name, result)
            return await self._repair_if_needed_async(module_name, result)
        except LLMExtractionError as retry_error:
            logger.warning(f"{module_name}: compact retry failed: {retry_error}")
            return None

    def self_check(self, product_document: dict[str, Any]) -> dict[str, Any]:
        return run_async_from_sync(self.self_check_async(product_document))

    async def self_check_async(self, product_document: dict[str, Any]) -> dict[str, Any]:
        prompt = build_self_check_prompt(json.dumps(product_document, ensure_ascii=False, indent=2))
        result = await self._ainvoke_json(prompt, expected_module="self_check")
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
                f"prompt_chars={len(prompt)}; original_error={exc.__class__.__name__}: {exc}",
                retryable=True,
            ) from exc
        return parse_json_response(response, expected_module=expected_module)

    async def _ainvoke_json(self, prompt: str, *, expected_module: str) -> Any:
        started_at = time.perf_counter()
        try:
            response = await self.llm.ainvoke([HumanMessage(content=prompt)])
        except AttributeError:
            return await asyncio.to_thread(self._invoke_json, prompt, expected_module=expected_module)
        except Exception as exc:
            elapsed = time.perf_counter() - started_at
            raise LLMExtractionError(
                f"{expected_module}: LLM request failed after {elapsed:.1f}s; "
                f"prompt_chars={len(prompt)}; original_error={exc.__class__.__name__}: {exc}",
                retryable=True,
            ) from exc
        return parse_json_response(response, expected_module=expected_module)


def parse_json_response(response: Any, *, expected_module: str) -> Any:
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


def run_async_from_sync(coro: Awaitable[Any]) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    coro.close()
    raise RuntimeError("An event loop is already running. Use the corresponding async workflow method.")


def build_primary_module_prompt(module_name: str, context: str, *, max_context_chars: int) -> str:
    if module_name in COMPACT_FIRST_MODULES:
        compact_context = compact_context_for_retry(module_name, context, max_chars=min(max_context_chars, 6500))
        return build_compact_module_prompt(module_name, compact_context)
    return build_module_prompt(module_name, context[:max_context_chars])


RETRY_CONTEXT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "eligibility_and_constraints": (
        "仅限",
        "不适用",
        "不能",
        "不得",
        "不可",
        "必须",
        "条件",
        "要求",
        "限制",
        "阻断",
        "推荐",
        "停用",
        "停止",
        "IP",
        "地址",
        "资质",
        "实名",
        "违法",
        "违规",
        "承诺",
    ),
    "optional_packages": ("可选", "权益", "增值", "固话", "商云通", "移动业务", "升速", "订购", "申请线数"),
    "base_package": ("基础套餐", "套餐类型", "接口标准", "速率", "上行", "下行", "月", "年", "语音"),
}


def compact_context_for_retry(module_name: str, context: str, *, max_chars: int = 6000) -> str:
    """Keep high-signal context lines for a timeout retry without losing source locations."""
    lines = [line for line in context.splitlines() if line.strip()]
    if not lines or len(context) <= max_chars:
        return context[:max_chars]

    keywords = RETRY_CONTEXT_KEYWORDS.get(module_name, ())
    selected: list[str] = []
    seen: set[str] = set()

    for line in lines[:12]:
        if line not in seen:
            selected.append(line)
            seen.add(line)

    for line in lines:
        if keywords and not any(keyword in line for keyword in keywords):
            continue
        if line in seen:
            continue
        selected.append(line)
        seen.add(line)

    compacted: list[str] = []
    total = 0
    for line in selected:
        if total + len(line) + 1 > max_chars:
            break
        compacted.append(line)
        total += len(line) + 1

    return "\n".join(compacted) if compacted else context[:max_chars]


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


def validate_module_output(module_name: str, result: Any) -> list[dict[str, Any]]:
    schema = get_module_json_schema(module_name)
    return validate_against_schema(result, schema)


def format_validation_issues(issues: list[dict[str, Any]], *, limit: int = 40) -> str:
    lines: list[str] = []
    for issue in issues[:limit]:
        parts = [
            str(issue.get("severity", "")),
            str(issue.get("path", "")),
            str(issue.get("message", "")),
        ]
        if "expected" in issue:
            parts.append(f"expected={issue['expected']}")
        if "actual" in issue:
            parts.append(f"actual={issue['actual']}")
        lines.append(" | ".join(part for part in parts if part))
    if len(issues) > limit:
        lines.append(f"... {len(issues) - limit} more issues")
    return "\n".join(lines)
