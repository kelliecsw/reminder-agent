import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from reminder_agent.ai.model import create_model, probe_model
from reminder_agent.config import Settings


def test_kimi_defaults_to_k3_with_provider_requirements():
    settings = Settings(
        _env_file=None,
        LLM_API_KEY="secret",
        llm_provider="kimi",
    )
    with patch("reminder_agent.ai.model.ChatOpenAI") as chat:
        create_model(settings)

    kwargs = chat.call_args.kwargs
    assert kwargs["model"] == "k3"
    assert kwargs["base_url"] == "https://api.kimi.com/coding/v1"
    assert kwargs["temperature"] == 0.6
    assert kwargs["default_headers"] == {"User-Agent": "KimiCLI/1.3"}
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def test_cliproxy_gpt5_uses_session_and_effort_without_temperature():
    settings = Settings(
        _env_file=None,
        llm_provider="proxy",
        llm_model="gpt-5.5",
        llm_temperature=0.2,
        llm_reasoning_effort="HIGH",
    )
    with patch("reminder_agent.ai.model.ChatOpenAI") as chat:
        create_model(settings)

    kwargs = chat.call_args.kwargs
    assert kwargs["api_key"] == "cliproxyapi"
    assert kwargs["base_url"] == "http://cli-proxy-api:8317/v1"
    assert kwargs["default_headers"]["Session_id"].startswith("reminder-agent-")
    assert kwargs["reasoning_effort"] == "high"
    assert "temperature" not in kwargs


def test_cliproxy_prefers_llm_api_key_over_default():
    settings = Settings(
        _env_file=None,
        llm_provider="cliproxyapi",
        LLM_API_KEY="custom-key",
    )
    with patch("reminder_agent.ai.model.ChatOpenAI") as chat:
        create_model(settings)

    assert chat.call_args.kwargs["api_key"] == "custom-key"


def test_cliproxy_non_reasoning_model_keeps_temperature():
    settings = Settings(
        _env_file=None,
        llm_provider="cliproxyapi",
        llm_model="plain-chat-model",
        llm_temperature=0.3,
    )
    with patch("reminder_agent.ai.model.ChatOpenAI") as chat:
        create_model(settings)

    assert chat.call_args.kwargs["temperature"] == 0.3
    assert "reasoning_effort" not in chat.call_args.kwargs


def test_probe_model_accepts_nonempty_response():
    model = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value=SimpleNamespace(content="OK", tool_calls=[]),
        ),
    )

    asyncio.run(probe_model(model))

    model.ainvoke.assert_awaited_once()


def test_probe_model_rejects_empty_response():
    model = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value=SimpleNamespace(content="", tool_calls=[]),
        ),
    )

    with pytest.raises(RuntimeError, match="empty response"):
        asyncio.run(probe_model(model))


def test_probe_model_rejects_tool_calls():
    model = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value=SimpleNamespace(
                content="OK",
                tool_calls=[{"name": "unexpected"}],
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="unexpectedly returned tool calls"):
        asyncio.run(probe_model(model))


def test_openrouter_defaults_include_usage_and_provider_preferences():
    settings = Settings(
        _env_file=None,
        LLM_API_KEY="secret",
        llm_provider="router",
    )
    with patch("reminder_agent.ai.model.ChatOpenAI") as chat:
        create_model(settings)

    kwargs = chat.call_args.kwargs
    assert kwargs["model"] == "anthropic/claude-sonnet-4.6"
    assert kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert kwargs["api_key"] == "secret"
    assert kwargs["extra_body"] == {
        "usage": {"include": True},
        "provider": {"allow_fallbacks": True, "data_collection": "deny"},
    }
    assert "reasoning_effort" not in kwargs
    assert "default_headers" not in kwargs


def test_openrouter_custom_model_and_base_url_override():
    settings = Settings(
        _env_file=None,
        LLM_API_KEY="secret",
        llm_provider="openrouter",
        llm_model="openai/gpt-5.1",
        LLM_API_BASE="https://proxy.example.com/v1",
    )
    with patch("reminder_agent.ai.model.ChatOpenAI") as chat:
        create_model(settings)

    kwargs = chat.call_args.kwargs
    assert kwargs["model"] == "openai/gpt-5.1"
    assert kwargs["base_url"] == "https://proxy.example.com/v1"


def test_openrouter_requires_api_key():
    settings = Settings(_env_file=None, llm_provider="openrouter")

    with pytest.raises(RuntimeError, match="LLM_API_KEY is required"):
        create_model(settings)


def test_openrouter_forwards_temperature():
    settings = Settings(
        _env_file=None,
        LLM_API_KEY="secret",
        llm_provider="openrouter",
        llm_temperature=0.2,
    )
    with patch("reminder_agent.ai.model.ChatOpenAI") as chat:
        create_model(settings)

    assert chat.call_args.kwargs["temperature"] == 0.2

