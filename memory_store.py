"""Simple local JSON memory store for Adioranye AI.

This file is included so the ZIP can run even if your old `memory_store.py` is not
present. It supports the methods used by app.py and telegram_service.py.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

try:
    from zoneinfo import ZoneInfo
    WIB_TZ = ZoneInfo("Asia/Jakarta")
except Exception:  # pragma: no cover
    WIB_TZ = None


def _now_text() -> str:
    if WIB_TZ:
        return datetime.now(WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


class MemoryStore:
    def __init__(self, path: str = "assistant_memory.json") -> None:
        self.path = Path(path or "assistant_memory.json")
        self.path.parent.mkdir(parents=True, exist_ok=True) if self.path.parent != Path('.') else None
        if not self.path.exists():
            self._write([])

    def _read(self) -> List[Dict[str, Any]]:
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = data.get("items", [])
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except Exception:
            return []
        return []

    def _write(self, items: List[Dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def add(self, text: str, source: str = "manual") -> bool:
        body = _clean(text)
        if not body:
            return False
        items = self._read()
        lowered = body.lower()
        if any(_clean(item.get("text", "")).lower() == lowered for item in items):
            return False
        items.append({
            "text": body,
            "source": str(source or "manual"),
            "created_at": _now_text(),
            "ts": time.time(),
        })
        self._write(items[-1000:])
        return True

    def as_prompt_text(self, limit: int = 12) -> str:
        items = self._read()[-max(1, int(limit or 12)):]
        return "\n".join(f"- {item.get('text', '')}" for item in items if _clean(item.get("text", "")))

    def list_text(self, limit: int = 80) -> str:
        items = self._read()[-max(1, int(limit or 80)):]
        start = max(1, len(self._read()) - len(items) + 1)
        lines = []
        for idx, item in enumerate(items, start=start):
            created_at = item.get("created_at", "")
            source = item.get("source", "manual")
            text = item.get("text", "")
            if _clean(text):
                lines.append(f"{idx}. [{created_at} | {source}] {text}")
        return "\n".join(lines)

    def forget_contains(self, keyword: str) -> int:
        key = _clean(keyword).lower()
        if not key:
            return 0
        items = self._read()
        remaining = [item for item in items if key not in _clean(item.get("text", "")).lower()]
        removed = len(items) - len(remaining)
        if removed:
            self._write(remaining)
        return removed

    def reset(self) -> int:
        count = len(self._read())
        self._write([])
        return count


def handle_local_memory_command(user_input: str, memory: MemoryStore) -> str:
    """Handle simple memory commands from public chat.

    Admin gating is done in app.py; this helper only parses the commands.
    """
    raw = str(user_input or "").strip()
    lower = raw.lower()
    if lower.startswith(("/ingat ", "ingat ")):
        body = raw.split(" ", 1)[1].strip() if " " in raw else ""
        if not body:
            return "Memory kosong. Tulis: /ingat isi memory"
        ok = memory.add(body, source="chat-command")
        return "✅ Memory disimpan." if ok else "Memory sudah ada atau kosong."
    if lower in {"/memory", "/memori", "/ingat"}:
        listed = memory.list_text(limit=30)
        return listed or "Belum ada memory lokal."
    if lower.startswith(("/lupa ", "lupa ")):
        key = raw.split(" ", 1)[1].strip() if " " in raw else ""
        count = memory.forget_contains(key)
        return f"✅ {count} memory dihapus." if count else "Tidak ada memory yang cocok."
    if lower in {"/reset memory", "/reset memori"}:
        count = memory.reset()
        return f"✅ {count} memory dihapus."
    return ""
