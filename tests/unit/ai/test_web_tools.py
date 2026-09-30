import asyncio
from datetime import UTC, datetime

from reminder_agent.ai.tools import get_web_tools
from reminder_agent.ai.tools import web as web_module
from reminder_agent.ai.tools.registry import (
    ToolContext,
    create_tool_registry,
)
from reminder_agent.ai.tools.web import web_search
from reminder_agent.services.goals import GoalService
from reminder_agent.services.reminders import ReminderService
from reminder_agent.services.tasks import TaskService


class FakeTavilyClient:
    def __init__(self, response=None, error=None):
        self.response = response or {"results": []}
        self.error = error
        self.calls = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def test_web_tool_is_always_discovered():
    assert get_web_tools() == [web_search]


def test_tavily_tool_uses_bounded_basic_search_and_normalizes_results(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-secret")
    monkeypatch.setenv("WEB_SEARCH_MAX_RESULTS", "5")
    monkeypatch.setenv("WEB_SEARCH_TIMEOUT_SECONDS", "15")
    client = FakeTavilyClient(
        {
            "answer": "provider-generated answer must not be returned",
            "raw_content": "raw content must not be returned",
            "results": [
                {
                    "title": "Forecast",
                    "url": "https://example.com/weather",
                    "content": "Sunny",
                    "published_date": "2026-09-11",
                },
                {
                    "title": "Unsafe URL",
                    "url": "file:///etc/passwd",
                    "content": "discard me",
                },
            ],
        }
    )
    factories = []

    def factory(**kwargs):
        factories.append(kwargs)
        return client

    monkeypatch.setattr(web_module, "AsyncTavilyClient", factory)
    result = asyncio.run(
        web_search.ainvoke(
            {
                "query": "weather tomorrow",
                "topic": "general",
                "max_results": 5,
            }
        )
    )

    assert factories == [{"api_key": "tvly-secret"}]
    assert client.closed is True
    assert client.calls == [
        {
            "query": "weather tomorrow",
            "topic": "general",
            "search_depth": "basic",
            "max_results": 5,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_usage": False,
            "safe_search": True,
            "timeout": 15.0,
        }
    ]
    assert result == {
        "untrusted_web_content": True,
        "provider": "tavily",
        "query": "weather tomorrow",
        "results": [
            {
                "title": "Forecast",
                "url": "https://example.com/weather",
                "content": "Sunny",
                "published_date": "2026-09-11",
            }
        ],
    }
    assert web_search.metadata["execution_kind"] == "external_read"
    assert "runtime" not in web_search.tool_call_schema.model_fields


def test_tavily_tool_bounds_provider_content(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-secret")
    client = FakeTavilyClient(
        {
            "results": [
                {
                    "title": "Title",
                    "url": "https://example.com/result",
                    "content": "x" * 1_300,
                }
            ]
        }
    )
    monkeypatch.setattr(
        web_module,
        "AsyncTavilyClient",
        lambda **_: client,
    )

    result = asyncio.run(web_search.ainvoke({"query": "test"}))

    assert len(result["results"][0]["content"]) == 1_200


def test_registry_registers_web_tool_that_uses_environment(
    database,
    user_id,
    monkeypatch,
):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-secret")
    client = FakeTavilyClient()
    monkeypatch.setattr(
        web_module,
        "AsyncTavilyClient",
        lambda **_: client,
    )
    registry = create_tool_registry(
        tasks=TaskService(database),
        goals=GoalService(database),
        reminders=ReminderService(database),
    )

    result = asyncio.run(
        registry.aexecute(
            name="web_search",
            arguments={"query": "Tokyo Marathon 2027 date"},
            context=ToolContext(
                user_id=user_id,
                timezone="Asia/Singapore",
                now_utc=datetime(2026, 9, 13, tzinfo=UTC),
            ),
        )
    )

    assert "web_search" in {tool.name for tool in registry.schemas()}
    assert result["status"] == "success"
    assert client.calls[0]["query"] == "Tokyo Marathon 2027 date"
