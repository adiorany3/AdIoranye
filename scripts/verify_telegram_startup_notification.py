import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram_service import TelegramService


service = TelegramService()
service._config = {
    "telegram_admin_chat_ids": "101, 202",
    "telegram_poll_timeout_seconds": 1,
}
service._token = "test-token"
service._worker_id = "test-worker"
sent: list[tuple[str, str]] = []


def fake_request(method: str, payload=None, timeout: int = 60):
    assert method == "getUpdates"
    service._stop_event.set()
    return {"ok": True, "result": []}


service._telegram_request = fake_request
service._send_text = lambda chat_id, text, reply_to=None: sent.append((str(chat_id), text))
service._deliver_due_reminders = lambda: None
service._poll_loop()
service._notify_polling_started_once()

assert {chat_id for chat_id, _ in sent} == {"101", "202"}
assert len(sent) == 2
assert all("test-worker" in text and "polling berjalan" in text for _, text in sent)
print("Notifikasi startup terkirim sekali ke setiap admin setelah polling berhasil.")
