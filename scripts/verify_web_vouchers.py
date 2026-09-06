"""Run directly: verify voucher quota, ownership, concurrency and grace."""
import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web_vouchers import create_voucher, save_voucher_state, restore_voucher_state, list_saved_states, voucher_access, list_active_vouchers, _hash


with tempfile.TemporaryDirectory() as directory:
    path = str(Path(directory) / "vouchers.sqlite3")
    code = create_voucher(path, 5, 60)
    assert list_active_vouchers(path)[0]["remaining"] == 5
    assert not voucher_access(path, "invalid", "owner", "claim")
    assert voucher_access(path, code.lower(), "owner", "claim")["remaining"] == 5
    assert not voucher_access(path, code, "other", "claim")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: voucher_access(path, code, "owner", "consume"), range(20)))
    assert sum(result["allowed"] for result in results) == 5
    assert list_active_vouchers(path) == []
    # Simpan / pulihkan sisa kuota
    code2 = create_voucher(path, 5, 60)
    voucher_access(path, code2, "owner2", "claim")
    voucher_access(path, code2, "owner2", "consume")
    assert save_voucher_state(path, None) is None
    saved = save_voucher_state(path, "owner2")
    assert saved is not None and len(saved) == 8
    assert list_saved_states(path, "owner2")[0]["remaining"] == 4
    assert list_saved_states(path, "stranger") == []
    new_code = restore_voucher_state(path, "owner2", saved)
    assert new_code is not None and new_code.startswith("VC-")
    voucher_access(path, new_code, "owner2", "claim")
    assert voucher_access(path, new_code, "owner2", "status")["remaining"] == 4
    # Habiskan new_code agar /cekvoucher kosong untuk tes berikutnya
    for _ in range(4):
        voucher_access(path, new_code, "owner2", "consume")
    assert list_saved_states(path, "owner2") == []
    assert restore_voucher_state(path, "owner2", "XXXX") is None
    assert restore_voucher_state(path, "owner2", "") is None
    assert restore_voucher_state(path, None, saved) is None
    # Simpan dengan owner salah / voucher aktif tidak ada → None
    assert save_voucher_state(path, "no-such-owner") is None
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
                 "create_voucher": create_voucher, "list_active_vouchers": list_active_vouchers,
                 "save_voucher_state": save_voucher_state, "restore_voucher_state": restore_voucher_state,
                 "list_saved_states": list_saved_states,
                 "_hash": _hash, "sqlite3": sqlite3, "os": __import__("os")}
    exec(compile(ast.Module(body=[handler], type_ignores=[]), str(source), "exec"), namespace)
    class Service:
        _config = {"web_voucher_db_path": path}
        _is_admin_chat = lambda self, chat_id: chat_id in (123, -123)
    command = namespace["_handle_admin_command"]
    assert "ditolak" in command(Service(), 999, "/cekvoucher")
    assert "bukan grup" in command(Service(), -123, "/cekvoucher")
    assert "Tidak ada" in command(Service(), 123, "/cekvoucher")
    active = create_voucher(path, 3)
    voucher_access(path, active, "active-owner", "claim")
    voucher_access(path, active, "active-owner", "consume")
    report = command(Service(), 123, "/cekvoucher@bot")
    assert "Total sisa: 2" in report and "1/3 | 2" in report
    assert _hash(code)[:12] not in report and _hash(active)[:12] in report
    assert active not in report
    for bad in ("abc", "1 2", "²", "9" * 5000):
        assert "Format:" in command(Service(), 123, f"/cekvoucher {bad}")
    assert "minimal" in command(Service(), 123, "/cekvoucher 0")
    assert "tidak tersedia" in command(Service(), 123, "/cekvoucher 2")
    for _ in range(20):
        create_voucher(path, 1)
    assert "Halaman 1/2" in command(Service(), 123, "/cekvoucher")
    assert "Halaman 2/2" in command(Service(), 123, "/cekvoucher 2")
    with patch.dict(namespace, list_active_vouchers=lambda _: (_ for _ in ()).throw(sqlite3.OperationalError())):
        assert "gagal dibaca" in command(Service(), 123, "/cekvoucher")
    assert "ditolak" in command(Service(), 999, "/voucher 5")
    assert "bukan grup" in command(Service(), -123, "/voucher 5")
    assert "Voucher: VC-" in command(Service(), 123, "/voucher@bot 2 30")
    assert "tidak dibuat" in command(Service(), 123, "/voucher 0")
    assert "Format:" in command(Service(), 123, "/voucher")

print("PASS: atomic quota, session ownership, grace expiry, save/restore, validation, Telegram authorization")