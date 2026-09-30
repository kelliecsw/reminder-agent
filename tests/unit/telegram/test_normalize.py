from types import SimpleNamespace

from reminder_agent.telegram.normalize import TelegramInputKind, normalize_update


def _update(*, text=None, callback_data=None):
    query = (
        SimpleNamespace(data=callback_data)
        if callback_data is not None
        else None
    )
    message = SimpleNamespace(text=text) if text is not None else None
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=456, type="private"),
        effective_message=message,
        callback_query=query,
    )


def test_normalizes_command_text_and_callback():
    command = normalize_update(_update(text="/tasks"))
    text = normalize_update(_update(text="Remind me tomorrow"))
    callback = normalize_update(_update(callback_data="d:42:done"))

    assert command.kind == TelegramInputKind.COMMAND
    assert command.text == "/tasks"
    assert text.kind == TelegramInputKind.TEXT
    assert text.text == "Remind me tomorrow"
    assert callback.kind == TelegramInputKind.CALLBACK
    assert callback.callback_data == "d:42:done"
