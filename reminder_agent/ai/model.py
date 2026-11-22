"""OpenAI-compatible model construction for the supported LLM providers."""

from __future__ import annotations

import uuid

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from reminder_agent.ai.providers import (
    PROVIDERS,
    normalize_provider_name,
    resolve_api_key,
    resolve_base_url,
)
from reminder_agent.config import Settings

_SESSION_ID = f"reminder-agent-{uuid.uuid4().hex[:16]}"


def create_model(settings: Settings) -> ChatOpenAI:
    provider = normalize_provider_name(settings.llm_provider)
    builder = _MODEL_BUILDERS.get(provider)
    if builder is None:
        known = ", ".join(sorted(_MODEL_BUILDERS))
        raise ValueError(
            f"Unsupported LLM provider: {settings.llm_provider}. "
            f"Known providers: {known}"
        )
    return builder(settings)


def _create_kimi_model(settings: Settings) -> ChatOpenAI:
    api_key = resolve_api_key(settings, "kimicode")
    if api_key is None:
        raise RuntimeError("LLM_API_KEY is required for the Kimi Code provider.")
    return ChatOpenAI(
        model=settings.llm_model or PROVIDERS["kimicode"].default_model,
        api_key=api_key.get_secret_value(),
        base_url=resolve_base_url(settings, "kimicode"),
        temperature=0.6,
        max_tokens=settings.llm_max_tokens,
        max_retries=0,
        timeout=settings.llm_timeout_seconds,
        default_headers={"User-Agent": "KimiCLI/1.3"},
        extra_body={"thinking": {"type": "disabled"}},
    )


def _create_cliproxy_model(settings: Settings) -> ChatOpenAI:
    model = settings.llm_model or PROVIDERS["cliproxyapi"].default_model
    api_key = resolve_api_key(settings, "cliproxyapi")
    kwargs: dict[str, object] = {
        "model": model,
        "api_key": api_key.get_secret_value() if api_key is not None else "",
        "base_url": resolve_base_url(settings, "cliproxyapi"),
        "max_tokens": settings.llm_max_tokens,
        "max_retries": 0,
        "timeout": settings.llm_timeout_seconds,
        "default_headers": {"Session_id": _SESSION_ID},
    }
    if model.strip().lower().startswith("gpt-5"):
        if settings.llm_reasoning_effort.strip():
            kwargs["reasoning_effort"] = settings.llm_reasoning_effort.strip().lower()
    else:
        kwargs["temperature"] = settings.llm_temperature
    return ChatOpenAI(**kwargs)


def _create_openrouter_model(settings: Settings) -> ChatOpenAI:
    api_key = resolve_api_key(settings, "openrouter")
    if api_key is None:
        raise RuntimeError(
            "LLM_API_KEY is required for the OpenRouter provider."
        )
    return ChatOpenAI(
        model=settings.llm_model or PROVIDERS["openrouter"].default_model,
        api_key=api_key.get_secret_value(),
        base_url=resolve_base_url(settings, "openrouter"),
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        max_retries=0,
        timeout=settings.llm_timeout_seconds,
        extra_body={
            "usage": {"include": True},
            "provider": {"allow_fallbacks": True, "data_collection": "deny"},
        },
    )


_MODEL_BUILDERS = {
    "kimicode": _create_kimi_model,
    "cliproxyapi": _create_cliproxy_model,
    "openrouter": _create_openrouter_model,
}


async def probe_model(model: ChatOpenAI) -> None:
    response = await model.ainvoke(
        [HumanMessage(content="Reply with exactly OK. This is a startup health check.")]
    )
    if response.tool_calls:
        raise RuntimeError("LLM startup probe unexpectedly returned tool calls.")
    if not str(response.content).strip():
        raise RuntimeError("LLM startup probe returned an empty response.")


__all__ = ["create_model", "probe_model"]
