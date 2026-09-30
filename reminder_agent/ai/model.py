"""OpenAI-compatible model construction for Kimi Code and CLIProxyAPI."""

from __future__ import annotations

import uuid

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from reminder_agent.config import Settings


_KIMI_DEFAULT_MODEL = "k3"
_KIMI_DEFAULT_BASE_URL = "https://api.kimi.com/coding/v1"
_CLIPROXY_DEFAULT_MODEL = "gpt-5.6-sol"
_CLIPROXY_DEFAULT_BASE_URL = "http://localhost:8317/v1"

_SESSION_ID = f"reminder-agent-{uuid.uuid4().hex[:16]}"


def create_model(settings: Settings) -> ChatOpenAI:
    if settings.llm_provider == "kimicode":
        return _create_kimi_model(settings)
    if settings.llm_provider == "cliproxyapi":
        return _create_cliproxy_model(settings)
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


def _create_kimi_model(settings: Settings) -> ChatOpenAI:
    if settings.kimicode_api_key is None:
        raise RuntimeError("KIMICODE_API_KEY is required for the Kimi Code provider.")
    return ChatOpenAI(
        model=settings.llm_model or _KIMI_DEFAULT_MODEL,
        api_key=settings.kimicode_api_key.get_secret_value(),
        base_url=settings.llm_api_base or _KIMI_DEFAULT_BASE_URL,
        temperature=0.6,
        max_tokens=settings.llm_max_tokens,
        max_retries=0,
        timeout=settings.llm_timeout_seconds,
        default_headers={"User-Agent": "KimiCLI/1.3"},
        extra_body={"thinking": {"type": "disabled"}},
    )


def _create_cliproxy_model(settings: Settings) -> ChatOpenAI:
    model = settings.llm_model or _CLIPROXY_DEFAULT_MODEL
    kwargs = {
        "model": model,
        "api_key": settings.cliproxyapi_api_key.get_secret_value(),
        "base_url": settings.llm_api_base or _CLIPROXY_DEFAULT_BASE_URL,
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


async def probe_model(model: ChatOpenAI) -> None:
    response = await model.ainvoke(
        [HumanMessage(content="Reply with exactly OK. This is a startup health check.")]
    )
    if response.tool_calls:
        raise RuntimeError("LLM startup probe unexpectedly returned tool calls.")
    if not str(response.content).strip():
        raise RuntimeError("LLM startup probe returned an empty response.")


__all__ = ["create_model", "probe_model"]

