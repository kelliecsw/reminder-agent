"""Sanitization for untrusted content before it reaches the model."""

from __future__ import annotations

import re
from typing import Any


_RESERVED_MARKER_RE = re.compile(
    r"</?system_message(?:\s[^>]*)?>",
    re.IGNORECASE,
)
_UNSAFE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_untrusted_content(value: Any) -> Any:
    """Recursively remove reserved markers and unsafe control characters."""

    if isinstance(value, str):
        without_markers = _RESERVED_MARKER_RE.sub("", value)
        return _UNSAFE_CONTROL_RE.sub("", without_markers)
    if isinstance(value, list):
        return [sanitize_untrusted_content(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_untrusted_content(item) for item in value)
    if isinstance(value, dict):
        return {
            key: sanitize_untrusted_content(item)
            for key, item in value.items()
        }
    return value


__all__ = ["sanitize_untrusted_content"]
