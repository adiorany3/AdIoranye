import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
service._build_answer = lambda text, chat_id, recent_messages=None: ("Jawaban selesai.", {})

service._config = {"send_processing_message": True}
service._handle_message({"message_id": 12, "chat": {"id": 34}, "text": "Pertanyaan"})
assert events == [
    ("send", "OK siap..."),
    ("send", "Jawaban selesai."),
    ("delete", 900),
]

events.clear()
service._config = {"send_processing_message": False}
service._handle_message({"message_id": 13, "chat": {"id": 34}, "text": "Pertanyaan"})
assert events == [("send", "Jawaban selesai.")], events

concurrency_service = TelegramService()
concurrency_service._message_executor = ThreadPoolExecutor(max_workers=3)
execution_events: list[tuple[str, str]] = []
execution_lock = threading.Lock()
active = 0
max_active = 0


def fake_handle(message: dict[str, object]) -> None:
    global active, max_active
    label = str(message["text"])
    with execution_lock:
        active += 1
        max_active = max(max_active, active)
        execution_events.append(("start", label))
    time.sleep(0.05)
    with execution_lock:
        execution_events.append(("end", label))
        active -= 1


concurrency_service._handle_message = fake_handle
concurrency_service._submit_message({"chat": {"id": 1}, "text": "chat-1-a"})
concurrency_service._submit_message({"chat": {"id": 1}, "text": "chat-1-b"})
concurrency_service._submit_message({"chat": {"id": 2}, "text": "chat-2-a"})
concurrency_service._message_executor.shutdown(wait=True)

assert max_active == 2, execution_events
assert execution_events.index(("end", "chat-1-a")) < execution_events.index(("start", "chat-1-b")), execution_events
print("Jawaban dikirim sebelum cleanup; chat berbeda paralel; urutan chat sama terjaga.")
