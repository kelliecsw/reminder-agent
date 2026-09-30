from unittest.mock import patch

from reminder_agent.ai.model import create_model
from reminder_agent.config import Settings


def test_kimi_defaults_to_k3_with_provider_requirements():
    settings = Settings(
        _env_file=None,
        KIMICODE_API_KEY="secret",
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
    assert kwargs["base_url"] == "http://localhost:8317/v1"
    assert kwargs["default_headers"]["Session_id"].startswith("reminder-agent-")
    assert kwargs["reasoning_effort"] == "high"
    assert "temperature" not in kwargs


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

