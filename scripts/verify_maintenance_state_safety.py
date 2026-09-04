import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from telegram_service import TelegramService

app_source = (ROOT / "app.py").read_text(encoding="utf-8")
assert "return maintenance_read_error_state()" in app_source
assert "os.fsync(file.fileno())" in app_source
assert "os.replace(tmp_path, path)" in app_source
assert "except Exception:\n        pass" not in app_source[app_source.index("def write_maintenance_lock_state"):app_source.index("def _auto_relock_when_due")]

with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "maintenance.json"
    path.write_text("{", encoding="utf-8")
    service = TelegramService()
    service._config = {"maintenance_lock_file": str(path)}
    state = service._read_maintenance_state()
    assert state["locked"] is True, state
    assert state["reason"] == "maintenance_state_read_error", state

    service._write_maintenance_state_payload({"locked": False, "status": "unlocked"})
    assert path.read_text(encoding="utf-8").startswith("{")
    assert not list(path.parent.glob("*.tmp"))

print("State maintenance fail-closed dan ditulis atomik.")
