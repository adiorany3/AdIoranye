"""Small plain-text formatter for Telegram readability."""

from __future__ import annotations

import re


def format_telegram_message(text: str, limit: int = 4000) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"```(?:[a-zA-Z0-9_+-]+)?\n?", "", value)
    value = value.replace("```", "")
    value = re.sub(r"^#{1,6}\s*", "", value, flags=re.MULTILINE)
    value = re.sub(r"^\s*[-*]\s+", "• ", value, flags=re.MULTILINE)
    value = re.sub(r"^\s*(\d+)[.)]\s+", r"\1. ", value, flags=re.MULTILINE)
    value = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", value)
    value = re.sub(r"__([^_\n]+)__", r"\1", value)
    value = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", value)
    value = re.sub(r"`([^`\n]+)`", r"\1", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = "\n".join(line.rstrip() for line in value.splitlines()).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"
