import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import telegram_service
from telegram_service import TelegramService

service = TelegramService()
service._config = {
    "slashai_api_url": "https://example.invalid/v1/chat/completions",
    "slashai_api_key": "test",
    "power_rag_enabled": False,
    "power_response_cache_enabled": True,
    "power_latency_budget_enabled": True,
}
captured: dict[str, object] = {}


def fake_generate(**kwargs: object) -> tuple[str, dict[str, object]]:
    captured.update(kwargs)
    return "Jawaban model.", {}


telegram_service.safe_generate_power_answer = fake_generate

answer, meta = service._build_answer("Halo", chat_id=42)
assert answer.startswith("Halo juga")
assert meta["telegram_local_fast_path"] == "greeting"
assert captured == {}, "Sapaan sederhana seharusnya tidak memanggil model."

answer, _ = service._build_answer("Jelaskan fotosintesis", chat_id=42)
assert answer == "Jawaban model."
assert captured["user_id"] == "telegram:42"
assert captured["channel"] == "telegram"
assert captured["enable_rag"] is False
assert captured["enable_response_cache"] is True
assert captured["latency_budget_enabled"] is True

print("Fast path lokal dan identitas/config Telegram diteruskan dengan benar.")
