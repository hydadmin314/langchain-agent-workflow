from langchain_openai import ChatOpenAI
from config.settings import (
    LLM_MAX_RETRIES,
    LLM_MODEL,
    LLM_TIMEOUT,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    PRODUCT_DOC_OCR_LLM_API_KEY,
    PRODUCT_DOC_OCR_LLM_BASE_URL,
    PRODUCT_DOC_OCR_LLM_MAX_RETRIES,
    PRODUCT_DOC_OCR_LLM_MODEL,
    PRODUCT_DOC_OCR_LLM_TIMEOUT,
    PRODUCT_DOC_TEXT_LLM_API_KEY,
    PRODUCT_DOC_TEXT_LLM_BASE_URL,
    PRODUCT_DOC_TEXT_LLM_DISABLE_THINKING,
    PRODUCT_DOC_TEXT_LLM_MAX_RETRIES,
    PRODUCT_DOC_TEXT_LLM_MODEL,
    PRODUCT_DOC_TEXT_LLM_TIMEOUT,
)


def _build_chat_openai(
    *,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
    temperature: float,
    timeout: float,
    max_retries: int,
    extra_body: dict | None = None,
) -> ChatOpenAI:
    kwargs = {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "temperature": temperature,
        "timeout": timeout,
        "max_retries": max_retries,
    }
    if extra_body:
        kwargs["extra_body"] = extra_body
    return ChatOpenAI(
        **kwargs,
    )


def get_llm(temperature: float = 0.1) -> ChatOpenAI:
    return _build_chat_openai(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        model=LLM_MODEL,
        temperature=temperature,
        timeout=LLM_TIMEOUT,
        max_retries=LLM_MAX_RETRIES,
    )


def get_product_doc_text_llm(temperature: float = 0) -> ChatOpenAI:
    extra_body = {"thinking": {"type": "disabled"}} if PRODUCT_DOC_TEXT_LLM_DISABLE_THINKING else None
    return _build_chat_openai(
        api_key=PRODUCT_DOC_TEXT_LLM_API_KEY,
        base_url=PRODUCT_DOC_TEXT_LLM_BASE_URL,
        model=PRODUCT_DOC_TEXT_LLM_MODEL,
        temperature=temperature,
        timeout=PRODUCT_DOC_TEXT_LLM_TIMEOUT,
        max_retries=PRODUCT_DOC_TEXT_LLM_MAX_RETRIES,
        extra_body=extra_body,
    )


def get_product_doc_ocr_llm(temperature: float = 0) -> ChatOpenAI:
    return _build_chat_openai(
        api_key=PRODUCT_DOC_OCR_LLM_API_KEY,
        base_url=PRODUCT_DOC_OCR_LLM_BASE_URL,
        model=PRODUCT_DOC_OCR_LLM_MODEL,
        temperature=temperature,
        timeout=PRODUCT_DOC_OCR_LLM_TIMEOUT,
        max_retries=PRODUCT_DOC_OCR_LLM_MAX_RETRIES,
    )
