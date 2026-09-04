import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reminder_skill import ReminderStore, WIB
from telegram_service import TelegramService

with tempfile.TemporaryDirectory() as directory:
    store = ReminderStore(str(Path(directory) / "reminders.json"))
    reminder_id = store.add("34", (datetime.now(WIB) - timedelta(minutes=1)).isoformat(), "Uji")
    service = TelegramService()
    service._reminders = store

    service._send_text = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline"))
    service._deliver_due_reminders()
    assert [item["id"] for item in store.due()] == [reminder_id]

    service._send_text = lambda *args, **kwargs: 1
    service._deliver_due_reminders()
    assert store.due() == []

print("Reminder dipertahankan saat gagal dan dihapus setelah sukses.")
