import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram_service import TelegramService

service = TelegramService()
events: list[tuple[str, object]] = []


def fake_send(chat_id: object, text: str, reply_to: object = None) -> int:
    events.append(("send", text))
    return 900 if text == "OK siap..." else 901


service._send_text = fake_send
service._delete_message = lambda chat_id, message_id: events.append(("delete", message_id))
service._build_answer = lambda text, recent_messages=None: ("Jawaban selesai.", {})
service._handle_message({"message_id": 12, "chat": {"id": 34}, "text": "Pertanyaan"})

assert events == [
    ("send", "OK siap..."),
    ("delete", 900),
    ("send", "Jawaban selesai."),
]
print("Pesan sementara dikirim, dihapus, lalu jawaban akhir dikirim.")
