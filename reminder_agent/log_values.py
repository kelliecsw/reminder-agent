from __future__ import annotations

import json
import logging
from typing import Any


INTERACTION_SEPARATOR = "=" * 60


def private_text(value: Any, *, enabled: bool) -> str:
    """Return private content for a readable log block, or redact it."""
    if not enabled:
        return "<redacted>"
    return str(value)


def log_interaction(
    logger: logging.Logger,
    *,
    heading: str,
    content: str,
    metadata: str,
    result: str | None = None,
    level: int = logging.INFO,
) -> None:
    """Log one complete human-readable interaction event."""
    logger.log(level, "")
    logger.log(level, INTERACTION_SEPARATOR)
    logger.log(level, "[TELEGRAM] %s", heading)
    logger.log(level, "%s", content)
    logger.log(level, "[METADATA] %s", metadata)
    if result is not None:
        logger.log(level, "[RESULT] %s", result)
    logger.log(level, INTERACTION_SEPARATOR)


def keyboard_labels(keyboard: dict[str, Any] | None) -> str:
    if keyboard is None:
        return "[]"
    labels = []
    for row in keyboard.get("inline_keyboard", []):
        for button in row:
            label = button.get("text") if isinstance(button, dict) else None
            if isinstance(label, str):
                labels.append(label)
    return json.dumps(labels, ensure_ascii=False)


__all__ = [
    "INTERACTION_SEPARATOR",
    "keyboard_labels",
    "log_interaction",
    "private_text",
]
