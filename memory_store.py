import json
import os
from datetime import datetime
from typing import Dict, Any


class MemoryStore:
    def __init__(self, path: str = "assistant_memory.json"):
        self.path = path

    def _default_data(self) -> Dict[str, Any]:
        return {"memories": []}

    def load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return self._default_data()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return self._default_data()
            if "memories" not in data or not isinstance(data["memories"], list):
                data["memories"] = []
            return data
        except Exception:
            return self._default_data()

    def save(self, data: Dict[str, Any]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add(self, text: str, source: str = "manual") -> None:
        text = (text or "").strip()
        if not text:
            return
        data = self.load()
        memories = data["memories"]
        for item in memories:
            if item.get("text", "").strip().lower() == text.lower():
                return
        memories.append({"text": text, "source": source, "created_at": datetime.now().isoformat(timespec="seconds")})
        data["memories"] = memories[-80:]
        self.save(data)

    def list_text(self, limit: int = 20) -> str:
        data = self.load()
        memories = data.get("memories", [])[-limit:]
        if not memories:
            return ""
        lines = []
        for idx, item in enumerate(memories, start=1):
            lines.append(f"{idx}. {item.get('text', '')}")
        return "\n".join(lines)

    def as_prompt_text(self, limit: int = 20) -> str:
        return self.list_text(limit=limit)

    def forget_contains(self, keyword: str) -> int:
        keyword = (keyword or "").strip().lower()
        if not keyword:
            return 0
        data = self.load()
        old = data.get("memories", [])
        new = [m for m in old if keyword not in m.get("text", "").lower()]
        data["memories"] = new
        self.save(data)
        return len(old) - len(new)

    def reset(self) -> None:
        self.save(self._default_data())


def handle_local_memory_command(text: str, memory: MemoryStore) -> str:
    clean = (text or "").strip()
    lower = clean.lower()

    if lower.startswith("/ingat "):
        value = clean[7:].strip()
        memory.add(value, source="command")
        return "✅ Sudah saya ingat."

    if lower in {"/memori", "/memory"}:
        listed = memory.list_text(limit=30)
        if listed:
            return "📌 Memori saat ini:\n\n" + listed
        return "Belum ada memori yang disimpan."

    if lower.startswith("/lupa "):
        keyword = clean[6:].strip()
        count = memory.forget_contains(keyword)
        return f"✅ Menghapus {count} memori yang mengandung: {keyword}"

    if lower in {"/reset memori", "/reset memory"}:
        memory.reset()
        return "✅ Semua memori sudah dihapus."

    return ""
