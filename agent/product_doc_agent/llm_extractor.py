from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
from collections.abc import Awaitable, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from agent.product_doc_agent.document_classifier import classify_document
from agent.product_doc_agent.schema_normalizer import normalize_to_module_schema
from agent.product_doc_agent.validator import validate_against_schema
from config.llm_config import get_product_doc_text_llm
from config.settings import (
    PRODUCT_DOC_AGENT_MODULE_CACHE_DIR,
    PRODUCT_DOC_AGENT_MODULE_CACHE_ENABLED,
    PRODUCT_DOC_AGENT_RATE_LIMIT_MAX_ATTEMPTS,
    PRODUCT_DOC_AGENT_RATE_LIMIT_RETRY_SECONDS,
)
from prompts.product_doc_agent_prompts import (
    build_compact_module_prompt,
    build_module_prompt,
    build_module_repair_prompt,
    build_module_rework_prompt,
    build_self_check_prompt,
)
from schema import get_module_json_schema
from schema.product_schema import EXTRACTION_MODULES as PRODUCT_SCHEMA_EXTRACTION_MODULES
from utils.logger import logger


# 新 schema 使用点路径模块名，便于区分申请表信息和补充资料信息。
EXTRACTION_MODULES = list(PRODUCT_SCHEMA_EXTRACTION_MODULES)

# 申请表字段较多，默认先用紧凑提示词，降低超时概率。
COMPACT_FIRST_MODULES = {"application_form_info.parties_and_application"}
CACHEABLE_MODULES = {
    "application_form_info.agreement_rules",
    "application_form_info.eligibility_and_constraints",
}
MODULE_CACHE_SCHEMA_VERSION = "product_doc_module_cache_v1"


def modules_for_document(file_path: str) -> list[str]:
    """根据文件名分类结果，返回该文档应该抽取的新 schema 模块。"""

    return list(classify_document(file_path).target_modules)


class LLMExtractionError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.rate_limited = rate_limited


class ProductDocumentLLMExtractor:
    """按模块调用大模型，并把响应解析为严格 JSON。"""

    def __init__(
        self,
        llm: BaseChatModel | None = None,
        *,
        max_context_chars: int = 60000,
        max_concurrency: int | None = None,
    ) -> None:
        self.llm = llm or get_product_doc_text_llm(temperature=0)
        self.max_context_chars = max_context_chars
        self.max_concurrency = max(1, max_concurrency or len(EXTRACTION_MODULES))
        self.rate_limit_max_attempts = max(1, PRODUCT_DOC_AGENT_RATE_LIMIT_MAX_ATTEMPTS)
        self.rate_limit_retry_seconds = max(0.0, PRODUCT_DOC_AGENT_RATE_LIMIT_RETRY_SECONDS)
        self.module_cache_enabled = PRODUCT_DOC_AGENT_MODULE_CACHE_ENABLED
        self.module_cache_dir = Path(PRODUCT_DOC_AGENT_MODULE_CACHE_DIR)
        self._async_rate_limit_lock = asyncio.Lock()
        self._sync_rate_limit_lock = threading.Lock()

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
        request_semaphore: asyncio.Semaphore | None = None,
    ) -> dict[str, Any]:
        """并发抽取多个 schema 模块。"""

        module_names = list(modules or EXTRACTION_MODULES)
        semaphore = request_semaphore or asyncio.Semaphore(max(1, max_concurrency or self.max_concurrency))

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

    async def rework_modules_async(
        self,
        document_context: dict[str, str],
        module_reasons: dict[str, str],
        previous_outputs: dict[str, Any],
        *,
        continue_on_error: bool = True,
        max_concurrency: int | None = None,
    ) -> dict[str, Any]:
        """按自检结果返工指定模块。

        返工和结构修复不一样：结构修复只修 JSON 结构；返工会重新读取当前模块 Markdown，
        重新抽取业务事实，用于解决漏抽或归属错误。
        """

        module_names = [module for module in EXTRACTION_MODULES if module in module_reasons]
        semaphore = asyncio.Semaphore(max(1, max_concurrency or min(self.max_concurrency, len(module_names) or 1)))

        async def run_one(module_name: str) -> tuple[str, Any, dict[str, str] | None]:
            async with semaphore:
                return await self._rework_one_module_async(
                    module_name,
                    document_context.get(module_name, ""),
                    previous_outputs.get(module_name, empty_module_output(module_name)),
                    module_reasons.get(module_name, ""),
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
        """抽取单个模块，并在结构不合法时做结构修复。"""

        context = document_context.get(module_name, "") if isinstance(document_context, dict) else document_context
        prompt = build_primary_module_prompt(module_name, context, max_context_chars=self.max_context_chars)
        logger.info(f"start module extraction: {module_name}, prompt_chars={len(prompt)}")
        started_at = time.perf_counter()
        cache_key = self._module_cache_key(module_name, prompt)
        cached_result = self._read_module_cache(module_name, cache_key)
        if cached_result is not None:
            elapsed = time.perf_counter() - started_at
            logger.info(f"module cache hit: {module_name}, elapsed_seconds={elapsed:.1f}")
            return module_name, cached_result, None
        if self._is_module_cacheable(module_name):
            logger.info(f"module cache miss: {module_name}")

        try:
            result = await self._ainvoke_json(prompt, expected_module=module_name)
            result = normalize_to_module_schema(module_name, result)
            result = await self._repair_if_needed_async(module_name, result)
            self._write_module_cache(module_name, cache_key, result)
            elapsed = time.perf_counter() - started_at
            logger.info(f"finish module extraction: {module_name}, elapsed_seconds={elapsed:.1f}")
            return module_name, result, None
        except LLMExtractionError as exc:
            retry_result = await self._retry_with_compact_prompt_async(module_name, context, exc)
            if retry_result is not None:
                elapsed = time.perf_counter() - started_at
                logger.info(
                    f"finish module extraction: {module_name}, "
                    f"elapsed_seconds={elapsed:.1f}, retry=compact"
                )
                return module_name, retry_result, None
            if not continue_on_error:
                raise
            elapsed = time.perf_counter() - started_at
            logger.warning(f"fail module extraction: {module_name}, elapsed_seconds={elapsed:.1f}")
            logger.warning(str(exc))
            fallback = getattr(exc, "fallback_result", None)
            result = fallback if fallback is not None else empty_module_output(module_name)
            return module_name, result, {"module": module_name, "error": str(exc)}

    async def _rework_one_module_async(
        self,
        module_name: str,
        context: str,
        previous_output: Any,
        reason: str,
        *,
        continue_on_error: bool,
    ) -> tuple[str, Any, dict[str, str] | None]:
        """返工单个模块：回到模块 Markdown 重新抽取。"""

        previous_json = json.dumps(previous_output, ensure_ascii=False, indent=2)
        prompt = build_module_rework_prompt(
            module_name,
            context[: self.max_context_chars],
            previous_json,
            reason,
        )
        logger.info(f"start module rework: {module_name}, prompt_chars={len(prompt)}")
        started_at = time.perf_counter()
        try:
            result = await self._ainvoke_json(prompt, expected_module=f"{module_name}.rework")
            result = normalize_to_module_schema(module_name, result)
            result = await self._repair_if_needed_async(module_name, result)
            elapsed = time.perf_counter() - started_at
            logger.info(f"finish module rework: {module_name}, elapsed_seconds={elapsed:.1f}")
            return module_name, result, None
        except LLMExtractionError as exc:
            retry_result = await self._retry_rework_with_compact_prompt_async(
                module_name,
                context,
                previous_json,
                reason,
                exc,
            )
            if retry_result is not None:
                elapsed = time.perf_counter() - started_at
                logger.info(
                    f"finish module rework: {module_name}, "
                    f"elapsed_seconds={elapsed:.1f}, retry=compact"
                )
                return module_name, retry_result, None
            if not continue_on_error:
                raise
            elapsed = time.perf_counter() - started_at
            logger.warning(f"fail module rework: {module_name}, elapsed_seconds={elapsed:.1f}")
            logger.warning(str(exc))
            fallback = getattr(exc, "fallback_result", None)
            result = fallback if fallback is not None else previous_output
            return module_name, result, {"module": module_name, "error": str(exc)}

    def _repair_if_needed(self, module_name: str, result: Any) -> Any:
        """同步结构修复：只修 schema，不补业务事实。"""

        issues = validate_module_output(module_name, result)
        if not issues:
            return result

        issue_text = format_validation_issues(issues)
        logger.warning(f"{module_name}: 模块输出不符合 schema，开始结构修复；issue_count={len(issues)}")
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
                f"{module_name}: 结构修复后的输出仍不符合 schema；"
                f"issue_count={len(remaining_issues)}; first_issue={format_validation_issues(remaining_issues[:1])}"
            )
            error.fallback_result = result
            raise error
        return repaired

    async def _repair_if_needed_async(self, module_name: str, result: Any) -> Any:
        """异步结构修复：只修 schema，不补业务事实。"""

        issues = validate_module_output(module_name, result)
        if not issues:
            return result

        issue_text = format_validation_issues(issues)
        logger.warning(f"{module_name}: 模块输出不符合 schema，开始结构修复；issue_count={len(issues)}")
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
                f"{module_name}: 结构修复后的输出仍不符合 schema；"
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
        """请求超时时用更紧凑的上下文重试一次。"""

        if not error.retryable:
            return None
        if error.rate_limited:
            logger.warning(f"{module_name}: 模型服务商返回限流错误，跳过紧凑重试")
            return None

        compact_context = compact_context_for_retry(module_name, context)
        compact_prompt = build_compact_module_prompt(module_name, compact_context)
        logger.warning(
            f"{module_name}: 请求失败后使用紧凑 prompt 重试；"
            f"prompt_chars={len(compact_prompt)}"
        )
        try:
            result = await self._ainvoke_json(compact_prompt, expected_module=f"{module_name}.compact_retry")
            result = normalize_to_module_schema(module_name, result)
            return await self._repair_if_needed_async(module_name, result)
        except LLMExtractionError as retry_error:
            logger.warning(f"{module_name}: 紧凑重试失败：{retry_error}")
            return None

    async def _retry_rework_with_compact_prompt_async(
        self,
        module_name: str,
        context: str,
        previous_json: str,
        reason: str,
        error: LLMExtractionError,
    ) -> Any | None:
        """返工请求超时时，用紧凑上下文重试，但继续保留旧 JSON 和返工原因。"""

        if not error.retryable:
            return None
        if error.rate_limited:
            logger.warning(f"{module_name}: 模型服务商返回限流错误，跳过返工紧凑重试")
            return None

        compact_context = compact_context_for_retry(module_name, context)
        compact_prompt = build_module_rework_prompt(
            module_name,
            compact_context,
            previous_json,
            reason,
        )
        logger.warning(
            f"{module_name}: 返工请求失败后使用紧凑 prompt 重试；"
            f"prompt_chars={len(compact_prompt)}"
        )
        try:
            result = await self._ainvoke_json(compact_prompt, expected_module=f"{module_name}.rework_compact_retry")
            result = normalize_to_module_schema(module_name, result)
            return await self._repair_if_needed_async(module_name, result)
        except LLMExtractionError as retry_error:
            logger.warning(f"{module_name}: 返工紧凑重试失败：{retry_error}")
            return None

    def self_check(self, product_document: dict[str, Any]) -> dict[str, Any]:
        """同步执行大模型语义自检。"""

        return run_async_from_sync(self.self_check_async(product_document))

    async def self_check_async(self, product_document: dict[str, Any]) -> dict[str, Any]:
        """异步执行大模型语义自检。"""

        prompt = build_self_check_prompt(json.dumps(product_document, ensure_ascii=False, indent=2))
        result = await self._ainvoke_json(prompt, expected_module="self_check")
        if not isinstance(result, dict):
            raise LLMExtractionError("self_check 响应必须是 JSON 对象")
        return result

    def _invoke_json(self, prompt: str, *, expected_module: str) -> Any:
        response = self._invoke_with_rate_limit_retry(prompt, expected_module=expected_module)
        return parse_json_response(response, expected_module=expected_module)

    async def _ainvoke_json(self, prompt: str, *, expected_module: str) -> Any:
        started_at = time.perf_counter()
        logger.info(
            "LLM request: "
            f"module={expected_module}, "
            f"model={getattr(self.llm, 'model_name', None) or getattr(self.llm, 'model', None)}, "
            f"base_url={getattr(self.llm, 'openai_api_base', None)}, "
            f"prompt_chars={len(prompt)}"
        )
        try:
            response = await self._ainvoke_with_rate_limit_retry(prompt, expected_module=expected_module)
        except AttributeError:
            return await asyncio.to_thread(self._invoke_json, prompt, expected_module=expected_module)
        elapsed = time.perf_counter() - started_at
        logger.info(f"LLM response: module={expected_module}, elapsed_seconds={elapsed:.1f}")
        return parse_json_response(response, expected_module=expected_module)

    def _invoke_with_rate_limit_retry(self, prompt: str, *, expected_module: str) -> Any:
        started_at = time.perf_counter()
        for attempt in range(1, self.rate_limit_max_attempts + 1):
            try:
                return self.llm.invoke([HumanMessage(content=prompt)])
            except Exception as exc:
                rate_limited = is_rate_limit_error(exc)
                if rate_limited and attempt < self.rate_limit_max_attempts:
                    self._sleep_before_rate_limit_retry(expected_module, attempt)
                    continue
                raise build_llm_request_error(
                    expected_module,
                    prompt,
                    started_at,
                    exc,
                    retryable=not rate_limited,
                    rate_limited=rate_limited,
                ) from exc

    async def _ainvoke_with_rate_limit_retry(self, prompt: str, *, expected_module: str) -> Any:
        started_at = time.perf_counter()
        for attempt in range(1, self.rate_limit_max_attempts + 1):
            try:
                return await self.llm.ainvoke([HumanMessage(content=prompt)])
            except AttributeError:
                raise
            except Exception as exc:
                rate_limited = is_rate_limit_error(exc)
                if rate_limited and attempt < self.rate_limit_max_attempts:
                    await self._sleep_before_rate_limit_retry_async(expected_module, attempt)
                    continue
                raise build_llm_request_error(
                    expected_module,
                    prompt,
                    started_at,
                    exc,
                    retryable=not rate_limited,
                    rate_limited=rate_limited,
                ) from exc

    def _sleep_before_rate_limit_retry(self, expected_module: str, attempt: int) -> None:
        with self._sync_rate_limit_lock:
            delay = self._rate_limit_retry_delay(attempt)
            logger.warning(
                f"{expected_module}: rate limited by LLM provider; "
                f"serialized retry in {delay:.1f}s "
                f"({attempt}/{self.rate_limit_max_attempts})"
            )
            time.sleep(delay)

    async def _sleep_before_rate_limit_retry_async(self, expected_module: str, attempt: int) -> None:
        async with self._async_rate_limit_lock:
            delay = self._rate_limit_retry_delay(attempt)
            logger.warning(
                f"{expected_module}: rate limited by LLM provider; "
                f"serialized retry in {delay:.1f}s "
                f"({attempt}/{self.rate_limit_max_attempts})"
            )
            await asyncio.sleep(delay)

    def _rate_limit_retry_delay(self, attempt: int) -> float:
        return self.rate_limit_retry_seconds * max(1, attempt)

    def _is_module_cacheable(self, module_name: str) -> bool:
        return self.module_cache_enabled and module_name in CACHEABLE_MODULES

    def _module_cache_key(self, module_name: str, prompt: str) -> str:
        if not self._is_module_cacheable(module_name):
            return ""
        payload = {
            "schema_version": MODULE_CACHE_SCHEMA_VERSION,
            "module_name": module_name,
            "model": getattr(self.llm, "model_name", None) or getattr(self.llm, "model", None),
            "base_url": str(getattr(self.llm, "openai_api_base", None) or ""),
            "extra_body": getattr(self.llm, "extra_body", None),
            "max_context_chars": self.max_context_chars,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8", errors="ignore")).hexdigest(),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()

    def _module_cache_path(self, module_name: str, cache_key: str) -> Path:
        safe_module = module_name.replace(".", "__")
        return self.module_cache_dir / safe_module / f"{cache_key}.json"

    def _read_module_cache(self, module_name: str, cache_key: str) -> Any | None:
        if not self._is_module_cacheable(module_name) or not cache_key:
            return None
        path = self._module_cache_path(module_name, cache_key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != MODULE_CACHE_SCHEMA_VERSION:
                return None
            if payload.get("module_name") != module_name:
                return None
            result = normalize_to_module_schema(module_name, payload.get("output"))
            issues = validate_module_output(module_name, result)
            if issues:
                logger.warning(f"module cache invalid: {module_name}, issue_count={len(issues)}")
                return None
            return strip_source_file_fields(result)
        except Exception as exc:
            logger.warning(f"module cache read failed: {module_name}, error={exc.__class__.__name__}: {exc}")
            return None

    def _write_module_cache(self, module_name: str, cache_key: str, result: Any) -> None:
        if not self._is_module_cacheable(module_name) or not cache_key:
            return
        result = strip_source_file_fields(result)
        issues = validate_module_output(module_name, result)
        if issues:
            logger.warning(f"module cache skipped: {module_name}, issue_count={len(issues)}")
            return
        path = self._module_cache_path(module_name, cache_key)
        payload = {
            "schema_version": MODULE_CACHE_SCHEMA_VERSION,
            "module_name": module_name,
            "output": result,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(path)
            logger.info(f"module cache written: {module_name}")
        except Exception as exc:
            logger.warning(f"module cache write failed: {module_name}, error={exc.__class__.__name__}: {exc}")

def parse_json_response(response: Any, *, expected_module: str) -> Any:
    """从大模型响应中解析 JSON；兼容 ```json 代码块。"""

    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "\n".join(str(item) for item in content)
    if not isinstance(content, str):
        raise LLMExtractionError(f"{expected_module}: 大模型响应不是文本")
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        candidate = extract_json_from_text(content)
        if candidate is None:
            raise LLMExtractionError(f"{expected_module}: 响应不是合法 JSON")
        return candidate


def run_async_from_sync(coro: Awaitable[Any]) -> Any:
    """在同步入口中运行异步协程。"""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    coro.close()
    raise RuntimeError("当前已有事件循环在运行，请使用对应的异步 workflow 方法。")


def build_primary_module_prompt(module_name: str, context: str, *, max_context_chars: int) -> str:
    """构建模块初抽 prompt；部分模块默认使用紧凑上下文。"""

    if module_name in COMPACT_FIRST_MODULES:
        compact_context = compact_context_for_retry(module_name, context, max_chars=min(max_context_chars, 6500))
        return build_compact_module_prompt(module_name, compact_context)
    return build_module_prompt(module_name, context[:max_context_chars])


RETRY_CONTEXT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "application_form_info.eligibility_and_constraints": (
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
    "application_form_info.pricing_info": ("资费", "套餐", "价格", "月付", "年付", "协议期", "语音", "增值", "权益", "折扣"),
    "supplementary_info.pricing_info": ("资费", "套餐", "价格", "月付", "年付", "协议期", "语音", "增值", "权益", "折扣"),
    "supplementary_info.application_materials": ("材料", "申请手续", "签字", "盖章", "复印件", "原件", "授权", "担保"),
}


def compact_context_for_retry(module_name: str, context: str, *, max_chars: int = 6000) -> str:
    """为超时重试保留高信号上下文行。"""

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
    """从非纯 JSON 文本中尽力提取 JSON 片段。"""

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


def is_rate_limit_error(error: Exception) -> bool:
    """判断是否为模型服务商侧的限流错误。"""

    error_name = error.__class__.__name__.lower()
    error_text = str(error).lower()
    return any(
        marker in error_name or marker in error_text
        for marker in ("ratelimit", "rate limit", "429", "limit_requests", "too many requests")
    )

def strip_source_file_fields(value: Any) -> Any:
    """缓存只保存业务字段，来源文件由合并阶段按当前文件重新补齐。"""

    if isinstance(value, list):
        return [strip_source_file_fields(item) for item in value]
    if isinstance(value, dict):
        result = deepcopy(value)
        result.pop("source_file", None)
        for key, item in list(result.items()):
            if isinstance(item, (dict, list)):
                result[key] = strip_source_file_fields(item)
        return result
    return value


def build_llm_request_error(
    expected_module: str,
    prompt: str,
    started_at: float,
    original_error: Exception,
    *,
    retryable: bool,
    rate_limited: bool,
) -> LLMExtractionError:
    """统一包装大模型请求异常，保留耗时和 prompt 长度。"""

    elapsed = time.perf_counter() - started_at
    return LLMExtractionError(
        f"{expected_module}: 大模型请求在 {elapsed:.1f}s 后失败；"
        f"prompt_chars={len(prompt)}; "
        f"original_error={original_error.__class__.__name__}: {original_error}",
        retryable=retryable,
        rate_limited=rate_limited,
    )


def empty_module_output(module_name: str) -> Any:
    """模块失败时生成空输出，保证主流程可继续。"""

    schema = get_module_json_schema(module_name)
    return {} if schema.get("type") == "object" else []


def validate_module_output(module_name: str, result: Any) -> list[dict[str, Any]]:
    """按模块 schema 校验单个模块输出。"""

    schema = get_module_json_schema(module_name)
    return validate_against_schema(result, schema)


def format_validation_issues(issues: list[dict[str, Any]], *, limit: int = 40) -> str:
    """把校验问题格式化成 repair/rework prompt 可读文本。"""

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
