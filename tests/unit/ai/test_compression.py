import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from reminder_agent.ai.compression import (
    CompressionConfig,
    CompressionError,
    count_text_tokens,
    prepare_compression,
    split_conversation_exchanges,
    truncate_text_tokens,
    update_context_summary,
)


def _config(**overrides):
    values = {
        "token_threshold": 100_000,
        "target_tokens": 90_000,
        "step_interval": 100,
        "turn_trigger": 4,
        "turn_target": 3,
        "keep_recent_turns": 1,
        "summary_max_tokens": 100,
        "summary_input_tokens": 1_000,
        "tool_payload_max_tokens": 20,
    }
    values.update(overrides)
    return CompressionConfig(**values)


def test_split_conversation_exchanges_keeps_tool_round_with_user_turn():
    messages = [
        HumanMessage(content="Create it"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "create_task",
                    "args": {"title": "Read"},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content='{"status":"success"}',
            name="create_task",
            tool_call_id="call-1",
        ),
        AIMessage(content="Created."),
        HumanMessage(content="Thanks"),
    ]

    exchanges = split_conversation_exchanges(messages)

    assert [len(exchange) for exchange in exchanges] == [4, 1]


def test_prepare_uses_turn_trigger_and_truncates_retained_old_tools():
    large_value = "detail " * 200
    messages = [
        HumanMessage(content="Oldest question"),
        AIMessage(content="Oldest answer"),
        HumanMessage(content="Show my tasks"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "list_tasks",
                    "args": {"filter": large_value},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps({"status": "success", "items": large_value}),
            name="list_tasks",
            tool_call_id="call-1",
        ),
        AIMessage(content="Here they are."),
        HumanMessage(content="Older follow-up"),
        AIMessage(content="Older response"),
        HumanMessage(content="Current request"),
    ]

    prepared = prepare_compression(
        messages=messages,
        context_summary="",
        compression_step_count=0,
        system_prompt="system",
        tool_schema_tokens=0,
        config=_config(),
    )

    assert prepared is not None
    assert prepared["trigger_reasons"] == ["turns"]
    assert prepared["turns_removed"] == 1
    assert prepared["summary_exchanges"][0][0].content == "Oldest question"
    assert prepared["messages"][0].content == "Show my tasks"

    call = prepared["messages"][1].tool_calls[0]
    result = prepared["messages"][2]
    assert call["id"] == "call-1"
    assert result.tool_call_id == "call-1"
    assert len(str(call["args"])) < len(large_value)
    assert len(str(result.content)) < len(large_value)
    assert prepared["messages"][-1].content == "Current request"


def test_prepare_can_trigger_from_persisted_step_count():
    prepared = prepare_compression(
        messages=[
            HumanMessage(content="Old request"),
            AIMessage(content="Old response"),
            HumanMessage(content="Current request"),
        ],
        context_summary="",
        compression_step_count=3,
        system_prompt="system",
        tool_schema_tokens=0,
        config=_config(step_interval=3, turn_trigger=10, turn_target=5),
    )

    assert prepared is not None
    assert prepared["trigger_reasons"] == ["steps"]
    assert prepared["turns_removed"] == 1


def test_prepare_can_trigger_from_token_threshold():
    prepared = prepare_compression(
        messages=[
            HumanMessage(content="old " * 1_000),
            AIMessage(content="old answer"),
            HumanMessage(content="Current request"),
        ],
        context_summary="",
        compression_step_count=0,
        system_prompt="system",
        tool_schema_tokens=0,
        config=_config(
            token_threshold=300,
            target_tokens=200,
            turn_trigger=10,
            turn_target=5,
            summary_max_tokens=20,
        ),
    )

    assert prepared is not None
    assert prepared["trigger_reasons"] == ["tokens"]
    assert prepared["turns_removed"] == 1
    assert prepared["messages"][0].content == "Current request"


def test_prepare_returns_none_when_no_trigger_fires():
    prepared = prepare_compression(
        messages=[HumanMessage(content="Current request")],
        context_summary="",
        compression_step_count=0,
        system_prompt="system",
        tool_schema_tokens=0,
        config=_config(turn_trigger=10, turn_target=5),
    )

    assert prepared is None


def test_prepare_rejects_an_orphaned_tool_result():
    with pytest.raises(CompressionError, match="no matching tool call"):
        prepare_compression(
            messages=[
                HumanMessage(content="Broken history"),
                ToolMessage(content="{}", tool_call_id="missing"),
            ],
            context_summary="",
            compression_step_count=0,
            system_prompt="system",
            tool_schema_tokens=0,
            config=_config(),
        )


class _SummaryModel:
    async def ainvoke(self, _messages):
        return AIMessage(content="updated " * 100)


def test_context_summary_is_bounded():
    summary = asyncio.run(
        update_context_summary(
            model=_SummaryModel(),
            existing_summary="Earlier context",
            exchanges=[[HumanMessage(content="An old conversation")]],
            config=_config(summary_max_tokens=12),
        )
    )

    assert "truncated" in summary
    assert count_text_tokens(summary) <= 12


def test_token_helpers_accept_special_token_literals_from_user_text():
    value = "Keep this literal: <|endoftext|> " * 20

    assert count_text_tokens(value) > 10
    assert count_text_tokens(truncate_text_tokens(value, 10)) <= 10


def test_compression_config_rejects_inconsistent_targets():
    with pytest.raises(ValueError, match="target_tokens"):
        _config(token_threshold=100, target_tokens=100)
