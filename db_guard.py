"""SQLite backup, integrity-check, and auto-restore guard for Adioranye AI.

This module is intentionally dependency-free. It protects the main PowerStore
SQLite database from the common Streamlit/GitHub Actions problem where an active
SQLite DB or WAL file becomes malformed after partial commits, concurrent writes,
or interrupted update jobs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

WIB_TZ = ZoneInfo("Asia/Jakarta") if ZoneInfo else timezone.utc
DEFAULT_DB_PATH = ".adioranye_power.db"
DEFAULT_BACKUP_DIR = ".db_backups"
DEFAULT_MAX_BACKUPS = 10
DEFAULT_MIN_BACKUP_INTERVAL_SECONDS = 6 * 60 * 60


@dataclass
class DBGuardResult:
    ok: bool
    action: str
    db_path: str
    backup_path: str = ""
    message: str = ""
    details: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if data.get("details") is None:
            data["details"] = {}
        return data


def now_wib_text() -> str:
    return datetime.now(WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")


def now_stamp() -> str:
    return datetime.now(WIB_TZ).strftime("%Y%m%d_%H%M%S")


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def default_backup_dir() -> str:
    return str(os.getenv("DB_BACKUP_DIR", DEFAULT_BACKUP_DIR) or DEFAULT_BACKUP_DIR)


def default_max_backups() -> int:
    try:
        return max(1, int(os.getenv("DB_BACKUP_MAX_COUNT", str(DEFAULT_MAX_BACKUPS)) or DEFAULT_MAX_BACKUPS))
    except Exception:
        return DEFAULT_MAX_BACKUPS


def db_guard_enabled() -> bool:
    return parse_bool(os.getenv("DB_BACKUP_ENABLED", "true"), default=True)


def db_auto_restore_enabled() -> bool:
    return parse_bool(os.getenv("DB_AUTO_RESTORE_ENABLED", "true"), default=True)


def _resolve(path: str | Path) -> Path:
    return Path(str(path or DEFAULT_DB_PATH)).expanduser()


def _safe_stem(path: Path) -> str:
    stem = path.name
    stem = re.sub(r"[^a-zA-Z0-9_.-]+", "_", stem)
    return stem.strip("._") or "database"


def _sidecar_paths(db_path: str | Path) -> List[Path]:
    p = _resolve(db_path)
    return [Path(str(p) + "-wal"), Path(str(p) + "-shm"), Path(str(p) + "-journal")]


def _manifest_path(backup_dir: str | Path) -> Path:
    return _resolve(backup_dir) / "db_backup_manifest.jsonl"


def _write_manifest(backup_dir: str | Path, event: Dict[str, Any]) -> None:
    try:
        bdir = _resolve(backup_dir)
        bdir.mkdir(parents=True, exist_ok=True)
        event = dict(event)
        event.setdefault("created_at_wib", now_wib_text())
        with _manifest_path(bdir).open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


def sqlite_integrity_check(db_path: str | Path, *, quick: bool = True) -> DBGuardResult:
    """Return whether the SQLite file is readable and passes quick/integrity check."""
    p = _resolve(db_path)
    if not p.exists():
        return DBGuardResult(True, "missing_ok", str(p), message="Database belum ada; akan dibuat ulang otomatis.")
    if p.is_dir():
        return DBGuardResult(False, "invalid_path", str(p), message="Path database adalah folder, bukan file.")
    if p.stat().st_size == 0:
        return DBGuardResult(True, "empty_ok", str(p), message="Database kosong; SQLite akan menginisialisasi ulang.")

    uri = f"file:{p.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            pragma = "quick_check" if quick else "integrity_check"
            rows = conn.execute(f"PRAGMA {pragma};").fetchall()
            values = [str(row[0]).lower() for row in rows if row]
            ok = bool(values) and all(value == "ok" for value in values)
            return DBGuardResult(
                ok,
                pragma,
                str(p),
                message="ok" if ok else "; ".join(values[:5]),
                details={"rows": values[:20]},
            )
        finally:
            conn.close()
    except Exception as exc:
        return DBGuardResult(False, "integrity_error", str(p), message=str(exc)[:500])


def sqlite_checkpoint(db_path: str | Path) -> DBGuardResult:
    """Checkpoint WAL into the main db file so Git commits/backups are consistent."""
    p = _resolve(db_path)
    if not p.exists():
        return DBGuardResult(True, "checkpoint_skipped_missing", str(p), message="Database belum ada.")
    try:
        conn = sqlite3.connect(str(p), timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000;")
            try:
                rows = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchall()
            except sqlite3.DatabaseError:
                rows = []
            try:
                conn.execute("PRAGMA optimize;")
            except sqlite3.DatabaseError:
                pass
            conn.commit()
            return DBGuardResult(True, "checkpoint", str(p), message="WAL checkpoint selesai.", details={"rows": [tuple(r) for r in rows]})
        finally:
            conn.close()
    except Exception as exc:
        return DBGuardResult(False, "checkpoint_error", str(p), message=str(exc)[:500])


def _backup_files(backup_dir: str | Path, db_path: str | Path) -> List[Path]:
    bdir = _resolve(backup_dir)
    if not bdir.exists():
        return []
    safe = _safe_stem(_resolve(db_path))
    return sorted(bdir.glob(f"{safe}_*.db"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)


def latest_backup_age_seconds(backup_dir: str | Path, db_path: str | Path) -> Optional[float]:
    files = _backup_files(backup_dir, db_path)
    if not files:
        return None
    try:
        return max(0.0, time.time() - files[0].stat().st_mtime)
    except Exception:
        return None


def rotate_backups(backup_dir: str | Path, db_path: str | Path, max_backups: int = DEFAULT_MAX_BACKUPS) -> List[str]:
    files = _backup_files(backup_dir, db_path)
    removed: List[str] = []
    keep = max(1, int(max_backups or DEFAULT_MAX_BACKUPS))
    for item in files[keep:]:
        try:
            item.unlink()
            removed.append(str(item))
        except Exception:
            pass
    return removed


def create_sqlite_backup(
    db_path: str | Path,
    backup_dir: str | Path = DEFAULT_BACKUP_DIR,
    *,
    label: str = "manual",
    max_backups: int = DEFAULT_MAX_BACKUPS,
) -> DBGuardResult:
    """Create a consistent backup using SQLite's online backup API."""
    p = _resolve(db_path)
    bdir = _resolve(backup_dir)
    bdir.mkdir(parents=True, exist_ok=True)

    if not p.exists() or p.stat().st_size == 0:
        return DBGuardResult(True, "backup_skipped_empty", str(p), message="Database belum ada/kosong; backup dilewati.")

    check = sqlite_integrity_check(p, quick=True)
    if not check.ok:
        _write_manifest(bdir, {"event": "backup_rejected", "db_path": str(p), "message": check.message})
        return DBGuardResult(False, "backup_rejected", str(p), message=f"Database tidak sehat: {check.message}")

    sqlite_checkpoint(p)
    safe = _safe_stem(p)
    safe_label = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(label or "manual"))[:40] or "manual"
    target = bdir / f"{safe}_{now_stamp()}_{safe_label}.db"

    try:
        src = sqlite3.connect(str(p), timeout=30)
        dst = sqlite3.connect(str(target), timeout=30)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
            src.close()
    except Exception as exc:
        try:
            if target.exists():
                target.unlink()
        except Exception:
            pass
        return DBGuardResult(False, "backup_error", str(p), message=str(exc)[:500])

    backup_check = sqlite_integrity_check(target, quick=True)
    if not backup_check.ok:
        try:
            target.unlink()
        except Exception:
            pass
        return DBGuardResult(False, "backup_invalid", str(p), backup_path=str(target), message=backup_check.message)

    removed = rotate_backups(bdir, p, max_backups=max_backups)
    _write_manifest(bdir, {
        "event": "backup_created",
        "db_path": str(p),
        "backup_path": str(target),
        "label": safe_label,
        "size_bytes": target.stat().st_size if target.exists() else 0,
        "removed_old_backups": removed,
    })
    return DBGuardResult(True, "backup_created", str(p), backup_path=str(target), message="Backup database berhasil dibuat.", details={"removed": removed})


def maybe_create_periodic_backup(
    db_path: str | Path,
    backup_dir: str | Path = DEFAULT_BACKUP_DIR,
    *,
    label: str = "periodic",
    min_interval_seconds: int = DEFAULT_MIN_BACKUP_INTERVAL_SECONDS,
    max_backups: int = DEFAULT_MAX_BACKUPS,
) -> DBGuardResult:
    age = latest_backup_age_seconds(backup_dir, db_path)
    if age is not None and age < max(0, int(min_interval_seconds or 0)):
        return DBGuardResult(True, "backup_skipped_recent", str(_resolve(db_path)), message=f"Backup terakhir masih baru ({int(age)} detik).")
    return create_sqlite_backup(db_path, backup_dir, label=label, max_backups=max_backups)


def quarantine_current_database(db_path: str | Path, backup_dir: str | Path = DEFAULT_BACKUP_DIR, *, reason: str = "corrupt") -> List[str]:
    p = _resolve(db_path)
    bdir = _resolve(backup_dir)
    qdir = bdir / "corrupt"
    qdir.mkdir(parents=True, exist_ok=True)
    moved: List[str] = []
    safe = _safe_stem(p)
    stamp = now_stamp()
    for item in [p] + _sidecar_paths(p):
        try:
            if item.exists():
                target = qdir / f"{safe}_{stamp}_{reason}_{item.name}"
                shutil.move(str(item), str(target))
                moved.append(str(target))
        except Exception:
            pass
    _write_manifest(bdir, {"event": "database_quarantined", "db_path": str(p), "reason": reason, "moved": moved})
    return moved


def restore_latest_valid_backup(
    db_path: str | Path,
    backup_dir: str | Path = DEFAULT_BACKUP_DIR,
    *,
    quarantine_bad_current: bool = True,
) -> DBGuardResult:
    """Restore newest valid backup. Current broken db is moved to .db_backups/corrupt."""
    p = _resolve(db_path)
    bdir = _resolve(backup_dir)
    backups = _backup_files(bdir, p)
    if not backups:
        moved = quarantine_current_database(p, bdir, reason="no_valid_backup") if quarantine_bad_current else []
        return DBGuardResult(False, "restore_no_backup", str(p), message="Tidak ada backup database yang tersedia.", details={"quarantined": moved})

    tried: List[Dict[str, str]] = []
    for backup in backups:
        check = sqlite_integrity_check(backup, quick=True)
        if not check.ok:
            tried.append({"backup": str(backup), "status": "invalid", "message": check.message})
            continue
        moved = quarantine_current_database(p, bdir, reason="before_restore") if quarantine_bad_current else []
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(backup), str(p))
            for sidecar in _sidecar_paths(p):
                try:
                    if sidecar.exists():
                        sidecar.unlink()
                except Exception:
                    pass
            final_check = sqlite_integrity_check(p, quick=True)
            if final_check.ok:
                _write_manifest(bdir, {"event": "database_restored", "db_path": str(p), "backup_path": str(backup), "quarantined": moved})
                return DBGuardResult(True, "restored", str(p), backup_path=str(backup), message="Database dipulihkan dari backup terakhir yang valid.", details={"quarantined": moved})
            tried.append({"backup": str(backup), "status": "restored_but_invalid", "message": final_check.message})
        except Exception as exc:
            tried.append({"backup": str(backup), "status": "restore_error", "message": str(exc)[:500]})
            continue

    moved = quarantine_current_database(p, bdir, reason="all_backups_invalid") if quarantine_bad_current else []
    return DBGuardResult(False, "restore_failed", str(p), message="Semua backup gagal dipakai.", details={"tried": tried, "quarantined": moved})


def ensure_database_ready(
    db_path: str | Path,
    backup_dir: str | Path = DEFAULT_BACKUP_DIR,
    *,
    auto_restore: bool = True,
    create_periodic_backup: bool = False,
    min_backup_interval_seconds: int = DEFAULT_MIN_BACKUP_INTERVAL_SECONDS,
    max_backups: int = DEFAULT_MAX_BACKUPS,
) -> DBGuardResult:
    """Ensure DB is usable. If malformed, restore latest backup or quarantine current DB."""
    if not db_guard_enabled():
        return DBGuardResult(True, "guard_disabled", str(_resolve(db_path)), message="DB guard dinonaktifkan.")

    p = _resolve(db_path)
    check = sqlite_integrity_check(p, quick=True)
    if check.ok:
        if create_periodic_backup and p.exists() and p.stat().st_size > 0:
            return maybe_create_periodic_backup(p, backup_dir, label="auto", min_interval_seconds=min_backup_interval_seconds, max_backups=max_backups)
        return DBGuardResult(True, "healthy", str(p), message="Database sehat.")

    if auto_restore and db_auto_restore_enabled():
        restored = restore_latest_valid_backup(p, backup_dir, quarantine_bad_current=True)
        if restored.ok:
            return restored

    moved = quarantine_current_database(p, backup_dir, reason="malformed")
    return DBGuardResult(True, "quarantined_new_empty", str(p), message="Database rusak dipindahkan ke karantina; database baru akan dibuat otomatis.", details={"original_error": check.message, "quarantined": moved})


def print_result(result: DBGuardResult) -> None:
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SQLite guard: backup, check, checkpoint, and restore Adioranye database.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--db", default=os.getenv("POWER_DB_PATH", DEFAULT_DB_PATH), help="Path database SQLite.")
        p.add_argument("--backup-dir", default=default_backup_dir(), help="Folder backup database.")
        p.add_argument("--max-backups", type=int, default=default_max_backups(), help="Jumlah backup valid yang disimpan.")

    p_check = sub.add_parser("check", help="Cek integritas database; opsional auto restore.")
    add_common(p_check)
    p_check.add_argument("--restore", action="store_true", help="Restore backup otomatis jika database rusak.")
    p_check.add_argument("--periodic-backup", action="store_true", help="Buat backup berkala jika database sehat.")

    p_backup = sub.add_parser("backup", help="Buat backup database konsisten.")
    add_common(p_backup)
    p_backup.add_argument("--label", default="manual", help="Label backup.")

    p_restore = sub.add_parser("restore", help="Restore backup valid terbaru.")
    add_common(p_restore)

    p_checkpoint = sub.add_parser("checkpoint", help="Checkpoint WAL ke database utama.")
    add_common(p_checkpoint)

    args = parser.parse_args(argv)

    if args.command == "check":
        result = ensure_database_ready(
            args.db,
            args.backup_dir,
            auto_restore=bool(args.restore),
            create_periodic_backup=bool(args.periodic_backup),
            max_backups=args.max_backups,
        )
        print_result(result)
        return 0 if result.ok else 2
    if args.command == "backup":
        result = create_sqlite_backup(args.db, args.backup_dir, label=args.label, max_backups=args.max_backups)
        print_result(result)
        return 0 if result.ok else 2
    if args.command == "restore":
        result = restore_latest_valid_backup(args.db, args.backup_dir, quarantine_bad_current=True)
        print_result(result)
        return 0 if result.ok else 2
    if args.command == "checkpoint":
        result = sqlite_checkpoint(args.db)
        print_result(result)
        return 0 if result.ok else 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
