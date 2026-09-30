"""Deterministic system-prompt construction."""

from pathlib import Path
from typing import Any


_TEMPLATE = (Path(__file__).parent / "prompts" / "system.md").read_text(
    encoding="utf-8"
)


def build_system_prompt(state: dict[str, Any]) -> str:
    return _TEMPLATE.format(
        timezone=state["timezone"],
    )


__all__ = ["build_system_prompt"]
