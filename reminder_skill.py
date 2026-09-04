"""Persistent Telegram reminders using stdlib only."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")


def _load(path: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save(path: str, reminders: List[Dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(reminders, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def parse_reminder_command(text: str, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    parts = str(text or "").strip().split(maxsplit=2)
    if len(parts) < 3 or parts[0].lower().split("@", 1)[0] not in {"/ingat", "/reminder"}:
        return None
    try:
        due = datetime.strptime(parts[1], "%Y-%m-%d_%H:%M").replace(tzinfo=WIB)
    except ValueError:
        return {"error": "Format salah. Pakai: /ingat YYYY-MM-DD_HH:MM isi pengingat"}
    if due <= (now or datetime.now(WIB)):
        return {"error": "Waktu pengingat harus di masa depan."}
    return {"due_at": due.isoformat(), "text": parts[2].strip()}


class ReminderStore:
    def __init__(self, path: str = ".adioranye_reminders.json") -> None:
        self.path = path
        self._lock = threading.Lock()

    def add(self, chat_id: Any, due_at: str, text: str) -> int:
        with self._lock:
            reminders = _load(self.path)
            next_id = max((int(item.get("id", 0)) for item in reminders), default=0) + 1
            reminders.append({"id": next_id, "chat_id": str(chat_id), "due_at": due_at, "text": text})
            _save(self.path, reminders)
            return next_id

    def list(self, chat_id: Any) -> List[Dict[str, Any]]:
        with self._lock:
            return [item for item in _load(self.path) if item.get("chat_id") == str(chat_id)]

    def delete(self, chat_id: Any, reminder_id: int) -> bool:
        with self._lock:
            reminders = _load(self.path)
            kept = [item for item in reminders if not (item.get("chat_id") == str(chat_id) and int(item.get("id", 0)) == reminder_id)]
            if len(kept) == len(reminders):
                return False
            _save(self.path, kept)
            return True

    def due(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        current = now or datetime.now(WIB)
        with self._lock:
            ready = []
            for item in _load(self.path):
                try:
                    is_due = datetime.fromisoformat(str(item["due_at"])) <= current
                except (KeyError, ValueError, TypeError):
                    is_due = False
                if is_due:
                    ready.append(item)
            return ready
