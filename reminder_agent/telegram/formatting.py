from __future__ import annotations

import logging
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml, unescapeAll
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token


logger = logging.getLogger(__name__)


class _TelegramRichRenderer(RendererHTML):
    """Render GFM as the HTML subset accepted by Telegram Rich Messages."""

    def table_open(
        self,
        tokens: list[Token],
        idx: int,
        options: dict[str, Any],
        env: dict[str, Any],
    ) -> str:
        return "<table bordered striped compact>\n"

    def thead_open(
        self,
        tokens: list[Token],
        idx: int,
        options: dict[str, Any],
        env: dict[str, Any],
    ) -> str:
        return ""

    def thead_close(
        self,
        tokens: list[Token],
        idx: int,
        options: dict[str, Any],
        env: dict[str, Any],
    ) -> str:
        return ""

    def tbody_open(
        self,
        tokens: list[Token],
        idx: int,
        options: dict[str, Any],
        env: dict[str, Any],
    ) -> str:
        return ""

    def tbody_close(
        self,
        tokens: list[Token],
        idx: int,
        options: dict[str, Any],
        env: dict[str, Any],
    ) -> str:
        return ""

    def th_open(
        self,
        tokens: list[Token],
        idx: int,
        options: dict[str, Any],
        env: dict[str, Any],
    ) -> str:
        return _table_cell_open(tokens[idx], "th")

    def td_open(
        self,
        tokens: list[Token],
        idx: int,
        options: dict[str, Any],
        env: dict[str, Any],
    ) -> str:
        return _table_cell_open(tokens[idx], "td")

    def fence(
        self,
        tokens: list[Token],
        idx: int,
        options: dict[str, Any],
        env: dict[str, Any],
    ) -> str:
        token = tokens[idx]
        info = unescapeAll(token.info).strip() if token.info else ""
        language = info.split(maxsplit=1)[0].lower() if info else ""
        if language == "math":
            formula = escapeHtml(token.content.rstrip("\n"))
            return f"<tg-math-block>{formula}</tg-math-block>\n"
        return super().fence(tokens, idx, options, env)


def _table_cell_open(token: Token, tag: str) -> str:
    style = token.attrGet("style")
    align = None
    if style and style.startswith("text-align:"):
        candidate = style.removeprefix("text-align:").strip().lower()
        if candidate in {"left", "center", "right"}:
            align = candidate
    return f'<{tag} align="{align}">' if align else f"<{tag}>"


_MARKDOWN = MarkdownIt(
    "js-default",
    {
        "html": False,
        "typographer": False,
    },
    renderer_cls=_TelegramRichRenderer,
)


def render_markdown(source: str) -> dict[str, str] | None:
    """Render ordinary GFM into a persisted Telegram Rich Message payload."""
    try:
        html = _MARKDOWN.render(source).rstrip()
    except Exception:
        logger.warning(
            "Could not render Telegram Rich HTML; falling back to plain text.",
            exc_info=True,
        )
        return None
    return {"html": html} if html else None


__all__ = ["render_markdown"]
