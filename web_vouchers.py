"""Atomic, session-bound web question vouchers shared with Telegram."""
import hashlib
import secrets
import sqlite3
import time


def _connect(path):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS web_vouchers (
        code_hash TEXT PRIMARY KEY, quota INTEGER NOT NULL,
        used INTEGER NOT NULL DEFAULT 0, grace INTEGER NOT NULL,
        owner TEXT, close_at REAL, created_by TEXT NOT NULL)""")
    return db


def _hash(code):
    return hashlib.sha256(str(code).strip().upper().encode()).hexdigest()


def list_active_vouchers(path):
    db = _connect(path)
    try:
        return [dict(row) for row in db.execute(
            "SELECT substr(code_hash, 1, 12) AS id, quota, used, quota - used AS remaining "
            "FROM web_vouchers WHERE used < quota ORDER BY rowid DESC"
        )]
    finally:
        db.close()

def create_voucher(path, quota, grace=60, created_by="telegram-admin"):
    if type(quota) is not int or not 1 <= quota <= 10000:
        raise ValueError("Jumlah pertanyaan harus 1-10000.")
    if type(grace) is not int or not 1 <= grace <= 3600:
        raise ValueError("Jeda harus 1-3600 detik.")
    code = "VC-" + secrets.token_hex(16).upper()
    db = _connect(path)
    try:
        with db:
            db.execute("INSERT INTO web_vouchers(code_hash, quota, grace, created_by) VALUES (?, ?, ?, ?)",
                       (_hash(code), quota, grace, str(created_by)))
    finally:
        db.close()
    return code


def voucher_access(path, code, owner, action="status"):
    if not owner or action not in {"status", "claim", "consume", "finish"}:
        raise ValueError("Sesi atau operasi voucher tidak valid.")
    db = _connect(path)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            key = _hash(code)
            row = db.execute("SELECT * FROM web_vouchers WHERE code_hash = ?", (key,)).fetchone()
            if row is None or row["owner"] not in (None, owner):
                return {}
            if action == "claim" and row["owner"] is None:
                db.execute("UPDATE web_vouchers SET owner = ? WHERE code_hash = ?", (owner, key))
            elif row["owner"] != owner:
                return {}
            remaining = row["quota"] - row["used"]
            allowed = action == "consume" and remaining > 0
            if allowed:
                db.execute("UPDATE web_vouchers SET used = used + 1 WHERE code_hash = ?", (key,))
                remaining -= 1
            close_at = row["close_at"]
            if action == "finish" and remaining == 0 and close_at is None:
                close_at = time.time() + row["grace"]
                db.execute("UPDATE web_vouchers SET close_at = ? WHERE code_hash = ?", (close_at, key))
            return {"remaining": remaining, "allowed": allowed, "grace": row["grace"],
                    "close_at": close_at, "readable": remaining > 0 or bool(close_at and time.time() < close_at)}
    finally:
        db.close()