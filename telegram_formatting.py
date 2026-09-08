"""Small plain-text formatter for Telegram readability."""

from __future__ import annotations

import re


def format_telegram_message(text: str, limit: int = 4000) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"```(?:[a-zA-Z0-9_+-]+)?\n?", "", value)
    value = value.replace("```", "")
    value = re.sub(r"^#{1,6}\s*", "", value, flags=re.MULTILINE)
    value = re.sub(r"^[ \t]*[-*][ \t]+", "• ", value, flags=re.MULTILINE)
    value = re.sub(r"^[ \t]*(\d+)[.)][ \t]+", r"\1. ", value, flags=re.MULTILINE)
    value = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", r"\1 — \2", value)
    value = re.sub(r"^\s*[─—-]{3,}\s*$", "", value, flags=re.MULTILINE)
    value = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", value)
    value = re.sub(r"__([^_\n]+)__", r"\1", value)
    value = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", value)
    value = re.sub(r"`([^`\n]+)`", r"\1", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = "\n".join(line.rstrip() for line in value.splitlines()).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def split_telegram_message(text: str, limit: int = 4000) -> list[str]:
    """Split on paragraph/line boundaries; Telegram rejects messages over 4096 chars."""
    value = format_telegram_message(text, limit=10_000_000)
    if len(value) <= limit:
        return [value]
    chunks: list[str] = []
    while value:
        cut = min(limit, len(value))
        if cut < len(value):
            boundary = max(value.rfind("\n\n", 0, cut), value.rfind("\n", 0, cut))
            if boundary > limit // 2:
                cut = boundary
        chunk, value = value[:cut].strip(), value[cut:].strip()
        if chunk:
            chunks.append(chunk)
    return chunks
