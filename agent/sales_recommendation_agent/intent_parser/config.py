from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - 当前项目已安装 python-dotenv，这里只是兜底。
    load_dotenv = None


@dataclass(frozen=True)
class SalesRecommendationSettings:
    """销售推荐 Agent 的配置。

    这里优先复用项目已有的 OpenAI-compatible 配置名，避免再引入一套新的环境变量。
    """

    llm_api_key: str | None
    llm_model: str
    llm_base_url: str | None
    llm_temperature: float
    llm_max_tokens: int
    llm_timeout_seconds: float
    llm_enable_thinking: bool | None
    llm_enable_search: bool | None
    per_user_bandwidth_mbps: float
    high_bandwidth_threshold_mbps: int

    @classmethod
    def from_env(cls) -> "SalesRecommendationSettings":
        if load_dotenv:
            load_dotenv()

        return cls(
            llm_api_key=os.getenv("SALES_RECOMMENDATION_LLM_API_KEY")
            or os.getenv("LLM_API_KEY")
            or os.getenv("OPENAI_API_KEY"),
            llm_model=os.getenv("SALES_RECOMMENDATION_LLM_MODEL")
            or os.getenv("LLM_MODEL")
            or "gpt-4o-mini",
            llm_base_url=os.getenv("SALES_RECOMMENDATION_LLM_BASE_URL")
            or os.getenv("LLM_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or None,
            llm_temperature=float(os.getenv("SALES_RECOMMENDATION_LLM_TEMPERATURE", "0")),
            llm_max_tokens=int(os.getenv("SALES_RECOMMENDATION_LLM_MAX_TOKENS", "500")),
            llm_timeout_seconds=float(
                os.getenv("SALES_RECOMMENDATION_LLM_TIMEOUT")
                or os.getenv("LLM_TIMEOUT")
                or "60"
            ),
            llm_enable_thinking=_parse_optional_bool(
                os.getenv("SALES_RECOMMENDATION_LLM_ENABLE_THINKING"),
                default=False,
            ),
            llm_enable_search=_parse_optional_bool(
                os.getenv("SALES_RECOMMENDATION_LLM_ENABLE_SEARCH"),
                default=False,
            ),
            per_user_bandwidth_mbps=float(os.getenv("PER_USER_BANDWIDTH_MBPS", "1")),
            high_bandwidth_threshold_mbps=int(os.getenv("HIGH_BANDWIDTH_THRESHOLD_MBPS", "100")),
        )


def _parse_optional_bool(value: str | None, *, default: bool | None = None) -> bool | None:
    """解析可选布尔环境变量，空值表示不传给模型服务。"""

    if value is None or value.strip() == "":
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")
