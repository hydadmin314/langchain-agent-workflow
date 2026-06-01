from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from collections.abc import Awaitable, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from agent.product_doc_agent.schema_normalizer import normalize_to_module_schema
from agent.product_doc_agent.validator import validate_against_schema
from config.llm_config import get_llm
from config.settings import (
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
        self.llm = llm or get_llm(temperature=0)
        self.max_context_chars = max_context_chars
        self.max_concurrency = max(1, max_concurrency or len(EXTRACTION_MODULES))
        self.rate_limit_max_attempts = max(1, PRODUCT_DOC_AGENT_RATE_LIMIT_MAX_ATTEMPTS)
        self.rate_limit_retry_seconds = max(0.0, PRODUCT_DOC_AGENT_RATE_LIMIT_RETRY_SECONDS)
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
    ) -> dict[str, Any]:
        """并发抽取多个 schema 模块。"""

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
        try:
            result = await self._ainvoke_json(prompt, expected_module=f"{module_name}.rework")
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

    if module_name in {"document_info", "parties_and_application", "base_package"}:
        return {}
    return []


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
