import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram_service import TelegramService


root = Path(__file__).resolve().parents[1]
app_tree = ast.parse((root / "app.py").read_text(encoding="utf-8"))
router = next(
    node
    for node in app_tree.body
    if isinstance(node, ast.FunctionDef) and node.name == "run_adioranye_router"
)
assert any(
    isinstance(node, ast.Call)
    and isinstance(node.func, ast.Name)
    and node.func.id == "start_telegram_if_needed"
    for node in ast.walk(router)
), "Router harus memulai worker Telegram otomatis."

service = TelegramService()
service._config = {"telegram_poll_timeout_seconds": 1}
service._token = "test-token"
reminder_calls = 0
poll_calls = 0


def flaky_reminders() -> None:
    global reminder_calls
    reminder_calls += 1
    if reminder_calls == 1:
        raise RuntimeError("simulated reminder failure")


def stop_after_poll(method: str, payload=None, timeout: int = 60) -> dict[str, object]:
    global poll_calls
    assert method == "getUpdates"
    poll_calls += 1
    service._stop_event.set()
    return {"ok": True, "result": []}


service._deliver_due_reminders = flaky_reminders  # type: ignore[method-assign]
service._telegram_request = stop_after_poll  # type: ignore[method-assign]
service._stop_event.wait = lambda timeout=None: False  # type: ignore[method-assign]
service._poll_loop()

assert reminder_calls == 2
assert poll_calls == 1, "Worker harus lanjut polling setelah reminder gagal."
assert "simulated reminder failure" in service.status()["last_error"]
print("Auto-start dan ketahanan loop worker Telegram terverifikasi.")
