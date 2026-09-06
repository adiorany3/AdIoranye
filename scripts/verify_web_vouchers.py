"""Run directly: verify voucher quota, ownership, concurrency and grace."""
import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web_vouchers import create_voucher, voucher_access


with tempfile.TemporaryDirectory() as directory:
    path = str(Path(directory) / "vouchers.sqlite3")
    code = create_voucher(path, 5, 60)
    assert not voucher_access(path, "invalid", "owner", "claim")
    assert voucher_access(path, code.lower(), "owner", "claim")["remaining"] == 5
    assert not voucher_access(path, code, "other", "claim")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: voucher_access(path, code, "owner", "consume"), range(20)))
    assert sum(result["allowed"] for result in results) == 5
    assert not voucher_access(path, code, "owner")["readable"]
    with patch("web_vouchers.time.time", return_value=1000):
        final = voucher_access(path, code, "owner", "finish")
        assert final["close_at"] == 1060 and final["readable"]
    with patch("web_vouchers.time.time", return_value=1059):
        assert voucher_access(path, code, "owner", "finish")["close_at"] == 1060
    with patch("web_vouchers.time.time", return_value=1060):
        assert not voucher_access(path, code, "owner")["readable"]
        assert not voucher_access(path, code, "owner", "consume")["allowed"]
    for quota, grace in [(0, 60), (10001, 60), (1, 0), (1, 3601), (True, 60)]:
        try:
            create_voucher(path, quota, grace)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid limits accepted")

    # Exercise actual handler without importing unrelated AI/runtime services.
    source = Path(__file__).resolve().parents[1] / "telegram_service.py"
    tree = ast.parse(source.read_text())
    handler = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_handle_admin_command")
    namespace = {"Any": object, "Optional": __import__("typing").Optional,
                 "create_voucher": create_voucher, "sqlite3": sqlite3, "os": __import__("os")}
    exec(compile(ast.Module(body=[handler], type_ignores=[]), str(source), "exec"), namespace)
    class Service:
        _config = {"web_voucher_db_path": path}
        _is_admin_chat = lambda self, chat_id: chat_id in (123, -123)
    command = namespace["_handle_admin_command"]
    assert "ditolak" in command(Service(), 999, "/voucher 5")
    assert "bukan grup" in command(Service(), -123, "/voucher 5")
    assert "Voucher: VC-" in command(Service(), 123, "/voucher@bot 2 30")
    assert "tidak dibuat" in command(Service(), 123, "/voucher 0")
    assert "Format:" in command(Service(), 123, "/voucher")

print("PASS: atomic quota, session ownership, grace expiry, validation, Telegram authorization")