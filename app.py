import base64
import hmac
import inspect
import html
import json
import os
import shutil
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Tuple

import requests
import streamlit as st
import streamlit.components.v1 as components

from ai_core import (
    ALL_SLASHAI_MODELS,
    ALL_CHEAP_MODELS,
    ALL_CAPABLE_MODELS,
    TOP_USAGE_MODEL_CANDIDATES,
    discover_available_models_from_api,
    DEFAULT_CHEAP_FALLBACK_MODELS,
    DEFAULT_EXPENSIVE_FALLBACK_MODELS,
    DEFAULT_FALLBACK_MODELS,
    MODEL_PRICE_IDR,
    generate_answer,
    model_cost_tier,
    model_price,
    model_price_label,
)
from memory_store import MemoryStore, handle_local_memory_command
from telegram_service import get_telegram_service
from power_features import (
    get_power_store,
    handle_power_command,
    generate_power_answer,
    classify_intent_text,
    run_model_benchmark,
    extract_text_from_file_bytes,
)
from db_guard import (
    maybe_create_periodic_backup,
    default_backup_dir,
    default_max_backups,
)

from daily_kb_scraper import (
    DEFAULT_SOURCES_FILE as KB_DEFAULT_SOURCES_FILE,
    load_sources as load_kb_scraper_sources,
    run_daily_kb_update,
)

from kb_manager import (
    advanced_incremental_kb_update,
    archive_documents_by_source,
    clear_live_cache,
    delete_archived_documents,
    ensure_kb_sources_file,
    export_kb_audit_log,
    init_kb_manager_schema,
    kb_manager_overview,
    read_live_cache,
    search_kb_v2_context,
    write_live_cache,
)

st.set_page_config(
    page_title="Adioranye AI by Galuh Adi Insani",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# =========================
# Helpers
# =========================


def get_secret(name: str, default: Any = "") -> Any:
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def safe_compare(left: Any, right: Any) -> bool:
    return hmac.compare_digest(str(left or ""), str(right or ""))


def load_model_performance_stats_from_file() -> Dict[str, Dict[str, Any]]:
    """Load performance stats early, before init_state is executed."""
    path = str(globals().get("model_performance_state_file", ".adioranye_model_performance.json"))
    try:
        if not path or not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


PUBLIC_AI_ERROR_MESSAGE = (
    "Maaf, Adioranye sedang mengalami gangguan koneksi/model. "
    "Silakan coba lagi beberapa saat lagi."
)

TECHNICAL_ERROR_PATTERNS = [
    "semua model gagal",
    "detail ringkas",
    "httpsconnectionpool",
    "read timed out",
    "timeout=",
    "api status",
    "external billing",
    "insufficient balance",
    "insufficient_user_quota",
    "invalid model",
    "openai-compatible",
    "slashai/",
    "traceback",
    "requests.exceptions",
    "connectionerror",
    "httperror",
    "401002",
]


def make_public_ai_error_message() -> str:
    return PUBLIC_AI_ERROR_MESSAGE


def _maintenance_now_text() -> str:
    try:
        return _wib_now_text()
    except Exception:
        try:
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return ""


def default_maintenance_state() -> Dict[str, Any]:
    return {
        "locked": False,
        "status": "unlocked",
        "message": maintenance_default_message,
        "reason": "",
        "updated_at": "",
        "updated_by": "",
        "channel": "",
    }


def read_maintenance_lock_state() -> Dict[str, Any]:
    try:
        if not maintenance_lock_file or not os.path.exists(maintenance_lock_file):
            return default_maintenance_state()

        with open(maintenance_lock_file, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            return default_maintenance_state()

        state = default_maintenance_state()
        state.update(data)
        state["locked"] = bool(state.get("locked"))
        state["status"] = "locked" if state.get("locked") else "unlocked"

        # Bersihkan field lama dari fitur timed lock agar tidak dipakai lagi.
        for legacy_key in [
            "locked_until_ts",
            "locked_until_text",
            "auto_unlock",
            "auto_unlocked_at",
        ]:
            state.pop(legacy_key, None)

        return state
    except Exception:
        return default_maintenance_state()

def write_maintenance_lock_state(state: Dict[str, Any]) -> None:
    try:
        payload = default_maintenance_state()
        payload.update(state or {})
        payload["locked"] = bool(payload.get("locked"))
        payload["status"] = "locked" if payload.get("locked") else "unlocked"
        payload["updated_at"] = payload.get("updated_at") or _maintenance_now_text()

        with open(maintenance_lock_file, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
    except Exception:
        pass


def set_maintenance_lock(
    locked: bool,
    updated_by: str = "admin",
    channel: str = "web-admin",
    reason: str = "",
) -> Dict[str, Any]:
    state = read_maintenance_lock_state()
    state.update(
        {
            "locked": bool(locked),
            "status": "locked" if locked else "unlocked",
            "message": maintenance_default_message,
            "reason": str(reason or "").strip(),
            "updated_at": _maintenance_now_text(),
            "updated_by": str(updated_by or "admin"),
            "channel": str(channel or "web-admin"),
        }
    )

    for legacy_key in [
        "locked_until_ts",
        "locked_until_text",
        "auto_unlock",
        "auto_unlocked_at",
    ]:
        state.pop(legacy_key, None)

    write_maintenance_lock_state(state)
    return state



def read_akses_terbatas_boot_guard() -> Dict[str, Any]:
    try:
        if not akses_terbatas_boot_guard_file or not os.path.exists(akses_terbatas_boot_guard_file):
            return {}

        with open(akses_terbatas_boot_guard_file, "r", encoding="utf-8") as file:
            data = json.load(file)

        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_akses_terbatas_boot_guard(state: Dict[str, Any]) -> None:
    try:
        with open(akses_terbatas_boot_guard_file, "w", encoding="utf-8") as file:
            json.dump(state or {}, file, ensure_ascii=False, indent=2)
    except Exception:
        pass


def current_process_boot_signature() -> str:
    """Signature ringan untuk membedakan cold start/server reboot dari rerun biasa.

    Streamlit rerun biasanya tetap memakai PID yang sama. Setelah server/container
    restart, PID umumnya berubah. Jika `/proc` tersedia, starttime proses ikut
    dipakai agar lebih akurat.
    """
    pid = os.getpid()
    start_marker = ""

    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as file:
            parts = file.read().split()
        if len(parts) > 21:
            start_marker = parts[21]
    except Exception:
        start_marker = ""

    return f"{pid}:{start_marker}"


def apply_auto_akses_terbatas_on_boot() -> Dict[str, Any]:
    """Aktifkan akses terbatas otomatis satu kali per proses server.

    Tidak memakai st.rerun. Tidak mengunci ulang setiap rerun Streamlit.
    """
    if not bool(akses_terbatas_auto_on_boot):
        return {"applied": False, "reason": "disabled"}

    signature = current_process_boot_signature()
    guard = read_akses_terbatas_boot_guard()

    if str(guard.get("process_signature") or "") == signature:
        return {
            "applied": False,
            "reason": "already_processed",
            "process_signature": signature,
        }

    state = set_maintenance_lock(
        True,
        updated_by="system-boot",
        channel="server-boot",
        reason=akses_terbatas_boot_reason,
    )

    write_akses_terbatas_boot_guard(
        {
            "process_signature": signature,
            "applied_at": _maintenance_now_text(),
            "pid": os.getpid(),
            "reason": akses_terbatas_boot_reason,
        }
    )

    return {
        "applied": True,
        "reason": "boot_auto_lock",
        "process_signature": signature,
        "state": state,
    }


def normalize_maintenance_access_key(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def default_maintenance_access_key_state() -> Dict[str, Any]:
    return {"version": 1, "keys": {}, "updated_at": ""}


def read_maintenance_access_key_state() -> Dict[str, Any]:
    try:
        if not maintenance_access_key_file or not os.path.exists(maintenance_access_key_file):
            return default_maintenance_access_key_state()
        with open(maintenance_access_key_file, "r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            return default_maintenance_access_key_state()
        state = default_maintenance_access_key_state()
        state.update(data)
        if not isinstance(state.get("keys"), dict):
            state["keys"] = {}
        return state
    except Exception:
        return default_maintenance_access_key_state()


def write_maintenance_access_key_state(state: Dict[str, Any]) -> None:
    try:
        payload = default_maintenance_access_key_state()
        payload.update(state or {})
        payload["updated_at"] = _maintenance_now_text()
        if not isinstance(payload.get("keys"), dict):
            payload["keys"] = {}
        with open(maintenance_access_key_file, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
    except Exception:
        pass


def generate_maintenance_access_key(
    note: str = "",
    created_by: str = "admin",
    max_questions: int | None = None,
) -> Dict[str, Any]:
    state = read_maintenance_access_key_state()
    keys = state.setdefault("keys", {})
    max_uses = max(1, int(max_questions or maintenance_access_key_max_questions or 5))

    for _ in range(20):
        raw = base64.urlsafe_b64encode(os.urandom(6)).decode("utf-8").rstrip("=")
        key = normalize_maintenance_access_key(f"AK-{raw}")
        if key not in keys:
            break
    else:
        key = normalize_maintenance_access_key(f"AK-{int(time.time())}")

    record = {
        "key": key,
        "active": True,
        "max_uses": max_uses,
        "used": 0,
        "note": str(note or "").strip(),
        "created_at": _maintenance_now_text(),
        "created_by": str(created_by or "admin"),
        "last_used_at": "",
        "last_used_by": "",
    }
    keys[key] = record
    write_maintenance_access_key_state(state)
    return record


def validate_maintenance_access_key(value: Any) -> Dict[str, Any]:
    key = normalize_maintenance_access_key(value)
    state = read_maintenance_access_key_state()
    record = (state.get("keys") or {}).get(key)

    if not key:
        return {"valid": False, "key": "", "reason": "empty", "remaining": 0}
    if not isinstance(record, dict):
        return {"valid": False, "key": key, "reason": "not_found", "remaining": 0}
    if not bool(record.get("active", True)):
        return {"valid": False, "key": key, "reason": "inactive", "remaining": 0, "record": record}

    max_uses = max(1, int(record.get("max_uses") or maintenance_access_key_max_questions or 5))
    used = max(0, int(record.get("used") or 0))
    remaining = max(0, max_uses - used)

    if remaining <= 0:
        return {"valid": False, "key": key, "reason": "quota_exhausted", "remaining": 0, "record": record}

    return {
        "valid": True,
        "key": key,
        "reason": "ok",
        "remaining": remaining,
        "max_uses": max_uses,
        "used": used,
        "record": record,
    }


def consume_maintenance_access_question(value: Any, used_by: str = "web-public") -> Dict[str, Any]:
    key = normalize_maintenance_access_key(value)
    validation = validate_maintenance_access_key(key)
    if not validation.get("valid"):
        validation["allowed"] = False
        return validation

    state = read_maintenance_access_key_state()
    keys = state.setdefault("keys", {})
    record = keys.get(key)
    if not isinstance(record, dict):
        validation["allowed"] = False
        validation["reason"] = "not_found"
        return validation

    record["used"] = int(record.get("used") or 0) + 1
    record["last_used_at"] = _maintenance_now_text()
    record["last_used_by"] = str(used_by or "web-public")
    keys[key] = record
    write_maintenance_access_key_state(state)

    refreshed = validate_maintenance_access_key(key)
    refreshed["allowed"] = True
    refreshed["used_now"] = int(record.get("used") or 0)
    return refreshed


def revoke_maintenance_access_key(value: Any, revoked_by: str = "admin") -> Dict[str, Any]:
    key = normalize_maintenance_access_key(value)
    state = read_maintenance_access_key_state()
    keys = state.setdefault("keys", {})
    record = keys.get(key)
    if not isinstance(record, dict):
        return {"ok": False, "key": key, "reason": "not_found"}

    record["active"] = False
    record["revoked_at"] = _maintenance_now_text()
    record["revoked_by"] = str(revoked_by or "admin")
    keys[key] = record
    write_maintenance_access_key_state(state)
    return {"ok": True, "key": key, "record": record}


def get_current_maintenance_access_key_status() -> Dict[str, Any]:
    status = validate_maintenance_access_key(st.session_state.get("maintenance_access_key", ""))
    st.session_state.maintenance_access_key_status = status
    return status


def render_maintenance_access_key_active_notice(status: Dict[str, Any]) -> None:
    remaining = int(status.get("remaining") or 0)
    key = str(status.get("key") or "")
    st.success(f"Access key akses terbatas aktif. Sisa kuota: {remaining} pertanyaan.")
    with st.expander("Detail access key", expanded=False):
        st.code(key)
        if st.button("Keluar dari access key", use_container_width=True, key="maintenance_access_key_logout"):
            st.session_state.maintenance_access_key = ""
            st.session_state.maintenance_access_key_status = {}
            st.rerun()


def render_maintenance_access_key_form(state: Dict[str, Any] | None = None) -> None:
    st.info(
        "Jika Anda punya access key dari admin, masukkan key agar tetap bisa chat maksimal "
        f"{maintenance_access_key_max_questions} pertanyaan selama maintenance."
    )
    with st.form("maintenance_access_key_form", clear_on_submit=False):
        key_value = st.text_input(
            "Access key akses terbatas",
            value=str(st.session_state.get("maintenance_access_key_input", "")),
            placeholder="Contoh: AK-XXXXXX",
        )
        submitted = st.form_submit_button("Gunakan access key")

    if submitted:
        key = normalize_maintenance_access_key(key_value)
        status = validate_maintenance_access_key(key)
        if status.get("valid"):
            st.session_state.maintenance_access_key = key
            st.session_state.maintenance_access_key_input = ""
            st.session_state.maintenance_access_key_status = status
            st.success(f"Access key valid. Sisa kuota: {int(status.get('remaining') or 0)} pertanyaan.")
            st.rerun()

        reason = status.get("reason")
        if reason == "quota_exhausted":
            st.error("Access key sudah habis kuotanya.")
        elif reason == "inactive":
            st.error("Access key sudah tidak aktif.")
        else:
            st.error("Access key tidak valid.")


def maintenance_access_key_summary() -> Dict[str, Any]:
    state = read_maintenance_access_key_state()
    keys = state.get("keys") or {}
    total = len(keys)
    active = 0
    exhausted = 0
    for record in keys.values():
        if not isinstance(record, dict):
            continue
        max_uses = max(1, int(record.get("max_uses") or maintenance_access_key_max_questions or 5))
        used = max(0, int(record.get("used") or 0))
        if bool(record.get("active", True)) and used < max_uses:
            active += 1
        if used >= max_uses:
            exhausted += 1
    return {"total": total, "active": active, "exhausted": exhausted}


def is_maintenance_locked() -> bool:
    return bool(read_maintenance_lock_state().get("locked"))


def maintenance_state_signature(state: Dict[str, Any]) -> str:
    return "|".join(
        [
            "locked" if state.get("locked") else "unlocked",
            str(state.get("locked_until_ts") or ""),
            str(state.get("updated_at") or ""),
            str(state.get("auto_unlocked_at") or ""),
        ]
    )


def maintenance_until_text_from_ts(
    locked_until_ts: float | int | str | None,
) -> str:
    """Compatibility shim: timed maintenance lock sudah dinonaktifkan."""
    return ""


def parse_maintenance_until_datetime(
    until_date: Any,
    until_time: Any,
    timezone_label: str = "WIB",
) -> Tuple[float, str]:
    """Compatibility shim: timed maintenance lock sudah dinonaktifkan."""
    return 0, ""

def render_maintenance_realtime_status(
    initial_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Render status maintenance tanpa fragment, reload, script, atau rerun otomatis.

    Ini mode paling aman untuk menghindari React error #185.
    Status lock tetap dibaca saat rerun normal Streamlit.
    """
    state = read_maintenance_lock_state()
    st.session_state.maintenance_lock_signature = maintenance_state_signature(state)

    if state.get("locked"):
        render_maintenance_banner(state)

    return state


def render_maintenance_locked_public_guard(
    state: Dict[str, Any] | None = None,
) -> None:
    """Paksa sembunyikan chat UI ketika maintenance lock aktif.

    Ini guard defensif agar tombol/elemen lama seperti `Chat baru` tidak tetap
    terlihat dari render sebelumnya.
    """
    if not bool(maintenance_hide_chat_when_locked):
        return

    state = state or read_maintenance_lock_state()

    if not state.get("locked"):
        return

    st.session_state.pending_prompt = ""

    st.markdown(
        """
        <style>
        div[data-testid="stChatInput"],
        section[data-testid="stChatInput"],
        div.stChatInput,
        .stChatInput {
            display: none !important;
            visibility: hidden !important;
            pointer-events: none !important;
            height: 0 !important;
            min-height: 0 !important;
            overflow: hidden !important;
        }

        div[data-testid="stButton"] {
            display: none !important;
            visibility: hidden !important;
            pointer-events: none !important;
            height: 0 !important;
            min-height: 0 !important;
            overflow: hidden !important;
        }

        .chat-input-safe-space {
            display: none !important;
            height: 0 !important;
            min-height: 0 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_maintenance_safe_meta_refresh(
    state: Dict[str, Any] | None = None,
    is_admin: bool = False,
) -> None:
    """Auto-refresh aman untuk sinkronisasi lock/unlock.

    Tidak memakai JavaScript, st.fragment, components.html, setTimeout,
    atau st.rerun otomatis. Hanya memakai meta refresh HTML.
    """
    if is_admin:
        return

    if not bool(maintenance_auto_refresh_enabled):
        return

    state = state or read_maintenance_lock_state()
    locked = bool(state.get("locked"))

    if not locked and not bool(maintenance_auto_refresh_when_unlocked):
        return

    interval = max(
        5,
        min(60, int(maintenance_auto_refresh_interval_seconds or 8)),
    )
    status_text = "lock aktif" if locked else "cek lock"

    st.markdown(
        f"""
        <div class="maintenance-refresh-note">
            🔄 Auto-check {status_text} setiap {interval} detik
        </div>
        <meta http-equiv="refresh" content="{interval}">
        """,
        unsafe_allow_html=True,
    )


def maintenance_public_message() -> str:
    state = read_maintenance_lock_state()
    message = str(state.get("message") or maintenance_default_message).strip()
    reason = str(state.get("reason") or "").strip()
    updated_at = str(state.get("updated_at") or "").strip()

    lines = [
        "🛠️ **Akses terbatas**",
        "",
        message,
    ]

    if reason:
        lines.append("")
        lines.append(f"Catatan admin: {reason}")

    if updated_at:
        lines.append("")
        lines.append(f"Status diperbarui: {updated_at}")

    return "\n".join(lines)


def render_maintenance_banner(state: Dict[str, Any] | None = None) -> None:
    state = state or read_maintenance_lock_state()
    if not state.get("locked"):
        return

    reason = str(state.get("reason") or "").strip()
    updated_at = str(state.get("updated_at") or "").strip()
    updated_by = str(state.get("updated_by") or "admin").strip()

    st.markdown(
        f"""
        <div class="maintenance-lock-banner">
            <div class="maintenance-lock-icon">🛠️</div>
            <div>
                <div class="maintenance-lock-title">Akses terbatas</div>
                <div class="maintenance-lock-text">
                    Chat publik sedang dalam akses terbatas. Hanya admin atau user dengan access key yang dapat menggunakan Adioranye sampai akses dibuka kembali.
                </div>
                <div class="maintenance-lock-meta">
                    {_html_escape(reason or "Manual unlock")}
                    {" • " if updated_at else ""}
                    {_html_escape(updated_at)}
                    {" • " if updated_by else ""}
                    {_html_escape(updated_by)}
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def looks_like_technical_error(text: Any) -> bool:
    lowered = str(text or "").lower()
    if not lowered.strip():
        return False
    return any(pattern in lowered for pattern in TECHNICAL_ERROR_PATTERNS)


def sanitize_public_ai_answer(
    answer_text: Any,
    meta: Dict[str, Any] | None = None,
    show_technical_detail: bool = False,
) -> str:
    """Hide raw provider/model errors from the public chat UI.

    Technical details are kept in metadata for admin debug, but visitors only see
    a short user-friendly message.
    """
    answer = str(answer_text or "").strip()
    if show_technical_detail or not looks_like_technical_error(answer):
        return answer

    if isinstance(meta, dict):
        meta["public_error_sanitized"] = True
        meta["hidden_public_error_detail"] = answer[:5000]

    return make_public_ai_error_message()



def is_retryable_model_error_answer(
    answer_text: Any,
    meta: Dict[str, Any] | None = None,
) -> bool:
    """Cek apakah jawaban perlu dicoba ulang ke model aktif lain."""
    answer = str(answer_text or "").strip()
    meta_data = meta or {}

    if not answer:
        return True

    if answer == str(PUBLIC_AI_ERROR_MESSAGE).strip():
        return True

    if bool(
        meta_data.get("public_error_sanitized")
        or meta_data.get("public_error_hidden")
        or meta_data.get("public_safe_message")
    ):
        return True

    if looks_like_technical_error(answer):
        return True

    return False


def _simple_unique_models(
    models: List[str],
) -> List[str]:
    seen = set()
    result: List[str] = []

    for model in models:
        model_name = str(model or "").strip()

        if not model_name or model_name in seen:
            continue

        seen.add(model_name)
        result.append(model_name)

    return result




def build_local_safe_fallback_answer(
    user_text: str,
    failure_reason: str = "",
) -> Tuple[str, Dict[str, Any]]:
    """Jawaban lokal terakhir jika semua model gagal.

    Tujuannya bukan menggantikan AI, tetapi mencegah pesan gangguan untuk
    permintaan umum yang masih bisa dijawab dengan template aman.
    """
    text = str(user_text or "").strip()
    lower = text.lower()

    # Fallback spesifik yang diminta user: ransum kuda.
    if "ransum" in lower and ("kuda" in lower or "horse" in lower):
        answer = """Berikut contoh **draft ransum kuda** yang bisa dijadikan acuan awal. Ini perlu disesuaikan lagi dengan **bobot badan, umur, aktivitas, kondisi kesehatan, dan kualitas hijauan**.

### Contoh ransum harian kuda dewasa ±400 kg kerja ringan

**1. Hijauan utama**
- Rumput/hay: ±6–8 kg per hari.
- Berikan dalam beberapa kali pemberian, jangan sekaligus terlalu banyak.
- Hijauan sebaiknya menjadi porsi terbesar karena pencernaan kuda bergantung pada serat.

**2. Konsentrat/energi tambahan**
- Dedak halus/bekatul: ±0,5–1 kg per hari.
- Jagung giling/oat: ±0,5–1 kg per hari.
- Jangan langsung memberi porsi besar; naikkan bertahap selama beberapa hari.

**3. Protein tambahan**
- Bungkil kedelai atau sumber protein lain: ±0,2–0,4 kg per hari.
- Pakai secukupnya, terutama bila kuda sedang latihan, pemulihan, atau bobot badannya kurang.

**4. Mineral dan garam**
- Garam mineral/block mineral: tersedia bebas atau ±30–50 gram per hari.
- Air minum bersih harus selalu tersedia.

### Pola pemberian sederhana
- Pagi: rumput/hay + sedikit konsentrat.
- Siang: rumput/hay.
- Sore/malam: rumput/hay + konsentrat.
- Hindari memberi konsentrat banyak dalam satu waktu.

### Catatan penting
- Untuk kuda ±400 kg, total pakan kering umumnya sekitar **1,5–2,5% dari bobot badan per hari**.
- Perubahan ransum harus bertahap agar tidak mengganggu pencernaan.
- Jika kuda kurus, bunting, menyusui, sakit, atau kerja berat, formulanya harus dihitung ulang.
- Untuk ransum final, sebaiknya konsultasikan dengan dokter hewan atau ahli nutrisi ternak/kuda.

Jika datanya tersedia, ransum bisa dihitung lebih tepat berdasarkan:
**bobot kuda, umur, jenis aktivitas, kondisi tubuh, jenis rumput, dan bahan pakan yang tersedia.**"""
        return answer, {
            "local_safe_fallback_used": True,
            "local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    # Fallback umum untuk permintaan "buatkan" yang tidak membutuhkan info terkini.
    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        answer = (
            "Model sedang tidak stabil, jadi saya buatkan **draft awal** secara lokal agar pekerjaan tetap bisa lanjut.\n\n"
            "Silakan kirim detail tambahan seperti tujuan, format, panjang jawaban, dan data yang harus dipakai agar hasilnya bisa disesuaikan lebih tepat."
        )
        return answer, {
            "local_safe_fallback_used": True,
            "local_safe_fallback_type": "generic_draft",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    return "", {}



def extract_explicit_failed_models_from_meta(
    meta: Dict[str, Any] | None,
    original_model: str = "",
) -> List[str]:
    """Ambil model yang benar-benar terindikasi gagal.

    Jangan masukkan seluruh fallback list sebagai failed, karena pada thinking mode
    fallback mungkin belum dicoba ulang di layer luar.
    """
    meta_data = meta or {}
    failed: List[str] = []

    if original_model:
        failed.append(str(original_model).strip())

    direct_keys = [
        "failed_model",
        "error_model",
        "model_failed",
        "model_requested",
    ]

    for key in direct_keys:
        value = str(meta_data.get(key) or "").strip()
        if value:
            failed.append(value)

    list_keys = [
        "failed_models",
        "blocked_models",
        "runtime_failed_models",
    ]

    for key in list_keys:
        value = meta_data.get(key)
        if isinstance(value, (list, tuple, set)):
            failed.extend(
                str(item or "").strip()
                for item in value
                if str(item or "").strip()
            )

    return _simple_unique_models(
        [
            item
            for item in failed
            if item
        ]
    )



def get_active_model_retry_candidates(
    failed_models: List[str] | None = None,
) -> List[str]:
    """Ambil kandidat model aktif lain dari health check dan fallback list."""
    failed_set = {
        str(model or "").strip()
        for model in (failed_models or [])
        if str(model or "").strip()
    }

    health_cache = st.session_state.get("model_health_cache") or {}

    active_from_health = [
        model_name
        for model_name, item in health_cache.items()
        if isinstance(item, dict)
        and item.get("active")
        and str(model_name or "").strip() not in failed_set
    ]

    def latency_value(model_name: str) -> float:
        try:
            value = health_cache.get(model_name, {}).get("latency_ms")
            if value is not None:
                return float(value)
        except Exception:
            pass

        return 999999.0

    active_from_health = sorted(
        active_from_health,
        key=latency_value,
    )

    fallback_lists: List[str] = []
    fallback_lists.extend(
        st.session_state.get("active_cheap_fallback_models")
        or DEFAULT_CHEAP_FALLBACK_MODELS.copy()
    )
    fallback_lists.extend(
        st.session_state.get("active_medium_fallback_models")
        or MEDIUM_MODEL_OPTIONS.copy()
    )
    fallback_lists.extend(
        st.session_state.get("active_expensive_fallback_models")
        or DEFAULT_EXPENSIVE_FALLBACK_MODELS.copy()
    )
    fallback_lists.extend(DEFAULT_FALLBACK_MODELS.copy())
    fallback_lists.extend(TOP_USAGE_MODEL_CANDIDATES)

    candidates = sort_health_models_for_simple_chat(
        active_from_health + fallback_lists,
        health_cache,
    ) or _simple_unique_models(
        active_from_health
        + fallback_lists
    )

    return [
        model_name
        for model_name in candidates
        if model_name not in failed_set
    ]


def retry_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> Tuple[str, Dict[str, Any]]:
    """Retry pertanyaan yang sama memakai model aktif lain.

    Dipakai saat hasil awal adalah pesan gangguan/koneksi/model.
    """
    enabled = parse_bool(
        get_secret("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", True),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not is_retryable_model_error_answer(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            int(get_secret("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", 3) or 3),
            5,
        ),
    )
    timeout_seconds = int(
        get_secret("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", 35) or 35
    )

    try:
        maybe_auto_refresh_model_status(
            reason="retry-after-thinking-or-model-error",
        )
    except Exception:
        pass

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = extract_explicit_failed_models_from_meta(
        original_meta,
        original_model=original_model,
    )

    candidates = get_active_model_retry_candidates(
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["auto_model_retry_enabled"] = True
        meta_data["auto_model_retry_attempts"] = 0
        meta_data["auto_model_retry_reason"] = "no-active-candidate"
        return original_answer, meta_data

    tried_models: List[str] = []
    retry_errors: List[str] = []

    for candidate_model in candidates[:max_attempts]:
        tried_models.append(candidate_model)

        retry_kwargs = dict(original_kwargs)
        retry_kwargs["model"] = candidate_model
        retry_kwargs["fallback_models"] = [
            model
            for model in candidates
            if model != candidate_model
        ][:max_attempts]
        retry_kwargs["expensive_fallback_models"] = []
        retry_kwargs["allow_expensive_fallback"] = True
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            int(retry_kwargs.get("timeout") or timeout_seconds),
            timeout_seconds,
        )
        retry_kwargs["_auto_retry_depth"] = retry_depth + 1

        try:
            retry_answer, retry_meta = safe_generate_power_answer(
                **retry_kwargs
            )
        except Exception as exc:
            retry_errors.append(
                f"{candidate_model}: {exc.__class__.__name__}: {str(exc)[:180]}"
            )
            continue

        if not isinstance(retry_meta, dict):
            retry_meta = {}

        retry_answer_text = str(retry_answer or "").strip()

        if not is_retryable_model_error_answer(
            retry_answer_text,
            meta=retry_meta,
        ):
            retry_meta["auto_model_retry_success"] = True
            retry_meta["auto_model_retry_attempts"] = len(tried_models)
            retry_meta["auto_model_retry_models"] = tried_models
            retry_meta["auto_model_retry_from_model"] = original_model
            retry_meta["auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["auto_model_retry_errors"] = retry_errors
            return retry_answer_text, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["auto_model_retry_enabled"] = True
    meta_data["auto_model_retry_success"] = False
    meta_data["auto_model_retry_attempts"] = len(tried_models)
    meta_data["auto_model_retry_models"] = tried_models
    meta_data["auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_public_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data



def safe_generate_power_answer(**kwargs: Any) -> Tuple[str, Dict[str, Any]]:
    """Call generate_power_answer safely across mixed app/power_features versions.

    This also retries public model/connection error answers with another
    health-checked active model, so the user does not immediately see the
    generic connection/model failure message.
    """
    retry_depth = int(kwargs.pop("_auto_retry_depth", 0) or 0)
    original_kwargs = dict(kwargs)

    try:
        signature = inspect.signature(generate_power_answer)
        parameters = signature.parameters
        accepts_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()
        )

        if accepts_kwargs:
            answer, meta = generate_power_answer(**kwargs)
            return retry_power_answer_with_active_models(
                str(answer or ""),
                meta if isinstance(meta, dict) else {},
                original_kwargs,
                retry_depth=retry_depth,
            )

        filtered_kwargs = {
            key: value for key, value in kwargs.items() if key in parameters
        }
        dropped_keys = sorted(set(kwargs) - set(filtered_kwargs))
        answer, meta = generate_power_answer(**filtered_kwargs)

        if not isinstance(meta, dict):
            meta = {}

        if dropped_keys:
            meta["power_answer_compat_dropped_kwargs"] = dropped_keys

        return retry_power_answer_with_active_models(
            str(answer or ""),
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        # Extra fallback for older Python signatures or partially deployed files.
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)
        if match:
            bad_key = match.group(1)
            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)
                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])
                    if bad_key not in dropped:
                        dropped.append(bad_key)
                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)
                return answer, meta

        if retry_depth <= 0:
            retry_answer, retry_meta = retry_power_answer_with_active_models(
                make_public_ai_error_message(),
                {
                    "public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_public_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not is_retryable_model_error_answer(
                retry_answer,
                meta=retry_meta,
            ):
                return retry_answer, retry_meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            retry_answer, retry_meta = retry_power_answer_with_active_models(
                make_public_ai_error_message(),
                {
                    "public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_public_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not is_retryable_model_error_answer(
                retry_answer,
                meta=retry_meta,
            ):
                return retry_answer, retry_meta

            if isinstance(retry_meta, dict):
                retry_meta["public_error_sanitized"] = True
                return make_public_ai_error_message(), retry_meta

        raise


def format_token_status(label: str, value: str) -> None:
    if value:
        st.success(f"{label} terdeteksi")
    else:
        st.error(f"{label} belum diisi")


def mask_secret_value(value: Any, keep_start: int = 4, keep_end: int = 4) -> str:
    """Mask token/API key for admin UI."""
    raw = str(value or "")
    if not raw:
        return "belum diisi"
    if len(raw) <= keep_start + keep_end + 3:
        return "•" * max(6, len(raw))
    return raw[:keep_start] + "…" + raw[-keep_end:]


def validate_runtime_secrets() -> List[Dict[str, Any]]:
    """Return runtime config validation rows without exposing sensitive values."""
    checks = [
        ("ADMIN_USERNAME", admin_username, True, "Login admin web"),
        ("ADMIN_PASSWORD", admin_password, True, "Login admin web"),
        ("SLASHAI_API_KEY", api_key, True, "Wajib untuk memanggil model"),
        ("SLASHAI_API_URL", api_url, True, "Endpoint chat completions"),
        ("SLASHAI_MODEL", default_model, True, "Model default"),
        (
            "TELEGRAM_BOT_TOKEN",
            telegram_token,
            bool(auto_start),
            "Wajib jika Telegram auto-start",
        ),
        (
            "TELEGRAM_ADMIN_CHAT_IDS",
            get_secret("TELEGRAM_ADMIN_CHAT_IDS", ""),
            False,
            "Disarankan agar command admin Telegram tidak terbuka",
        ),
        (
            "POWER_FEATURES_ENABLED",
            power_features_enabled,
            False,
            "Fitur memory/RAG/usage/optimizer",
        ),
        ("POWER_DB_PATH", power_db_path, False, "Database SQLite power features"),
        ("POWER_RAG_ENABLED", power_rag_enabled, False, "Knowledge Base / RAG"),
        ("POWER_RAG_TOP_K", power_rag_top_k, False, "Jumlah potongan KB yang dipakai"),
        (
            "POWER_STRICT_RAG_MODE",
            power_strict_rag_mode,
            False,
            "Jika aktif, AI hanya menjawab jika KB cukup",
        ),
        ("POWER_KB_MAX_FILE_MB", power_kb_max_file_mb, False, "Batas upload KB"),
        (
            "KB_SCRAPER_SOURCES_FILE",
            kb_scraper_sources_file,
            False,
            "Daftar sumber auto-update KB",
        ),
        (
            "KB_SCRAPER_MAX_ITEMS_PER_SOURCE",
            kb_scraper_max_items_per_source,
            False,
            "Batas item per sumber saat update KB",
        ),
    ]
    rows: List[Dict[str, Any]] = []
    for name, value, required, note in checks:
        ok = bool(value) if not isinstance(value, bool) else True
        if name == "TELEGRAM_BOT_TOKEN" and not auto_start:
            ok = True
        if required and not value:
            level = "error"
        elif name == "TELEGRAM_ADMIN_CHAT_IDS" and not str(value or "").strip():
            level = "warning"
        else:
            level = "ok" if ok else "warning"
        rows.append(
            {
                "status": (
                    "✅ OK"
                    if level == "ok"
                    else ("⚠️ Perlu dicek" if level == "warning" else "❌ Kurang")
                ),
                "secret": name,
                "nilai": (
                    mask_secret_value(value)
                    if any(key in name for key in ["TOKEN", "KEY", "PASSWORD"])
                    else str(value)
                ),
                "keterangan": note,
            }
        )
    return rows


def check_optional_dependency(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except Exception:
        return False


def file_size_label(path: str) -> str:
    try:
        size = os.path.getsize(path)
    except Exception:
        return "0 B"
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def read_file_bytes_safe(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return b""


def _pdf_escape(text: str) -> str:
    """Escape teks untuk content stream PDF sederhana."""
    return str(text or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _clean_text_for_pdf(text: str) -> str:
    """Bersihkan markdown ringan agar nyaman dibaca di PDF."""
    clean = str(text or "")
    clean = re.sub(r"```([a-zA-Z0-9_+-]*)\n", "", clean)
    clean = clean.replace("```", "")
    clean = re.sub(r"`([^`]*)`", r"\1", clean)
    clean = re.sub(r"\*\*([^*]+)\*\*", r"\1", clean)
    clean = re.sub(r"\*([^*]+)\*", r"\1", clean)
    clean = re.sub(r"__([^_]+)__", r"\1", clean)
    clean = re.sub(r"_([^_]+)_", r"\1", clean)
    clean = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1 (\2)", clean)
    clean = re.sub(r"<[^>]+>", "", clean)
    clean = clean.replace("\r\n", "\n").replace("\r", "\n")
    # Built-in Helvetica PDF aman untuk Latin-1. Karakter lain diganti agar PDF tidak rusak.
    clean = clean.encode("latin-1", "replace").decode("latin-1")
    return clean.strip()


def _wrap_pdf_text(text: str, max_chars: int = 92) -> List[str]:
    """Wrap teks tanpa dependency eksternal."""
    lines: List[str] = []
    for paragraph in _clean_text_for_pdf(text).split("\n"):
        raw = paragraph.strip()
        if not raw:
            lines.append("")
            continue
        while len(raw) > max_chars:
            split_at = raw.rfind(" ", 0, max_chars + 1)
            if split_at <= 20:
                split_at = max_chars
            lines.append(raw[:split_at].strip())
            raw = raw[split_at:].strip()
        lines.append(raw)
    return lines


def make_answer_pdf_bytes(
    answer_text: str, title: str = "Jawaban Adioranye AI", meta_text: str = ""
) -> bytes:
    """Buat PDF teks sederhana agar hasil jawaban web bisa diunduh.

    Sengaja tidak memakai reportlab/fpdf supaya tidak menambah dependency di hosting online.
    PDF memakai font Helvetica bawaan PDF viewer.
    """
    page_width = 595
    page_height = 842
    left = 48
    top = 790
    bottom = 54
    line_height = 15
    max_chars = 92

    title_clean = _clean_text_for_pdf(title or "Jawaban Adioranye AI")[:120]
    meta_clean = _clean_text_for_pdf(meta_text or "")
    body_lines = _wrap_pdf_text(answer_text, max_chars=max_chars)

    pages: List[List[str]] = []
    current: List[str] = []
    usable_lines = int((top - bottom - 46) / line_height)
    for line in body_lines:
        if len(current) >= usable_lines:
            pages.append(current)
            current = []
        current.append(line)
    pages.append(current or [""])

    objects: List[bytes] = []

    def add_obj(payload: str | bytes) -> int:
        if isinstance(payload, str):
            payload_b = payload.encode("latin-1", "replace")
        else:
            payload_b = payload
        objects.append(payload_b)
        return len(objects)

    catalog_id = add_obj("<< /Type /Catalog /Pages 2 0 R >>")
    pages_id = add_obj(b"")
    font_id = add_obj("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: List[int] = []

    for idx, page_lines in enumerate(pages, start=1):
        stream_lines: List[str] = [
            "BT",
            f"/F1 16 Tf {left} {top} Td ({_pdf_escape(title_clean)}) Tj",
            f"/F1 9 Tf 0 -18 Td ({_pdf_escape('Dibuat: ' + _wib_now_text())}) Tj",
        ]
        if meta_clean:
            stream_lines.append(f"0 -13 Td ({_pdf_escape(meta_clean[:120])}) Tj")
        stream_lines.append(
            f"/F1 11 Tf 0 -24 Td ({_pdf_escape(page_lines[0] if page_lines else '')}) Tj"
        )
        for line in page_lines[1:]:
            if line == "":
                stream_lines.append(f"0 -{line_height} Td ( ) Tj")
            else:
                stream_lines.append(f"0 -{line_height} Td ({_pdf_escape(line)}) Tj")
        footer = f"Halaman {idx} dari {len(pages)}"
        stream_lines.extend(
            [
                "ET",
                "BT",
                f"/F1 9 Tf {left} 30 Td ({_pdf_escape(footer)}) Tj",
                "ET",
            ]
        )
        stream = "\n".join(stream_lines).encode("latin-1", "replace")
        content_id = add_obj(
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
        page_id = add_obj(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        )
        page_ids.append(page_id)

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[pages_id - 1] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1")
    )

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{i} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        pdf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(pdf)


def answer_pdf_download_button(
    answer_text: str,
    key: str,
    model_name: str = "",
) -> None:
    """Render link kecil download PDF untuk sebuah jawaban assistant."""
    if not str(answer_text or "").strip():
        return

    meta_text = f"Model: {model_name}" if model_name else ""
    filename = (
        "jawaban-adioranye-"
        f"{datetime.now(WIB_TZ).strftime('%Y%m%d-%H%M%S')}"
        ".pdf"
    )
    pdf_bytes = make_answer_pdf_bytes(
        answer_text,
        meta_text=meta_text,
    )
    pdf_base64 = base64.b64encode(pdf_bytes).decode("ascii")
    safe_filename = html.escape(
        filename,
        quote=True,
    )
    link_id = html.escape(
        str(key or "download-pdf"),
        quote=True,
    )

    st.markdown(
        f"""
        <div class="answer-pdf-link-wrap" id="{link_id}">
            <a
                class="answer-pdf-link"
                href="data:application/pdf;base64,{pdf_base64}"
                download="{safe_filename}"
                title="Download jawaban ke PDF"
            >
                Klik disini untuk download PDF
            </a>
        </div>
        """,
        unsafe_allow_html=True,
    )


def init_state() -> None:
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []
    if "admin_authenticated" not in st.session_state:
        st.session_state.admin_authenticated = False
    if "active_model" not in st.session_state:
        st.session_state.active_model = str(
            get_secret("SLASHAI_MODEL", "slashai/gpt-5-nano")
        )
    if "active_persona" not in st.session_state:
        st.session_state.active_persona = str(
            get_secret("ASSISTANT_PERSONA", DEFAULT_PERSONA)
        )
    if "active_temperature" not in st.session_state:
        st.session_state.active_temperature = float(
            get_secret("TEMPERATURE", 0.3) or 0.3
        )
    if "active_max_tokens" not in st.session_state:
        st.session_state.active_max_tokens = int(
            get_secret("MAX_COMPLETION_TOKENS", 2600) or 2600
        )
    if "show_debug" not in st.session_state:
        st.session_state.show_debug = False
    if "active_smart_router" not in st.session_state:
        st.session_state.active_smart_router = parse_bool(
            get_secret("SMART_MODEL_ROUTER", True), default=True
        )
    if "active_return_to_primary" not in st.session_state:
        st.session_state.active_return_to_primary = parse_bool(
            get_secret("RETURN_TO_PRIMARY_MODEL", True), default=True
        )
    if "active_max_smart_models" not in st.session_state:
        st.session_state.active_max_smart_models = int(
            get_secret("MAX_SMART_MODELS", 2) or 2
        )
    if "allow_expensive_fallback" not in st.session_state:
        st.session_state.allow_expensive_fallback = parse_bool(
            get_secret("ALLOW_EXPENSIVE_FALLBACK", True), default=True
        )
    if "max_expensive_models" not in st.session_state:
        st.session_state.max_expensive_models = int(
            get_secret("MAX_EXPENSIVE_MODELS", 1) or 1
        )
    if "last_answer_meta" not in st.session_state:
        st.session_state.last_answer_meta = {}
    if "pending_prompt" not in st.session_state:
        st.session_state.pending_prompt = ""
    if "active_default_memory" not in st.session_state:
        st.session_state.active_default_memory = str(
            get_secret("DEFAULT_MEMORY_CONTEXT", DEFAULT_MEMORY_CONTEXT)
        )
    if "model_health_cache" not in st.session_state:
        st.session_state.model_health_cache = {}
    if "model_health_checked_at" not in st.session_state:
        st.session_state.model_health_checked_at = 0.0
    if "active_cheap_fallback_models" not in st.session_state:
        st.session_state.active_cheap_fallback_models = (
            DEFAULT_CHEAP_FALLBACK_MODELS.copy()
        )
    if "active_expensive_fallback_models" not in st.session_state:
        st.session_state.active_expensive_fallback_models = (
            DEFAULT_EXPENSIVE_FALLBACK_MODELS.copy()
        )
    if "active_medium_fallback_models" not in st.session_state:
        st.session_state.active_medium_fallback_models = (
            MEDIUM_MODEL_OPTIONS.copy()
        )
    if "last_model_health_error" not in st.session_state:
        st.session_state.last_model_health_error = ""
    if "active_rotate_cheap_primary" not in st.session_state:
        st.session_state.active_rotate_cheap_primary = parse_bool(
            get_secret("ROTATE_CHEAP_PRIMARY", True), default=True
        )
    if "cheap_model_rotation_index" not in st.session_state:
        st.session_state.cheap_model_rotation_index = 0
    if "last_rotated_primary_model" not in st.session_state:
        st.session_state.last_rotated_primary_model = ""
    if "active_use_streamlit_cache_memory" not in st.session_state:
        st.session_state.active_use_streamlit_cache_memory = parse_bool(
            get_secret("USE_STREAMLIT_CACHE_MEMORY", True), default=True
        )
    if "active_thinking_model_router" not in st.session_state:
        st.session_state.active_thinking_model_router = parse_bool(
            get_secret("THINKING_MODEL_ROUTER", True), default=True
        )
    if "active_thinking_min_chars" not in st.session_state:
        st.session_state.active_thinking_min_chars = int(
            get_secret("THINKING_MIN_CHARS", 180) or 180
        )
    if "active_fast_normal_model_router" not in st.session_state:
        st.session_state.active_fast_normal_model_router = parse_bool(
            get_secret("FAST_NORMAL_MODEL_ROUTER", True), default=True
        )
    if "dynamic_api_models" not in st.session_state:
        st.session_state.dynamic_api_models = []
    if "dynamic_api_models_checked_at" not in st.session_state:
        st.session_state.dynamic_api_models_checked_at = 0.0
    if "dynamic_model_discovery_error" not in st.session_state:
        st.session_state.dynamic_model_discovery_error = ""
    if "dynamic_model_discovery_source" not in st.session_state:
        st.session_state.dynamic_model_discovery_source = ""
    if "active_operation_mode" not in st.session_state:
        st.session_state.active_operation_mode = str(
            get_secret("AI_OPERATION_MODE", "Seimbang") or "Seimbang"
        )

    if "sound_enabled" not in st.session_state:
        st.session_state.sound_enabled = bool(message_effects_enabled and answer_sound_enabled)
    if "public_rate_events" not in st.session_state:
        st.session_state.public_rate_events = []
    if "model_runtime_blocks" not in st.session_state:
        st.session_state.model_runtime_blocks = {}
    if "public_usage_stats" not in st.session_state:
        st.session_state.public_usage_stats = {
            "total_questions": 0,
            "blocked_by_rate_limit": 0,
            "public_errors_hidden": 0,
            "model_blocks_created": 0,
            "last_error_summary": "",
            "last_error_at": "",
        }
    if "answer_streaming_preview_enabled" not in st.session_state:
        st.session_state.answer_streaming_preview_enabled = (
            parse_bool(
                get_secret("ANSWER_STREAMING_PREVIEW_ENABLED", False),
                default=False,
            )
            and not bool(frontend_ultra_safe_mode)
        )
    if "model_performance_stats" not in st.session_state:
        st.session_state.model_performance_stats = load_model_performance_stats_from_file()
    if "last_model_performance_event" not in st.session_state:
        st.session_state.last_model_performance_event = {}
    if "active_health_check_scope" not in st.session_state:
        st.session_state.active_health_check_scope = "quick"


    # Frontend React #185 safe guard:
    # paksa fitur frontend agresif mati walaupun session lama menyimpan True.
    if bool(frontend_ultra_safe_mode):
        st.session_state.sound_enabled = False
        st.session_state.answer_streaming_preview_enabled = False


# =========================
# Defaults
# =========================

DEFAULT_PERSONA = (
    "Nama kamu adalah adioranye. "
    "Kamu dibuat oleh Galuh Adi Insani. "
    "Peran kamu adalah asisten AI profesional yang cepat, teliti, ramah, dan sangat praktis. "
    "Gunakan bahasa Indonesia yang natural, sopan, jelas, dan tidak bertele-tele. "
    "Untuk pertanyaan sederhana, jawab langsung. Untuk tugas akademik, coding, bisnis, riset, dokumen, atau analisis, jawab terstruktur, bertahap, dan siap dipakai. "
    "Utamakan akurasi: jangan mengarang fakta, angka, sumber, hukum, medis, keuangan, atau informasi terbaru. Jika data belum cukup, jelaskan batasannya dan berikan langkah aman. "
    "Untuk pertanyaan waktu dalam bahasa Indonesia, gunakan zona waktu Indonesia sebagai acuan: WIB sebagai default, lalu WITA/WIT jika wilayahnya jelas. Jangan menjawab seolah-olah UTC adalah waktu lokal pengguna. "
    "Saat memperbaiki kode, sebutkan letak masalah, solusi inti, lalu berikan kode yang bisa langsung ditempel. "
    "Jika menulis kode, format kode harus vertikal ke bawah dengan line break yang rapi, bukan dipadatkan panjang ke samping. "
    "Pecah parameter, list, dictionary, command, dan chain method panjang ke beberapa baris agar nyaman dibaca di layar HP. "
    "Pecah parameter, dictionary, list, fungsi, dan command panjang ke beberapa baris agar mudah dibaca. "
    "Saat membuat tulisan, ikuti format pengguna dan gunakan gaya bahasa manusiawi, rapi, serta mudah dipahami. "
    "Jika permintaan berisiko atau melanggar aturan, tolak singkat dan arahkan ke alternatif yang aman."
)

DEFAULT_MEMORY_CONTEXT = """
Memory default Adioranye:
- Identitas: Adioranye dibuat oleh Galuh Adi Insani dan berperan sebagai asisten AI praktis untuk kebutuhan umum, akademik, teknis, bisnis, kreatif, coding, analisis data, strategi konten, dokumen, dan produktivitas.
- Gaya jawaban: profesional, ramah, jelas, ringkas untuk pertanyaan ringan, dan detail bertahap untuk pekerjaan kompleks.
- Prinsip akurasi: jangan mengarang. Untuk data terbaru, hukum, medis, keuangan, harga, jadwal, atau keputusan berisiko, sampaikan bahwa data perlu diverifikasi atau gunakan sumber yang tersedia.
- Konteks waktu: jika pengguna bertanya dalam bahasa Indonesia, gunakan waktu Indonesia. Default gunakan WIB, tetapi sesuaikan ke WITA atau WIT jika wilayah/kota pengguna jelas. UTC hanya dipakai sebagai referensi teknis, bukan dianggap waktu lokal pengguna.
- Sapaan waktu: jika pengguna hanya menyapa dengan selamat pagi/siang/sore/malam, sesuaikan sapaan dengan waktu Indonesia saat ini dan jawab sebagai sapaan, bukan sebagai pertanyaan.
- Cache pertanyaan: untuk pertanyaan umum yang sering muncul dan tidak bergantung pada waktu terkini, gunakan cache jawaban agar respons lebih cepat. Jangan cache berita, harga, jadwal, cuaca, atau info yang cepat berubah.
- Model sehat: jika model pilihan awal belum siap tetapi model sehat tersedia, otomatis gunakan model sehat agar status tetap siap.
- Auto-refresh status model: lakukan quick health check berkala secara ringan agar status model aktif terverifikasi tetap terbaru tanpa mengganggu chat publik.
- Retry gangguan model: jika jawaban awal adalah pesan gangguan koneksi/model, coba ulang pertanyaan yang sama memakai model aktif lain sebelum menampilkan pesan gagal.
- Sapaan: gunakan sapaan profesional/netral. Jangan memakai panggilan seperti kakak, bro, atau sejenisnya kecuali pengguna memintanya.
- Akademik: bantu dengan struktur rapi, bahasa natural, contoh konkret, dan penjelasan yang mudah dipahami.
- Coding/aplikasi: fokus pada diagnosis masalah, titik perubahan, kode siap tempel, dan langkah deploy yang realistis.
- Format kode: tulis kode ke bawah dengan baris yang rapi. Jangan menulis kode panjang dalam satu baris jika bisa dipecah ke beberapa baris. Untuk parameter, list, dictionary, command, CSS, HTML attribute, dan function call panjang, pecah menjadi beberapa baris dengan indentasi.
- Bisnis/konten/desain: berikan ide yang menarik, aman dipakai, mudah dieksekusi, dan sesuai platform.
- Dokumen/Knowledge Base: jika ada data internal atau dokumen yang relevan, prioritaskan data tersebut. Jika sumber tidak cukup, jangan memaksakan jawaban.
- Format: ikuti format pengguna. Jika format tidak disebutkan, gunakan struktur paling mudah dibaca.
- Ketidakjelasan: tetap berikan jawaban terbaik berdasarkan konteks, lalu sebutkan asumsi singkat yang dipakai.
""".strip()

CHEAP_MODEL_OPTIONS = list(
    dict.fromkeys(ALL_CHEAP_MODELS or DEFAULT_CHEAP_FALLBACK_MODELS.copy())
)
EXPENSIVE_MODEL_OPTIONS = list(
    dict.fromkeys(ALL_CAPABLE_MODELS or DEFAULT_EXPENSIVE_FALLBACK_MODELS.copy())
)
MODEL_OPTIONS = list(
    dict.fromkeys(
        ALL_SLASHAI_MODELS
        + TOP_USAGE_MODEL_CANDIDATES
        + CHEAP_MODEL_OPTIONS
        + EXPENSIVE_MODEL_OPTIONS
    )
)
MEDIUM_MODEL_OPTIONS = [
    model
    for model in MODEL_OPTIONS
    if model_cost_tier(model) in {"medium", "menengah"}
]
HIGH_COST_MODEL_OPTIONS = [
    model
    for model in MODEL_OPTIONS
    if model_cost_tier(model) not in {"cheap", "medium", "menengah"}
]

# Secrets
api_key = str(get_secret("SLASHAI_API_KEY", ""))
api_url = str(
    get_secret("SLASHAI_API_URL", "https://api.slashai.my.id/v1/chat/completions")
)
default_model = str(get_secret("SLASHAI_MODEL", "slashai/gpt-5-nano"))
telegram_token = str(get_secret("TELEGRAM_BOT_TOKEN", ""))
memory_file = str(get_secret("MEMORY_FILE", "assistant_memory.json"))
persona_from_secret = str(get_secret("ASSISTANT_PERSONA", DEFAULT_PERSONA))
default_memory_context_from_secret = str(
    get_secret("DEFAULT_MEMORY_CONTEXT", DEFAULT_MEMORY_CONTEXT)
)
auto_start = parse_bool(get_secret("TELEGRAM_AUTO_START", False), default=False)
drop_pending_updates = parse_bool(
    get_secret("TELEGRAM_DROP_PENDING_UPDATES", True), default=True
)
send_processing_message = parse_bool(
    get_secret("TELEGRAM_SEND_PROCESSING_MESSAGE", False), default=False
)
telegram_parse_mode = str(get_secret("TELEGRAM_PARSE_MODE", "") or "")
telegram_status_test_cache_ttl_seconds = int(
    get_secret("TELEGRAM_STATUS_TEST_CACHE_TTL_SECONDS", 60) or 60
)
telegram_status_test_timeout_seconds = int(
    get_secret("TELEGRAM_STATUS_TEST_TIMEOUT_SECONDS", 12) or 12
)
telegram_lock_file = str(
    get_secret("TELEGRAM_LOCK_FILE", "/tmp/adioranye_telegram_bot_worker.lock")
)
maintenance_lock_file = str(
    get_secret(
        "AKSES_TERBATAS_LOCK_FILE",
        get_secret("MAINTENANCE_LOCK_FILE", ".adioranye_maintenance_lock.json"),
    )
    or ".adioranye_maintenance_lock.json"
)
maintenance_access_key_file = str(
    get_secret(
        "AKSES_TERBATAS_KEY_FILE",
        get_secret("MAINTENANCE_ACCESS_KEY_FILE", ".adioranye_maintenance_access_keys.json"),
    )
    or ".adioranye_maintenance_access_keys.json"
)
maintenance_access_key_max_questions = int(
    get_secret(
        "AKSES_TERBATAS_KEY_MAX_QUESTIONS",
        get_secret("MAINTENANCE_ACCESS_KEY_MAX_QUESTIONS", 5),
    )
    or 5
)
akses_terbatas_auto_on_boot = parse_bool(
    get_secret(
        "AKSES_TERBATAS_AUTO_ON_BOOT",
        get_secret("MAINTENANCE_AUTO_LOCK_ON_BOOT", True),
    ),
    default=True,
)
akses_terbatas_boot_guard_file = str(
    get_secret(
        "AKSES_TERBATAS_BOOT_GUARD_FILE",
        get_secret("MAINTENANCE_BOOT_GUARD_FILE", ".adioranye_akses_terbatas_boot_guard.json"),
    )
    or ".adioranye_akses_terbatas_boot_guard.json"
)
akses_terbatas_boot_reason = str(
    get_secret(
        "AKSES_TERBATAS_BOOT_REASON",
        get_secret("MAINTENANCE_BOOT_REASON", "Akses terbatas otomatis aktif setelah server reboot."),
    )
    or "Akses terbatas otomatis aktif setelah server reboot."
)
maintenance_default_message = str(
    get_secret(
        "AKSES_TERBATAS_MESSAGE",
        get_secret(
            "MAINTENANCE_MESSAGE",
            "Adioranye sedang dalam mode akses terbatas. Silakan coba lagi setelah admin membuka akses.",
        ),
    )
    or "Adioranye sedang dalam mode akses terbatas. Silakan coba lagi setelah admin membuka akses."
)
maintenance_auto_check_interval_seconds = int(
    get_secret("MAINTENANCE_AUTO_CHECK_INTERVAL_SECONDS", 5) or 5
)
maintenance_auto_refresh_enabled = parse_bool(
    get_secret("MAINTENANCE_AUTO_REFRESH_ENABLED", True),
    default=True,
)
maintenance_auto_refresh_interval_seconds = int(
    get_secret("MAINTENANCE_AUTO_REFRESH_INTERVAL_SECONDS", 8) or 8
)
maintenance_auto_refresh_when_unlocked = parse_bool(
    get_secret("MAINTENANCE_AUTO_REFRESH_WHEN_UNLOCKED", True),
    default=True,
)
maintenance_hide_chat_when_locked = parse_bool(
    get_secret("MAINTENANCE_HIDE_CHAT_WHEN_LOCKED", True),
    default=True,
)
maintenance_fragment_enabled = parse_bool(
    get_secret("MAINTENANCE_FRAGMENT_ENABLED", False),
    default=False,
)
maintenance_browser_reload_enabled = parse_bool(
    get_secret("MAINTENANCE_BROWSER_RELOAD_ENABLED", False),
    default=False,
)
model_status_fragment_enabled = parse_bool(
    get_secret("MODEL_STATUS_FRAGMENT_ENABLED", False),
    default=False,
)
frontend_ultra_safe_mode = parse_bool(
    get_secret("FRONTEND_ULTRA_SAFE_MODE", True),
    default=True,
)
message_effects_enabled = parse_bool(
    get_secret("MESSAGE_EFFECTS_ENABLED", True),
    default=True,
)
custom_components_enabled = parse_bool(
    get_secret("CUSTOM_COMPONENTS_ENABLED", True),
    default=True,
)
auto_scroll_enabled = parse_bool(
    get_secret("AUTO_SCROLL_ENABLED", True),
    default=True,
)
answer_sound_enabled = parse_bool(
    get_secret("ANSWER_SOUND_ENABLED", True),
    default=True,
)
typewriter_enabled = parse_bool(
    get_secret("TYPEWRITER_ENABLED", False),
    default=False,
)
animated_loading_enabled = parse_bool(
    get_secret("ANIMATED_LOADING_ENABLED", False),
    default=False,
)
telegram_show_model_info = parse_bool(
    get_secret("TELEGRAM_SHOW_MODEL_INFO", True), default=True
)
telegram_speed_update_code = str(
    get_secret("TELEGRAM_SPEED_UPDATE_CODE", "4321") or "4321"
).strip()
telegram_admin_chat_ids = str(get_secret("TELEGRAM_ADMIN_CHAT_IDS", "") or "").strip()
telegram_runtime_state_file = str(
    get_secret("TELEGRAM_RUNTIME_STATE_FILE", ".telegram_runtime_state.json")
    or ".telegram_runtime_state.json"
).strip()
telegram_model_mode_default = str(
    get_secret("TELEGRAM_MODEL_MODE", "auto") or "auto"
).strip().lower()
telegram_auto_rotate_on_model_error = parse_bool(
    get_secret("TELEGRAM_AUTO_ROTATE_ON_MODEL_ERROR", True),
    default=True,
)
# GitHub Actions trigger untuk perintah Telegram /update Knowledge Base
github_actions_token = str(get_secret("GITHUB_ACTIONS_TOKEN", "") or "").strip()
github_repo = str(get_secret("GITHUB_REPO", "") or "").strip()
github_workflow_file = str(
    get_secret("GITHUB_WORKFLOW_FILE", "daily-kb-update.yml") or "daily-kb-update.yml"
).strip()
github_branch = str(get_secret("GITHUB_BRANCH", "main") or "main").strip()
# Safe default untuk /update Telegram: jalankan batch kecil agar workflow selesai <20 menit.
github_update_source_limit = str(
    get_secret("GITHUB_UPDATE_SOURCE_LIMIT", "8") or "8"
).strip()
github_update_max_items = str(get_secret("GITHUB_UPDATE_MAX_ITEMS", "1") or "1").strip()
allow_unrestricted_model_commands = parse_bool(
    get_secret("ALLOW_UNRESTRICTED_MODEL_COMMANDS", False), default=False
)
admin_username = str(get_secret("ADMIN_USERNAME", "admin"))
admin_password = str(get_secret("ADMIN_PASSWORD", "Admin"))
smart_model_router_default = parse_bool(
    get_secret("SMART_MODEL_ROUTER", True), default=True
)
return_to_primary_default = parse_bool(
    get_secret("RETURN_TO_PRIMARY_MODEL", True), default=True
)
max_smart_models_default = int(get_secret("MAX_SMART_MODELS", 2) or 2)
model_health_check_interval = int(
    get_secret("MODEL_HEALTH_CHECK_INTERVAL_SECONDS", 90000) or 90000
)
model_health_timeout = int(get_secret("MODEL_HEALTH_TIMEOUT_SECONDS", 8) or 8)
model_health_workers = int(get_secret("MODEL_HEALTH_WORKERS", 4) or 4)
model_health_retries = int(get_secret("MODEL_HEALTH_RETRIES", 0) or 0)
model_health_quick_limit = int(get_secret("MODEL_HEALTH_QUICK_LIMIT", 6) or 6)
model_health_probe_max_tokens = int(get_secret("MODEL_HEALTH_PROBE_MAX_TOKENS", 2) or 2)
model_health_probe_gpt5_max_tokens = int(
    get_secret("MODEL_HEALTH_PROBE_GPT5_MAX_TOKENS", 8) or 8
)
model_health_probe_prompt = str(get_secret("MODEL_HEALTH_PROBE_PROMPT", "ping") or "ping")
model_health_force_scope = str(get_secret("MODEL_HEALTH_FORCE_SCOPE", "quick") or "quick").strip().lower()
model_health_full_limit = int(get_secret("MODEL_HEALTH_FULL_LIMIT", 24) or 24)
model_health_preserve_cache = parse_bool(
    get_secret("MODEL_HEALTH_PRESERVE_CACHE", True),
    default=True,
)
model_performance_state_file = str(
    get_secret("MODEL_PERFORMANCE_STATE_FILE", ".adioranye_model_performance.json")
    or ".adioranye_model_performance.json"
).strip()
model_performance_routing_enabled = parse_bool(
    get_secret("MODEL_PERFORMANCE_ROUTING_ENABLED", True), default=True
)
model_performance_min_samples = int(get_secret("MODEL_PERFORMANCE_MIN_SAMPLES", 2) or 2)
request_timeout_seconds = int(get_secret("MODEL_REQUEST_TIMEOUT_SECONDS", 45) or 45)
# Health check model aktif kapan saja.
# Default baru: tidak perlu menunggu jendela waktu tertentu.
model_health_midnight_only = parse_bool(
    get_secret("MODEL_HEALTH_MIDNIGHT_ONLY", False), default=False
)
model_health_hour_wib = int(get_secret("MODEL_HEALTH_HOUR_WIB", 0) or 0)
model_health_window_minutes = int(get_secret("MODEL_HEALTH_WINDOW_MINUTES", 60) or 60)
rotate_cheap_primary_default = parse_bool(
    get_secret("ROTATE_CHEAP_PRIMARY", True), default=True
)
use_streamlit_cache_memory_default = parse_bool(
    get_secret("USE_STREAMLIT_CACHE_MEMORY", True), default=True
)
streamlit_cache_memory_limit = int(
    get_secret("STREAMLIT_CACHE_MEMORY_LIMIT", 200) or 200
)
thinking_model_router_default = parse_bool(
    get_secret("THINKING_MODEL_ROUTER", True), default=True
)
thinking_min_chars_default = int(get_secret("THINKING_MIN_CHARS", 180) or 180)
thinking_capable_model_override = str(
    get_secret("THINKING_CAPABLE_MODEL", "") or ""
).strip()
fast_normal_model_router_default = parse_bool(
    get_secret("FAST_NORMAL_MODEL_ROUTER", True), default=True
)
model_discovery_enabled = parse_bool(
    get_secret("MODEL_DISCOVERY_ENABLED", True), default=True
)
models_api_url = str(get_secret("SLASHAI_MODELS_API_URL", "") or "").strip()
model_discovery_timeout = int(get_secret("MODEL_DISCOVERY_TIMEOUT_SECONDS", 12) or 12)
model_discovery_interval = int(
    get_secret("MODEL_DISCOVERY_INTERVAL_SECONDS", 3600) or 3600
)

# Power features: persistent memory, RAG, logging, budget guard, self-check.
power_features_enabled = parse_bool(
    get_secret("POWER_FEATURES_ENABLED", True), default=True
)
power_db_path = str(
    get_secret("POWER_DB_PATH", ".adioranye_power.db") or ".adioranye_power.db"
)
db_backup_enabled = parse_bool(get_secret("DB_BACKUP_ENABLED", True), default=True)
db_backup_dir = str(get_secret("DB_BACKUP_DIR", ".db_backups") or ".db_backups")
db_backup_max_count = int(get_secret("DB_BACKUP_MAX_COUNT", 10) or 10)
db_backup_min_interval_seconds = int(
    get_secret("DB_BACKUP_MIN_INTERVAL_SECONDS", 21600) or 21600
)
db_auto_restore_enabled = parse_bool(
    get_secret("DB_AUTO_RESTORE_ENABLED", True), default=True
)
power_rag_enabled = parse_bool(get_secret("POWER_RAG_ENABLED", True), default=True)
power_rag_top_k = int(get_secret("POWER_RAG_TOP_K", 5) or 5)
power_strict_rag_mode = parse_bool(
    get_secret("POWER_STRICT_RAG_MODE", False), default=False
)
power_anti_hallucination_enabled = parse_bool(
    get_secret("POWER_ANTI_HALLUCINATION_ENABLED", True), default=True
)
power_anti_hallucination_auto_strict = parse_bool(
    get_secret("POWER_ANTI_HALLUCINATION_AUTO_STRICT", True), default=True
)
power_anti_hallucination_min_sources = int(
    get_secret("POWER_ANTI_HALLUCINATION_MIN_SOURCES", 1) or 1
)
power_anti_hallucination_min_quality = float(
    get_secret("POWER_ANTI_HALLUCINATION_MIN_QUALITY", 0) or 0
)
power_anti_hallucination_min_freshness = float(
    get_secret("POWER_ANTI_HALLUCINATION_MIN_FRESHNESS", 0) or 0
)
power_anti_hallucination_append_sources = parse_bool(
    get_secret("POWER_ANTI_HALLUCINATION_APPEND_SOURCES", True), default=True
)
power_rag_min_sources = int(get_secret("POWER_RAG_MIN_SOURCES", 1) or 1)
power_rag_min_score = float(get_secret("POWER_RAG_MIN_SCORE", 0) or 0)
power_kb_max_file_mb = int(get_secret("POWER_KB_MAX_FILE_MB", 12) or 12)
power_persistent_memory_enabled = parse_bool(
    get_secret("POWER_PERSISTENT_MEMORY_ENABLED", True), default=True
)
power_prompt_templates_enabled = parse_bool(
    get_secret("POWER_PROMPT_TEMPLATES_ENABLED", True), default=True
)
power_self_verification_enabled = parse_bool(
    get_secret("POWER_SELF_VERIFICATION_ENABLED", False), default=False
)
power_quality_control_enabled = parse_bool(
    get_secret("POWER_QUALITY_CONTROL_ENABLED", True), default=True
)
power_quality_verifier_enabled = parse_bool(
    get_secret("POWER_QUALITY_VERIFIER_ENABLED", True), default=True
)
power_quality_verifier_model = str(
    get_secret("POWER_QUALITY_VERIFIER_MODEL", "") or ""
).strip()
power_quality_min_score = float(get_secret("POWER_QUALITY_MIN_SCORE", 0.72) or 0.72)
power_quality_append_footer = parse_bool(
    get_secret("POWER_QUALITY_APPEND_FOOTER", False), default=False
)
power_default_answer_mode = (
    str(get_secret("POWER_DEFAULT_ANSWER_MODE", "auto") or "auto").strip().lower()
)
power_hide_kb_sources_for_casual = parse_bool(
    get_secret("POWER_HIDE_KB_SOURCES_FOR_CASUAL", True), default=True
)
power_disable_rag_for_casual = parse_bool(
    get_secret("POWER_DISABLE_RAG_FOR_CASUAL", True), default=True
)
power_performance_optimizer_enabled = parse_bool(
    get_secret("POWER_PERFORMANCE_OPTIMIZER_ENABLED", True), default=True
)
power_query_rewriter_enabled = parse_bool(
    get_secret("POWER_QUERY_REWRITER_ENABLED", True), default=True
)
power_reranker_enabled = parse_bool(
    get_secret("POWER_RERANKER_ENABLED", True), default=True
)
power_semantic_cache_enabled = parse_bool(
    get_secret("POWER_SEMANTIC_CACHE_ENABLED", True), default=True
)
power_semantic_cache_threshold = float(
    get_secret("POWER_SEMANTIC_CACHE_THRESHOLD", 0.78) or 0.78
)
power_semantic_cache_ttl_seconds = int(
    get_secret("POWER_SEMANTIC_CACHE_TTL_SECONDS", 86400) or 86400
)
power_latency_budget_enabled = parse_bool(
    get_secret("POWER_LATENCY_BUDGET_ENABLED", True), default=True
)
power_retrieval_eval_enabled = parse_bool(
    get_secret("POWER_RETRIEVAL_EVAL_ENABLED", True), default=True
)
live_music_chart_enabled = parse_bool(
    get_secret("LIVE_MUSIC_CHART_ENABLED", True), default=True
)
live_music_chart_limit = int(get_secret("LIVE_MUSIC_CHART_LIMIT", 10) or 10)
live_music_chart_timeout_seconds = int(
    get_secret("LIVE_MUSIC_CHART_TIMEOUT_SECONDS", 8) or 8
)
live_web_fallback_enabled = parse_bool(
    get_secret("LIVE_WEB_FALLBACK_ENABLED", True), default=True
)
live_web_fallback_provider = str(
    get_secret("LIVE_WEB_FALLBACK_PROVIDER", "tavily") or "tavily"
)
tavily_api_key = str(get_secret("TAVILY_API_KEY", "") or "").strip()
if tavily_api_key:
    os.environ["TAVILY_API_KEY"] = tavily_api_key
live_web_fallback_max_results = int(get_secret("LIVE_WEB_FALLBACK_MAX_RESULTS", 4) or 4)
live_web_fallback_timeout_seconds = int(
    get_secret("LIVE_WEB_FALLBACK_TIMEOUT_SECONDS", 10) or 10
)
live_web_fallback_min_sources = int(get_secret("LIVE_WEB_FALLBACK_MIN_SOURCES", 1) or 1)
live_web_fallback_include_raw_content = parse_bool(
    get_secret("LIVE_WEB_FALLBACK_INCLUDE_RAW_CONTENT", True), default=True
)
live_web_fallback_max_content_chars = int(
    get_secret("LIVE_WEB_FALLBACK_MAX_CONTENT_CHARS", 3200) or 3200
)
live_web_fallback_auto_save_to_kb = parse_bool(
    get_secret("LIVE_WEB_FALLBACK_AUTO_SAVE_TO_KB", True), default=True
)
live_web_fallback_ttl_hours = int(get_secret("LIVE_WEB_FALLBACK_TTL_HOURS", 24) or 24)
live_web_fallback_force_for_current = parse_bool(
    get_secret("LIVE_WEB_FALLBACK_FORCE_FOR_CURRENT", True), default=True
)
live_web_fallback_topic = str(get_secret("LIVE_WEB_FALLBACK_TOPIC", "auto") or "auto")
auto_live_scraping_enabled = parse_bool(
    get_secret("AUTO_LIVE_SCRAPING_ENABLED", True),
    default=True,
)
auto_live_scraping_show_status = parse_bool(
    get_secret("AUTO_LIVE_SCRAPING_SHOW_STATUS", True),
    default=True,
)
auto_live_scraping_min_query_chars = int(
    get_secret("AUTO_LIVE_SCRAPING_MIN_QUERY_CHARS", 8) or 8
)
daily_cost_limit_idr = float(get_secret("DAILY_COST_LIMIT_IDR", 0) or 0)
max_expensive_calls_per_day = int(get_secret("MAX_EXPENSIVE_CALLS_PER_DAY", 0) or 0)
benchmark_max_models = int(get_secret("BENCHMARK_MAX_MODELS", 8) or 8)
power_response_cache_enabled = parse_bool(
    get_secret("POWER_RESPONSE_CACHE_ENABLED", True), default=True
)
power_response_cache_ttl_seconds = int(
    get_secret("POWER_RESPONSE_CACHE_TTL_SECONDS", 1800) or 1800
)
power_adaptive_scoring_enabled = parse_bool(
    get_secret("POWER_ADAPTIVE_SCORING_ENABLED", True), default=True
)
power_circuit_breaker_enabled = parse_bool(
    get_secret("POWER_CIRCUIT_BREAKER_ENABLED", True), default=True
)
model_circuit_max_failures = int(get_secret("MODEL_CIRCUIT_MAX_FAILURES", 3) or 3)
model_circuit_cooldown_seconds = int(
    get_secret("MODEL_CIRCUIT_COOLDOWN_SECONDS", 1800) or 1800
)


# Public safety / production controls
public_rate_limit_enabled = parse_bool(
    get_secret("PUBLIC_RATE_LIMIT_ENABLED", True),
    default=True,
)
public_rate_limit_max_requests = int(
    get_secret("PUBLIC_RATE_LIMIT_MAX_REQUESTS", 10) or 10
)
public_rate_limit_window_seconds = int(
    get_secret("PUBLIC_RATE_LIMIT_WINDOW_SECONDS", 600) or 600
)
public_max_prompt_chars = int(
    get_secret("PUBLIC_MAX_PROMPT_CHARS", 6000) or 6000
)
runtime_model_block_enabled = parse_bool(
    get_secret("RUNTIME_MODEL_BLOCK_ENABLED", True),
    default=True,
)
runtime_model_block_timeout_seconds = int(
    get_secret("RUNTIME_MODEL_BLOCK_TIMEOUT_SECONDS", 900) or 900
)
runtime_model_block_quota_seconds = int(
    get_secret("RUNTIME_MODEL_BLOCK_QUOTA_SECONDS", 3600) or 3600
)
runtime_model_block_invalid_seconds = int(
    get_secret("RUNTIME_MODEL_BLOCK_INVALID_SECONDS", 86400) or 86400
)
runtime_model_block_generic_seconds = int(
    get_secret("RUNTIME_MODEL_BLOCK_GENERIC_SECONDS", 1200) or 1200
)


# Daily Knowledge Base auto-update / scraper
kb_scraper_sources_file = str(
    get_secret("KB_SCRAPER_SOURCES_FILE", KB_DEFAULT_SOURCES_FILE)
    or KB_DEFAULT_SOURCES_FILE
)
kb_scraper_state_file = str(
    get_secret("KB_SCRAPER_STATE_FILE", ".adioranye_kb_scrape_state.json")
    or ".adioranye_kb_scrape_state.json"
)
kb_scraper_max_items_per_source = int(
    get_secret("KB_SCRAPER_MAX_ITEMS_PER_SOURCE", 5) or 5
)
kb_scraper_timeout = int(get_secret("KB_SCRAPER_TIMEOUT", 20) or 20)

model_readiness_state_file = str(
    get_secret("MODEL_READINESS_STATE_FILE", ".adioranye_model_readiness.json")
    or ".adioranye_model_readiness.json"
).strip()
model_readiness_stale_seconds = int(
    get_secret("MODEL_READINESS_STALE_SECONDS", 1800) or 1800
)
kb_v2_retrieval_enabled = parse_bool(
    get_secret("KB_V2_RETRIEVAL_ENABLED", True),
    default=True,
)
kb_v2_retrieval_limit = int(
    get_secret("KB_V2_RETRIEVAL_LIMIT", 5) or 5
)

frequent_question_cache_enabled = parse_bool(
    get_secret("FREQUENT_QUESTION_CACHE_ENABLED", True),
    default=True,
)
frequent_question_cache_file = str(
    get_secret("FREQUENT_QUESTION_CACHE_FILE", ".adioranye_frequent_questions.json")
    or ".adioranye_frequent_questions.json"
).strip()
frequent_question_cache_ttl_seconds = int(
    get_secret("FREQUENT_QUESTION_CACHE_TTL_SECONDS", 86400) or 86400
)
frequent_question_cache_max_entries = int(
    get_secret("FREQUENT_QUESTION_CACHE_MAX_ENTRIES", 500) or 500
)
frequent_question_cache_min_chars = int(
    get_secret("FREQUENT_QUESTION_CACHE_MIN_CHARS", 4) or 4
)

auto_replace_inactive_primary_model = parse_bool(
    get_secret("AUTO_REPLACE_INACTIVE_PRIMARY_MODEL", True),
    default=True,
)
auto_replace_primary_prefer_cheap = parse_bool(
    get_secret("AUTO_REPLACE_PRIMARY_PREFER_CHEAP", True),
    default=True,
)
model_status_auto_refresh_enabled = parse_bool(
    get_secret("MODEL_STATUS_AUTO_REFRESH_ENABLED", True),
    default=True,
)
model_status_auto_refresh_interval_seconds = int(
    get_secret("MODEL_STATUS_AUTO_REFRESH_INTERVAL_SECONDS", 90) or 90
)
model_status_auto_refresh_scope = str(
    get_secret("MODEL_STATUS_AUTO_REFRESH_SCOPE", "quick") or "quick"
).strip().lower()
model_status_auto_refresh_public_panel = parse_bool(
    get_secret("MODEL_STATUS_AUTO_REFRESH_PUBLIC_PANEL", True),
    default=True,
)

token_saver_enabled = parse_bool(
    get_secret("TOKEN_SAVER_ENABLED", True),
    default=True,
)
token_saver_default_mode = str(
    get_secret("TOKEN_SAVER_DEFAULT_MODE", "balanced") or "balanced"
).strip().lower()
max_tokens_casual = int(get_secret("MAX_TOKENS_CASUAL", 500) or 500)
max_tokens_normal = int(get_secret("MAX_TOKENS_NORMAL", 1200) or 1200)
max_tokens_technical = int(get_secret("MAX_TOKENS_TECHNICAL", 2200) or 2200)
max_tokens_long = int(get_secret("MAX_TOKENS_LONG", 3000) or 3000)
web_history_limit = int(get_secret("WEB_HISTORY_LIMIT", 6) or 6)
web_history_recent_full = int(get_secret("WEB_HISTORY_RECENT_FULL", 4) or 4)
memory_context_max_chars = int(get_secret("MEMORY_CONTEXT_MAX_CHARS", 2600) or 2600)
kb_context_max_chars = int(get_secret("KB_CONTEXT_MAX_CHARS", 3500) or 3500)
kb_chunk_max_chars = int(get_secret("KB_CHUNK_MAX_CHARS", 900) or 900)
kb_max_chunks_token_saver = int(get_secret("KB_MAX_CHUNKS", 3) or 3)
live_web_context_max_chars = int(get_secret("LIVE_WEB_CONTEXT_MAX_CHARS", 4200) or 4200)
live_web_source_content_max_chars = int(
    get_secret("LIVE_WEB_SOURCE_CONTENT_MAX_CHARS", 1200) or 1200
)

# Operational safety / retention
ai_operation_mode_default = str(
    get_secret("AI_OPERATION_MODE", "Seimbang") or "Seimbang"
)
power_log_retention_days = int(get_secret("POWER_LOG_RETENTION_DAYS", 30) or 30)
power_cache_retention_days = int(get_secret("POWER_CACHE_RETENTION_DAYS", 7) or 7)
power_benchmark_retention_days = int(
    get_secret("POWER_BENCHMARK_RETENTION_DAYS", 14) or 14
)

init_state()
memory = MemoryStore(memory_file)
service = get_telegram_service()
power_store = get_power_store(power_db_path)
try:
    init_kb_manager_schema(power_db_path)
    ensure_kb_sources_file(
        kb_scraper_sources_file,
        json.loads(DEFAULT_RELEVANT_KB_SOURCES_JSON)["sources"]
        if "DEFAULT_RELEVANT_KB_SOURCES_JSON" in globals()
        else [],
    )
except Exception:
    pass
if db_backup_enabled:
    try:
        maybe_create_periodic_backup(
            power_db_path,
            db_backup_dir or default_backup_dir(),
            label="streamlit-startup",
            min_interval_seconds=db_backup_min_interval_seconds,
            max_backups=db_backup_max_count or default_max_backups(),
        )
    except Exception:
        pass


@st.cache_resource(show_spinner=False)
def get_streamlit_memory_cache_store() -> Dict[str, Any]:
    """Cache memory berbasis RAM Streamlit.

    Catatan: cache ini bertahan melewati rerun selama proses/container Streamlit masih hidup,
    tetapi bisa hilang saat app sleep, restart, clear cache, atau redeploy.
    """
    return {"items": []}


def _streamlit_cache_memory_items() -> List[Dict[str, Any]]:
    store = get_streamlit_memory_cache_store()
    items = store.setdefault("items", [])
    if not isinstance(items, list):
        store["items"] = []
        items = store["items"]
    return items


def add_streamlit_cache_memory(
    text: str, source: str = "streamlit-cache-admin"
) -> bool:
    clean_text = str(text or "").strip()
    if not clean_text:
        return False

    items = _streamlit_cache_memory_items()
    # Hindari duplikasi persis agar prompt tidak membengkak.
    if any(str(item.get("text", "")).strip() == clean_text for item in items):
        return False

    items.append(
        {
            "text": clean_text,
            "source": source,
            "created_at": datetime.now(ZoneInfo("Asia/Jakarta")).strftime(
                "%Y-%m-%d %H:%M:%S WIB"
            ),
        }
    )

    max_items = max(1, int(streamlit_cache_memory_limit or 200))
    if len(items) > max_items:
        del items[: len(items) - max_items]
    return True


def streamlit_cache_memory_prompt_text(limit: int = 12) -> str:
    if not bool(st.session_state.get("active_use_streamlit_cache_memory", True)):
        return ""

    items = _streamlit_cache_memory_items()
    if not items:
        return ""

    selected_items = items[-max(1, int(limit or 12)) :]
    return "\n".join(
        f"- {str(item.get('text', '')).strip()}"
        for item in selected_items
        if str(item.get("text", "")).strip()
    )


def streamlit_cache_memory_list_text(limit: int = 80) -> str:
    items = _streamlit_cache_memory_items()
    if not items:
        return ""

    selected_items = items[-max(1, int(limit or 80)) :]
    lines = []
    start_number = max(1, len(items) - len(selected_items) + 1)
    for idx, item in enumerate(selected_items, start=start_number):
        created_at = str(item.get("created_at") or "").strip()
        source = str(item.get("source") or "cache").strip()
        body = str(item.get("text") or "").strip()
        if body:
            prefix = f"{idx}."
            meta = f" [{created_at} | {source}]" if created_at or source else ""
            lines.append(f"{prefix}{meta} {body}")
    return "\n".join(lines)


def forget_streamlit_cache_memory_contains(keyword: str) -> int:
    keyword_clean = str(keyword or "").strip().lower()
    if not keyword_clean:
        return 0

    items = _streamlit_cache_memory_items()
    before = len(items)
    items[:] = [
        item for item in items if keyword_clean not in str(item.get("text", "")).lower()
    ]
    return before - len(items)


def reset_streamlit_cache_memory() -> int:
    items = _streamlit_cache_memory_items()
    count = len(items)
    items.clear()
    return count


def build_memory_text(limit: int = 12) -> str:
    """Gabungkan memory default, cache online, dan memory lokal admin."""
    default_context = str(
        st.session_state.get("active_default_memory")
        or default_memory_context_from_secret
        or DEFAULT_MEMORY_CONTEXT
    ).strip()
    cache_memory = str(streamlit_cache_memory_prompt_text(limit=limit) or "").strip()
    local_memory = str(memory.as_prompt_text(limit=limit) or "").strip()

    sections = []
    time_context = _indonesia_time_context_text()
    if time_context:
        sections.append(time_context)
    if default_context:
        sections.append("MEMORY DEFAULT AKTIF:\n" + default_context)
    if cache_memory:
        sections.append("MEMORY CACHE STREAMLIT AKTIF:\n" + cache_memory)
    if local_memory:
        sections.append("MEMORY TAMBAHAN ADMIN FILE LOKAL:\n" + local_memory)
    if power_features_enabled and power_persistent_memory_enabled:
        try:
            # Persistent SQLite memory is query-aware in generate_power_answer; here we only
            # add the most recent general records as a safe baseline.
            recent_power_memory = power_store.search_memories(
                "preferensi konteks proyek", user_id="global", limit=6
            )
            if recent_power_memory:
                sections.append(
                    "MEMORY SQLITE UMUM AKTIF:\n"
                    + "\n".join(f"- {item['text']}" for item in recent_power_memory)
                )
        except Exception:
            pass
    return "\n\n".join(sections)


def persona_with_default_memory(persona: str) -> str:
    """Dipakai untuk Bot Telegram agar memory default/cache tetap masuk ke instruksi bot."""
    default_context = str(
        st.session_state.get("active_default_memory")
        or default_memory_context_from_secret
        or DEFAULT_MEMORY_CONTEXT
    ).strip()
    cache_context = str(streamlit_cache_memory_prompt_text(limit=20) or "").strip()

    context_sections = []
    time_context = _indonesia_time_context_text()
    if time_context:
        context_sections.append(time_context)
    if default_context:
        context_sections.append(
            "Konteks default yang selalu dipakai:\n" + default_context
        )
    if cache_context:
        context_sections.append("Memory cache online aktif:\n" + cache_context)

    if not context_sections:
        return persona
    return f"{persona}\n\n" + "\n\n".join(context_sections)



# =========================
# Model performance tracking & latency-based routing
# =========================
def load_model_performance_stats_from_file() -> Dict[str, Dict[str, Any]]:
    path = str(globals().get("model_performance_state_file", ".adioranye_model_performance.json"))
    try:
        if not path or not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_model_performance_stats_to_file() -> None:
    path = str(globals().get("model_performance_state_file", ".adioranye_model_performance.json"))
    if not path:
        return
    try:
        data = st.session_state.get("model_performance_stats") or {}
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_model_performance_stats() -> Dict[str, Dict[str, Any]]:
    stats = st.session_state.get("model_performance_stats") or {}
    if not isinstance(stats, dict):
        stats = {}
    st.session_state.model_performance_stats = stats
    return stats


def _model_perf_entry(model_name: str) -> Dict[str, Any]:
    stats = get_model_performance_stats()
    model = str(model_name or "").strip()
    if not model:
        model = "unknown"
    entry = stats.get(model) or {}
    defaults = {
        "requests": 0,
        "success": 0,
        "failures": 0,
        "timeouts": 0,
        "quota_errors": 0,
        "invalid_errors": 0,
        "total_latency_ms": 0.0,
        "avg_latency_ms": None,
        "last_latency_ms": None,
        "last_success_at": "",
        "last_failure_at": "",
        "last_error": "",
    }
    for key, value in defaults.items():
        entry.setdefault(key, value)
    stats[model] = entry
    return entry


def classify_error_category(error_text: str) -> str:
    lowered = str(error_text or "").lower()
    if any(marker in lowered for marker in ["timeout", "timed out", "read timed out"]):
        return "timeout"
    if any(marker in lowered for marker in ["insufficient", "quota", "billing", "creditsdepleted", "401002"]):
        return "quota"
    if any(marker in lowered for marker in ["invalid model", "model not found", "unknown model", "does not exist"]):
        return "invalid_model"
    if any(marker in lowered for marker in ["content filter", "filtered", "policy"]):
        return "content_filter"
    if any(marker in lowered for marker in ["502", "503", "504", "overload", "upstream"]):
        return "provider_overload"
    if any(marker in lowered for marker in ["connection", "network", "connectionpool"]):
        return "network"
    if any(marker in lowered for marker in ["empty", "kosong"]):
        return "empty_response"
    return "generic_error"


def record_model_performance(
    model_name: str,
    latency_seconds: float,
    success: bool,
    error_text: str = "",
) -> None:
    model = str(model_name or "").strip()
    if not model:
        return
    entry = _model_perf_entry(model)
    latency_ms = max(0.0, float(latency_seconds or 0) * 1000)
    entry["requests"] = int(entry.get("requests", 0) or 0) + 1
    entry["last_latency_ms"] = round(latency_ms, 1)
    entry["total_latency_ms"] = float(entry.get("total_latency_ms", 0) or 0) + latency_ms
    entry["avg_latency_ms"] = round(
        float(entry.get("total_latency_ms", 0) or 0) / max(1, int(entry.get("requests", 1) or 1)),
        1,
    )
    if success:
        entry["success"] = int(entry.get("success", 0) or 0) + 1
        entry["last_success_at"] = _wib_now_text()
        entry["last_error"] = ""
    else:
        entry["failures"] = int(entry.get("failures", 0) or 0) + 1
        entry["last_failure_at"] = _wib_now_text()
        entry["last_error"] = str(error_text or "")[:700]
        category = classify_error_category(error_text)
        if category == "timeout":
            entry["timeouts"] = int(entry.get("timeouts", 0) or 0) + 1
        elif category == "quota":
            entry["quota_errors"] = int(entry.get("quota_errors", 0) or 0) + 1
        elif category == "invalid_model":
            entry["invalid_errors"] = int(entry.get("invalid_errors", 0) or 0) + 1
    st.session_state.last_model_performance_event = {
        "model": model,
        "success": bool(success),
        "latency_ms": round(latency_ms, 1),
        "error_category": "" if success else classify_error_category(error_text),
        "at": _wib_now_text(),
    }
    save_model_performance_stats_to_file()


def record_model_performance_from_meta(
    route: Dict[str, Any] | None,
    meta: Dict[str, Any] | None,
    latency_seconds: float,
    answer_text: str = "",
    error_text: str = "",
) -> None:
    route_data = route or {}
    meta_data = meta or {}
    model_name = str(
        meta_data.get("active_model_final")
        or meta_data.get("model")
        or meta_data.get("model_requested")
        or meta_data.get("telegram_model_requested")
        or route_data.get("primary_model")
        or ""
    ).strip()
    technical_error = bool(error_text) or looks_like_technical_error(answer_text)
    public_error = is_public_connection_error_answer(answer_text, meta=meta_data)
    record_model_performance(
        model_name=model_name,
        latency_seconds=latency_seconds,
        success=not (technical_error or public_error),
        error_text=error_text or answer_text,
    )


def get_model_success_rate(model_name: str) -> float:
    entry = (st.session_state.get("model_performance_stats") or {}).get(str(model_name or ""), {})
    requests_count = int(entry.get("requests", 0) or 0)
    if requests_count <= 0:
        return 0.92
    return float(entry.get("success", 0) or 0) / max(1, requests_count)


def get_model_effective_latency_ms(
    model_name: str,
    health_cache: Dict[str, Dict[str, Any]] | None = None,
) -> float:
    health = health_cache or {}
    health_latency = health.get(model_name, {}).get("latency_ms")
    perf = (st.session_state.get("model_performance_stats") or {}).get(str(model_name or ""), {})
    perf_latency = perf.get("avg_latency_ms")
    values = []
    for value in [perf_latency, health_latency]:
        try:
            if value is not None and float(value) > 0:
                values.append(float(value))
        except Exception:
            pass
    if not values:
        return 999999.0
    return min(values)


def get_model_performance_penalty(model_name: str) -> float:
    if not bool(model_performance_routing_enabled):
        return 0.0
    entry = (st.session_state.get("model_performance_stats") or {}).get(str(model_name or ""), {})
    requests_count = int(entry.get("requests", 0) or 0)
    if requests_count < int(model_performance_min_samples or 2):
        return 0.0
    success_rate = get_model_success_rate(model_name)
    timeouts = int(entry.get("timeouts", 0) or 0)
    failures = int(entry.get("failures", 0) or 0)
    penalty = (1.0 - success_rate) * 10000
    penalty += min(5000, timeouts * 900)
    penalty += min(3000, failures * 250)
    return penalty


def reset_model_runtime_and_performance() -> None:
    st.session_state.model_runtime_blocks = {}
    st.session_state.model_performance_stats = {}
    st.session_state.last_model_performance_event = {}
    save_model_performance_stats_to_file()


# =========================
# Model health check & active fallback priority
# =========================
def unique_models(models: List[str]) -> List[str]:
    """Hilangkan model kosong/duplikat sambil mempertahankan urutan."""
    return list(
        dict.fromkeys(str(model).strip() for model in models if str(model).strip())
    )


WIB_TZ = ZoneInfo("Asia/Jakarta")
WITA_TZ = ZoneInfo("Asia/Makassar")
WIT_TZ = ZoneInfo("Asia/Jayapura")


def _wib_now_text() -> str:
    return datetime.now(WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")


def _indonesia_time_context_text() -> str:
    """Konteks waktu untuk jawaban bahasa Indonesia.

    UTC tetap dicatat sebagai referensi teknis, tetapi jawaban Indonesia
    harus memakai WIB/WITA/WIT, bukan menganggap UTC sebagai waktu lokal.
    """
    now_utc = datetime.now(timezone.utc)
    now_wib = now_utc.astimezone(WIB_TZ)
    now_wita = now_utc.astimezone(WITA_TZ)
    now_wit = now_utc.astimezone(WIT_TZ)

    return (
        "KONTEKS WAKTU INDONESIA AKTIF:\n"
        f"- UTC sekarang: {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}.\n"
        f"- WIB sekarang: {now_wib.strftime('%Y-%m-%d %H:%M:%S WIB')} "
        "(Jakarta, Sumatra, Jawa, Kalimantan Barat/Tengah).\n"
        f"- WITA sekarang: {now_wita.strftime('%Y-%m-%d %H:%M:%S WITA')} "
        "(Bali, Sulawesi, Nusa Tenggara, Kalimantan Timur/Selatan/Utara).\n"
        f"- WIT sekarang: {now_wit.strftime('%Y-%m-%d %H:%M:%S WIT')} "
        "(Maluku, Papua).\n"
        "- Jika pengguna memakai bahasa Indonesia dan tidak menyebut zona/kota, "
        "gunakan WIB sebagai default.\n"
        "- Jika wilayah/kota jelas masuk WITA atau WIT, gunakan zona tersebut.\n"
        "- Jangan menyebut UTC sebagai waktu lokal pengguna kecuali pengguna memang meminta UTC."
    )



def _indonesia_part_of_day(
    dt: datetime,
) -> str:
    """Tentukan sapaan berdasarkan jam lokal."""
    hour = int(dt.hour)

    if 4 <= hour <= 10:
        return "pagi"

    if 11 <= hour <= 14:
        return "siang"

    if 15 <= hour <= 17:
        return "sore"

    return "malam"


def _normalize_short_greeting_text(
    text: str,
) -> str:
    normalized = str(text or "").strip().lower()
    normalized = re.sub(r"[!?.。,，:;]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def detect_indonesia_time_greeting(
    user_text: str,
) -> Dict[str, Any]:
    """Deteksi sapaan waktu pendek, bukan pertanyaan/tugas.

    Contoh yang ditangani lokal:
    - selamat pagi
    - selamat siang adioranye
    - pagi
    - malam min
    """
    normalized = _normalize_short_greeting_text(user_text)

    if not normalized:
        return {
            "matched": False,
            "said": "",
        }

    # Jangan ambil alih jika user sedang meminta dibuatkan teks/ucapan.
    task_markers = {
        "buat",
        "buatkan",
        "tulis",
        "tuliskan",
        "caption",
        "template",
        "contoh",
        "arti",
        "apa",
        "kenapa",
        "mengapa",
        "jelaskan",
        "translate",
        "terjemahkan",
    }

    tokens = normalized.split()

    if any(token in task_markers for token in tokens):
        return {
            "matched": False,
            "said": "",
        }

    # Batasi hanya sapaan pendek agar tidak mengganggu prompt biasa.
    if len(tokens) > 5:
        return {
            "matched": False,
            "said": "",
        }

    greeting_patterns = [
        ("pagi", r"^(selamat\s+)?pagi(\s+(adioranye|admin|min|ai|bot))?$"),
        ("siang", r"^(selamat\s+)?siang(\s+(adioranye|admin|min|ai|bot))?$"),
        ("sore", r"^(selamat\s+)?sore(\s+(adioranye|admin|min|ai|bot))?$"),
        ("malam", r"^(selamat\s+)?malam(\s+(adioranye|admin|min|ai|bot))?$"),
    ]

    for label, pattern in greeting_patterns:
        if re.match(pattern, normalized, flags=re.I):
            return {
                "matched": True,
                "said": label,
            }

    return {
        "matched": False,
        "said": "",
    }


def build_indonesia_time_greeting_reply(
    user_text: str,
) -> Tuple[str, Dict[str, Any]]:
    """Balas sapaan waktu sesuai waktu Indonesia saat ini tanpa memanggil model."""
    detected = detect_indonesia_time_greeting(user_text)

    if not detected.get("matched"):
        return "", {}

    now_utc = datetime.now(timezone.utc)
    zone_rows = [
        (
            "WIB",
            now_utc.astimezone(WIB_TZ),
            "Jakarta/Sumatra/Jawa",
        ),
        (
            "WITA",
            now_utc.astimezone(WITA_TZ),
            "Bali/Sulawesi/Nusa Tenggara",
        ),
        (
            "WIT",
            now_utc.astimezone(WIT_TZ),
            "Maluku/Papua",
        ),
    ]

    default_dt = zone_rows[0][1]
    default_part = _indonesia_part_of_day(default_dt)
    user_said = str(detected.get("said") or "").strip()

    greeting = f"Selamat {default_part}."

    detail_lines = [
        f"Saat ini acuan default Indonesia adalah {default_dt.strftime('%H:%M')} WIB, jadi sapaan yang paling sesuai adalah **selamat {default_part}**."
    ]

    if user_said and user_said != default_part:
        detail_lines.append(
            f"Sapaan Anda tadi “selamat {user_said}”, saya sesuaikan dengan waktu Indonesia saat ini."
        )

    zone_summary = []

    for zone_name, dt_value, area_label in zone_rows:
        zone_part = _indonesia_part_of_day(dt_value)
        zone_summary.append(
            f"{zone_name} {dt_value.strftime('%H:%M')} ({zone_part})"
        )

    detail_lines.append(
        "Ringkas zona Indonesia: "
        + "; ".join(zone_summary)
        + "."
    )

    answer = greeting + "\n\n" + "\n".join(detail_lines)

    return answer, {
        "local_time_greeting": True,
        "greeting_detected": user_said,
        "greeting_adjusted_to": default_part,
        "timezone_default": "WIB",
        "wib_time": zone_rows[0][1].strftime("%Y-%m-%d %H:%M:%S WIB"),
        "wita_time": zone_rows[1][1].strftime("%Y-%m-%d %H:%M:%S WITA"),
        "wit_time": zone_rows[2][1].strftime("%Y-%m-%d %H:%M:%S WIT"),
        "model_skipped": True,
    }



def normalize_frequent_question_key(
    user_text: str,
) -> str:
    """Normalisasi pertanyaan agar cache tahan variasi kecil."""
    text = str(user_text or "").strip().lower()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[^\w\s\-:/.,]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def frequent_question_cache_key(
    user_text: str,
) -> str:
    normalized = normalize_frequent_question_key(user_text)
    return hmac.new(
        b"adioranye-frequent-question-cache",
        normalized.encode("utf-8", "ignore"),
        digestmod="sha256",
    ).hexdigest()


def load_frequent_question_cache() -> Dict[str, Any]:
    """Load cache jawaban sering muncul dari file JSON."""
    try:
        if not frequent_question_cache_file or not os.path.exists(frequent_question_cache_file):
            return {
                "version": 1,
                "items": {},
            }

        with open(
            frequent_question_cache_file,
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        if not isinstance(data, dict):
            return {
                "version": 1,
                "items": {},
            }

        if not isinstance(data.get("items"), dict):
            data["items"] = {}

        return data
    except Exception:
        return {
            "version": 1,
            "items": {},
        }


def save_frequent_question_cache(
    cache_data: Dict[str, Any],
) -> None:
    """Simpan cache jawaban sering muncul."""
    try:
        items = cache_data.get("items") or {}

        if len(items) > int(frequent_question_cache_max_entries or 500):
            sorted_items = sorted(
                items.items(),
                key=lambda pair: (
                    int(pair[1].get("hit_count", 0) or 0),
                    float(pair[1].get("updated_at_ts", 0) or 0),
                ),
                reverse=True,
            )
            trimmed = dict(
                sorted_items[: int(frequent_question_cache_max_entries or 500)]
            )
            cache_data["items"] = trimmed

        cache_data["saved_at"] = _wib_now_text()

        with open(
            frequent_question_cache_file,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                cache_data,
                file,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        pass


def is_current_or_dynamic_question_for_cache(
    user_text: str,
) -> bool:
    """Jangan cache pertanyaan yang jawabannya cepat berubah."""
    try:
        profile = detect_auto_live_scraping_need(user_text)
        if bool(profile.get("needed")):
            return True
    except Exception:
        pass

    lowered = str(user_text or "").lower()

    dynamic_markers = [
        "hari ini",
        "sekarang",
        "terbaru",
        "terkini",
        "update",
        "berita",
        "harga",
        "kurs",
        "cuaca",
        "jadwal",
        "viral",
        "tren",
        "trend",
        "live",
        "real time",
        "real-time",
        "saat ini",
        "barusan",
        "minggu ini",
        "bulan ini",
    ]

    return any(marker in lowered for marker in dynamic_markers)


def should_use_frequent_question_cache(
    user_text: str,
) -> bool:
    if not bool(frequent_question_cache_enabled):
        return False

    normalized = normalize_frequent_question_key(user_text)

    if len(normalized) < int(frequent_question_cache_min_chars or 4):
        return False

    if is_current_or_dynamic_question_for_cache(user_text):
        return False

    # Jangan cache command/admin/memory/upload.
    command_prefixes = (
        "/",
        "!",
        "#",
    )

    if normalized.startswith(command_prefixes):
        return False

    skip_markers = [
        "upload",
        "file ini",
        "pdf ini",
        "gambar ini",
        "kode ini",
        "error ini",
        "log ini",
        "kerjakan file",
        "perbaiki file",
        "buatkan desain",
        "terjemahkan pdf",
    ]

    if any(marker in normalized for marker in skip_markers):
        return False

    return True


def get_frequent_question_cached_answer(
    user_text: str,
) -> Tuple[str, Dict[str, Any]]:
    """Ambil jawaban cache jika masih valid."""
    if not should_use_frequent_question_cache(user_text):
        return "", {}

    cache_data = load_frequent_question_cache()
    items = cache_data.get("items") or {}
    key = frequent_question_cache_key(user_text)
    item = items.get(key)

    if not isinstance(item, dict):
        return "", {}

    now_ts = time.time()
    expires_at = float(item.get("expires_at_ts", 0) or 0)

    if expires_at and expires_at < now_ts:
        try:
            del items[key]
            cache_data["items"] = items
            save_frequent_question_cache(cache_data)
        except Exception:
            pass
        return "", {}

    answer = str(item.get("answer") or "").strip()

    if not answer:
        return "", {}

    item["hit_count"] = int(item.get("hit_count", 0) or 0) + 1
    item["last_hit_at"] = _wib_now_text()
    item["last_hit_at_ts"] = now_ts
    items[key] = item
    cache_data["items"] = items
    save_frequent_question_cache(cache_data)

    return answer, {
        "frequent_question_cache_hit": True,
        "frequent_question_cache_key": key,
        "frequent_question_cache_hit_count": item.get("hit_count", 0),
        "model_skipped": True,
        "cached_at": item.get("created_at", ""),
        "expires_at": item.get("expires_at", ""),
    }


def save_frequent_question_cached_answer(
    user_text: str,
    answer: str,
    meta: Dict[str, Any] | None = None,
) -> bool:
    """Simpan jawaban sukses ke frequent question cache."""
    if not should_use_frequent_question_cache(user_text):
        return False

    answer_text = str(answer or "").strip()

    if not answer_text or len(answer_text) < 3:
        return False

    meta_data = meta or {}

    if is_public_connection_error_answer(answer_text, meta=meta_data):
        return False

    if meta_data.get("rate_limited") or meta_data.get("public_error_sanitized"):
        return False

    if meta_data.get("current_info_mode") or meta_data.get("auto_live_scraping_needed"):
        return False

    if meta_data.get("local_time_greeting"):
        return False

    key = frequent_question_cache_key(user_text)
    normalized = normalize_frequent_question_key(user_text)
    now_ts = time.time()
    ttl = max(60, int(frequent_question_cache_ttl_seconds or 86400))
    expires_at_ts = now_ts + ttl

    cache_data = load_frequent_question_cache()
    items = cache_data.get("items") or {}
    existing = items.get(key) if isinstance(items.get(key), dict) else {}

    items[key] = {
        "question": str(user_text or "").strip()[:500],
        "normalized_question": normalized[:500],
        "answer": answer_text,
        "meta": {
            "model": meta_data.get("active_model_final")
            or meta_data.get("model")
            or meta_data.get("model_requested")
            or "",
            "strategy": meta_data.get("strategy_label", ""),
        },
        "created_at": existing.get("created_at") or _wib_now_text(),
        "created_at_ts": existing.get("created_at_ts") or now_ts,
        "updated_at": _wib_now_text(),
        "updated_at_ts": now_ts,
        "expires_at": _timestamp_to_wib_text(expires_at_ts),
        "expires_at_ts": expires_at_ts,
        "hit_count": int(existing.get("hit_count", 0) or 0),
    }

    cache_data["items"] = items
    save_frequent_question_cache(cache_data)

    return True


def clear_frequent_question_cache() -> int:
    cache_data = load_frequent_question_cache()
    items = cache_data.get("items") or {}
    count = len(items)
    cache_data["items"] = {}
    save_frequent_question_cache(cache_data)
    return count


def frequent_question_cache_stats() -> Dict[str, Any]:
    cache_data = load_frequent_question_cache()
    items = cache_data.get("items") or {}
    now_ts = time.time()

    active = 0
    expired = 0
    total_hits = 0

    for item in items.values():
        if not isinstance(item, dict):
            continue

        expires_at_ts = float(item.get("expires_at_ts", 0) or 0)

        if expires_at_ts and expires_at_ts < now_ts:
            expired += 1
        else:
            active += 1

        total_hits += int(item.get("hit_count", 0) or 0)

    top_items = sorted(
        [
            item
            for item in items.values()
            if isinstance(item, dict)
        ],
        key=lambda item: int(item.get("hit_count", 0) or 0),
        reverse=True,
    )[:10]

    return {
        "active": active,
        "expired": expired,
        "total": len(items),
        "total_hits": total_hits,
        "top_items": top_items,
    }


def export_frequent_question_cache_rows() -> List[Dict[str, Any]]:
    cache_data = load_frequent_question_cache()
    items = cache_data.get("items") or {}

    rows = []

    for key, item in items.items():
        if not isinstance(item, dict):
            continue

        rows.append(
            {
                "key": key,
                "question": item.get("question", ""),
                "normalized_question": item.get("normalized_question", ""),
                "hit_count": item.get("hit_count", 0),
                "created_at": item.get("created_at", ""),
                "updated_at": item.get("updated_at", ""),
                "expires_at": item.get("expires_at", ""),
                "answer_preview": str(item.get("answer", ""))[:240],
            }
        )

    rows.sort(
        key=lambda row: int(row.get("hit_count", 0) or 0),
        reverse=True,
    )

    return rows


def _health_window_label_wib() -> str:
    """Label jendela health check model dalam WIB."""
    hour = max(0, min(23, int(model_health_hour_wib or 0)))
    window = max(1, min(60, int(model_health_window_minutes or 60)))
    end_minute = window - 1
    return f"{hour:02d}:00-{hour:02d}:{end_minute:02d} WIB"


def is_model_health_check_allowed_now() -> bool:
    """Health check model aktif kapan saja."""
    return True


def _timestamp_to_wib_text(timestamp_value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(timestamp_value), WIB_TZ).strftime(
            "%Y-%m-%d %H:%M:%S WIB"
        )
    except Exception:
        return "belum pernah"


def _to_wib_display_text(value: Any) -> str:
    """Konversi datetime/timestamp/string UTC/ISO ke tampilan WIB jika memungkinkan."""
    if value in (None, ""):
        return ""

    if isinstance(value, (int, float)):
        return _timestamp_to_wib_text(value)

    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")

    raw = str(value).strip()
    if not raw:
        return ""
    if "WIB" in raw.upper():
        return raw

    try:
        if raw.endswith(" UTC"):
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S UTC").replace(
                tzinfo=timezone.utc
            )
        else:
            normalized = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")
    except Exception:
        return raw


def _tier_rank(model: str) -> int:
    tier = model_cost_tier(model)
    if tier == "cheap":
        return 0
    if tier in {"medium", "menengah"}:
        return 1
    return 2


def filter_models_by_tier(
    models: List[str],
    tier_rank: int,
) -> List[str]:
    return [
        model
        for model in unique_models(models)
        if _tier_rank(model) == int(tier_rank)
    ]


def sort_health_models_for_simple_chat(
    models: List[str],
    health_cache: Dict[str, Dict[str, Any]],
) -> List[str]:
    """Percakapan sederhana: murah dulu, lalu latency/harga."""
    active_models = [
        model
        for model in unique_models(models)
        if health_cache.get(model, {}).get("active")
    ]

    def sort_key(model: str) -> Tuple[int, float, int, float, str]:
        price = model_price(model)
        latency = get_model_effective_latency_ms(model, health_cache)
        penalty = get_model_performance_penalty(model)
        success_rate = get_model_success_rate(model)
        return (
            _tier_rank(model),
            latency + penalty,
            int(price.get("output", 999999999)),
            -success_rate,
            model,
        )

    return sorted(active_models, key=sort_key)


def prioritize_active_models(
    models: List[str], health_cache: Dict[str, Dict[str, Any]]
) -> List[str]:
    """Urutkan fallback aktif: tier hemat dulu, harga output rendah, lalu latency rendah."""
    active_models = [
        model
        for model in unique_models(models)
        if health_cache.get(model, {}).get("active")
    ]

    def sort_key(model: str) -> Tuple[int, float, float, int, str]:
        price = model_price(model)
        latency = get_model_effective_latency_ms(model, health_cache)
        success_rate = get_model_success_rate(model)
        penalty = get_model_performance_penalty(model)
        return (
            _tier_rank(model),
            -success_rate,
            latency + penalty,
            int(price.get("output", 999999999)),
            model,
        )

    return sorted(active_models, key=sort_key)


def prioritize_fastest_active_models(
    models: List[str], health_cache: Dict[str, Dict[str, Any]]
) -> List[str]:
    """Urutkan model aktif berdasarkan latency terendah untuk pertanyaan ringan/non-thinking."""
    active_models = [
        model
        for model in unique_models(models)
        if health_cache.get(model, {}).get("active")
    ]

    def sort_key(model: str) -> Tuple[float, float, int, str]:
        price = model_price(model)
        latency = get_model_effective_latency_ms(model, health_cache)
        success_rate = get_model_success_rate(model)
        penalty = get_model_performance_penalty(model)
        return (latency + penalty, -success_rate, int(price.get("output", 999999999)), model)

    return sorted(active_models, key=sort_key)


TRANSIENT_HEALTH_HTTP_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


def _is_gpt5_health_model(model: str) -> bool:
    return "gpt-5" in str(model or "").lower()


def _extract_health_content(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    choice = choices[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item or ""))
        return "\n".join(part for part in parts if part.strip()).strip()
    text = choice.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    delta = choice.get("delta") or {}
    delta_content = delta.get("content")
    if isinstance(delta_content, str):
        return delta_content.strip()
    return ""


def _build_model_health_payload(model: str) -> Dict[str, Any]:
    """Payload health check ultra-hemat token.

    Tujuan health check hanya membuktikan model bisa merespons, bukan meminta
    jawaban panjang. Karena itu prompt dan completion budget dibuat minimal.
    """
    max_tokens = (
        int(model_health_probe_gpt5_max_tokens or 8)
        if _is_gpt5_health_model(model)
        else int(model_health_probe_max_tokens or 2)
    )
    max_tokens = max(1, min(max_tokens, 12))
    prompt = str(model_health_probe_prompt or "ping").strip()[:20] or "ping"

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "OK"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_completion_tokens": max_tokens,
        "stream": False,
    }
    if _is_gpt5_health_model(model):
        payload["reasoning_effort"] = "minimal"
    return payload


def check_single_model_health(
    model: str, timeout: int = 12, retries: int = 1
) -> Dict[str, Any]:
    """Cek apakah model benar-benar aktif, bukan sekadar HTTP 200.

    Aktif hanya jika API mengembalikan choices dan content assistant tidak kosong.
    Error sementara 429/5xx/timeout dicoba ulang dan diberi label transient.
    """
    started = time.time()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    attempts = max(1, int(retries or 0) + 1)
    last_error = ""
    last_status = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(
                api_url,
                headers=headers,
                json=_build_model_health_payload(model),
                timeout=timeout,
            )
            latency_ms = round((time.time() - started) * 1000, 1)
            last_status = response.status_code

            if response.status_code != 200:
                is_transient = response.status_code in TRANSIENT_HEALTH_HTTP_CODES
                last_error = response.text[:500]
                if is_transient and attempt < attempts:
                    time.sleep(min(1.2, 0.35 * attempt))
                    continue
                return {
                    "active": False,
                    "health_status": "transient" if is_transient else "dead",
                    "error_class": "transient_http" if is_transient else "http_error",
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                    "attempts": attempt,
                    "checked_at": _wib_now_text(),
                    "tier": model_cost_tier(model),
                    "error": last_error,
                }

            try:
                data = response.json()
            except Exception:
                return {
                    "active": False,
                    "health_status": "dead",
                    "error_class": "invalid_json",
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                    "attempts": attempt,
                    "checked_at": _wib_now_text(),
                    "tier": model_cost_tier(model),
                    "error": "Respons 200 tetapi bukan JSON valid: "
                    + (response.text or "")[:500],
                }

            choices = data.get("choices") or [] if isinstance(data, dict) else []
            content = _extract_health_content(data)
            finish_reason = ""
            if choices and isinstance(choices[0], dict):
                finish_reason = str(choices[0].get("finish_reason") or "")

            usage = data.get("usage") if isinstance(data, dict) else {}
            if choices and content:
                return {
                    "active": True,
                    "health_status": "active",
                    "error_class": "",
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                    "attempts": attempt,
                    "checked_at": _wib_now_text(),
                    "tier": model_cost_tier(model),
                    "finish_reason": finish_reason,
                    "sample": content[:80],
                    "usage": usage if isinstance(usage, dict) else {},
                    "health_probe_tokens": int(model_health_probe_gpt5_max_tokens if _is_gpt5_health_model(model) else model_health_probe_max_tokens),
                    "error": "",
                }

            usage = data.get("usage") if isinstance(data, dict) else None
            details = (
                (usage or {}).get("completion_tokens_details")
                if isinstance(usage, dict)
                else None
            )
            reasoning_tokens = (
                (details or {}).get("reasoning_tokens")
                if isinstance(details, dict)
                else None
            )
            last_error = "Response 200 tetapi choices/content kosong"
            if reasoning_tokens:
                last_error += f"; reasoning_tokens={reasoning_tokens}"
            return {
                "active": False,
                "health_status": "dead",
                "error_class": "empty_content",
                "status_code": response.status_code,
                "latency_ms": latency_ms,
                "attempts": attempt,
                "checked_at": _wib_now_text(),
                "tier": model_cost_tier(model),
                "finish_reason": finish_reason,
                "error": last_error,
            }
        except requests.Timeout as exc:
            last_error = str(exc)[:500]
            if attempt < attempts:
                time.sleep(min(1.2, 0.35 * attempt))
                continue
            return {
                "active": False,
                "health_status": "transient",
                "error_class": "timeout",
                "status_code": last_status,
                "latency_ms": round((time.time() - started) * 1000, 1),
                "attempts": attempt,
                "checked_at": _wib_now_text(),
                "tier": model_cost_tier(model),
                "error": last_error,
            }
        except requests.RequestException as exc:
            last_error = str(exc)[:500]
            if attempt < attempts:
                time.sleep(min(1.2, 0.35 * attempt))
                continue
            return {
                "active": False,
                "health_status": "transient",
                "error_class": "request_exception",
                "status_code": last_status,
                "latency_ms": round((time.time() - started) * 1000, 1),
                "attempts": attempt,
                "checked_at": _wib_now_text(),
                "tier": model_cost_tier(model),
                "error": last_error,
            }
        except Exception as exc:
            return {
                "active": False,
                "health_status": "dead",
                "error_class": "unexpected_error",
                "status_code": last_status,
                "latency_ms": round((time.time() - started) * 1000, 1),
                "attempts": attempt,
                "checked_at": _wib_now_text(),
                "tier": model_cost_tier(model),
                "error": str(exc)[:500],
            }

    return {
        "active": False,
        "health_status": "dead",
        "error_class": "unknown",
        "status_code": last_status,
        "latency_ms": round((time.time() - started) * 1000, 1),
        "attempts": attempts,
        "checked_at": _wib_now_text(),
        "tier": model_cost_tier(model),
        "error": last_error or "Health check gagal tanpa detail.",
    }


def discover_api_model_candidates(force: bool = False) -> List[str]:
    """Ambil daftar model terbaru dari endpoint API provider jika tersedia."""
    if not bool(model_discovery_enabled):
        return TOP_USAGE_MODEL_CANDIDATES.copy()
    if not api_key:
        st.session_state.dynamic_model_discovery_error = (
            "SLASHAI_API_KEY belum diisi. Memakai katalog lokal."
        )
        return TOP_USAGE_MODEL_CANDIDATES.copy()
    now = time.time()
    cached = st.session_state.get("dynamic_api_models") or []
    last_checked = float(st.session_state.get("dynamic_api_models_checked_at") or 0)
    interval = max(300, int(model_discovery_interval or 3600))
    if not force and cached and now - last_checked < interval:
        return unique_models(cached + TOP_USAGE_MODEL_CANDIDATES)
    result = discover_available_models_from_api(
        api_url=api_url,
        api_key=api_key,
        models_api_url=models_api_url,
        timeout=int(model_discovery_timeout or 12),
    )
    models = unique_models((result.get("models") or []) + TOP_USAGE_MODEL_CANDIDATES)
    st.session_state.dynamic_api_models = models
    st.session_state.dynamic_api_models_checked_at = now
    st.session_state.dynamic_model_discovery_source = str(
        result.get("source_url") or ""
    )
    st.session_state.dynamic_model_discovery_error = (
        "" if result.get("ok") else str(result.get("error") or "")[:1200]
    )
    return models


def refresh_model_health_if_needed(force: bool = False, scope: str = "auto") -> Dict[str, Dict[str, Any]]:
    """Refresh health model.

    Otomatis tetap mengikuti jendela tengah malam WIB. Namun force=True dari admin
    boleh menjalankan cek saat itu juga, supaya tombol manual dan /rotate tidak memakai cache basi.
    """
    if not api_key:
        st.session_state.last_model_health_error = "SLASHAI_API_KEY belum diisi."
        return st.session_state.model_health_cache

    now = time.time()
    last_checked = float(st.session_state.get("model_health_checked_at") or 0)
    cache = st.session_state.get("model_health_cache") or {}
    interval = max(60, int(model_health_check_interval or 900))


    if not force and cache and now - last_checked < interval:
        return cache

    api_discovered_models = discover_api_model_candidates(force=force)
    all_models_to_check = unique_models(
        [st.session_state.get("active_model") or default_model, default_model]
        + api_discovered_models
        + TOP_USAGE_MODEL_CANDIDATES
        + MODEL_OPTIONS
        + CHEAP_MODEL_OPTIONS
        + MEDIUM_MODEL_OPTIONS
        + HIGH_COST_MODEL_OPTIONS
        + EXPENSIVE_MODEL_OPTIONS
        + DEFAULT_CHEAP_FALLBACK_MODELS
        + DEFAULT_EXPENSIVE_FALLBACK_MODELS
    )
    scope_clean = str(scope or st.session_state.get("active_health_check_scope") or "auto").lower()
    if scope_clean == "auto":
        scope_clean = (
            str(model_health_force_scope or "quick").lower()
            if force
            else "quick"
        )
    if scope_clean not in {"quick", "full"}:
        scope_clean = "quick"
    if scope_clean == "quick":
        quick_pool = unique_models(
            [st.session_state.get("active_model") or default_model, default_model]
            + DEFAULT_CHEAP_FALLBACK_MODELS[:4]
            + CHEAP_MODEL_OPTIONS[:6]
            + MEDIUM_MODEL_OPTIONS[:4]
            + DEFAULT_EXPENSIVE_FALLBACK_MODELS[:3]
            + HIGH_COST_MODEL_OPTIONS[:4]
            + TOP_USAGE_MODEL_CANDIDATES
        )
        limit = max(3, int(model_health_quick_limit or 8))
        models_to_check = quick_pool[:limit]
    else:
        full_limit = int(model_health_full_limit or 0)
        models_to_check = (
            all_models_to_check[:full_limit]
            if full_limit > 0
            else all_models_to_check
        )

    fresh_cache: Dict[str, Dict[str, Any]] = (
        dict(cache)
        if bool(model_health_preserve_cache) and isinstance(cache, dict)
        else {}
    )
    max_workers = max(1, min(int(model_health_workers or 4), len(models_to_check), 8))
    retries = max(0, min(int(model_health_retries or 0), 1))

    if not models_to_check:
        return fresh_cache

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                check_single_model_health,
                model_name,
                int(model_health_timeout or 12),
                retries,
            ): model_name
            for model_name in models_to_check
        }
        for future in as_completed(future_map):
            model_name = future_map[future]
            try:
                fresh_cache[model_name] = future.result()
            except Exception as exc:
                fresh_cache[model_name] = {
                    "active": False,
                    "health_status": "dead",
                    "error_class": "future_error",
                    "status_code": None,
                    "latency_ms": None,
                    "attempts": 0,
                    "checked_at": _wib_now_text(),
                    "tier": model_cost_tier(model_name),
                    "error": str(exc)[:500],
                }

    discovered_and_known_models = unique_models(
        MODEL_OPTIONS
        + api_discovered_models
        + TOP_USAGE_MODEL_CANDIDATES
        + DEFAULT_CHEAP_FALLBACK_MODELS
        + DEFAULT_EXPENSIVE_FALLBACK_MODELS
    )
    active_cheap = prioritize_active_models(
        filter_models_by_tier(discovered_and_known_models, 0),
        fresh_cache,
    )
    active_medium = prioritize_active_models(
        filter_models_by_tier(discovered_and_known_models, 1),
        fresh_cache,
    )
    active_expensive = prioritize_active_models(
        filter_models_by_tier(discovered_and_known_models, 2),
        fresh_cache,
    )

    st.session_state.model_health_cache = fresh_cache
    st.session_state.model_health_checked_at = now
    save_model_readiness_state_to_file(
        fresh_cache,
        now,
    )
    st.session_state.model_health_last_checked_count = len(models_to_check)
    st.session_state.model_health_last_scope = scope_clean
    st.session_state.model_health_token_saver = {
        "probe_max_tokens": int(model_health_probe_max_tokens or 2),
        "probe_gpt5_max_tokens": int(model_health_probe_gpt5_max_tokens or 8),
        "retries": retries,
        "workers": max_workers,
        "preserve_cache": bool(model_health_preserve_cache),
    }

    if active_cheap:
        st.session_state.active_cheap_fallback_models = active_cheap
    if active_medium:
        st.session_state.active_medium_fallback_models = active_medium
    if active_expensive:
        st.session_state.active_expensive_fallback_models = active_expensive

    active_total = sum(1 for item in fresh_cache.values() if item.get("active"))
    transient_total = sum(
        1 for item in fresh_cache.values() if item.get("health_status") == "transient"
    )
    if active_total:
        st.session_state.last_model_health_error = ""
    elif transient_total:
        st.session_state.last_model_health_error = "Belum ada model aktif; sebagian error sementara/transient. Coba ulang beberapa saat lagi."
    else:
        st.session_state.last_model_health_error = (
            "Tidak ada model yang lolos health check terakhir."
        )
    return fresh_cache


def get_prioritized_fallback_models() -> Tuple[List[str], List[str]]:
    """Ambil fallback aktif.

    Return kedua tetap kompatibel, tetapi urutannya sekarang:
    medium dulu, lalu mahal. Model murah tetap pada return pertama.
    """
    refresh_model_health_if_needed(force=False)
    cheap = (
        st.session_state.get("active_cheap_fallback_models")
        or DEFAULT_CHEAP_FALLBACK_MODELS.copy()
    )
    medium = (
        st.session_state.get("active_medium_fallback_models")
        or MEDIUM_MODEL_OPTIONS.copy()
    )
    expensive = (
        st.session_state.get("active_expensive_fallback_models")
        or DEFAULT_EXPENSIVE_FALLBACK_MODELS.copy()
    )
    return unique_models(cheap), unique_models(medium + expensive)


def get_rotating_cheap_primary(
    active_cheap_models: List[str], advance: bool = False
) -> str:
    """
    Ambil model murah aktif secara round-robin.
    - advance=False: hanya mengintip model murah berikutnya, aman untuk render UI.
    - advance=True: dipakai saat request benar-benar dikirim, lalu indeks digeser ke model murah berikutnya.
    """
    models = unique_models(active_cheap_models)
    if not models:
        return ""

    try:
        index = int(st.session_state.get("cheap_model_rotation_index", 0) or 0)
    except Exception:
        index = 0

    index = index % len(models)
    primary_model = models[index]

    if advance:
        st.session_state.cheap_model_rotation_index = (index + 1) % len(models)
        st.session_state.last_rotated_primary_model = primary_model
        st.session_state.active_model = primary_model

    return primary_model


def sync_rotation_index_to_selected_model(active_cheap_models: List[str]) -> None:
    """Jika admin memilih model murah tertentu, jadikan pilihan itu titik awal rotasi berikutnya."""
    models = unique_models(active_cheap_models)
    selected_model = str(st.session_state.get("active_model") or default_model).strip()
    if selected_model in models:
        st.session_state.cheap_model_rotation_index = models.index(selected_model)


def _contains_any(text: str, keywords: List[str]) -> bool:
    lowered = f" {str(text or '').lower()} "
    return any(keyword in lowered for keyword in keywords)


LIGHTWEIGHT_INTENT_KEYWORDS = [
    "hai",
    "halo",
    "hello",
    "thanks",
    "terima kasih",
    "makasih",
    "siapa kamu",
    "apa kabar",
    "oke",
    "ok",
    "sip",
    "lanjut",
    "ya",
    "tidak",
    "bisa",
    "test",
]

THINKING_INTENT_KEYWORDS = [
    "thinking",
    "reasoning",
    "berpikir",
    "nalar",
    "logika",
    "analisis",
    "analisa",
    "evaluasi",
    "bandingkan",
    "pertimbangkan",
    "strategi",
    "arsitektur",
    "algoritma",
    "debug",
    "error",
    "traceback",
    "exception",
    "bug",
    "refactor",
    "optimasi",
    "optimize",
    "perbaiki kode",
    "cek kode",
    "review kode",
    "audit kode",
    "skripsi",
    "tesis",
    "jurnal",
    "riset",
    "metodologi",
    "smartpls",
    "statistik",
    "regresi",
    "sentimen",
    "indobert",
    "buatkan alur",
    "bagan alur",
    "step by step",
    "langkah-langkah",
    "kenapa",
    "mengapa",
    "apa penyebab",
    "solusi terbaik",
    "rekomendasi terbaik",
    "prioritaskan",
    "model yang capable",
    "jawaban mendalam",
    "berpikir dalam",
    "tuning",
    "tune",
    "deploy",
    "vercel",
    "streamlit",
]

CODE_OR_LOG_MARKERS = [
    "```",
    "def ",
    "class ",
    "import ",
    "from ",
    "return ",
    "npm ",
    "pip ",
    "vercel",
    "status code",
    "response:",
    "build failed",
    "failed",
    "unauthorized",
    "creditsdepleted",
    "traceback",
    "exception",
    "streamlit",
    "session_state",
    "generate_answer",
    "generate_power_answer",
    "<html",
    "<script",
    "select * from",
]

FILE_EDITING_INTENT_KEYWORDS = [
    "kerjakan ke file",
    "buat file",
    "generate file",
    "download file",
    "replace app.py",
    "patch",
    "diff",
    "zip",
    "perbaiki file",
    "ubah file",
    "edit file",
    "upload ke github",
    "redeploy",
]

ACCURACY_CRITICAL_KEYWORDS = [
    "jangan mengarang",
    "akurat",
    "valid",
    "verifikasi",
    "fakta",
    "hukum",
    "medis",
    "keuangan",
    "regulasi",
    "sumber resmi",
    "kutipan",
    "referensi",
    "terbaru",
    "hari ini",
]

CAPABLE_MODEL_PRIORITY_PATTERNS = [
    ("gpt-5", 100),
    ("gpt-4.1", 92),
    ("gpt-4o", 86),
    ("o3", 84),
    ("o4", 82),
    ("claude", 80),
    ("sonnet", 78),
    ("opus", 82),
    ("gemini-2.5-pro", 78),
    ("gemini", 68),
    ("deepseek-r1", 72),
    ("qwen", 62),
    ("llama", 58),
]

FAST_MODEL_PENALTY_PATTERNS = [
    ("nano", -18),
    ("mini", -10),
    ("flash", -8),
    ("lite", -10),
]

RETRIEVAL_TRIGGER_KEYWORDS = [
    "berdasarkan file",
    "berdasarkan dokumen",
    "dari file",
    "dari dokumen",
    "kb",
    "knowledge base",
    "sumber",
    "referensi",
    "kutipan",
    "jurnal",
    "pdf",
    "docx",
    "data terbaru",
    "terbaru",
    "hari ini",
    "update",
    "berita",
    "harga",
    "jadwal",
    "aturan",
    "hukum",
    "regulasi",
    "medis",
    "keuangan",
    "riset",
    "paper",
]


def _keyword_hits(text: str, keywords: List[str]) -> List[str]:
    lowered = f" {str(text or '').lower()} "
    return [keyword for keyword in keywords if keyword in lowered]


def _normalize_operation_mode(value: Any) -> str:
    raw = str(value or "Seimbang").strip().lower()
    aliases = {
        "hemat": "Hemat",
        "murah": "Hemat",
        "cheap": "Hemat",
        "balanced": "Seimbang",
        "balance": "Seimbang",
        "seimbang": "Seimbang",
        "maksimal": "Maksimal",
        "max": "Maksimal",
        "maximum": "Maksimal",
        "pintar": "Maksimal",
    }
    return aliases.get(raw, "Seimbang")


def estimate_prompt_complexity(user_text: str) -> Dict[str, Any]:
    """Skor kompleksitas prompt untuk router model, RAG, dan quality control.

    Prinsip algoritma:
    - Prompt ringan tetap cepat dan hemat.
    - Prompt teknis/akademik/kode/file memakai jalur lebih capable.
    - Prompt yang membutuhkan fakta/sumber memicu retrieval dan verifikasi.
    - Skor tidak hanya berdasarkan panjang, tetapi juga jenis tugas.
    """
    text = str(user_text or "").strip()
    lowered = text.lower()
    words = re.findall(r"\b\w+\b", lowered)
    word_count = len(words)
    line_count = max(1, len(text.splitlines()))
    char_count = len(text)
    score = 0
    signals: List[str] = []

    light_hits = _keyword_hits(lowered, LIGHTWEIGHT_INTENT_KEYWORDS)
    thinking_hits = _keyword_hits(lowered, THINKING_INTENT_KEYWORDS)
    code_hits = _keyword_hits(lowered, CODE_OR_LOG_MARKERS)
    file_hits = _keyword_hits(lowered, FILE_EDITING_INTENT_KEYWORDS)
    retrieval_hits = _keyword_hits(lowered, RETRIEVAL_TRIGGER_KEYWORDS)
    accuracy_hits = _keyword_hits(lowered, ACCURACY_CRITICAL_KEYWORDS)

    if thinking_hits:
        score += min(7, 2 + len(thinking_hits) * 2)
        signals.append("thinking-task")

    if code_hits:
        score += min(8, 4 + len(code_hits) * 2)
        signals.append("code-or-log-task")

    if file_hits:
        score += min(7, 4 + len(file_hits))
        signals.append("file-editing-task")

    if retrieval_hits:
        score += min(5, 2 + len(retrieval_hits))
        signals.append("retrieval-needed")

    if accuracy_hits:
        score += min(4, 1 + len(accuracy_hits))
        signals.append("accuracy-critical")

    min_chars = int(
        st.session_state.get("active_thinking_min_chars", thinking_min_chars_default)
        or 180
    )

    if char_count >= min_chars and word_count >= 24:
        score += 2
        signals.append("long-context")

    if char_count >= 700 or word_count >= 95:
        score += 2
        signals.append("very-long-context")

    if line_count >= 8:
        score += 2
        signals.append("multi-line-context")

    if text.count("?") >= 2 and word_count >= 18:
        score += 2
        signals.append("multi-question")

    if (
        any(token in lowered for token in ["1.", "2.", "3.", "- ", "• "])
        and word_count >= 25
    ):
        score += 1
        signals.append("structured-request")

    if any(
        ext in lowered
        for ext in [".py", ".js", ".tsx", ".jsx", ".html", ".css", ".sql", ".toml", ".json"]
    ):
        score += 3
        signals.append("file-extension")

    if re.search(r"\b(error|exception|traceback|failed|status\s*code|unauthorized)\b", lowered):
        score += 3
        signals.append("error-diagnostic")

    if re.search(r"\b(api|database|sqlite|streamlit|vercel|github|deploy|router|cache|rag)\b", lowered):
        score += 2
        signals.append("technical-system")

    if light_hits and word_count <= 10 and not code_hits and not thinking_hits and not file_hits:
        score -= 4
        signals.append("casual-short")
    elif word_count <= 6 and not code_hits and not thinking_hits and not file_hits:
        score -= 2
        signals.append("short-prompt")

    score = max(0, min(score, 20))

    return {
        "score": score,
        "signals": signals,
        "thinking_hits": thinking_hits[:8],
        "code_hits": code_hits[:8],
        "file_hits": file_hits[:8],
        "retrieval_hits": retrieval_hits[:8],
        "accuracy_hits": accuracy_hits[:8],
        "word_count": word_count,
        "char_count": char_count,
        "line_count": line_count,
    }


def should_use_retrieval_for_prompt(user_text: str) -> bool:
    """Aktifkan RAG/web fallback hanya ketika manfaatnya jelas."""
    if not bool(power_features_enabled and power_rag_enabled):
        return False
    if bool(power_strict_rag_mode):
        return True

    info = estimate_prompt_complexity(user_text)
    text = str(user_text or "").strip().lower()

    if info.get("retrieval_hits") or info.get("accuracy_hits"):
        return True

    if info.get("file_hits") and any(
        token in text
        for token in [
            "file",
            "dokumen",
            "lampiran",
            "pdf",
            "docx",
            "data",
            "sumber",
        ]
    ):
        return True

    if len(text) >= 320 and not _contains_any(text, LIGHTWEIGHT_INTENT_KEYWORDS):
        return True

    if any(
        token in text
        for token in [
            "lampiran",
            "upload",
            "database",
            "dokumen",
            "sumber",
            "referensi",
            "berdasarkan",
        ]
    ):
        return True

    return False


def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = minimum
    return max(minimum, min(parsed, maximum))


def _model_capability_score(model: str) -> int:
    """Skor kasar kapabilitas model dari nama model.

    Dipakai hanya untuk memilih primary model pada prompt kompleks.
    Health check tetap menjadi syarat utama.
    """
    lowered = str(model or "").lower()
    score = 50

    for pattern, value in CAPABLE_MODEL_PRIORITY_PATTERNS:
        if pattern in lowered:
            score = max(score, value)

    for pattern, penalty in FAST_MODEL_PENALTY_PATTERNS:
        if pattern in lowered:
            score += penalty

    if model_cost_tier(model) == "cheap":
        score -= 12

    return max(0, score)



CURRENT_INFO_KEYWORDS = [
    "terbaru",
    "terkini",
    "hari ini",
    "sekarang",
    "saat ini",
    "barusan",
    "minggu ini",
    "bulan ini",
    "tahun ini",
    "update",
    "berita",
    "news",
    "viral",
    "tren",
    "trend",
    "jadwal",
    "harga",
    "kurs",
    "cuaca",
    "rilis",
    "rilisan",
    "regulasi",
    "aturan terbaru",
    "kebijakan terbaru",
    "presiden sekarang",
    "ceo sekarang",
    "siapa sekarang",
    "data terbaru",
    "statistik terbaru",
    "live",
    "real time",
    "real-time",
]

CURRENT_INFO_DOMAINS = [
    "politik",
    "pemerintah",
    "hukum",
    "regulasi",
    "harga",
    "kurs",
    "emas",
    "saham",
    "crypto",
    "cuaca",
    "jadwal",
    "olahraga",
    "film",
    "musik",
    "teknologi",
    "ai",
    "openai",
    "gemini",
    "chatgpt",
    "streamlit",
    "vercel",
    "github",
    "telegram",
    "bpjs",
    "kemenkes",
    "kementerian",
]


def detect_auto_live_scraping_need(
    user_text: str,
) -> Dict[str, Any]:
    """Deteksi apakah pertanyaan perlu info web/scraping terkini."""
    text = str(user_text or "").strip()
    lowered = text.lower()
    word_count = len(re.findall(r"\w+", lowered))

    if not bool(auto_live_scraping_enabled):
        return {
            "needed": False,
            "reason": "disabled",
            "topic": "auto",
            "keywords": [],
        }

    if len(text) < int(auto_live_scraping_min_query_chars or 8):
        return {
            "needed": False,
            "reason": "too_short",
            "topic": "auto",
            "keywords": [],
        }

    keyword_hits = [
        keyword
        for keyword in CURRENT_INFO_KEYWORDS
        if keyword in lowered
    ]

    domain_hits = [
        keyword
        for keyword in CURRENT_INFO_DOMAINS
        if keyword in lowered
    ]

    explicit_need = bool(keyword_hits)
    external_question = bool(
        domain_hits
        and any(
            marker in lowered
            for marker in [
                "apa",
                "berapa",
                "siapa",
                "kapan",
                "dimana",
                "di mana",
                "cek",
                "cari",
                "update",
                "info",
                "berita",
            ]
        )
    )

    dated_dynamic = bool(
        re.search(
            r"\b(20[2-9][0-9]|2026|2027|2028)\b",
            lowered,
        )
        and any(
            marker in lowered
            for marker in [
                "terbaru",
                "update",
                "data",
                "aturan",
                "jadwal",
                "harga",
            ]
        )
    )

    likely_current = bool(
        explicit_need
        or external_question
        or dated_dynamic
    )

    local_work_markers = [
        "perbaiki file",
        "kerjakan file",
        "ubah kode ini",
        "patch file",
        "buatkan desain",
        "lanjutkan",
    ]

    if any(marker in lowered for marker in local_work_markers) and not explicit_need:
        likely_current = False

    if not likely_current:
        return {
            "needed": False,
            "reason": "not_current_info",
            "topic": "auto",
            "keywords": keyword_hits + domain_hits,
        }

    if word_count <= 3 and not explicit_need:
        return {
            "needed": False,
            "reason": "too_short_without_current_marker",
            "topic": "auto",
            "keywords": keyword_hits + domain_hits,
        }

    return {
        "needed": True,
        "reason": "explicit_current_keyword"
        if explicit_need
        else "external_dynamic_question",
        "topic": text[:160].strip() or "auto",
        "keywords": keyword_hits + domain_hits,
    }




def _token_saver_clip_text(
    text: Any,
    max_chars: int,
    suffix: str = "\n...[dipangkas untuk hemat token]",
) -> str:
    value = str(text or "").strip()
    limit = int(max_chars or 0)

    if not value or limit <= 0:
        return value

    if len(value) <= limit:
        return value

    return value[: max(0, limit - len(suffix))].rstrip() + suffix


def _token_saver_mode() -> str:
    mode = str(token_saver_default_mode or "balanced").lower().strip()

    if mode not in {"off", "light", "balanced", "aggressive"}:
        mode = "balanced"

    return mode


def compact_recent_messages_for_token_saver(
    messages: List[Dict[str, Any]],
    limit: int | None = None,
    recent_full: int | None = None,
) -> List[Dict[str, str]]:
    """Kirim history pendek saja ke model.

    Pesan lama diringkas menjadi satu system note agar token tidak membengkak.
    """
    if not bool(token_saver_enabled):
        return messages or []

    raw_messages = [
        item
        for item in (messages or [])
        if isinstance(item, dict)
    ]

    if not raw_messages:
        return []

    max_messages = max(2, int(limit or web_history_limit or 6))
    full_count = max(2, int(recent_full or web_history_recent_full or 4))

    if len(raw_messages) <= max_messages:
        return [
            {
                "role": str(item.get("role") or "user"),
                "content": _token_saver_clip_text(
                    item.get("content", ""),
                    1200,
                ),
            }
            for item in raw_messages[-max_messages:]
        ]

    older = raw_messages[: -full_count]
    recent = raw_messages[-full_count:]

    older_lines: List[str] = []

    for item in older[-8:]:
        role = str(item.get("role") or "user")
        content = _token_saver_clip_text(
            item.get("content", ""),
            220,
            suffix="...",
        )
        if content:
            older_lines.append(f"{role}: {content}")

    compact: List[Dict[str, str]] = []

    if older_lines:
        compact.append(
            {
                "role": "system",
                "content": (
                    "Ringkasan percakapan lama untuk hemat token:\n"
                    + "\n".join(older_lines)
                ),
            }
        )

    for item in recent:
        compact.append(
            {
                "role": str(item.get("role") or "user"),
                "content": _token_saver_clip_text(
                    item.get("content", ""),
                    1400,
                ),
            }
        )

    return compact[-max_messages:]


def determine_dynamic_answer_token_budget(
    user_text: str,
    complexity: Dict[str, Any],
    route: Dict[str, Any],
    configured_max_tokens: int,
    live_scraping_needed: bool = False,
) -> int:
    """Budget jawaban dinamis untuk hemat token."""
    if not bool(token_saver_enabled):
        return int(configured_max_tokens)

    user_lower = str(user_text or "").lower()
    score = int((route or {}).get("complexity_score") or 0)
    very_complex = score >= 8
    complex_enough = bool((route or {}).get("thinking_mode")) or score >= 5
    complexity_hits = (route or {}).get("complexity_hits") or {}

    code_or_file = bool(
        complexity_hits.get("code")
        or any(
            marker in user_lower
            for marker in [
                "error",
                "kode",
                "code",
                "deploy",
                "file",
                "patch",
                "traceback",
                "log",
                "bug",
            ]
        )
    )

    asks_long = any(
        marker in user_lower
        for marker in [
            "jelaskan detail",
            "lengkap",
            "full",
            "panjang",
            "rinci",
            "mendalam",
            "step by step",
            "langkah lengkap",
        ]
    )

    asks_short = any(
        marker in user_lower
        for marker in [
            "singkat",
            "ringkas",
            "pendek",
            "inti saja",
            "langsung saja",
            "quick",
        ]
    )

    if asks_short:
        desired = min(max_tokens_casual, 450)
    elif live_scraping_needed:
        desired = min(max_tokens_normal, 1200)
    elif asks_long or very_complex:
        desired = max_tokens_long
    elif code_or_file:
        desired = max_tokens_technical
    elif complex_enough:
        desired = max_tokens_normal
    else:
        desired = max_tokens_casual

    operation_mode = _normalize_operation_mode(
        st.session_state.get("active_operation_mode", ai_operation_mode_default)
    )
    saver_mode = _token_saver_mode()

    if operation_mode == "Hemat":
        desired = int(desired * 0.75)
    elif operation_mode == "Maksimal":
        desired = int(desired * 1.2)

    if saver_mode == "light":
        desired = int(desired * 1.15)
    elif saver_mode == "aggressive":
        desired = int(desired * 0.65)
    elif saver_mode == "off":
        desired = int(configured_max_tokens)

    return max(256, min(int(configured_max_tokens), int(desired)))


def limit_context_for_token_saver(
    context: str,
    max_chars: int,
) -> str:
    if not bool(token_saver_enabled):
        return str(context or "")

    return _token_saver_clip_text(
        context,
        int(max_chars or 0),
    )



def choose_dynamic_runtime_options(
    user_text: str,
    route: Dict[str, Any],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Bangun opsi runtime AI berdasarkan jenis prompt.

    Prompt sederhana tetap cepat; prompt kompleks memakai RAG/verifier/self-check.
    Jika pertanyaan membutuhkan info terkini, live web fallback dipaksa aktif
    melalui metadata runtime.
    """
    complexity = route or {}
    score = int(complexity.get("complexity_score") or 0)
    thinking_mode = bool(complexity.get("thinking_mode"))
    live_scraping_profile = detect_auto_live_scraping_need(user_text)
    live_scraping_needed = bool(live_scraping_profile.get("needed"))

    retrieval_enabled = bool(
        power_features_enabled
        and power_rag_enabled
        and (
            should_use_retrieval_for_prompt(user_text)
            or live_scraping_needed
        )
    )
    accuracy_needed = bool(
        (complexity.get("complexity_hits") or {}).get("retrieval")
        or estimate_prompt_complexity(user_text).get("accuracy_hits")
        or live_scraping_needed
    )
    complex_enough = thinking_mode or score >= 5 or live_scraping_needed
    very_complex = score >= 8

    configured_max_tokens = _clamp_int(
        cfg.get("max_completion_tokens", 2600),
        minimum=500,
        maximum=12000,
    )
    operation_mode = _normalize_operation_mode(
        st.session_state.get("active_operation_mode", ai_operation_mode_default)
    )
    complexity_hits = complexity.get("complexity_hits") or {}
    code_or_file = bool(
        complexity_hits.get("code")
        or any(marker in str(user_text or "").lower() for marker in ["error", "kode", "code", "deploy", "file", "patch"])
    )
    dynamic_max_tokens = determine_dynamic_answer_token_budget(
        user_text=user_text,
        complexity=complexity,
        route=route,
        configured_max_tokens=configured_max_tokens,
        live_scraping_needed=live_scraping_needed,
    )

    rag_top_k = max(1, int(power_rag_top_k))
    if bool(token_saver_enabled):
        rag_top_k = min(
            rag_top_k,
            max(1, int(kb_max_chunks_token_saver or 3)),
        )
    response_cache_ttl_seconds = int(
        power_response_cache_ttl_seconds
        if not accuracy_needed
        else min(power_response_cache_ttl_seconds, 600)
    )

    current_info_mode = bool(live_scraping_needed)

    if current_info_mode:
        # Pertanyaan info terkini tidak boleh dijawab dari KB/cache lama.
        # Live web/Tavily harus menjadi sumber utama.
        retrieval_enabled = False
        rag_top_k = 0
        response_cache_ttl_seconds = 0

    return {
        "current_info_mode": current_info_mode,
        "enable_rag": bool(retrieval_enabled and not current_info_mode),
        "rag_top_k": rag_top_k,
        "enable_self_verification": bool(
            power_features_enabled
            and power_self_verification_enabled
            and (very_complex or accuracy_needed or retrieval_enabled)
        ),
        "quality_control_enabled": bool(
            power_quality_control_enabled and (complex_enough or retrieval_enabled)
        ),
        "quality_verifier_enabled": bool(
            power_quality_verifier_enabled and (very_complex or accuracy_needed)
        ),
        "query_rewriter_enabled": bool(
            power_query_rewriter_enabled and (retrieval_enabled or current_info_mode)
        ),
        "reranker_enabled": bool(power_reranker_enabled and retrieval_enabled),
        "semantic_cache_enabled": bool(
            power_semantic_cache_enabled and not very_complex and not current_info_mode
        ),
        "response_cache_ttl_seconds": response_cache_ttl_seconds,
        "max_completion_tokens": dynamic_max_tokens,
        "timeout": 90 if very_complex or live_scraping_needed else 60,
        "strategy_label": (
            "live-current-info"
            if live_scraping_needed
            else "analisis-mendalam"
            if very_complex
            else "analisis-standar"
            if complex_enough
            else "cepat-hemat"
        ),
        "accuracy_needed": accuracy_needed,
        "complex_enough": complex_enough,
        "very_complex": very_complex,
        "auto_live_scraping_needed": live_scraping_needed,
        "auto_live_scraping_reason": live_scraping_profile.get("reason", ""),
        "auto_live_scraping_topic": live_scraping_profile.get("topic", "auto"),
        "auto_live_scraping_keywords": live_scraping_profile.get("keywords", []),
    }




def is_thinking_question(user_text: str) -> bool:
    """Deteksi pertanyaan yang perlu model lebih capable/reasoning."""
    if not bool(st.session_state.get("active_thinking_model_router", True)):
        st.session_state.last_prompt_complexity = {
            "score": 0,
            "signals": ["thinking-router-off"],
        }
        return False

    complexity = estimate_prompt_complexity(user_text)
    st.session_state.last_prompt_complexity = complexity
    operation_mode = _normalize_operation_mode(
        st.session_state.get("active_operation_mode", ai_operation_mode_default)
    )

    if operation_mode == "Maksimal":
        threshold = 2
    elif operation_mode == "Hemat":
        threshold = 6
    else:
        threshold = 4

    return int(complexity.get("score") or 0) >= threshold


def get_capable_primary_model(
    active_expensive_models: List[str], health_cache: Dict[str, Dict[str, Any]]
) -> str:
    """Pilih model capable aktif untuk prompt kompleks.

    Versi ini tidak hanya mengambil model pertama dari daftar fallback.
    Model diperingkat dari estimasi kapabilitas, status aktif, latency, dan harga.
    """
    override = str(thinking_capable_model_override or "").strip()
    if override and health_cache.get(override, {}).get("active"):
        return override

    active_candidates: List[str] = []
    for model_name in unique_models(active_expensive_models + MODEL_OPTIONS):
        if health_cache.get(model_name, {}).get("active") and _tier_rank(model_name) > 0:
            active_candidates.append(model_name)

    if not active_candidates:
        return ""

    def sort_key(model_name: str) -> Tuple[int, float, float, int, str]:
        price = model_price(model_name)
        latency = get_model_effective_latency_ms(model_name, health_cache)
        success_rate = get_model_success_rate(model_name)
        penalty = get_model_performance_penalty(model_name)
        return (
            -_model_capability_score(model_name),
            -success_rate,
            latency + penalty,
            int(price.get("output", 999999999)),
            model_name,
        )

    return sorted(unique_models(active_candidates), key=sort_key)[0]



def choose_healthy_primary_model(
    selected_model: str,
    active_cheap_models: List[str],
    active_expensive_models: List[str],
    health_cache: Dict[str, Dict[str, Any]],
    operation_mode: str = "Seimbang",
) -> Dict[str, Any]:
    """Pilih model utama sehat.

    Jika model utama admin tidak aktif, sistem otomatis mencari model sehat
    dari daftar health check. Prioritas default: model hemat sehat dulu,
    baru model capable sehat.
    """
    selected = str(selected_model or "").strip()
    selected_health = health_cache.get(selected) or {}
    selected_active = bool(selected_health.get("active"))

    if selected and selected_active and not is_model_runtime_blocked(selected):
        return {
            "model": selected,
            "changed": False,
            "from_model": selected,
            "reason": "selected-primary-healthy",
            "source": "selected",
        }

    cheap_candidates = filter_runtime_blocked_models(
        sort_health_models_for_simple_chat(
            active_cheap_models,
            health_cache,
        )
    )
    higher_candidates = filter_runtime_blocked_models(
        prioritize_active_models(
            active_expensive_models,
            health_cache,
        )
    )

    prefer_cheap = bool(auto_replace_primary_prefer_cheap)

    if operation_mode == "Maksimal" and higher_candidates and not prefer_cheap:
        candidates = higher_candidates + cheap_candidates
    else:
        candidates = cheap_candidates + higher_candidates

    candidates = unique_models(
        [
            model
            for model in candidates
            if health_cache.get(model, {}).get("active")
            and not is_model_runtime_blocked(model)
        ]
    )

    if candidates:
        replacement = candidates[0]
        return {
            "model": replacement,
            "changed": replacement != selected,
            "from_model": selected,
            "reason": "auto-replaced-inactive-primary",
            "source": "health-check",
        }

    return {
        "model": selected or default_model,
        "changed": False,
        "from_model": selected,
        "reason": "no-healthy-primary-found",
        "source": "fallback",
    }


def apply_healthy_primary_model(
    selected_model: str = "",
    reason: str = "auto",
) -> Dict[str, Any]:
    """Ganti session active_model ke model sehat jika primary saat ini mati."""
    health_cache = st.session_state.get("model_health_cache") or {}

    if not health_cache:
        try:
            refresh_model_health_if_needed(
                force=True,
                scope="quick",
            )
        except Exception as exc:
            st.session_state.last_model_health_error = str(exc)[:500]

        health_cache = st.session_state.get("model_health_cache") or {}

    active_cheap_models, active_expensive_models = get_prioritized_fallback_models()
    active_medium_models = (
        st.session_state.get("active_medium_fallback_models")
        or MEDIUM_MODEL_OPTIONS.copy()
    )
    active_high_cost_models = (
        st.session_state.get("active_expensive_fallback_models")
        or DEFAULT_EXPENSIVE_FALLBACK_MODELS.copy()
    )
    active_higher_models = unique_models(active_medium_models + active_high_cost_models)
    operation_mode = _normalize_operation_mode(
        st.session_state.get(
            "active_operation_mode",
            ai_operation_mode_default,
        )
    )

    selected = str(
        selected_model
        or st.session_state.get("active_model")
        or default_model
    ).strip()

    result = choose_healthy_primary_model(
        selected_model=selected,
        active_cheap_models=active_cheap_models,
        active_expensive_models=active_expensive_models,
        health_cache=health_cache,
        operation_mode=operation_mode,
    )

    replacement = str(result.get("model") or "").strip()

    if (
        bool(auto_replace_inactive_primary_model)
        and replacement
        and replacement != selected
        and result.get("changed")
    ):
        st.session_state.active_model = replacement
        sync_rotation_index_to_selected_model(active_cheap_models)
        st.session_state.last_auto_primary_replacement = {
            "from": selected,
            "to": replacement,
            "reason": reason,
            "at": _wib_now_text(),
            "routing_reason": result.get("reason", ""),
        }

    return result




def should_auto_refresh_model_status() -> bool:
    """Tentukan apakah auto-refresh status model perlu berjalan."""
    if not bool(model_status_auto_refresh_enabled):
        return False

    if not api_key:
        return False

    now = time.time()
    interval = max(
        30,
        int(model_status_auto_refresh_interval_seconds or 90),
    )
    last_auto = float(
        st.session_state.get("model_status_auto_refresh_last_ts") or 0
    )
    checked_at = float(
        st.session_state.get("model_health_checked_at") or 0
    )

    if not checked_at:
        return True

    if is_model_readiness_stale(checked_at):
        return True

    return (now - last_auto) >= interval


def maybe_auto_refresh_model_status(
    reason: str = "auto",
) -> Dict[str, Any]:
    """Refresh status model secara berkala tanpa memaksa reload halaman.

    - Hanya quick health check secara default.
    - Throttled memakai MODEL_STATUS_AUTO_REFRESH_INTERVAL_SECONDS.
    - Hasil disimpan ke session dan file persistent.
    - Jika model utama lama tidak sehat, otomatis dipromosikan ke model sehat.
    """
    if not should_auto_refresh_model_status():
        return {
            "ran": False,
            "reason": "not_due",
        }

    if st.session_state.get("model_status_auto_refresh_running"):
        return {
            "ran": False,
            "reason": "already_running",
        }

    st.session_state.model_status_auto_refresh_running = True
    started_at = time.time()

    try:
        scope = str(model_status_auto_refresh_scope or "quick").lower().strip()

        if scope not in {"quick", "full", "auto"}:
            scope = "quick"

        refresh_model_health_if_needed(
            force=False,
            scope=scope,
        )

        replacement_result = apply_healthy_primary_model(
            reason=f"auto-refresh-{reason}",
        )

        st.session_state.model_status_auto_refresh_last_ts = time.time()
        st.session_state.model_status_auto_refresh_last_text = _wib_now_text()
        st.session_state.model_status_auto_refresh_last_reason = reason
        st.session_state.model_status_auto_refresh_last_duration_ms = int(
            (time.time() - started_at) * 1000
        )

        return {
            "ran": True,
            "reason": reason,
            "scope": scope,
            "duration_ms": st.session_state.model_status_auto_refresh_last_duration_ms,
            "replacement": replacement_result,
        }
    except Exception as exc:
        st.session_state.last_model_health_error = str(exc)[:500]
        st.session_state.model_status_auto_refresh_last_error = str(exc)[:500]

        return {
            "ran": False,
            "reason": "error",
            "error": str(exc)[:500],
        }
    finally:
        st.session_state.model_status_auto_refresh_running = False


def build_live_model_status_html(
    readiness: Dict[str, Any],
    refresh_meta: Dict[str, Any] | None = None,
) -> str:
    """HTML kecil untuk status refresh model tanpa mengganggu chat."""
    refresh_meta = refresh_meta or {}
    status_class = str(readiness.get("class") or "checking")
    status_label = sanitize_model_readiness_text(
        readiness.get("label") or "Perlu cek model"
    )
    next_model = str(readiness.get("next_model") or "").strip()
    checked_at = str(readiness.get("checked_at") or "belum dicek")
    last_auto = str(
        st.session_state.get("model_status_auto_refresh_last_text")
        or "-"
    )
    duration = st.session_state.get("model_status_auto_refresh_last_duration_ms")
    duration_text = f" • {duration} ms" if duration else ""

    if refresh_meta.get("ran"):
        refresh_text = f"auto-refresh: baru saja{duration_text}"
    else:
        refresh_text = f"auto-refresh terakhir: {last_auto}{duration_text}"

    return f"""
    <div class="model-auto-refresh-panel status-{_html_escape(status_class)}">
        <span class="model-auto-dot"></span>
        <span><strong>{_html_escape(status_label)}</strong></span>
        <span>Model: {_html_escape(next_model or '-')}</span>
        <span>Cek: {_html_escape(checked_at)}</span>
        <span>{_html_escape(refresh_text)}</span>
    </div>
    """



def render_auto_model_status_refresh_panel() -> None:
    """Panel status model tanpa fragment/polling frontend."""
    if not bool(model_status_auto_refresh_public_panel):
        return

    refresh_meta = maybe_auto_refresh_model_status(
        reason="public-status-panel",
    )
    route_preview = build_model_routing_plan(
        user_text="halo",
    )
    readiness = get_model_readiness_state(route_preview)
    st.markdown(
        build_live_model_status_html(
            readiness,
            refresh_meta=refresh_meta,
        ),
        unsafe_allow_html=True,
    )

def build_model_routing_plan(
    advance_rotation: bool = False, user_text: str = ""
) -> Dict[str, Any]:
    """
    Routing tuning final:
    1) Mode Hemat: tahan model menengah/mahal kecuali tidak ada model hemat aktif.
    2) Mode Seimbang: chat ringan memakai model hemat tercepat; prompt kompleks langsung ke model capable.
    3) Mode Maksimal: agresif memakai model capable aktif.
    4) Fallback tetap aman: murah lain dulu, lalu capable jika diizinkan.
    5) Setelah request selesai, state dikembalikan ke model hemat aktif bila memungkinkan.
    """
    active_cheap_models, active_expensive_models = get_prioritized_fallback_models()
    active_medium_models = (
        st.session_state.get("active_medium_fallback_models")
        or MEDIUM_MODEL_OPTIONS.copy()
    )
    active_high_cost_models = (
        st.session_state.get("active_expensive_fallback_models")
        or DEFAULT_EXPENSIVE_FALLBACK_MODELS.copy()
    )
    active_higher_models = unique_models(
        active_medium_models + active_high_cost_models
    )
    operation_mode = _normalize_operation_mode(
        st.session_state.get("active_operation_mode", ai_operation_mode_default)
    )
    selected_model = str(st.session_state.get("active_model") or default_model).strip()
    health_cache = st.session_state.get("model_health_cache") or {}
    if bool(auto_replace_inactive_primary_model) and health_cache:
        healthy_primary_result = choose_healthy_primary_model(
            selected_model=selected_model,
            active_cheap_models=st.session_state.get("active_cheap_fallback_models")
            or DEFAULT_CHEAP_FALLBACK_MODELS.copy(),
            active_expensive_models=unique_models(
                (
                    st.session_state.get("active_medium_fallback_models")
                    or MEDIUM_MODEL_OPTIONS.copy()
                )
                + (
                    st.session_state.get("active_expensive_fallback_models")
                    or DEFAULT_EXPENSIVE_FALLBACK_MODELS.copy()
                )
            ),
            health_cache=health_cache,
            operation_mode=_normalize_operation_mode(
                st.session_state.get(
                    "active_operation_mode",
                    ai_operation_mode_default,
                )
            ),
        )
        healthy_primary_model = str(healthy_primary_result.get("model") or "").strip()
        if (
            healthy_primary_model
            and healthy_primary_model != selected_model
            and healthy_primary_result.get("changed")
        ):
            st.session_state.active_model = healthy_primary_model
            selected_model = healthy_primary_model
            st.session_state.last_auto_primary_replacement = {
                "from": healthy_primary_result.get("from_model", ""),
                "to": healthy_primary_model,
                "reason": "routing-auto-replace",
                "at": _wib_now_text(),
                "routing_reason": healthy_primary_result.get("reason", ""),
            }
    complexity = estimate_prompt_complexity(user_text)

    selected_is_cheap = _tier_rank(selected_model) == 0
    selected_is_active = bool(health_cache.get(selected_model, {}).get("active"))
    rotate_enabled = bool(st.session_state.get("active_rotate_cheap_primary", True))
    fast_normal_enabled = bool(
        st.session_state.get("active_fast_normal_model_router", True)
    )
    fastest_cheap_models = sort_health_models_for_simple_chat(
        active_cheap_models,
        health_cache,
    )
    thinking_mode = is_thinking_question(user_text)

    if operation_mode == "Hemat":
        capable_primary = ""
    else:
        capable_primary = get_capable_primary_model(
            active_higher_models, health_cache
        )

    direct_to_expensive = False
    thinking_direct_to_capable = False
    normal_fast_mode = False
    rotated_primary = ""
    routing_reason = ""

    if operation_mode == "Maksimal" and capable_primary:
        primary_model = capable_primary
        direct_to_expensive = True
        thinking_direct_to_capable = True
        routing_reason = "mode-maksimal-capable"
    elif thinking_mode and capable_primary and operation_mode != "Hemat":
        primary_model = capable_primary
        direct_to_expensive = True
        thinking_direct_to_capable = True
        routing_reason = "prompt-kompleks-capable"
    elif active_cheap_models:
        if fast_normal_enabled and fastest_cheap_models:
            primary_model = fastest_cheap_models[0]
            normal_fast_mode = True
            routing_reason = "prompt-ringan-model-hemat-tercepat"
        elif rotate_enabled:
            primary_model = get_rotating_cheap_primary(
                active_cheap_models, advance=advance_rotation
            )
            rotated_primary = primary_model
            routing_reason = "rotasi-model-hemat"
        elif (
            selected_is_cheap
            and selected_model in active_cheap_models
            and selected_is_active
        ):
            primary_model = selected_model
            routing_reason = "model-admin-aktif"
        elif default_model in active_cheap_models:
            primary_model = default_model
            routing_reason = "default-model-hemat"
        else:
            primary_model = active_cheap_models[0]
            routing_reason = "fallback-model-hemat-aktif"
    elif active_expensive_models:
        primary_model = active_expensive_models[0]
        direct_to_expensive = True
        routing_reason = "tidak-ada-model-hemat-aktif"
    else:
        primary_model = selected_model or default_model
        routing_reason = "fallback-terakhir-belum-terverifikasi"

    if thinking_direct_to_capable:
        cheap_fallback_models = []
    else:
        cheap_pool = (
            fastest_cheap_models
            if normal_fast_mode and fastest_cheap_models
            else active_cheap_models
        )
        cheap_fallback_models = [
            model for model in cheap_pool if model != primary_model
        ]

    expensive_fallback_models = [
        model for model in active_expensive_models if model != primary_model
    ]

    allow_expensive = bool(active_expensive_models) and (
        bool(st.session_state.get("allow_expensive_fallback", True))
        or direct_to_expensive
        or thinking_direct_to_capable
    )

    if operation_mode == "Hemat":
        allow_expensive = False
        expensive_fallback_models = []
        if primary_model not in active_cheap_models and active_cheap_models:
            primary_model = active_cheap_models[0]
            direct_to_expensive = False
            thinking_direct_to_capable = False
            routing_reason = "mode-hemat-model-hemat"
    elif operation_mode == "Maksimal" and active_expensive_models:
        allow_expensive = True

    max_expensive = int(st.session_state.get("max_expensive_models", 1) or 1)
    if expensive_fallback_models:
        max_expensive = max(1, min(max_expensive, len(expensive_fallback_models)))
    else:
        max_expensive = 1

    max_smart_models = max(
        int(st.session_state.get("active_max_smart_models", 2) or 2),
        len(cheap_fallback_models),
        1,
    )

    return_to_primary = (
        bool(st.session_state.get("active_return_to_primary", True))
        and not direct_to_expensive
    )

    next_cheap_model = (
        get_rotating_cheap_primary(active_cheap_models, advance=False)
        if active_cheap_models
        else ""
    )
    fastest_cheap_primary = fastest_cheap_models[0] if fastest_cheap_models else ""

    if is_model_runtime_blocked(primary_model):
        available_cheap = filter_runtime_blocked_models(active_cheap_models)
        available_expensive = filter_runtime_blocked_models(active_expensive_models)

        if available_cheap:
            primary_model = available_cheap[0]
            direct_to_expensive = False
            thinking_direct_to_capable = False
        elif available_expensive:
            primary_model = available_expensive[0]
            direct_to_expensive = True

    cheap_fallback_models = filter_runtime_blocked_models(cheap_fallback_models)
    expensive_fallback_models = filter_runtime_blocked_models(expensive_fallback_models)
    active_cheap_models = filter_runtime_blocked_models(active_cheap_models)
    active_expensive_models = filter_runtime_blocked_models(active_expensive_models)
    active_medium_models = filter_runtime_blocked_models(active_medium_models)
    active_high_cost_models = filter_runtime_blocked_models(active_high_cost_models)
    active_higher_models = filter_runtime_blocked_models(active_higher_models)
    fastest_cheap_models = filter_runtime_blocked_models(fastest_cheap_models)

    return {
        "primary_model": primary_model,
        "cheap_fallback_models": unique_models(cheap_fallback_models),
        "expensive_fallback_models": unique_models(expensive_fallback_models),
        "allow_expensive_fallback": allow_expensive,
        "max_expensive_models": max_expensive,
        "max_smart_models": max_smart_models,
        "return_to_primary": return_to_primary,
        "direct_to_expensive": direct_to_expensive,
        "thinking_mode": thinking_mode,
        "thinking_direct_to_capable": thinking_direct_to_capable,
        "normal_fast_mode": normal_fast_mode,
        "capable_primary_model": capable_primary,
        "fastest_cheap_primary_model": fastest_cheap_primary,
        "fast_cheap_models": unique_models(fastest_cheap_models),
        "active_cheap_models": active_cheap_models,
        "active_expensive_models": active_expensive_models,
        "active_medium_models": active_medium_models,
        "active_high_cost_models": active_high_cost_models,
        "health_candidate_tiers": "cheap-medium-expensive",
        "rotate_cheap_primary": rotate_enabled,
        "fast_normal_model_router": fast_normal_enabled,
        "rotated_primary_model": rotated_primary,
        "next_cheap_primary_model": next_cheap_model,
        "cheap_rotation_index": int(
            st.session_state.get("cheap_model_rotation_index", 0) or 0
        ),
        "operation_mode": operation_mode,
        "routing_reason": routing_reason,
        "auto_replace_inactive_primary_model": bool(auto_replace_inactive_primary_model),
        "last_auto_primary_replacement": st.session_state.get(
            "last_auto_primary_replacement",
            {},
        ),
        "complexity_score": int(complexity.get("score") or 0),
        "complexity_signals": complexity.get("signals", []),
        "complexity_hits": {
            "thinking": complexity.get("thinking_hits", []),
            "code": complexity.get("code_hits", []),
            "retrieval": complexity.get("retrieval_hits", []),
        },
    }


def restore_active_model_to_cheap(preferred_model: str = "") -> None:
    """Kembalikan pilihan model aktif ke model murah yang hidup setelah request memakai expensive."""
    active_cheap_models, _ = get_prioritized_fallback_models()
    if not active_cheap_models:
        return

    preferred_model = str(preferred_model or "").strip()
    if preferred_model in active_cheap_models:
        st.session_state.active_model = preferred_model
        return

    current_model = str(st.session_state.get("active_model") or default_model).strip()
    if current_model in active_cheap_models:
        return

    next_cheap_model = get_rotating_cheap_primary(active_cheap_models, advance=False)
    if next_cheap_model:
        st.session_state.active_model = next_cheap_model
    elif default_model in active_cheap_models:
        st.session_state.active_model = default_model
    else:
        st.session_state.active_model = active_cheap_models[0]


def render_model_health_table() -> None:
    """Tampilkan daftar model aktif/nonaktif untuk admin."""
    cache = st.session_state.get("model_health_cache") or {}
    if not cache:
        st.info(
            "Belum ada hasil cek model. Klik tombol cek manual atau kirim pertanyaan agar sistem mengecek otomatis."
        )
        return

    rows = []
    for model_name, info in cache.items():
        rows.append(
            {
                "status": "🟢 aktif" if info.get("active") else "🔴 mati",
                "model": model_name,
                "tier": model_cost_tier(model_name),
                "harga": model_price_label(model_name),
                "latency_ms": info.get("latency_ms"),
                "kode": info.get("status_code"),
                "dicek": _to_wib_display_text(info.get("checked_at")),
                "error": str(info.get("error") or "")[:120],
            }
        )

    rows.sort(
        key=lambda row: (
            0 if row["status"].startswith("🟢") else 1,
            row["tier"],
            row["latency_ms"] or 999999,
            row["model"],
        )
    )
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _html_escape(value: Any) -> str:
    # Escape teks sebelum dimasukkan ke HTML custom Streamlit.
    return html.escape(str(value or ""), quote=True)


def get_answer_model_name(meta: Dict[str, Any] | None, fallback: str = "") -> str:
    # Ambil nama model yang benar-benar dipakai dari metadata jawaban.
    data = meta or {}
    model_name = (
        data.get("active_model_final")
        or data.get("model_final")
        or data.get("model_used")
        or data.get("model")
        or data.get("model_requested")
        or fallback
        or ""
    )
    return str(model_name or "").strip()


def render_answer_model_caption(
    meta: Dict[str, Any] | None, fallback: str = "", admin_detail: bool = False
) -> None:
    # Tampilkan model yang menjawab di bawah respons assistant.
    model_name = get_answer_model_name(meta, fallback=fallback)
    if not model_name:
        return

    data = meta or {}
    caption_text = f"Model aktif: {model_name}"

    kb_sources = data.get("power_kb_sources") or data.get("power_rag_sources") or []
    show_kb_sources = bool(data.get("show_kb_sources", False))
    if kb_sources and (show_kb_sources or admin_detail):
        caption_text += f" • KB: {len(kb_sources)} sumber"

    # Detail jalur routing hanya untuk admin agar tampilan publik tetap bersih.
    if admin_detail:
        consulted = data.get("consulted_models") or []
        if consulted:
            caption_text += " • konsultasi: " + ", ".join(
                str(item) for item in consulted[:4]
            )
        if data.get("expensive_fallback_used"):
            caption_text += " • model menengah/mahal dipakai"
        if kb_sources:
            source_titles = []
            for item in kb_sources[:3]:
                label = str(item.get("citation") or item.get("title") or "").strip()
                if label:
                    source_titles.append(label[:80])
            if source_titles:
                caption_text += " • sumber: " + "; ".join(source_titles)

    st.caption(caption_text)


def show_feedback_notice(
    message: str,
    icon: str = "✅",
) -> None:
    """Show compact feedback confirmation without a large alert box."""
    try:
        st.toast(
            message,
            icon=icon,
        )
    except Exception:
        st.caption(f"{icon} {message}")


def render_feedback_controls(
    meta: Dict[str, Any] | None,
    answer_text: str = "",
    key_prefix: str = "feedback",
) -> None:
    """Render very compact feedback controls for assistant answers."""
    data = meta or {}
    interaction_id = int(data.get("power_interaction_id") or 0)

    if not interaction_id or not power_features_enabled:
        return

    user_id = (
        "web-admin"
        if st.session_state.get("admin_authenticated")
        else "web-public"
    )

    def save_feedback(
        rating: int,
        label: str,
    ) -> int:
        feedback_id = power_store.record_feedback(
            interaction_id=interaction_id,
            rating=rating,
            label=label,
            user_id=user_id,
        )

        if rating < 0:
            try:
                question_text = ""
                if len(st.session_state.chat_messages) >= 2:
                    previous_message = st.session_state.chat_messages[-2] or {}
                    question_text = str(previous_message.get("content", ""))

                power_store.log_knowledge_gap(
                    question=question_text,
                    reason="negative_feedback",
                    intent=str(data.get("power_intent") or "general"),
                    user_id=user_id,
                    channel="web",
                    priority=2,
                    meta={"interaction_id": interaction_id},
                )
            except Exception:
                pass

        return feedback_id

    st.markdown(
        """
        <div class="feedback-info-box feedback-info-box--mobile-compact">
            <span class="feedback-info-title">Feedback</span>
            <span class="feedback-info-text">
                Tekan 👍 jika sesuai, atau 👎 jika perlu diperbaiki.
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    feedback_state_key = f"{key_prefix}_recorded_{interaction_id}"
    thumbs_key = f"{key_prefix}_thumbs_{interaction_id}"

    if hasattr(st, "feedback"):
        selected_feedback = st.feedback(
            "thumbs",
            key=thumbs_key,
        )

        if selected_feedback is not None:
            already_recorded = st.session_state.get(feedback_state_key)

            if not already_recorded:
                rating = 1 if int(selected_feedback) == 1 else -1
                label = "bagus" if rating > 0 else "kurang"
                feedback_id = save_feedback(
                    rating=rating,
                    label=label,
                )

                st.session_state[feedback_state_key] = label

                if rating > 0:
                    show_feedback_notice(
                        f"Feedback tersimpan #{feedback_id}. Terima kasih.",
                        icon="✅",
                    )
                else:
                    show_feedback_notice(
                        f"Feedback tersimpan #{feedback_id}. Akan dipakai untuk perbaikan.",
                        icon="📝",
                    )
    else:
        cols = st.columns(
            [0.28, 0.28, 5.4],
            gap="small",
        )

        with cols[0]:
            if st.button(
                "👍",
                key=f"{key_prefix}_up_{interaction_id}",
                help="Bagus: jawaban sudah sesuai dan membantu.",
            ):
                feedback_id = save_feedback(
                    rating=1,
                    label="bagus",
                )
                show_feedback_notice(
                    f"Feedback tersimpan #{feedback_id}. Terima kasih.",
                    icon="✅",
                )

        with cols[1]:
            if st.button(
                "👎",
                key=f"{key_prefix}_down_{interaction_id}",
                help="Kurang: jawaban masih perlu diperbaiki.",
            ):
                feedback_id = save_feedback(
                    rating=-1,
                    label="kurang",
                )
                show_feedback_notice(
                    f"Feedback tersimpan #{feedback_id}. Akan dipakai untuk perbaikan.",
                    icon="📝",
                )

    if st.session_state.get("admin_authenticated"):
        template_cols = st.columns(
            [0.32, 6.0],
            gap="small",
        )

        with template_cols[0]:
            if st.button(
                "📌",
                key=f"{key_prefix}_tmpl_{interaction_id}",
                help="Simpan jawaban ini sebagai template admin.",
            ):
                trigger_query = ""
                if len(st.session_state.chat_messages) >= 2:
                    previous_message = st.session_state.chat_messages[-2] or {}
                    trigger_query = str(previous_message.get("content", ""))

                template_id = power_store.save_answer_template(
                    title=f"Template dari jawaban #{interaction_id}",
                    trigger_query=trigger_query,
                    answer=answer_text,
                    intent=str(data.get("power_intent") or "general"),
                    tags="feedback,best-answer",
                )
                show_feedback_notice(
                    f"Template tersimpan #{template_id}.",
                    icon="📌",
                )

    if data.get("strict_rag_blocked"):
        st.caption(
            f"Strict RAG memblokir jawaban. Gap ID: {data.get('knowledge_gap_id')}"
        )




def save_model_readiness_state_to_file(
    cache: Dict[str, Dict[str, Any]],
    checked_at: float,
) -> None:
    """Simpan status health check agar tetap ada setelah Streamlit restart."""
    try:
        payload = {
            "checked_at": float(checked_at or time.time()),
            "checked_at_text": _timestamp_to_wib_text(float(checked_at or time.time())),
            "cache": cache or {},
            "saved_at": time.time(),
        }

        with open(
            model_readiness_state_file,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        pass


def load_model_readiness_state_from_file() -> Dict[str, Any]:
    """Baca status health check persistent dari file JSON."""
    try:
        if not model_readiness_state_file or not os.path.exists(model_readiness_state_file):
            return {}

        with open(
            model_readiness_state_file,
            "r",
            encoding="utf-8",
        ) as file:
            payload = json.load(file)

        if not isinstance(payload, dict):
            return {}

        cache = payload.get("cache") or {}

        if not isinstance(cache, dict):
            return {}

        return payload
    except Exception:
        return {}


def hydrate_model_readiness_from_file() -> None:
    """Isi session health cache dari persistent file jika session masih kosong."""
    if st.session_state.get("model_health_cache"):
        return

    payload = load_model_readiness_state_from_file()

    if not payload:
        return

    cache = payload.get("cache") or {}
    checked_at = float(payload.get("checked_at") or 0)

    if cache and checked_at:
        st.session_state.model_health_cache = cache
        st.session_state.model_health_checked_at = checked_at


def is_model_readiness_stale(
    checked_at_ts: float,
) -> bool:
    if not checked_at_ts:
        return True

    max_age = max(
        60,
        int(model_readiness_stale_seconds or 1800),
    )

    return (time.time() - float(checked_at_ts)) > max_age


def build_kb_v2_context_for_prompt(
    user_query: str,
    limit: int | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """Ambil konteks langsung dari kb_summaries_v2 + kb_chunks_v2."""
    if not bool(kb_v2_retrieval_enabled):
        return "", {
            "kb_v2_used": False,
            "kb_v2_sources": [],
            "kb_v2_error": "",
        }

    try:
        result = search_kb_v2_context(
            power_db_path,
            user_query,
            limit=int(limit or kb_v2_retrieval_limit or 5),
            include_archived=False,
        )

        context = str(result.get("context") or "").strip()
        context = limit_context_for_token_saver(
            context,
            kb_context_max_chars,
        )
        sources = (result.get("sources") or [])[: max(1, int(kb_max_chunks_token_saver or 3))]

        return context, {
            "kb_v2_used": bool(context),
            "kb_v2_sources": sources,
            "kb_v2_error": "",
        }
    except Exception as exc:
        return "", {
            "kb_v2_used": False,
            "kb_v2_sources": [],
            "kb_v2_error": str(exc)[:700],
        }



def get_model_readiness_state(
    route: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Status kesiapan model berdasarkan health check nyata.

    Ini mengganti status statis seperti "aktif dan siap membantu".
    """
    route = route or {}
    hydrate_model_readiness_from_file()
    health_cache = st.session_state.get("model_health_cache") or {}
    checked_at_ts = float(st.session_state.get("model_health_checked_at") or 0)
    checked_at = (
        _timestamp_to_wib_text(checked_at_ts)
        if checked_at_ts
        else "belum pernah dicek"
    )

    next_model = str(
        route.get("primary_model")
        or st.session_state.get("active_model")
        or default_model
        or ""
    ).strip()

    primary_health = health_cache.get(next_model) or {}
    primary_active = bool(primary_health.get("active"))

    active_models = [
        model_name
        for model_name, item in health_cache.items()
        if isinstance(item, dict) and item.get("active")
    ]

    transient_models = [
        model_name
        for model_name, item in health_cache.items()
        if isinstance(item, dict) and item.get("health_status") == "transient"
    ]

    cheap_count = len(route.get("active_cheap_models") or [])
    expensive_count = len(route.get("active_expensive_models") or [])
    active_total = len(active_models) or cheap_count + expensive_count

    if not api_key:
        return {
            "class": "offline",
            "label": "API belum siap",
            "kicker": "API key belum diisi",
            "subtitle": "Chat belum bisa digunakan karena SLASHAI_API_KEY belum tersedia.",
            "next_model": next_model,
            "checked_at": checked_at,
            "active_total": active_total,
            "primary_active": False,
        }

    if not checked_at_ts or not health_cache:
        return {
            "class": "checking",
            "label": "Perlu cek model",
            "kicker": "status model belum dicek",
            "subtitle": "Klik Cek model di admin agar status kesiapan model benar-benar terverifikasi.",
            "next_model": next_model,
            "checked_at": checked_at,
            "active_total": active_total,
            "primary_active": False,
        }

    if is_model_readiness_stale(checked_at_ts):
        return {
            "class": "checking",
            "label": "Perlu cek ulang",
            "kicker": "status model sudah basi",
            "subtitle": (
                f"Health check terakhir sudah lewat dari {int(model_readiness_stale_seconds or 1800)} detik. "
                "Klik Cek model di admin untuk memastikan model masih siap."
            ),
            "next_model": next_model,
            "checked_at": checked_at,
            "active_total": active_total,
            "primary_active": False,
            "stale": True,
        }

    if primary_active:
        latency = primary_health.get("latency_ms")
        latency_text = f" • {int(latency)} ms" if isinstance(latency, (int, float)) else ""
        return {
            "class": "ready",
            "label": "Model siap",
            "kicker": f"model aktif terverifikasi{latency_text}",
            "subtitle": f"Model utama {next_model} lolos health check terakhir dan siap menjawab.",
            "next_model": next_model,
            "checked_at": checked_at,
            "active_total": active_total,
            "primary_active": True,
        }

    if active_total > 0:
        replacement_result = apply_healthy_primary_model(
            selected_model=next_model,
            reason="readiness-fallback-promote",
        )
        promoted_model = str(replacement_result.get("model") or "").strip()
        fallback_model = promoted_model or (
            active_models[0] if active_models else (
                (route.get("active_cheap_models") or route.get("active_expensive_models") or [""])[0]
            )
        )

        if (
            bool(auto_replace_inactive_primary_model)
            and promoted_model
            and promoted_model != next_model
            and replacement_result.get("changed")
        ):
            return {
                "class": "ready",
                "label": "Model sehat dipilih",
                "kicker": "model sehat otomatis dipilih",
                "subtitle": f"Sistem memilih model sehat yang siap digunakan: {promoted_model}.",
                "next_model": promoted_model,
                "checked_at": checked_at,
                "active_total": active_total,
                "primary_active": True,
                "auto_primary_replaced": True,
                "replacement_from": next_model,
                "replacement_to": promoted_model,
            }

        return {
            "class": "ready",
            "label": "Model siap",
            "kicker": "model aktif tersedia",
            "subtitle": f"Sistem memakai model aktif yang tersedia: {fallback_model}.",
            "next_model": fallback_model,
            "checked_at": checked_at,
            "active_total": active_total,
            "primary_active": False,
        }

    if transient_models:
        return {
            "class": "warning",
            "label": "Gangguan sementara",
            "kicker": "model sedang tidak stabil",
            "subtitle": "Belum ada model aktif; sebagian model mengalami error sementara/transient. Coba cek ulang beberapa saat lagi.",
            "next_model": next_model,
            "checked_at": checked_at,
            "active_total": 0,
            "primary_active": False,
        }

    return {
        "class": "offline",
        "label": "Model belum siap",
        "kicker": "tidak ada model aktif",
        "subtitle": "Health check terakhir tidak menemukan model yang siap. Periksa API key, saldo, quota, atau daftar model.",
        "next_model": next_model,
        "checked_at": checked_at,
        "active_total": 0,
        "primary_active": False,
    }



def sanitize_model_readiness_text(
    value: Any,
) -> str:
    """Hilangkan wording teknis health check dari UI publik."""
    text = str(value or "")

    replacements = {
        "Model utama belum lolos health check": "Sistem memakai model aktif yang tersedia",
        "belum lolos health check": "belum siap digunakan",
        "model utama tidak aktif, fallback tersedia": "model aktif tersedia",
        "fallback tersedia": "model aktif tersedia",
        "Fallback siap": "Model siap",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


def build_public_model_status_html(
    route: Dict[str, Any], last_meta: Dict[str, Any] | None = None
) -> str:
    # Panel ringkas agar pengguna/admin langsung tahu status route model tanpa membuka debug.
    route = route or {}
    readiness = get_model_readiness_state(route)
    last_model = get_answer_model_name(last_meta, fallback="")
    next_model = str(readiness.get("next_model") or "").strip()
    fast_model = str(route.get("fastest_cheap_primary_model") or "").strip()
    capable_model = str(route.get("capable_primary_model") or "").strip()
    cheap_count = len(route.get("active_cheap_models") or [])
    expensive_count = len(route.get("active_expensive_models") or [])
    checked_at = str(readiness.get("checked_at") or "belum pernah dicek")
    status_class = str(readiness.get("class") or "checking")
    status_label = sanitize_model_readiness_text(
        readiness.get("label") or "Perlu cek model"
    )

    last_model_html = (
        f'<div class="model-status-pill">Jawaban terakhir: <strong>{_html_escape(last_model)}</strong></div>'
        if last_model
        else ""
    )
    fast_model_html = (
        f'<div class="model-status-pill">Model ringan tercepat: <strong>{_html_escape(fast_model)}</strong></div>'
        if fast_model
        else ""
    )
    capable_model_html = (
        f'<div class="model-status-pill">Model thinking: <strong>{_html_escape(capable_model)}</strong></div>'
        if capable_model
        else ""
    )

    return f"""
    <div class="model-status-panel easy-status-panel status-{_html_escape(status_class)}">
        <div class="model-status-title">Status AI saat ini: {_html_escape(status_label)}</div>
        <div class="model-status-grid">
            <div class="model-status-pill">Kesiapan: <strong>{_html_escape(status_label)}</strong></div>
            <div class="model-status-pill">Model berikutnya: <strong>{_html_escape(next_model)}</strong></div>
            {last_model_html}
            {fast_model_html}
            {capable_model_html}
            <div class="model-status-pill">Model murah aktif: <strong>{cheap_count}</strong></div>
            <div class="model-status-pill">Model capable aktif: <strong>{expensive_count}</strong></div>
            <div class="model-status-pill">Cek model: <strong>{_html_escape(checked_at)}</strong></div>
        </div>
    </div>
    """


def build_quick_help_html(is_admin: bool = False) -> str:
    admin_hint = (
        '<div class="quick-help-card"><strong>Admin</strong><span>Buka sidebar kiri untuk model, Telegram, Knowledge Base, biaya, dan benchmark.</span></div>'
        if is_admin
        else '<div class="quick-help-card"><strong>Admin</strong><span>Login lewat sidebar untuk mengatur model, upload KB, dan cek biaya.</span></div>'
    )
    return f"""
    <div class="quick-help-panel">
        <div class="quick-help-title">Mulai cepat</div>
        <div class="quick-help-grid">
            <div class="quick-help-card"><strong>Tanya biasa</strong><span>Ketik pertanyaan singkat; sistem otomatis memilih model cepat.</span></div>
            <div class="quick-help-card"><strong>Tugas berat</strong><span>Untuk kode, analisis, skripsi, dan dokumen, router memilih model capable.</span></div>
            <div class="quick-help-card"><strong>Knowledge Base</strong><span>Upload dokumen di admin agar jawaban bisa memakai sumber internal.</span></div>
            {admin_hint}
        </div>
    </div>
    """


# =========================
# macOS-style glass desktop-first styling
# =========================
st.markdown(
    """
    <style>
    :root {
        --mac-bg-1: #f4f7fb;
        --mac-bg-2: #e8eef7;
        --mac-bg-3: #fdf7f0;
        --mac-text: #111827;
        --mac-muted: #667085;
        --mac-border: rgba(17, 24, 39, 0.10);
        --mac-border-strong: rgba(17, 24, 39, 0.16);
        --mac-window: rgba(255, 255, 255, 0.72);
        --mac-window-strong: rgba(255, 255, 255, 0.86);
        --mac-panel: rgba(255, 255, 255, 0.58);
        --mac-panel-soft: rgba(255, 255, 255, 0.42);
        --mac-toolbar: rgba(246, 248, 251, 0.72);
        --mac-blue: #0a84ff;
        --mac-blue-soft: rgba(10, 132, 255, 0.12);
        --mac-green-soft: rgba(48, 209, 88, 0.12);
        --mac-orange-soft: rgba(255, 159, 10, 0.13);
        --mac-user: rgba(10, 132, 255, 0.14);
        --mac-assistant: rgba(255, 255, 255, 0.66);
        --mac-shadow: 0 30px 90px rgba(15, 23, 42, 0.16);
        --mac-shadow-soft: 0 14px 36px rgba(15, 23, 42, 0.09);
        --mac-blur: blur(24px) saturate(170%);
        --mac-radius-window: 28px;
        --mac-radius-card: 22px;
        --mac-radius-bubble: 18px;
    }

    @media (prefers-color-scheme: dark) {
        :root {
            --mac-bg-1: #090f1d;
            --mac-bg-2: #111827;
            --mac-bg-3: #182235;
            --mac-text: #f8fafc;
            --mac-muted: #cbd5e1;
            --mac-border: rgba(255, 255, 255, 0.11);
            --mac-border-strong: rgba(255, 255, 255, 0.18);
            --mac-window: rgba(15, 23, 42, 0.70);
            --mac-window-strong: rgba(15, 23, 42, 0.86);
            --mac-panel: rgba(30, 41, 59, 0.58);
            --mac-panel-soft: rgba(30, 41, 59, 0.38);
            --mac-toolbar: rgba(15, 23, 42, 0.78);
            --mac-blue: #7cc4ff;
            --mac-blue-soft: rgba(124, 196, 255, 0.14);
            --mac-green-soft: rgba(48, 209, 88, 0.12);
            --mac-orange-soft: rgba(255, 184, 77, 0.14);
            --mac-user: rgba(10, 132, 255, 0.28);
            --mac-assistant: rgba(15, 23, 42, 0.62);
            --mac-shadow: 0 34px 96px rgba(0, 0, 0, 0.46);
            --mac-shadow-soft: 0 15px 38px rgba(0, 0, 0, 0.28);
        }
    }

    html, body, .stApp {
        min-height: 100%;
        color: var(--mac-text) !important;
        background:
            radial-gradient(circle at 16% 10%, rgba(10,132,255,0.16), transparent 28%),
            radial-gradient(circle at 82% 4%, rgba(255,159,10,0.14), transparent 26%),
            radial-gradient(circle at 62% 92%, rgba(48,209,88,0.10), transparent 34%),
            linear-gradient(145deg, var(--mac-bg-1), var(--mac-bg-2) 52%, var(--mac-bg-3)) !important;
        -webkit-font-smoothing: antialiased;
        text-rendering: optimizeLegibility;
    }

    #MainMenu, footer {
        visibility: hidden;
    }


    .answer-pdf-link-wrap {
        margin-top: 0.25rem;
        margin-bottom: 0.85rem;
        line-height: 1.2;
    }

    .answer-pdf-link {
        display: inline-flex;
        align-items: center;
        width: fit-content;
        max-width: 100%;
        font-size: 0.78rem;
        font-weight: 500;
        color: var(--mac-blue) !important;
        text-decoration: none !important;
        border-bottom: 1px solid transparent;
        opacity: 0.86;
        transition: opacity 0.16s ease, border-color 0.16s ease;
    }

    .answer-pdf-link:hover {
        opacity: 1;
        border-bottom-color: currentColor;
    }

    header[data-testid="stHeader"] {
        background: transparent !important;
    }

    .stApp::before {
        content: "";
        position: fixed;
        inset: 0;
        pointer-events: none;
        z-index: 0;
        background:
            linear-gradient(rgba(255,255,255,0.16) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,0.16) 1px, transparent 1px);
        background-size: 64px 64px;
        mask-image: linear-gradient(to bottom, rgba(0,0,0,0.28), transparent 72%);
    }

    .main .block-container {
        position: relative;
        z-index: 1;
        width: min(calc(100vw - 72px), 1120px);
        max-width: 1120px;
        min-height: calc(100svh - 48px);
        margin: 24px auto 24px;
        padding: 0 1.45rem 14.5rem;
        border: 1px solid var(--mac-border-strong);
        border-radius: var(--mac-radius-window);
        background:
            linear-gradient(180deg, rgba(255,255,255,0.44), rgba(255,255,255,0.16)),
            var(--mac-window);
        box-shadow: var(--mac-shadow), inset 0 1px 0 rgba(255,255,255,0.64);
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
        overflow: visible;
    }

    @media (prefers-color-scheme: dark) {
        .main .block-container {
            background:
                linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0.02)),
                var(--mac-window);
            box-shadow: var(--mac-shadow), inset 0 1px 0 rgba(255,255,255,0.10);
        }
    }

    @media (min-width: 1280px) {
        .main .block-container {
            width: min(calc(100vw - 140px), 1180px);
            max-width: 1180px;
            padding-left: 1.75rem;
            padding-right: 1.75rem;
        }
    }

    @media (max-width: 760px) {
        .main .block-container {
            width: 100%;
            max-width: 100%;
            min-height: 100svh;
            margin: 0;
            padding: 0.78rem 0.86rem 13.2rem;
            border: 0;
            border-radius: 0;
            box-shadow: none;
            background: transparent;
        }
    }

    div[data-testid="stSidebar"] {
        min-width: min(90vw, 390px) !important;
        max-width: min(90vw, 430px) !important;
        background: var(--mac-window-strong) !important;
        border-right: 1px solid var(--mac-border-strong);
        box-shadow: var(--mac-shadow);
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
    }

    div[data-testid="stSidebar"] * {
        color: var(--mac-text) !important;
    }

    div[data-testid="stSidebar"] section,
    div[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {
        background: transparent !important;
    }

    .mac-windowbar {
        display: grid;
        grid-template-columns: 120px 1fr 120px;
        align-items: center;
        min-height: 48px;
        margin: 0 -1.45rem 16px;
        padding: 0 18px;
        border-bottom: 1px solid var(--mac-border);
        border-radius: var(--mac-radius-window) var(--mac-radius-window) 0 0;
        background: var(--mac-toolbar);
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
    }

    .mac-traffic {
        display: inline-flex;
        align-items: center;
        gap: 8px;
    }

    .mac-traffic span {
        width: 13px;
        height: 13px;
        border-radius: 999px;
        display: inline-block;
        box-shadow: inset 0 0 0 1px rgba(0,0,0,0.10), 0 1px 2px rgba(0,0,0,0.08);
    }

    .mac-close { background: #ff5f57; }
    .mac-min { background: #ffbd2e; }
    .mac-max { background: #28c840; }

    .mac-window-title {
        text-align: center;
        color: var(--mac-muted);
        font-size: 0.92rem;
        font-weight: 720;
        letter-spacing: -0.01em;
    }

    .mac-window-actions {
        justify-self: end;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 7px;
        min-width: 84px;
        padding: 6px 11px;
        border-radius: 999px;
        border: 1px solid var(--mac-border);
        background: var(--mac-panel-soft);
        color: var(--mac-muted);
        font-size: 0.82rem;
        font-weight: 700;
        overflow: hidden;
    }

    .mac-window-actions::before {
        content: "";
        width: 7px;
        height: 7px;
        border-radius: 999px;
        background: #28c840;
        box-shadow: 0 0 0 4px rgba(40, 200, 64, 0.12);
    }

    .mac-window-actions.online-status {
        position: relative;
        isolation: isolate;
        min-width: 92px;
        padding: 6px 10px;
        border-color: rgba(52, 199, 89, 0.30);
        background:
            radial-gradient(circle at 20% 30%, rgba(52, 199, 89, 0.22), transparent 34%),
            linear-gradient(135deg, var(--mac-panel), var(--mac-panel-soft));
        color: var(--mac-text);
        box-shadow:
            inset 0 1px 0 rgba(255, 255, 255, 0.26),
            0 8px 22px rgba(52, 199, 89, 0.10);
    }

    .mac-window-actions.online-status::before {
        display: none;
    }

    .online-status::after {
        content: "";
        position: absolute;
        inset: -40% -70%;
        z-index: -1;
        background: linear-gradient(
            90deg,
            transparent,
            rgba(52, 199, 89, 0.18),
            transparent
        );
        transform: translateX(-55%) rotate(12deg);
        animation: onlineSweep 2.8s ease-in-out infinite;
    }

    .online-dot {
        position: relative;
        width: 8px;
        height: 8px;
        flex: 0 0 8px;
        border-radius: 999px;
        background: #34c759;
        box-shadow:
            0 0 0 0 rgba(52, 199, 89, 0.38),
            0 0 12px rgba(52, 199, 89, 0.72);
        animation: onlinePulse 1.65s ease-in-out infinite;
    }

    .online-dot::after {
        content: "";
        position: absolute;
        inset: -6px;
        border-radius: inherit;
        border: 1px solid rgba(52, 199, 89, 0.30);
        animation: onlineRing 1.65s ease-out infinite;
    }

    .online-text {
        line-height: 1;
        font-weight: 820;
        letter-spacing: -0.01em;
        background: linear-gradient(
            90deg,
            var(--mac-text),
            #34c759,
            var(--mac-text)
        );
        background-size: 220% 100%;
        -webkit-background-clip: text;
        background-clip: text;
        color: transparent;
        animation: onlineTextShimmer 3.2s ease-in-out infinite;
    }

    .online-wave {
        display: inline-flex;
        align-items: flex-end;
        gap: 2px;
        height: 11px;
        flex: 0 0 auto;
    }

    .online-wave span {
        display: block;
        width: 2px;
        height: 4px;
        border-radius: 999px;
        background: #34c759;
        opacity: 0.58;
        animation: onlineBars 1.15s ease-in-out infinite;
    }

    .online-wave span:nth-child(2) {
        animation-delay: 0.16s;
    }

    .online-wave span:nth-child(3) {
        animation-delay: 0.32s;
    }

    @keyframes onlinePulse {
        0%, 100% {
            transform: scale(0.92);
            box-shadow:
                0 0 0 0 rgba(52, 199, 89, 0.36),
                0 0 10px rgba(52, 199, 89, 0.62);
        }
        50% {
            transform: scale(1.10);
            box-shadow:
                0 0 0 6px rgba(52, 199, 89, 0),
                0 0 17px rgba(52, 199, 89, 0.92);
        }
    }

    @keyframes onlineRing {
        0% {
            transform: scale(0.70);
            opacity: 0.78;
        }
        100% {
            transform: scale(1.45);
            opacity: 0;
        }
    }

    @keyframes onlineBars {
        0%, 100% {
            height: 4px;
            opacity: 0.45;
        }
        50% {
            height: 11px;
            opacity: 0.95;
        }
    }

    @keyframes onlineSweep {
        0%, 35% {
            transform: translateX(-60%) rotate(12deg);
            opacity: 0;
        }
        55% {
            opacity: 1;
        }
        100% {
            transform: translateX(60%) rotate(12deg);
            opacity: 0;
        }
    }

    @keyframes onlineTextShimmer {
        0%, 100% {
            background-position: 0% 50%;
        }
        50% {
            background-position: 100% 50%;
        }
    }

    @media (prefers-reduced-motion: reduce) {
        .online-status::after,
        .online-dot,
        .online-dot::after,
        .online-text,
        .online-wave span {
            animation: none !important;
        }
    }

    @media (min-width: 1280px) {
        .mac-windowbar {
            margin-left: -1.75rem;
            margin-right: -1.75rem;
        }
    }

    @media (max-width: 760px) {
        .mac-windowbar {
            grid-template-columns: auto 1fr auto;
            min-height: 40px;
            margin: 0 0 12px;
            padding: 0 2px;
            border: 0;
            border-radius: 0;
            background: transparent;
            backdrop-filter: none;
            -webkit-backdrop-filter: none;
        }

        .mac-traffic span {
            width: 10px;
            height: 10px;
        }

        .mac-window-title {
            font-size: 0.82rem;
        }

        .mac-window-actions {
            min-width: 72px;
            padding: 5px 8px;
            font-size: 0.72rem;
        }

        .mac-window-actions.online-status {
            min-width: 82px;
            gap: 5px;
            padding: 5px 7px;
        }

        .online-dot {
            width: 7px;
            height: 7px;
            flex-basis: 7px;
        }

        .online-wave {
            height: 9px;
            gap: 1.5px;
        }

        .online-wave span {
            width: 2px;
            height: 3px;
        }
    }

    .app-hero {
        position: relative;
        overflow: hidden;
        display: flex;
        align-items: center;
        gap: 16px;
        padding: 24px 24px;
        margin: 0 0 16px;
        border: 1px solid var(--mac-border);
        border-radius: 24px;
        background:
            radial-gradient(circle at 10% 0%, rgba(255,255,255,0.72), transparent 34%),
            radial-gradient(circle at 96% 12%, var(--mac-blue-soft), transparent 38%),
            linear-gradient(145deg, var(--mac-panel), var(--mac-panel-soft));
        box-shadow: var(--mac-shadow-soft);
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
    }

    .app-hero::after {
        content: "";
        position: absolute;
        width: 210px;
        height: 210px;
        right: -90px;
        top: -116px;
        border-radius: 999px;
        background: radial-gradient(circle, rgba(10,132,255,0.17), transparent 66%);
        pointer-events: none;
    }

    .app-logo {
        position: relative;
        z-index: 1;
        flex: 0 0 64px;
        width: 64px;
        height: 64px;
        display: grid;
        place-items: center;
        border-radius: 18px;
        background:
            linear-gradient(145deg, rgba(255,255,255,0.78), rgba(255,255,255,0.36));
        border: 1px solid var(--mac-border);
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.72), 0 14px 30px rgba(15,23,42,0.12);
        font-size: 1.8rem;
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
    }

    .app-title {
        position: relative;
        z-index: 1;
        margin: 0;
        color: var(--mac-text);
        font-size: clamp(1.9rem, 3vw, 2.85rem);
        line-height: 1.02;
        font-weight: 820;
        letter-spacing: -0.052em;
    }

    .app-subtitle {
        position: relative;
        z-index: 1;
        max-width: 720px;
        margin: 8px 0 0;
        color: var(--mac-muted);
        font-size: clamp(0.96rem, 1.4vw, 1.06rem);
        line-height: 1.55;
        letter-spacing: -0.008em;
    }

    .developer-credit {
        display: flex;
        justify-content: center;
        align-items: center;
        margin: 0 0 16px;
        color: var(--mac-muted);
        font-size: 0.84rem;
        font-weight: 720;
        letter-spacing: -0.01em;
    }

    .developer-credit span {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        padding: 7px 13px;
        border-radius: 999px;
        border: 1px solid var(--mac-border);
        background: var(--mac-panel-soft);
        box-shadow: 0 8px 20px rgba(15, 23, 42, 0.06);
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
    }

    .model-status-panel {
        margin: -4px 0 16px;
        padding: 13px 14px;
        border: 1px solid var(--mac-border);
        border-radius: 20px;
        background: linear-gradient(145deg, var(--mac-panel), var(--mac-panel-soft));
        box-shadow: var(--mac-shadow-soft);
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
    }

    .model-status-title {
        margin-bottom: 9px;
        color: var(--mac-text);
        font-size: 0.91rem;
        font-weight: 820;
        letter-spacing: -0.012em;
    }

    .model-status-grid {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
    }

    .model-status-pill {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 7px 10px;
        border-radius: 999px;
        border: 1px solid var(--mac-border);
        background: var(--mac-panel-soft);
        color: var(--mac-muted);
        font-size: 0.82rem;
        font-weight: 650;
        letter-spacing: -0.006em;
    }

    .model-status-pill strong {
        color: var(--mac-text);
        font-weight: 820;
    }

    @media (max-width: 760px) {
        .app-hero {
            gap: 12px;
            padding: 16px 15px;
            border-radius: 22px;
        }

        .app-logo {
            width: 52px;
            height: 52px;
            flex-basis: 52px;
            border-radius: 16px;
            font-size: 1.45rem;
        }

        .app-title {
            font-size: clamp(1.42rem, 6vw, 1.9rem);
        }

        .app-subtitle {
            font-size: 0.93rem;
            line-height: 1.45;
        }
    }

    .ios-chat-meta {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        min-height: 34px;
        padding: 8px 13px;
        border-radius: 999px;
        border: 1px solid var(--mac-border);
        background: var(--mac-panel);
        color: var(--mac-muted);
        box-shadow: var(--mac-shadow-soft);
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
        font-size: 0.86rem;
        font-weight: 660;
    }

    .simple-note,
    .stCaptionContainer,
    div[data-testid="stCaptionContainer"] {
        color: var(--mac-muted) !important;
        font-size: 0.86rem;
    }

    div[data-testid="stChatMessage"] {
        border: 1px solid var(--mac-border);
        border-radius: var(--mac-radius-bubble);
        padding: 0.72rem 0.86rem;
        margin-bottom: 0.82rem;
        background: var(--mac-assistant);
        color: var(--mac-text) !important;
        box-shadow: 0 8px 22px rgba(15, 23, 42, 0.06);
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
    }

    div[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        background: var(--mac-user);
        border-color: rgba(10, 132, 255, 0.18);
    }

    @media (min-width: 900px) {
        div[data-testid="stChatMessage"] {
            max-width: 86%;
            border-radius: 20px;
            padding: 0.82rem 0.96rem;
        }

        div[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
            margin-left: auto;
        }
    }

    div[data-testid="stChatMessage"] * {
        color: var(--mac-text) !important;
    }


    div[data-testid="stChatMessage"]:last-of-type {
        scroll-margin-bottom: 240px;
    }

    @media (max-width: 760px) {
        div[data-testid="stChatMessage"]:last-of-type {
            scroll-margin-bottom: 210px;
        }
    }

    div[data-testid="stChatMessageAvatarUser"],
    div[data-testid="stChatMessageAvatarAssistant"] {
        width: 34px !important;
        height: 34px !important;
        background: var(--mac-panel) !important;
        border: 1px solid var(--mac-border) !important;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.40);
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
    }

    div[data-testid="stMarkdownContainer"] p,
    div[data-testid="stMarkdownContainer"] li {
        line-height: 1.64;
        font-size: clamp(0.97rem, 3.65vw, 1.03rem);
        letter-spacing: -0.003em;
    }

    @media (min-width: 900px) {
        div[data-testid="stMarkdownContainer"] p,
        div[data-testid="stMarkdownContainer"] li {
            font-size: 1.02rem;
        }
    }

    /* =========================
       Code block readability
       ========================= */
    pre,
    div[data-testid="stMarkdownContainer"] pre,
    div[data-testid="stCodeBlock"] pre {
        position: relative !important;
        max-width: 100% !important;
        overflow-x: hidden !important;
        overflow-y: auto !important;
        padding: 1rem 1.08rem !important;
        margin: 0.86rem 0 !important;
        border-radius: 18px !important;
        border: 1px solid rgba(148, 163, 184, 0.24) !important;
        background:
            linear-gradient(180deg, rgba(15, 23, 42, 0.96), rgba(2, 6, 23, 0.96)) !important;
        box-shadow:
            inset 0 1px 0 rgba(255,255,255,0.08),
            0 16px 34px rgba(15, 23, 42, 0.18) !important;
        backdrop-filter: blur(18px) saturate(160%) !important;
        -webkit-backdrop-filter: blur(18px) saturate(160%) !important;
        scrollbar-width: thin;
        scrollbar-color: rgba(148, 163, 184, 0.50) transparent;
    }

    pre::before,
    div[data-testid="stMarkdownContainer"] pre::before,
    div[data-testid="stCodeBlock"] pre::before {
        content: "CODE";
        display: block;
        width: fit-content;
        margin: -0.2rem 0 0.72rem;
        padding: 0.22rem 0.52rem;
        border-radius: 999px;
        border: 1px solid rgba(148, 163, 184, 0.24);
        background: rgba(255,255,255,0.06);
        color: rgba(226, 232, 240, 0.72) !important;
        font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-size: 0.68rem;
        font-weight: 800;
        letter-spacing: 0.08em;
    }

    pre::-webkit-scrollbar,
    div[data-testid="stMarkdownContainer"] pre::-webkit-scrollbar,
    div[data-testid="stCodeBlock"] pre::-webkit-scrollbar {
        height: 10px;
    }

    pre::-webkit-scrollbar-track,
    div[data-testid="stMarkdownContainer"] pre::-webkit-scrollbar-track,
    div[data-testid="stCodeBlock"] pre::-webkit-scrollbar-track {
        background: transparent;
    }

    pre::-webkit-scrollbar-thumb,
    div[data-testid="stMarkdownContainer"] pre::-webkit-scrollbar-thumb,
    div[data-testid="stCodeBlock"] pre::-webkit-scrollbar-thumb {
        border-radius: 999px;
        background: rgba(148, 163, 184, 0.46);
        border: 3px solid rgba(2, 6, 23, 0.96);
    }

    pre code,
    div[data-testid="stMarkdownContainer"] pre code,
    div[data-testid="stCodeBlock"] pre code {
        display: block !important;
        min-width: 0 !important;
        max-width: 100% !important;
        padding: 0 !important;
        margin: 0 !important;
        border: 0 !important;
        border-radius: 0 !important;
        background: transparent !important;
        color: #e5e7eb !important;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace !important;
        font-size: clamp(0.78rem, 2.65vw, 0.92rem) !important;
        line-height: 1.68 !important;
        white-space: pre-wrap !important;
        word-break: break-word !important;
        overflow-wrap: anywhere !important;
        tab-size: 4;
    }

    div[data-testid="stMarkdownContainer"],
    div[data-testid="stMarkdownContainer"] p,
    div[data-testid="stMarkdownContainer"] li,
    div[data-testid="stMarkdownContainer"] span {
        max-width: 100% !important;
        overflow-wrap: break-word !important;
        word-break: break-word !important;
    }

    div[data-testid="stMarkdownContainer"] pre span,
    div[data-testid="stCodeBlock"] pre span,
    div[data-testid="stChatMessage"] pre span {
        white-space: pre-wrap !important;
        overflow-wrap: anywhere !important;
        word-break: break-word !important;
    }

    div[data-testid="stMarkdownContainer"] :not(pre) > code,
    div[data-testid="stMarkdownContainer"] p code,
    div[data-testid="stMarkdownContainer"] li code {
        display: inline-block !important;
        max-width: 100% !important;
        padding: 0.12rem 0.42rem !important;
        border-radius: 8px !important;
        border: 1px solid rgba(148, 163, 184, 0.28) !important;
        background: rgba(15, 23, 42, 0.08) !important;
        color: var(--mac-text) !important;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace !important;
        font-size: 0.92em !important;
        line-height: 1.45 !important;
        white-space: normal !important;
        word-break: break-word !important;
    }

    @media (prefers-color-scheme: dark) {
        div[data-testid="stMarkdownContainer"] :not(pre) > code,
        div[data-testid="stMarkdownContainer"] p code,
        div[data-testid="stMarkdownContainer"] li code {
            background: rgba(255, 255, 255, 0.09) !important;
            border-color: rgba(255, 255, 255, 0.16) !important;
            color: #f8fafc !important;
        }
    }

    @media (max-width: 760px) {
        pre,
        div[data-testid="stMarkdownContainer"] pre,
        div[data-testid="stCodeBlock"] pre {
            margin-left: -0.1rem !important;
            margin-right: -0.1rem !important;
            padding: 0.86rem 0.88rem !important;
            border-radius: 16px !important;
        }

        pre code,
        div[data-testid="stMarkdownContainer"] pre code,
        div[data-testid="stCodeBlock"] pre code {
            font-size: 0.78rem !important;
            line-height: 1.62 !important;
            white-space: pre-wrap !important;
            word-break: break-word !important;
            overflow-wrap: anywhere !important;
        }
    }

    div[data-testid="stDataFrame"],
    div[data-testid="stTable"] {
        max-width: 100% !important;
        overflow-x: auto !important;
    }

    textarea,
    input,
    div[data-baseweb="input"] input,
    div[data-baseweb="textarea"] textarea {
        background: var(--mac-panel) !important;
        color: var(--mac-text) !important;
        border-color: var(--mac-border) !important;
        font-size: 16px !important;
        border-radius: 15px !important;
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
    }

    .chat-input-safe-space {
        height: 220px;
        width: 100%;
        flex-shrink: 0;
        pointer-events: none;
    }

    @media (max-width: 760px) {
        .chat-input-safe-space {
            height: 190px;
        }
    }

    div[data-testid="stChatInput"] {
        position: fixed !important;
        left: 50%;
        right: auto;
        bottom: 12px;
        transform: translateX(-50%);
        width: min(1040px, calc(100vw - 92px));
        max-height: min(28svh, 190px);
        border: 1px solid rgba(255, 255, 255, 0.34);
        border-radius: 22px;
        padding: 0.5rem 0.55rem max(0.5rem, env(safe-area-inset-bottom));
        background: linear-gradient(180deg, rgba(255,255,255,0.20), rgba(255,255,255,0.12)) !important;
        box-shadow: 0 22px 54px rgba(15, 23, 42, 0.18), inset 0 1px 0 rgba(255,255,255,0.45);
        backdrop-filter: blur(24px) saturate(180%);
        -webkit-backdrop-filter: blur(24px) saturate(180%);
        z-index: 999;
    }

    div[data-testid="stChatInput"] > div,
    div[data-testid="stChatInput"] [data-baseweb="textarea"],
    div[data-testid="stChatInput"] [data-baseweb="base-input"],
    div[data-testid="stChatInput"] [data-baseweb="textarea"] > div {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
    }

    div[data-testid="stChatInput"] textarea {
        min-height: 48px !important;
        border-radius: 16px !important;
        border: 1px solid rgba(255, 255, 255, 0.26) !important;
        background: rgba(255, 255, 255, 0.10) !important;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.22);
        backdrop-filter: blur(16px) saturate(170%);
        -webkit-backdrop-filter: blur(16px) saturate(170%);
    }

    div[data-testid="stChatInput"] textarea::placeholder {
        color: rgba(30, 41, 59, 0.72) !important;
    }

    @media (min-width: 1280px) {
        div[data-testid="stChatInput"] {
            width: min(1090px, calc(100vw - 160px));
        }
    }

    @media (max-width: 760px) {
        div[data-testid="stChatInput"] {
            bottom: 8px;
            width: calc(100vw - 20px);
            border-radius: 21px;
        }
    }

    button[kind="primary"],
    div[data-testid="stFormSubmitButton"] button,
    div[data-testid="stButton"] button,
    div[data-testid="stDownloadButton"] button {
        min-height: 42px;
        border-radius: 13px !important;
        border: 1px solid var(--mac-border) !important;
        background: linear-gradient(180deg, var(--mac-window-strong), var(--mac-panel)) !important;
        color: var(--mac-text) !important;
        font-weight: 700 !important;
        letter-spacing: -0.01em;
        text-align: center !important;
        box-shadow: 0 8px 20px rgba(15, 23, 42, 0.07) !important;
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
        transition: transform 150ms ease, box-shadow 150ms ease, border-color 150ms ease;
    }

    button[kind="primary"]:hover,
    div[data-testid="stFormSubmitButton"] button:hover,
    div[data-testid="stButton"] button:hover,
    div[data-testid="stDownloadButton"] button:hover {
        transform: translateY(-1px);
        border-color: rgba(10, 132, 255, 0.28) !important;
        box-shadow: 0 14px 30px rgba(15, 23, 42, 0.12) !important;
    }

    div[data-testid="stAlert"],
    .stAlert {
        border-radius: 18px !important;
        border: 1px solid var(--mac-border) !important;
        box-shadow: var(--mac-shadow-soft);
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
    }

    div[data-baseweb="select"] > div,
    div[data-baseweb="textarea"] > div,
    div[data-baseweb="input"] > div {
        border-radius: 14px !important;
        border-color: var(--mac-border) !important;
        background: var(--mac-panel-soft) !important;
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 7px;
    }

    .stTabs [data-baseweb="tab"] {
        min-height: 38px;
        border-radius: 12px;
        background: var(--mac-panel-soft);
        border: 1px solid var(--mac-border);
        color: var(--mac-text) !important;
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
    }


    @media (prefers-color-scheme: dark) {
        div[data-testid="stChatInput"] {
            border: 1px solid rgba(255, 255, 255, 0.16);
            background: linear-gradient(180deg, rgba(17,24,39,0.42), rgba(17,24,39,0.30)) !important;
            box-shadow: 0 22px 54px rgba(0, 0, 0, 0.30), inset 0 1px 0 rgba(255,255,255,0.10);
        }

        div[data-testid="stChatInput"] textarea {
            background: rgba(255, 255, 255, 0.06) !important;
            border: 1px solid rgba(255, 255, 255, 0.14) !important;
        }

        div[data-testid="stChatInput"] textarea::placeholder {
            color: rgba(226, 232, 240, 0.70) !important;
        }
    }

    hr {
        border-color: transparent !important;
        margin: 0.7rem 0 !important;
    }

    @media (max-width: 760px) {
        div[data-testid="column"] {
            width: 100% !important;
            flex: 1 1 100% !important;
            min-width: 100% !important;
        }

        div[data-testid="stButton"] button {
            width: 100% !important;
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: 5px;
            overflow-x: auto;
            white-space: nowrap;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# =========================
# Extra UX polish: easier navigation and admin panels
# =========================
st.markdown(
    """
    <style>
    .easy-status-panel {
        margin-top: 0;
    }

    .quick-help-panel {
        margin: -4px 0 18px;
        padding: 15px;
        border: 1px solid var(--mac-border);
        border-radius: 22px;
        background: linear-gradient(145deg, var(--mac-panel), var(--mac-panel-soft));
        box-shadow: var(--mac-shadow-soft);
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
    }

    .quick-help-title {
        margin-bottom: 10px;
        color: var(--mac-text);
        font-size: 0.94rem;
        font-weight: 840;
        letter-spacing: -0.012em;
    }

    .quick-help-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 10px;
    }

    .quick-help-card {
        min-height: 74px;
        padding: 12px;
        border: 1px solid var(--mac-border);
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.34);
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.38);
    }

    .quick-help-card strong {
        display: block;
        margin-bottom: 5px;
        color: var(--mac-text);
        font-size: 0.86rem;
        font-weight: 820;
    }

    .quick-help-card span {
        display: block;
        color: var(--mac-muted);
        font-size: 0.79rem;
        line-height: 1.35;
        font-weight: 610;
    }

    .easy-admin-panel {
        padding: 0.75rem 0.9rem 0.95rem;
        border: 1px solid var(--mac-border);
        border-radius: 20px;
        background: linear-gradient(145deg, var(--mac-panel), var(--mac-panel-soft));
        box-shadow: var(--mac-shadow-soft);
        margin-bottom: 1rem;
    }

    .easy-admin-panel h4 {
        margin: 0 0 0.15rem !important;
        color: var(--mac-text) !important;
    }

    .easy-admin-panel p {
        margin: 0 !important;
        color: var(--mac-muted) !important;
        font-size: 0.86rem;
        line-height: 1.45;
    }

    div[data-testid="stSidebar"] div[data-testid="stMetric"] {
        border: 1px solid var(--mac-border);
        border-radius: 16px;
        padding: 0.75rem;
        background: var(--mac-panel-soft);
    }

    @media (max-width: 900px) {
        .quick-help-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }

    @media (max-width: 560px) {
        .quick-help-grid {
            grid-template-columns: 1fr;
        }
        .quick-help-card {
            min-height: auto;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================
# Accessible light/dark visual tuning
# =========================
st.markdown(
    """
    <style>
    /*
      Final visual layer.
      Tujuan: membuat tampilan tetap terbaca pada mode Light dan Dark,
      sekaligus merapikan glass theme agar tidak terlalu transparan.
    */
    :root {
        color-scheme: light dark;

        --ui-bg-1: #f8fafc;
        --ui-bg-2: #eef4ff;
        --ui-bg-3: #fff7ed;
        --ui-surface: rgba(255, 255, 255, 0.88);
        --ui-surface-soft: rgba(255, 255, 255, 0.72);
        --ui-surface-solid: #ffffff;
        --ui-text: #0f172a;
        --ui-text-strong: #020617;
        --ui-muted: #475569;
        --ui-muted-2: #64748b;
        --ui-border: rgba(15, 23, 42, 0.14);
        --ui-border-strong: rgba(15, 23, 42, 0.22);
        --ui-accent: #2563eb;
        --ui-accent-strong: #1d4ed8;
        --ui-accent-soft: rgba(37, 99, 235, 0.12);
        --ui-user-bg: #dbeafe;
        --ui-user-text: #0f172a;
        --ui-assistant-bg: rgba(255, 255, 255, 0.92);
        --ui-assistant-text: #0f172a;
        --ui-input-bg: rgba(255, 255, 255, 0.96);
        --ui-input-text: #0f172a;
        --ui-code-bg: #0f172a;
        --ui-code-text: #e5e7eb;
        --ui-shadow: 0 18px 55px rgba(15, 23, 42, 0.16);
        --ui-shadow-soft: 0 10px 30px rgba(15, 23, 42, 0.10);

        --mac-bg-1: var(--ui-bg-1);
        --mac-bg-2: var(--ui-bg-2);
        --mac-bg-3: var(--ui-bg-3);
        --mac-text: var(--ui-text);
        --mac-muted: var(--ui-muted);
        --mac-border: var(--ui-border);
        --mac-border-strong: var(--ui-border-strong);
        --mac-window: var(--ui-surface);
        --mac-window-strong: var(--ui-surface-solid);
        --mac-panel: var(--ui-surface);
        --mac-panel-soft: var(--ui-surface-soft);
        --mac-user: var(--ui-user-bg);
        --mac-assistant: var(--ui-assistant-bg);
        --mac-shadow: var(--ui-shadow);
        --mac-shadow-soft: var(--ui-shadow-soft);
    }

    html[data-theme="dark"],
    body[data-theme="dark"],
    .stApp[data-theme="dark"],
    [data-theme="dark"] {
        --ui-bg-1: #020617;
        --ui-bg-2: #0f172a;
        --ui-bg-3: #111827;
        --ui-surface: rgba(15, 23, 42, 0.92);
        --ui-surface-soft: rgba(30, 41, 59, 0.78);
        --ui-surface-solid: #111827;
        --ui-text: #f8fafc;
        --ui-text-strong: #ffffff;
        --ui-muted: #cbd5e1;
        --ui-muted-2: #94a3b8;
        --ui-border: rgba(226, 232, 240, 0.16);
        --ui-border-strong: rgba(226, 232, 240, 0.26);
        --ui-accent: #60a5fa;
        --ui-accent-strong: #93c5fd;
        --ui-accent-soft: rgba(96, 165, 250, 0.18);
        --ui-user-bg: rgba(37, 99, 235, 0.46);
        --ui-user-text: #f8fafc;
        --ui-assistant-bg: rgba(30, 41, 59, 0.94);
        --ui-assistant-text: #f8fafc;
        --ui-input-bg: rgba(15, 23, 42, 0.96);
        --ui-input-text: #f8fafc;
        --ui-code-bg: #020617;
        --ui-code-text: #e2e8f0;
        --ui-shadow: 0 24px 80px rgba(0, 0, 0, 0.48);
        --ui-shadow-soft: 0 13px 36px rgba(0, 0, 0, 0.34);
    }

    @media (prefers-color-scheme: dark) {
        :root {
            --ui-bg-1: #020617;
            --ui-bg-2: #0f172a;
            --ui-bg-3: #111827;
            --ui-surface: rgba(15, 23, 42, 0.92);
            --ui-surface-soft: rgba(30, 41, 59, 0.78);
            --ui-surface-solid: #111827;
            --ui-text: #f8fafc;
            --ui-text-strong: #ffffff;
            --ui-muted: #cbd5e1;
            --ui-muted-2: #94a3b8;
            --ui-border: rgba(226, 232, 240, 0.16);
            --ui-border-strong: rgba(226, 232, 240, 0.26);
            --ui-accent: #60a5fa;
            --ui-accent-strong: #93c5fd;
            --ui-accent-soft: rgba(96, 165, 250, 0.18);
            --ui-user-bg: rgba(37, 99, 235, 0.46);
            --ui-user-text: #f8fafc;
            --ui-assistant-bg: rgba(30, 41, 59, 0.94);
            --ui-assistant-text: #f8fafc;
            --ui-input-bg: rgba(15, 23, 42, 0.96);
            --ui-input-text: #f8fafc;
            --ui-code-bg: #020617;
            --ui-code-text: #e2e8f0;
            --ui-shadow: 0 24px 80px rgba(0, 0, 0, 0.48);
            --ui-shadow-soft: 0 13px 36px rgba(0, 0, 0, 0.34);

            --mac-bg-1: var(--ui-bg-1);
            --mac-bg-2: var(--ui-bg-2);
            --mac-bg-3: var(--ui-bg-3);
            --mac-text: var(--ui-text);
            --mac-muted: var(--ui-muted);
            --mac-border: var(--ui-border);
            --mac-border-strong: var(--ui-border-strong);
            --mac-window: var(--ui-surface);
            --mac-window-strong: var(--ui-surface-solid);
            --mac-panel: var(--ui-surface);
            --mac-panel-soft: var(--ui-surface-soft);
            --mac-user: var(--ui-user-bg);
            --mac-assistant: var(--ui-assistant-bg);
            --mac-shadow: var(--ui-shadow);
            --mac-shadow-soft: var(--ui-shadow-soft);
        }
    }

    html,
    body,
    .stApp,
    .main,
    div[data-testid="stAppViewContainer"],
    div[data-testid="stMain"] {
        color: var(--ui-text) !important;
        background:
            radial-gradient(circle at 12% 8%, var(--ui-accent-soft), transparent 28%),
            radial-gradient(circle at 88% 4%, rgba(249, 115, 22, 0.10), transparent 28%),
            linear-gradient(145deg, var(--ui-bg-1), var(--ui-bg-2) 54%, var(--ui-bg-3)) !important;
    }

    .main .block-container {
        border: 1px solid var(--ui-border-strong) !important;
        background: linear-gradient(180deg, var(--ui-surface), var(--ui-surface-soft)) !important;
        box-shadow: var(--ui-shadow) !important;
    }

    /* Global text contrast */
    .stMarkdown,
    .stMarkdown p,
    .stMarkdown li,
    .stMarkdown span,
    .stMarkdown div,
    label,
    .stText,
    div[data-testid="stMarkdownContainer"],
    div[data-testid="stCaptionContainer"],
    .stCaptionContainer,
    .app-subtitle,
    .developer-credit,
    .ios-chat-meta,
    .quick-help-card span,
    .easy-admin-panel p {
        color: var(--ui-text) !important;
    }

    .app-subtitle,
    .developer-credit,
    .ios-chat-meta,
    .quick-help-card span,
    .easy-admin-panel p,
    .simple-note,
    div[data-testid="stCaptionContainer"] {
        color: var(--ui-muted) !important;
    }

    h1, h2, h3, h4, h5, h6,
    .app-title,
    .quick-help-title,
    .model-status-title,
    .easy-admin-panel h4,
    .quick-help-card strong {
        color: var(--ui-text-strong) !important;
        letter-spacing: -0.018em;
    }

    /* Main cards */
    .mac-windowbar,
    .app-hero,
    .model-status-panel,
    .quick-help-panel,
    .easy-admin-panel,
    .ios-chat-meta,
    .developer-credit span,
    div[data-testid="stMetric"],
    div[data-testid="stExpander"] details,
    div[data-testid="stSidebar"] div[data-testid="stMetric"] {
        border: 1px solid var(--ui-border) !important;
        background: linear-gradient(145deg, var(--ui-surface), var(--ui-surface-soft)) !important;
        box-shadow: var(--ui-shadow-soft) !important;
        color: var(--ui-text) !important;
    }

    .app-hero {
        align-items: flex-start !important;
        padding: 22px !important;
    }

    .app-logo {
        background: linear-gradient(145deg, var(--ui-surface-solid), var(--ui-surface-soft)) !important;
        border: 1px solid var(--ui-border) !important;
        color: var(--ui-text-strong) !important;
    }

    .app-title {
        margin-bottom: 0.25rem !important;
        font-size: clamp(1.25rem, 2vw, 1.75rem) !important;
    }

    .app-subtitle {
        max-width: 820px !important;
        line-height: 1.58 !important;
        font-size: 0.98rem !important;
    }

    .quick-help-card,
    .model-status-pill {
        border: 1px solid var(--ui-border) !important;
        background: var(--ui-surface-soft) !important;
        color: var(--ui-text) !important;
    }

    /* Chat bubbles */
    div[data-testid="stChatMessage"] {
        border: 1px solid var(--ui-border) !important;
        background: var(--ui-assistant-bg) !important;
        color: var(--ui-assistant-text) !important;
        box-shadow: var(--ui-shadow-soft) !important;
    }

    div[data-testid="stChatMessage"] * {
        color: inherit !important;
    }

    div[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        background: var(--ui-user-bg) !important;
        color: var(--ui-user-text) !important;
        border-color: color-mix(in srgb, var(--ui-accent) 38%, transparent) !important;
    }

    div[data-testid="stChatMessage"] a {
        color: var(--ui-accent-strong) !important;
        font-weight: 700 !important;
        text-decoration: underline;
        text-underline-offset: 3px;
    }

    div[data-testid="stChatMessage"] pre,
    div[data-testid="stChatMessage"] code,
    pre,
    code {
        background: var(--ui-code-bg) !important;
        color: var(--ui-code-text) !important;
        border-color: var(--ui-border) !important;
    }

    div[data-testid="stChatMessage"] pre,
    div[data-testid="stMarkdownContainer"] pre,
    div[data-testid="stCodeBlock"] pre {
        overflow-x: hidden !important;
        max-width: 100% !important;
        white-space: pre-wrap !important;
    }

    div[data-testid="stChatMessage"] pre code,
    div[data-testid="stMarkdownContainer"] pre code,
    div[data-testid="stCodeBlock"] pre code {
        min-width: 0 !important;
        max-width: 100% !important;
        white-space: pre-wrap !important;
        overflow-wrap: anywhere !important;
        word-break: break-word !important;
    }



    div[data-testid="stChatMessage"] table,
    div[data-testid="stTable"] table {
        color: var(--ui-text) !important;
        background: var(--ui-surface-solid) !important;
        border-color: var(--ui-border) !important;
    }

    div[data-testid="stChatMessage"] th,
    div[data-testid="stChatMessage"] td {
        border-color: var(--ui-border) !important;
    }

    /* Fixed input area: readable, not overly transparent */
    div[data-testid="stChatInput"] {
        border: 1px solid var(--ui-border-strong) !important;
        background: linear-gradient(180deg, var(--ui-surface), var(--ui-surface-soft)) !important;
        box-shadow: var(--ui-shadow) !important;
    }

    div[data-testid="stChatInput"] textarea,
    textarea,
    input,
    div[data-baseweb="textarea"] textarea,
    div[data-baseweb="input"] input {
        background: var(--ui-input-bg) !important;
        color: var(--ui-input-text) !important;
        border: 1px solid var(--ui-border) !important;
        caret-color: var(--ui-accent) !important;
        box-shadow: none !important;
    }

    div[data-testid="stChatInput"] textarea::placeholder,
    textarea::placeholder,
    input::placeholder {
        color: var(--ui-muted-2) !important;
        opacity: 1 !important;
    }

    div[data-testid="stChatInput"] button {
        border-radius: 14px !important;
        background: var(--ui-accent) !important;
        color: #ffffff !important;
        border: 1px solid color-mix(in srgb, var(--ui-accent) 70%, transparent) !important;
    }

    /* Buttons, selectors, tabs */
    .stButton > button,
    .stDownloadButton > button,
    button[kind],
    div[data-testid="baseButton-secondary"] button {
        min-height: 42px !important;
        border-radius: 14px !important;
        border: 1px solid var(--ui-border) !important;
        background: var(--ui-surface-solid) !important;
        color: var(--ui-text-strong) !important;
        box-shadow: 0 5px 16px rgba(15, 23, 42, 0.08) !important;
        font-weight: 750 !important;
    }

    .stButton > button:hover,
    .stDownloadButton > button:hover,
    button[kind]:hover {
        border-color: var(--ui-accent) !important;
        color: var(--ui-accent-strong) !important;
        transform: translateY(-1px);
    }

    div[data-baseweb="select"] > div,
    div[data-baseweb="textarea"] > div,
    div[data-baseweb="input"] > div,
    div[data-baseweb="slider"] {
        background: var(--ui-input-bg) !important;
        color: var(--ui-input-text) !important;
        border-color: var(--ui-border) !important;
    }

    .stTabs [data-baseweb="tab"] {
        background: var(--ui-surface-soft) !important;
        border: 1px solid var(--ui-border) !important;
        color: var(--ui-text) !important;
        font-weight: 760 !important;
    }

    .stTabs [aria-selected="true"] {
        background: var(--ui-accent-soft) !important;
        color: var(--ui-accent-strong) !important;
        border-color: color-mix(in srgb, var(--ui-accent) 38%, transparent) !important;
    }

    /* Alerts and status boxes */
    div[data-testid="stAlert"],
    .stAlert {
        border-radius: 16px !important;
        border: 1px solid var(--ui-border) !important;
        background: var(--ui-surface-solid) !important;
        color: var(--ui-text) !important;
    }

    div[data-testid="stAlert"] * {
        color: var(--ui-text) !important;
    }

    /* Sidebar */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, var(--ui-surface), var(--ui-surface-soft)) !important;
        border-right: 1px solid var(--ui-border) !important;
    }

    section[data-testid="stSidebar"] * {
        color: var(--ui-text) !important;
    }

    section[data-testid="stSidebar"] .stCaptionContainer,
    section[data-testid="stSidebar"] small,
    section[data-testid="stSidebar"] p {
        color: var(--ui-muted) !important;
    }

    /* Dataframes and expanders */
    div[data-testid="stDataFrame"],
    div[data-testid="stTable"],
    div[data-testid="stJson"] {
        border-radius: 16px !important;
        overflow: hidden !important;
        border: 1px solid var(--ui-border) !important;
        background: var(--ui-surface-solid) !important;
    }

    div[data-testid="stExpander"] details summary {
        color: var(--ui-text-strong) !important;
        font-weight: 760 !important;
    }

    /* Mobile layout */
    @media (max-width: 760px) {
        .main .block-container {
            width: calc(100vw - 18px) !important;
            margin: 9px auto 12px !important;
            border-radius: 22px !important;
            padding-left: 0.95rem !important;
            padding-right: 0.95rem !important;
            padding-bottom: 13rem !important;
        }

        .app-hero {
            flex-direction: column !important;
            gap: 12px !important;
            padding: 18px !important;
            border-radius: 20px !important;
        }

        .app-logo {
            width: 54px !important;
            height: 54px !important;
            flex-basis: 54px !important;
        }

        div[data-testid="stChatInput"] {
            width: calc(100vw - 22px) !important;
            bottom: 8px !important;
            border-radius: 20px !important;
        }

        div[data-testid="stChatMessage"] {
            max-width: 100% !important;
            border-radius: 18px !important;
        }
    }

    @supports not (backdrop-filter: blur(12px)) {
        .main .block-container,
        .mac-windowbar,
        .app-hero,
        .model-status-panel,
        .quick-help-panel,
        .easy-admin-panel,
        div[data-testid="stChatInput"] {
            background: var(--ui-surface-solid) !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================
# White-label cleanup: hide Streamlit chrome/branding
# =========================
st.markdown(
    """
    <style>
    /*
      Bersihkan elemen bawaan Streamlit yang biasanya masih terlihat:
      header, toolbar kanan atas, tombol deploy, menu hamburger, footer,
      status widget, decoration bar, dan anchor otomatis pada heading.
      Catatan: CSS ini hanya membersihkan tampilan dalam app. Domain streamlit.app
      tetap hanya bisa disamarkan dengan custom subdomain/domain wrapper.
    */
    #MainMenu,
    footer,
    header,
    div[data-testid="stHeader"],
    div[data-testid="stToolbar"],
    div[data-testid="toolbar"],
    div[data-testid="stDecoration"],
    div[data-testid="stStatusWidget"],
    div[data-testid="stAppDeployButton"],
    div[data-testid="stDeployButton"],
    .stDeployButton,
    .viewerBadge_container__1QSob,
    .viewerBadge_link__1S137,
    .viewerBadge_text__1JaDK,
    a[href="https://streamlit.io"],
    a[href^="https://streamlit.io"],
    a[href^="https://www.streamlit.io"],
    button[title="View fullscreen"],
    button[title="Deploy"],
    button[aria-label="Deploy"],
    button[aria-label="Main menu"],
    button[aria-label="Open menu"],
    [aria-label="Deploy"],
    [aria-label="Main menu"],
    [data-testid="StyledFullScreenButton"] {
        display: none !important;
        visibility: hidden !important;
        opacity: 0 !important;
        pointer-events: none !important;
        height: 0 !important;
        min-height: 0 !important;
        max-height: 0 !important;
        width: 0 !important;
        min-width: 0 !important;
        max-width: 0 !important;
        overflow: hidden !important;
    }

    /* Hilangkan ruang kosong bekas header Streamlit. */
    div[data-testid="stAppViewContainer"] > .main,
    div[data-testid="stMain"],
    section.main {
        padding-top: 0 !important;
    }

    .main .block-container,
    div[data-testid="stMainBlockContainer"],
    div.block-container {
        padding-top: 0.9rem !important;
    }

    /* Anchor/link bawaan yang muncul saat hover heading. */
    a.anchor-link,
    a.header-anchor,
    .anchor-link,
    .header-anchor,
    [data-testid="stMarkdownContainer"] a.anchor-link {
        display: none !important;
        visibility: hidden !important;
    }

    /* Bersihkan frame/decoration default pada embed/wrapper. */
    iframe,
    .st-emotion-cache-1dp5vir,
    .st-emotion-cache-18ni7ap,
    .st-emotion-cache-z5fcl4 {
        border: 0 !important;
        box-shadow: none !important;
    }

    /* Jangan tampilkan link branding Streamlit jika ada teks footer fallback. */
    footer:after,
    footer:before {
        content: "" !important;
        display: none !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)




# =========================
# Compact feedback controls
# =========================
st.markdown(
    """
    <style>
    .feedback-info-box {
        display: inline-flex;
        align-items: center;
        gap: 0.36rem;
        max-width: min(100%, 560px);
        margin: 0.12rem 0 0.28rem;
        padding: 0.30rem 0.50rem;
        border: 1px solid var(--ui-border, rgba(148, 163, 184, 0.26));
        border-radius: 999px;
        background: var(--ui-surface-soft, rgba(255, 255, 255, 0.62));
        color: var(--ui-muted, rgba(71, 85, 105, 0.90));
        font-size: 0.68rem;
        line-height: 1.28;
        box-shadow: 0 4px 10px rgba(15, 23, 42, 0.045);
    }

    .feedback-info-title {
        flex: 0 0 auto;
        font-weight: 800;
        color: var(--ui-text-strong, #0f172a);
    }

    .feedback-info-text {
        color: var(--ui-muted, #475569);
    }

    /* Tombol feedback fallback dan tombol template admin dibuat kecil. */
    div[class*="_feedback_up_"] button,
    div[class*="_feedback_down_"] button,
    div[class*="_feedback_tmpl_"] button,
    div[class*="st-key-latest_feedback_up_"] button,
    div[class*="st-key-latest_feedback_down_"] button,
    div[class*="st-key-latest_feedback_tmpl_"] button,
    div[class*="st-key-history_feedback_"][class*="_up_"] button,
    div[class*="st-key-history_feedback_"][class*="_down_"] button,
    div[class*="st-key-history_feedback_"][class*="_tmpl_"] button {
        width: 34px !important;
        min-width: 34px !important;
        max-width: 34px !important;
        min-height: 28px !important;
        height: 28px !important;
        padding: 0 !important;
        border-radius: 999px !important;
        font-size: 0.76rem !important;
        line-height: 1 !important;
        font-weight: 750 !important;
        box-shadow: 0 2px 8px rgba(15, 23, 42, 0.07) !important;
    }

    div[class*="_feedback_up_"] button:hover,
    div[class*="_feedback_down_"] button:hover,
    div[class*="_feedback_tmpl_"] button:hover,
    div[class*="st-key-latest_feedback_up_"] button:hover,
    div[class*="st-key-latest_feedback_down_"] button:hover,
    div[class*="st-key-latest_feedback_tmpl_"] button:hover,
    div[class*="st-key-history_feedback_"][class*="_up_"] button:hover,
    div[class*="st-key-history_feedback_"][class*="_down_"] button:hover,
    div[class*="st-key-history_feedback_"][class*="_tmpl_"] button:hover {
        transform: translateY(-1px);
    }

    /* st.feedback thumbs bawaan dibuat lebih ringkas. */
    div[data-testid="stFeedback"] {
        width: fit-content !important;
        max-width: 92px !important;
        margin: 0.04rem 0 0.28rem !important;
    }

    div[data-testid="stFeedback"] button {
        width: 32px !important;
        min-width: 32px !important;
        height: 30px !important;
        min-height: 30px !important;
        padding: 0 !important;
        border-radius: 999px !important;
        font-size: 0.78rem !important;
    }

    @media (prefers-color-scheme: dark) {
        .feedback-info-box {
            background: rgba(15, 23, 42, 0.52);
            border-color: rgba(255, 255, 255, 0.13);
            color: rgba(226, 232, 240, 0.82);
        }

        .feedback-info-title {
            color: rgba(248, 250, 252, 0.96);
        }

        .feedback-info-text {
            color: rgba(203, 213, 225, 0.82);
        }
    }

    @media (max-width: 760px) {
        .feedback-info-box {
            display: inline-flex;
            align-items: center;
            max-width: calc(100vw - 40px);
            margin-top: 0.08rem;
            margin-bottom: 0.22rem;
            padding: 0.26rem 0.42rem;
            border-radius: 999px;
            font-size: 0.64rem;
            line-height: 1.22;
        }

        .feedback-info-title {
            font-size: 0.63rem;
        }

        .feedback-info-text {
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        div[data-testid="stFeedback"] {
            max-width: 82px !important;
            margin-top: 0 !important;
            margin-bottom: 0.20rem !important;
        }

        div[data-testid="stFeedback"] button,
        div[class*="_feedback_up_"] button,
        div[class*="_feedback_down_"] button,
        div[class*="_feedback_tmpl_"] button,
        div[class*="st-key-latest_feedback_up_"] button,
        div[class*="st-key-latest_feedback_down_"] button,
        div[class*="st-key-latest_feedback_tmpl_"] button,
        div[class*="st-key-history_feedback_"][class*="_up_"] button,
        div[class*="st-key-history_feedback_"][class*="_down_"] button,
        div[class*="st-key-history_feedback_"][class*="_tmpl_"] button {
            width: 30px !important;
            min-width: 30px !important;
            max-width: 30px !important;
            height: 27px !important;
            min-height: 27px !important;
            padding: 0 !important;
            font-size: 0.72rem !important;
        }
    }

    @media (max-width: 420px) {
        .feedback-info-box {
            max-width: calc(100vw - 32px);
            padding: 0.24rem 0.38rem;
            font-size: 0.61rem;
        }

        .feedback-info-title {
            display: none;
        }

        div[data-testid="stFeedback"] {
            max-width: 76px !important;
        }

        div[data-testid="stFeedback"] button,
        div[class*="_feedback_up_"] button,
        div[class*="_feedback_down_"] button,
        div[class*="_feedback_tmpl_"] button,
        div[class*="st-key-latest_feedback_up_"] button,
        div[class*="st-key-latest_feedback_down_"] button,
        div[class*="st-key-latest_feedback_tmpl_"] button,
        div[class*="st-key-history_feedback_"][class*="_up_"] button,
        div[class*="st-key-history_feedback_"][class*="_down_"] button,
        div[class*="st-key-history_feedback_"][class*="_tmpl_"] button {
            width: 28px !important;
            min-width: 28px !important;
            max-width: 28px !important;
            height: 26px !important;
            min-height: 26px !important;
            font-size: 0.70rem !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)



# =========================
# Streamlit online emblem safe area
# =========================
st.markdown(
    """
    <style>
    /*
      Safe area khusus untuk emblem/badge bawaan Streamlit Online.
      Beberapa badge berada fixed di kanan-bawah dan bisa menutup pesan,
      tombol feedback, atau input chat. Bagian ini memberi ruang aman dan
      tetap menjaga tampilan rapi pada mode light maupun dark.
    */
    :root {
        --online-emblem-safe-right: 98px;
        --online-emblem-safe-bottom: 88px;
        --chat-floating-bottom: 18px;
        --chat-safe-space-desktop: 300px;
        --chat-safe-space-mobile: 330px;
    }

    /* Coba sembunyikan variasi badge Streamlit Cloud yang sering berubah nama class. */
    div[class*="viewerBadge"],
    div[class*="ViewerBadge"],
    div[class*="stStatusWidget"],
    div[class*="stDeployButton"],
    div[class*="stAppDeployButton"],
    div[class*="decoration"],
    div[class*="Decoration"],
    a[href*="streamlit.io"],
    a[href*="streamlit.app"],
    button[title*="Streamlit"],
    button[aria-label*="Streamlit"],
    [data-testid*="stStatusWidget"],
    [data-testid*="stDeployButton"],
    [data-testid*="stAppDeployButton"],
    [data-testid*="stToolbar"],
    [data-testid*="stDecoration"] {
        display: none !important;
        visibility: hidden !important;
        opacity: 0 !important;
        pointer-events: none !important;
    }

    /* Tambah ruang bawah supaya pesan terakhir tidak tertutup overlay apa pun. */
    .main .block-container,
    div[data-testid="stMainBlockContainer"],
    div.block-container {
        padding-bottom: var(--chat-safe-space-desktop) !important;
    }

    .chat-input-safe-space {
        height: var(--chat-safe-space-desktop) !important;
    }

    /* Pesan/feedback diberi jarak aman dari pojok kanan-bawah. */
    div[data-testid="stChatMessage"]:last-of-type,
    .feedback-info-box,
    .answer-pdf-link-wrap {
        scroll-margin-bottom: var(--chat-safe-space-desktop) !important;
    }

    /* Input tidak lagi terlalu dekat dengan kanan-bawah tempat badge biasa muncul. */
    div[data-testid="stChatInput"] {
        bottom: var(--chat-floating-bottom) !important;
        width: min(980px, calc(100vw - var(--online-emblem-safe-right) - 78px)) !important;
        max-width: calc(100vw - var(--online-emblem-safe-right) - 78px) !important;
        transform: translateX(calc(-50% - 18px)) !important;
        z-index: 998 !important;
    }

    /* Area klik tombol kirim dijaga agar tidak tepat di bawah badge kanan. */
    div[data-testid="stChatInput"] button {
        margin-right: 0.15rem !important;
        position: relative !important;
        z-index: 2 !important;
    }

    @media (min-width: 1280px) {
        div[data-testid="stChatInput"] {
            width: min(1040px, calc(100vw - var(--online-emblem-safe-right) - 132px)) !important;
            max-width: calc(100vw - var(--online-emblem-safe-right) - 132px) !important;
        }
    }

    @media (max-width: 760px) {
        :root {
            --online-emblem-safe-right: 74px;
            --online-emblem-safe-bottom: 86px;
            --chat-floating-bottom: calc(56px + env(safe-area-inset-bottom));
        }

        .main .block-container,
        div[data-testid="stMainBlockContainer"],
        div.block-container {
            padding-bottom: var(--chat-safe-space-mobile) !important;
        }

        .chat-input-safe-space {
            height: var(--chat-safe-space-mobile) !important;
        }

        div[data-testid="stChatInput"] {
            left: 50% !important;
            bottom: var(--chat-floating-bottom) !important;
            width: calc(100vw - 24px) !important;
            max-width: calc(100vw - 24px) !important;
            transform: translateX(-50%) !important;
            border-radius: 20px !important;
        }

        div[data-testid="stChatMessage"]:last-of-type,
        .feedback-info-box,
        .answer-pdf-link-wrap {
            scroll-margin-bottom: var(--chat-safe-space-mobile) !important;
        }
    }

    @media (max-width: 420px) {
        :root {
            --chat-floating-bottom: calc(64px + env(safe-area-inset-bottom));
        }

        div[data-testid="stChatInput"] {
            width: calc(100vw - 18px) !important;
            max-width: calc(100vw - 18px) !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# =========================
# Cute loading animation style
# =========================
st.markdown(
    """
    <style>
    .ai-loading-card {
        display: inline-flex;
        align-items: center;
        gap: 0.82rem;
        max-width: min(100%, 560px);
        margin: 0.15rem 0 0.65rem 0;
        padding: 0.78rem 0.9rem;
        border: 1px solid var(--mac-border);
        border-radius: 20px;
        background:
            linear-gradient(135deg, var(--mac-panel), var(--mac-panel-soft));
        box-shadow: var(--mac-shadow-soft);
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
        color: var(--mac-text);
        overflow: hidden;
        position: relative;
    }

    .ai-loading-card::before {
        content: "";
        position: absolute;
        inset: -45% auto auto -12%;
        width: 150px;
        height: 150px;
        border-radius: 999px;
        background:
            radial-gradient(
                circle,
                rgba(48, 209, 88, 0.16),
                transparent 64%
            );
        pointer-events: none;
        animation: aiCuteGlow 2.8s ease-in-out infinite;
    }

    .ai-loading-mascot {
        width: 50px;
        height: 54px;
        position: relative;
        flex: 0 0 50px;
        display: grid;
        place-items: center;
        animation: aiRobotFloat 1.55s ease-in-out infinite;
        transform-origin: center bottom;
    }

    .ai-robot-antenna {
        position: absolute;
        top: 0;
        left: 50%;
        width: 2px;
        height: 10px;
        border-radius: 999px;
        background: var(--mac-blue);
        transform: translateX(-50%);
        animation: aiAntennaBob 1.2s ease-in-out infinite;
    }

    .ai-robot-antenna::after {
        content: "";
        position: absolute;
        top: -5px;
        left: 50%;
        width: 7px;
        height: 7px;
        border-radius: 999px;
        background: rgba(48, 209, 88, 0.96);
        box-shadow: 0 0 14px rgba(48, 209, 88, 0.58);
        transform: translateX(-50%);
    }

    .ai-robot-ear {
        position: absolute;
        top: 20px;
        width: 8px;
        height: 16px;
        border-radius: 999px;
        background: rgba(148, 163, 184, 0.42);
        border: 1px solid var(--mac-border);
    }

    .ai-robot-ear.left {
        left: 2px;
    }

    .ai-robot-ear.right {
        right: 2px;
    }

    .ai-robot-head {
        width: 38px;
        height: 34px;
        margin-top: 10px;
        border-radius: 13px;
        position: relative;
        z-index: 2;
        background:
            linear-gradient(
                145deg,
                rgba(255, 255, 255, 0.92),
                rgba(226, 232, 240, 0.82)
            );
        border: 1px solid rgba(148, 163, 184, 0.55);
        box-shadow:
            0 10px 22px rgba(15, 23, 42, 0.14),
            inset 0 1px 0 rgba(255, 255, 255, 0.8);
    }

    @media (prefers-color-scheme: dark) {
        .ai-robot-head {
            background:
                linear-gradient(
                    145deg,
                    rgba(51, 65, 85, 0.96),
                    rgba(15, 23, 42, 0.9)
                );
            border-color: rgba(148, 163, 184, 0.36);
        }
    }

    .ai-robot-eyes {
        position: absolute;
        top: 10px;
        left: 8px;
        right: 8px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }

    .ai-robot-eye {
        width: 7px;
        height: 8px;
        border-radius: 999px;
        background: var(--mac-blue);
        box-shadow: 0 0 9px rgba(0, 122, 255, 0.42);
        animation: aiRobotBlink 2.6s ease-in-out infinite;
    }

    .ai-robot-cheek {
        position: absolute;
        top: 20px;
        width: 5px;
        height: 3px;
        border-radius: 999px;
        background: rgba(255, 159, 10, 0.55);
        opacity: 0.9;
    }

    .ai-robot-cheek.left {
        left: 7px;
    }

    .ai-robot-cheek.right {
        right: 7px;
    }

    .ai-robot-mouth {
        position: absolute;
        left: 50%;
        bottom: 8px;
        width: 10px;
        height: 5px;
        border-bottom: 2px solid rgba(48, 209, 88, 0.94);
        border-radius: 0 0 999px 999px;
        transform: translateX(-50%);
        animation: aiRobotSmile 1.8s ease-in-out infinite;
    }

    .ai-robot-arm {
        position: absolute;
        top: 30px;
        width: 10px;
        height: 4px;
        border-radius: 999px;
        background: var(--mac-blue);
        opacity: 0.78;
        transform-origin: center;
    }

    .ai-robot-arm.left {
        left: -1px;
        transform: rotate(24deg);
        animation: aiRobotWaveLeft 1.25s ease-in-out infinite;
    }

    .ai-robot-arm.right {
        right: -1px;
        transform: rotate(-24deg);
        animation: aiRobotWaveRight 1.25s ease-in-out infinite;
    }

    .ai-robot-shadow {
        position: absolute;
        bottom: 0;
        left: 50%;
        width: 32px;
        height: 7px;
        border-radius: 999px;
        background: rgba(15, 23, 42, 0.16);
        filter: blur(1px);
        transform: translateX(-50%);
        animation: aiRobotShadow 1.55s ease-in-out infinite;
    }

    .ai-loading-copy {
        display: flex;
        flex-direction: column;
        gap: 0.24rem;
        min-width: 0;
        position: relative;
        z-index: 2;
    }

    .ai-loading-title {
        display: inline-flex;
        align-items: center;
        gap: 0.42rem;
        font-size: 0.92rem;
        font-weight: 760;
        letter-spacing: -0.01em;
        color: var(--mac-text);
        line-height: 1.25;
    }

    .ai-loading-subtitle {
        font-size: 0.76rem;
        color: var(--mac-muted);
        line-height: 1.35;
    }

    .ai-loading-dots {
        display: inline-flex;
        align-items: center;
        gap: 0.18rem;
        transform: translateY(1px);
    }

    .ai-loading-dot {
        width: 5px;
        height: 5px;
        border-radius: 999px;
        background: currentColor;
        opacity: 0.45;
        animation: aiDotPulse 1.05s ease-in-out infinite;
    }

    .ai-loading-dot:nth-child(2) {
        animation-delay: 0.16s;
    }

    .ai-loading-dot:nth-child(3) {
        animation-delay: 0.32s;
    }

    .ai-loading-bar {
        width: min(250px, 56vw);
        height: 4px;
        overflow: hidden;
        border-radius: 999px;
        background: rgba(148, 163, 184, 0.22);
    }

    .ai-loading-bar::before {
        content: "";
        display: block;
        width: 42%;
        height: 100%;
        border-radius: inherit;
        background:
            linear-gradient(
                90deg,
                transparent,
                rgba(48, 209, 88, 0.96),
                var(--mac-blue),
                rgba(255, 159, 10, 0.92),
                transparent
            );
        animation: aiLoadingBar 1.35s ease-in-out infinite;
    }

    @keyframes aiCuteGlow {
        0%,
        100% {
            transform: scale(0.92);
            opacity: 0.55;
        }

        50% {
            transform: scale(1.08);
            opacity: 0.9;
        }
    }

    @keyframes aiRobotFloat {
        0%,
        100% {
            transform: translateY(0) rotate(-1deg);
        }

        50% {
            transform: translateY(-5px) rotate(1.5deg);
        }
    }

    @keyframes aiAntennaBob {
        0%,
        100% {
            transform: translateX(-50%) translateY(0);
        }

        50% {
            transform: translateX(-50%) translateY(-2px);
        }
    }

    @keyframes aiRobotBlink {
        0%,
        88%,
        100% {
            transform: scaleY(1);
        }

        92% {
            transform: scaleY(0.16);
        }
    }

    @keyframes aiRobotSmile {
        0%,
        100% {
            width: 9px;
        }

        50% {
            width: 13px;
        }
    }

    @keyframes aiRobotWaveLeft {
        0%,
        100% {
            transform: rotate(22deg) translateY(0);
        }

        50% {
            transform: rotate(42deg) translateY(-2px);
        }
    }

    @keyframes aiRobotWaveRight {
        0%,
        100% {
            transform: rotate(-22deg) translateY(0);
        }

        50% {
            transform: rotate(-42deg) translateY(-2px);
        }
    }

    @keyframes aiRobotShadow {
        0%,
        100% {
            transform: translateX(-50%) scaleX(1);
            opacity: 0.16;
        }

        50% {
            transform: translateX(-50%) scaleX(0.72);
            opacity: 0.09;
        }
    }

    @keyframes aiDotPulse {
        0%,
        80%,
        100% {
            transform: translateY(0);
            opacity: 0.35;
        }

        40% {
            transform: translateY(-4px);
            opacity: 1;
        }
    }

    @keyframes aiLoadingBar {
        0% {
            transform: translateX(-110%);
        }

        100% {
            transform: translateX(260%);
        }
    }

    @media (max-width: 520px) {
        .ai-loading-card {
            width: 100%;
            gap: 0.58rem;
            padding: 0.62rem 0.68rem;
            border-radius: 17px;
        }

        .ai-loading-mascot {
            width: 42px;
            height: 46px;
            flex-basis: 42px;
        }

        .ai-robot-head {
            width: 32px;
            height: 29px;
            border-radius: 11px;
        }

        .ai-robot-ear {
            top: 18px;
            width: 7px;
            height: 13px;
        }

        .ai-robot-arm {
            top: 27px;
            width: 8px;
        }

        .ai-loading-title {
            font-size: 0.82rem;
        }

        .ai-loading-subtitle {
            font-size: 0.68rem;
        }

        .ai-loading-bar {
            width: min(200px, 62vw);
        }
    }

    @media (max-width: 380px) {
        .ai-loading-card {
            align-items: flex-start;
            gap: 0.5rem;
        }

        .ai-loading-mascot {
            transform: scale(0.92);
            transform-origin: left top;
        }

        .ai-loading-title {
            font-size: 0.78rem;
        }

        .ai-loading-subtitle {
            font-size: 0.65rem;
        }
    }

    .auto-scroll-anchor {
        display: block;
        width: 100%;
        height: 1px;
        margin: 0;
        padding: 0;
        opacity: 0;
        pointer-events: none;
        scroll-margin-bottom: var(--chat-safe-space-desktop);
    }

    @media (max-width: 760px) {
        .auto-scroll-anchor {
            scroll-margin-bottom: var(--chat-safe-space-mobile);
        }
    }

    @media (prefers-reduced-motion: reduce) {
        .ai-loading-card::before,
        .ai-loading-mascot,
        .ai-robot-antenna,
        .ai-robot-eye,
        .ai-robot-mouth,
        .ai-robot-arm,
        .ai-robot-shadow,
        .ai-loading-dot,
        .ai-loading-bar::before {
            animation: none !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def render_loading_animation_html(
    title: str = "Adioranye sedang mengetik jawaban",
    subtitle: str = "Sebentar ya, robot kecilnya sedang berpikir dan merapikan respons.",
) -> str:
    safe_title = _html_escape(title)
    safe_subtitle = _html_escape(subtitle)

    if bool(frontend_ultra_safe_mode) or not bool(animated_loading_enabled):
        return f"""
        <div class="ai-loading-card" role="status" aria-live="polite">
            <div class="ai-loading-copy">
                <div class="ai-loading-title">
                    <span>⏳ {safe_title}</span>
                </div>
                <div class="ai-loading-subtitle">
                    {safe_subtitle}
                </div>
            </div>
        </div>
        """

    return f"""
    <div class="ai-loading-card" role="status" aria-live="polite">
        <div class="ai-loading-copy">
            <div class="ai-loading-title">
                <span>{safe_title}</span>
                <span class="ai-loading-dots" aria-hidden="true">
                    <span class="ai-loading-dot"></span>
                    <span class="ai-loading-dot"></span>
                    <span class="ai-loading-dot"></span>
                </span>
            </div>
            <div class="ai-loading-subtitle">
                {safe_subtitle}
            </div>
        </div>
    </div>
    """

def render_auto_scroll_script(
    target: str = "latest",
    delay_ms: int = 120,
) -> None:
    """Auto-scroll one-shot ke pesan terakhir.

    Tidak memakai polling, interval, fragment, atau reload. Hanya komponen kecil
    sekali render untuk mengarahkan layar ke pesan terakhir.
    """
    if not bool(message_effects_enabled and custom_components_enabled and auto_scroll_enabled):
        return

    safe_target = _html_escape(str(target or "latest"))
    safe_delay = max(0, min(800, int(delay_ms or 0)))
    scroll_key = f"{safe_target}-{int(time.time() * 1000)}"

    components.html(
        f"""
        <script>
        (function () {{
            const key = "adioranye-scroll-{scroll_key}";
            try {{
                const parentWindow = window.parent || window;
                if (parentWindow.__adioranyeLastScrollKey === key) {{
                    return;
                }}
                parentWindow.__adioranyeLastScrollKey = key;

                const run = function () {{
                    try {{
                        const doc = parentWindow.document || document;
                        const candidates = doc.querySelectorAll(
                            'div[data-testid="stChatMessage"], .auto-scroll-anchor, .ai-loading-card'
                        );
                        const targetElement = candidates && candidates.length
                            ? candidates[candidates.length - 1]
                            : doc.body;

                        if (targetElement && targetElement.scrollIntoView) {{
                            targetElement.scrollIntoView({{
                                behavior: "smooth",
                                block: "{'center' if safe_target == 'loading' else 'end'}",
                                inline: "nearest"
                            }});
                        }}
                    }} catch (err) {{}}
                }};

                if ({safe_delay} > 0) {{
                    window.setTimeout(run, {safe_delay});
                }} else if (window.requestAnimationFrame) {{
                    window.requestAnimationFrame(run);
                }} else {{
                    run();
                }}
            }} catch (err) {{}}
        }})();
        </script>
        """,
        height=0,
        scrolling=False,
    )

def render_sound_unlock_script() -> None:
    """Siapkan izin suara secara best-effort.

    Browser modern bisa tetap membatasi autoplay. Script ini hanya menandai
    bahwa efek suara boleh dicoba setelah user berinteraksi dengan halaman.
    """
    if not bool(message_effects_enabled and custom_components_enabled and answer_sound_enabled):
        return

    components.html(
        """
        <script>
        (function () {
            try {
                const parentWindow = window.parent || window;
                parentWindow.__adioranyeSoundEnabled = true;

                const unlock = function () {
                    try {
                        parentWindow.__adioranyeSoundEnabled = true;
                        parentWindow.removeEventListener("pointerdown", unlock, true);
                        parentWindow.removeEventListener("keydown", unlock, true);
                    } catch (err) {}
                };

                parentWindow.addEventListener("pointerdown", unlock, true);
                parentWindow.addEventListener("keydown", unlock, true);
            } catch (err) {}
        })();
        </script>
        """,
        height=0,
        scrolling=False,
    )

def render_answer_ready_sound_script(
    sound_key: str = "latest",
) -> None:
    """Mainkan suara pendek saat jawaban baru muncul.

    Efek ini one-shot berdasarkan `sound_key`, sehingga tidak berbunyi berulang
    pada render yang sama.
    """
    if (
        not bool(message_effects_enabled and custom_components_enabled and answer_sound_enabled)
        or not bool(st.session_state.get("sound_enabled", False))
    ):
        return

    safe_key = _html_escape(str(sound_key or "latest"))

    components.html(
        f"""
        <script>
        (function () {{
            try {{
                const parentWindow = window.parent || window;
                const key = "adioranye-answer-sound-{safe_key}";

                if (parentWindow.__adioranyeLastSoundKey === key) {{
                    return;
                }}
                parentWindow.__adioranyeLastSoundKey = key;

                const AudioContextClass =
                    parentWindow.AudioContext ||
                    parentWindow.webkitAudioContext ||
                    window.AudioContext ||
                    window.webkitAudioContext;

                if (!AudioContextClass) {{
                    return;
                }}

                const audioContext = new AudioContextClass();
                const oscillator = audioContext.createOscillator();
                const gainNode = audioContext.createGain();

                oscillator.type = "sine";
                oscillator.frequency.setValueAtTime(880, audioContext.currentTime);
                oscillator.frequency.exponentialRampToValueAtTime(
                    660,
                    audioContext.currentTime + 0.16
                );

                gainNode.gain.setValueAtTime(0.0001, audioContext.currentTime);
                gainNode.gain.exponentialRampToValueAtTime(
                    0.12,
                    audioContext.currentTime + 0.018
                );
                gainNode.gain.exponentialRampToValueAtTime(
                    0.0001,
                    audioContext.currentTime + 0.18
                );

                oscillator.connect(gainNode);
                gainNode.connect(audioContext.destination);

                const startSound = function () {{
                    try {{
                        oscillator.start();
                        oscillator.stop(audioContext.currentTime + 0.20);
                    }} catch (err) {{}}
                }};

                if (audioContext.state === "suspended") {{
                    audioContext.resume().then(startSound).catch(function () {{}});
                }} else {{
                    startSound();
                }}
            }} catch (err) {{}}
        }})();
        </script>
        """,
        height=0,
        scrolling=False,
    )

def _get_public_stats() -> Dict[str, Any]:
    stats = st.session_state.get("public_usage_stats") or {}

    if not isinstance(stats, dict):
        stats = {}

    defaults = {
        "total_questions": 0,
        "blocked_by_rate_limit": 0,
        "public_errors_hidden": 0,
        "model_blocks_created": 0,
        "last_error_summary": "",
        "last_error_at": "",
    }

    for key, value in defaults.items():
        stats.setdefault(key, value)

    st.session_state.public_usage_stats = stats
    return stats


def _increment_public_stat(
    key: str,
    amount: int = 1,
) -> None:
    stats = _get_public_stats()

    try:
        stats[key] = int(stats.get(key, 0) or 0) + int(amount)
    except Exception:
        stats[key] = amount


def _runtime_block_store() -> Dict[str, Dict[str, Any]]:
    store = st.session_state.get("model_runtime_blocks") or {}

    if not isinstance(store, dict):
        store = {}

    now = time.time()
    cleaned: Dict[str, Dict[str, Any]] = {}

    for model_name, info in store.items():
        if not isinstance(info, dict):
            continue

        until = float(info.get("until") or 0)

        if until > now:
            cleaned[str(model_name)] = info

    st.session_state.model_runtime_blocks = cleaned
    return cleaned


def is_model_runtime_blocked(
    model_name: str,
) -> bool:
    if not bool(runtime_model_block_enabled):
        return False

    model_clean = str(model_name or "").strip()

    if not model_clean:
        return False

    store = _runtime_block_store()
    info = store.get(model_clean) or {}

    return float(info.get("until") or 0) > time.time()


def register_runtime_model_block(
    model_name: str,
    reason: str,
    seconds: int,
    detail: str = "",
) -> None:
    if not bool(runtime_model_block_enabled):
        return

    model_clean = str(model_name or "").strip()

    if not model_clean:
        return

    seconds_safe = max(
        60,
        int(seconds or runtime_model_block_generic_seconds or 1200),
    )

    store = _runtime_block_store()
    store[model_clean] = {
        "reason": str(reason or "runtime_error")[:120],
        "detail": str(detail or "")[:700],
        "until": time.time() + seconds_safe,
        "created_at": _wib_now_text(),
    }

    st.session_state.model_runtime_blocks = store
    _increment_public_stat("model_blocks_created")


def classify_runtime_error_detail(
    detail: str,
) -> Tuple[str, int]:
    lowered = str(detail or "").lower()

    if any(
        marker in lowered
        for marker in [
            "insufficient balance",
            "insufficient_user_quota",
            "quota",
            "billing",
            "creditsdepleted",
            "pre-consume",
        ]
    ):
        return (
            "saldo/quota tidak cukup",
            int(runtime_model_block_quota_seconds or 3600),
        )

    if any(
        marker in lowered
        for marker in [
            "invalid model",
            "model not found",
            "unknown model",
            "does not exist",
            "please select a different model",
        ]
    ):
        return (
            "model tidak valid",
            int(runtime_model_block_invalid_seconds or 86400),
        )

    if any(
        marker in lowered
        for marker in [
            "read timed out",
            "timeout",
            "timed out",
            "connectionpool",
        ]
    ):
        return (
            "timeout koneksi",
            int(runtime_model_block_timeout_seconds or 900),
        )

    return (
        "error runtime",
        int(runtime_model_block_generic_seconds or 1200),
    )


def register_model_blocks_from_error_text(
    error_text: str,
    route: Dict[str, Any] | None = None,
) -> None:
    if not bool(runtime_model_block_enabled):
        return

    detail = str(error_text or "")

    if not detail.strip():
        return

    route_data = route or {}
    candidate_models: List[str] = []

    for key in [
        "primary_model",
        "capable_primary_model",
        "fastest_cheap_primary_model",
    ]:
        value = str(route_data.get(key) or "").strip()

        if value:
            candidate_models.append(value)

    for key in [
        "cheap_fallback_models",
        "expensive_fallback_models",
        "active_cheap_models",
        "active_expensive_models",
    ]:
        values = route_data.get(key) or []

        if isinstance(values, list):
            candidate_models.extend(
                str(item).strip()
                for item in values
                if str(item).strip()
            )

    detected_models = re.findall(
        r"slashai/[A-Za-z0-9_.:/+-]+",
        detail,
    )

    candidate_models.extend(detected_models)

    reason,
    seconds = classify_runtime_error_detail(detail)

    for model_name in unique_models(candidate_models):
        if model_name and model_name in detail:
            register_runtime_model_block(
                model_name=model_name,
                reason=reason,
                seconds=seconds,
                detail=detail,
            )


def filter_runtime_blocked_models(
    models: List[str],
) -> List[str]:
    return [
        model
        for model in unique_models(models)
        if not is_model_runtime_blocked(model)
    ]


def check_public_rate_limit(
    user_text: str,
) -> Tuple[bool, str]:
    if st.session_state.get("admin_authenticated", False):
        return True, ""

    if not bool(public_rate_limit_enabled):
        return True, ""

    clean_text = str(user_text or "")

    if len(clean_text) > int(public_max_prompt_chars or 6000):
        return (
            False,
            "Pertanyaan terlalu panjang. Mohon ringkas dulu agar bisa diproses lebih stabil.",
        )

    now = time.time()
    window = max(
        60,
        int(public_rate_limit_window_seconds or 600),
    )
    max_requests = max(
        1,
        int(public_rate_limit_max_requests or 10),
    )

    events = st.session_state.get("public_rate_events") or []

    if not isinstance(events, list):
        events = []

    events = [
        float(item)
        for item in events
        if now - float(item or 0) <= window
    ]

    if len(events) >= max_requests:
        st.session_state.public_rate_events = events
        _increment_public_stat("blocked_by_rate_limit")

        return (
            False,
            "Terlalu banyak permintaan dalam waktu singkat. Coba lagi beberapa saat.",
        )

    events.append(now)
    st.session_state.public_rate_events = events
    _increment_public_stat("total_questions")

    return True, ""


def get_safe_public_error_message() -> str:
    return (
        "Maaf, Adioranye sedang mengalami gangguan koneksi/model. "
        "Silakan coba lagi beberapa saat lagi."
    )


def is_public_connection_error_answer(
    answer_text: str,
    meta: Dict[str, Any] | None = None,
) -> bool:
    """Return True jika jawaban adalah pesan error publik yang aman.

    Untuk kondisi ini UI tidak boleh menampilkan efek loading/typewriter,
    karena proses sudah selesai dan gagal secara aman.
    """
    answer = str(answer_text or "").strip()
    safe_messages = {
        str(PUBLIC_AI_ERROR_MESSAGE).strip(),
        str(get_safe_public_error_message()).strip(),
        str(make_public_ai_error_message()).strip(),
    }

    if answer in safe_messages:
        return True

    if isinstance(meta, dict):
        return bool(
            meta.get("public_error_sanitized")
            or meta.get("public_error_hidden")
            or meta.get("public_safe_message")
        )

    return False


def looks_like_public_error_detail(
    answer_text: str,
) -> bool:
    lowered = str(answer_text or "").lower()

    markers = [
        "semua model gagal",
        "api status",
        "httpsconnectionpool",
        "read timed out",
        "insufficient balance",
        "insufficient_user_quota",
        "invalid model",
        "external billing",
        "creditsdepleted",
        "traceback",
        "request id:",
    ]

    return any(marker in lowered for marker in markers)


def sanitize_public_answer(
    answer_text: str,
    meta: Dict[str, Any] | None = None,
    route: Dict[str, Any] | None = None,
) -> str:
    raw_answer = str(answer_text or "")

    if not looks_like_public_error_detail(raw_answer):
        return raw_answer

    register_model_blocks_from_error_text(
        raw_answer,
        route=route,
    )

    stats = _get_public_stats()
    stats["public_errors_hidden"] = int(stats.get("public_errors_hidden", 0) or 0) + 1
    stats["last_error_summary"] = raw_answer[:500]
    stats["last_error_at"] = _wib_now_text()

    if isinstance(meta, dict):
        meta["public_error_hidden"] = True
        meta["public_error_original_preview"] = raw_answer[:700]

    return get_safe_public_error_message()



def test_tavily_connection(
    query: str = "berita AI terbaru hari ini",
) -> Dict[str, Any]:
    """Tes koneksi Tavily langsung ke endpoint resmi."""
    api_key = str(tavily_api_key or os.getenv("TAVILY_API_KEY", "") or "").strip()

    if not api_key:
        return {
            "ok": False,
            "status_code": None,
            "error": "TAVILY_API_KEY belum terbaca dari Streamlit Secrets/environment.",
            "result_count": 0,
            "sample_title": "",
            "sample_url": "",
        }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "query": str(query or "berita AI terbaru hari ini"),
        "topic": "general",
        "search_depth": "basic",
        "max_results": 3,
        "include_answer": True,
        "include_raw_content": False,
    }

    try:
        response = requests.post(
            "https://api.tavily.com/search",
            headers=headers,
            json=payload,
            timeout=15,
        )
    except requests.Timeout:
        return {
            "ok": False,
            "status_code": None,
            "error": "Request ke Tavily timeout.",
            "result_count": 0,
            "sample_title": "",
            "sample_url": "",
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status_code": None,
            "error": f"Request ke Tavily gagal: {exc}",
            "result_count": 0,
            "sample_title": "",
            "sample_url": "",
        }

    text_preview = ""
    try:
        data = response.json()
    except Exception:
        data = {}
        text_preview = response.text[:700]

    if response.status_code != 200:
        return {
            "ok": False,
            "status_code": response.status_code,
            "error": text_preview or str(data)[:700],
            "result_count": 0,
            "sample_title": "",
            "sample_url": "",
        }

    results = data.get("results") or []
    first = results[0] if results and isinstance(results[0], dict) else {}

    return {
        "ok": True,
        "status_code": response.status_code,
        "error": "",
        "result_count": len(results),
        "sample_title": str(first.get("title") or "")[:180],
        "sample_url": str(first.get("url") or "")[:240],
        "answer_preview": str(data.get("answer") or "")[:300],
    }


def render_tavily_connection_panel() -> None:
    """Panel admin untuk memastikan Tavily benar-benar tersambung."""
    st.markdown("#### Koneksi Tavily / Live Web")
    col_a, col_b, col_c = st.columns(3)

    with col_a:
        st.metric(
            "Live Web",
            "ON" if live_web_fallback_enabled else "OFF",
        )

    with col_b:
        st.metric(
            "Provider",
            live_web_fallback_provider,
        )

    with col_c:
        st.metric(
            "Tavily Key",
            "Terbaca" if bool(tavily_api_key) else "Kosong",
        )

    if not tavily_api_key:
        st.error(
            "TAVILY_API_KEY belum terbaca. Isi di Streamlit Secrets, lalu Save dan Reboot app."
        )
        return

    test_query = st.text_input(
        "Query tes Tavily",
        value="berita AI terbaru hari ini",
        key="tavily_test_query",
    )

    if st.button(
        "🔎 Tes koneksi Tavily",
        use_container_width=True,
        key="tavily_connection_test_button",
    ):
        result = test_tavily_connection(test_query)

        if result.get("ok"):
            st.success(
                f"Tavily tersambung. Hasil ditemukan: {result.get('result_count', 0)}"
            )
            if result.get("sample_title"):
                st.caption(f"Contoh: {result.get('sample_title')}")
            if result.get("sample_url"):
                st.caption(f"URL: {result.get('sample_url')}")
            if result.get("answer_preview"):
                st.info(result.get("answer_preview"))
        else:
            st.error("Tavily belum tersambung.")
            st.code(
                str(result.get("error") or "Tidak ada detail error.")[:1200],
                language="text",
            )
            status_code = result.get("status_code")
            if status_code == 401:
                st.warning(
                    "HTTP 401 biasanya berarti API key salah, expired, atau belum disimpan di Secrets."
                )
            elif status_code == 429:
                st.warning(
                    "HTTP 429 berarti kuota/rate limit Tavily habis atau terlalu sering request."
                )



def fetch_tavily_live_context(
    query: str,
    max_results: int | None = None,
) -> Dict[str, Any]:
    """Ambil konteks langsung dari Tavily dengan live_cache_v2."""
    query_clean = str(query or "").strip()

    cached = None

    try:
        cached = read_live_cache(
            power_db_path,
            query_clean,
            provider=live_web_fallback_provider or "tavily",
        )
    except Exception:
        cached = None

    if cached:
        cached_sources = cached.get("sources") or []
        context_parts = [
            "KONTEKS LIVE CACHE UNTUK INFO TERKINI",
            f"Query: {query_clean}",
            _indonesia_time_context_text(),
            f"Provider: {cached.get('provider', 'tavily')}",
            f"Cached at: {cached.get('created_at', '')}",
            f"Expires at: {cached.get('expires_at', '')}",
            "",
        ]

        answer = str(cached.get("answer") or "").strip()

        if answer:
            context_parts.append("Ringkasan cache:")
            context_parts.append(answer[:1600])
            context_parts.append("")

        for index, item in enumerate(cached_sources, start=1):
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            content = str(item.get("content") or "").strip()

            context_parts.append(f"Sumber cache {index}: {title or 'Tanpa judul'}")
            if url:
                context_parts.append(f"URL: {url}")
            if content:
                context_parts.append(f"Isi ringkas: {content[:1000]}")
            context_parts.append("")

        context_parts.append(
            "Instruksi penggunaan: jawab berdasarkan live cache ini untuk bagian info terkini. "
            "Jangan memakai Knowledge Base lama jika bertentangan."
        )

        return {
            "ok": True,
            "error": "",
            "context": "\n".join(context_parts).strip(),
            "sources": cached_sources,
            "answer_preview": answer,
            "cache_hit": True,
        }

    api_key = str(tavily_api_key or os.getenv("TAVILY_API_KEY", "") or "").strip()

    if not api_key:
        return {
            "ok": False,
            "error": "TAVILY_API_KEY belum terbaca.",
            "context": "",
            "sources": [],
            "cache_hit": False,
        }

    max_results_safe = int(max_results or live_web_fallback_max_results or 4)

    payload = {
        "query": query_clean,
        "topic": "general",
        "search_depth": "advanced",
        "max_results": max(1, min(max_results_safe, 8)),
        "include_answer": True,
        "include_raw_content": bool(live_web_fallback_include_raw_content),
    }

    try:
        response = requests.post(
            "https://api.tavily.com/search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=int(live_web_fallback_timeout_seconds or 10),
        )
    except requests.Timeout:
        return {
            "ok": False,
            "error": "Tavily timeout.",
            "context": "",
            "sources": [],
            "cache_hit": False,
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "error": f"Request Tavily gagal: {exc}",
            "context": "",
            "sources": [],
            "cache_hit": False,
        }

    try:
        data = response.json()
    except Exception:
        data = {}

    if response.status_code != 200:
        return {
            "ok": False,
            "error": f"HTTP {response.status_code}: {response.text[:900]}",
            "context": "",
            "sources": [],
            "cache_hit": False,
        }

    results = data.get("results") or []
    sources: List[Dict[str, str]] = []

    context_parts = [
        "KONTEKS LIVE WEB/TAVILY UNTUK INFO TERKINI",
        f"Query: {query_clean}",
        _indonesia_time_context_text(),
        f"Waktu cek utama: {_wib_now_text()}",
        "",
    ]

    answer_preview = str(data.get("answer") or "").strip()

    if answer_preview:
        context_parts.append("Ringkasan Tavily:")
        context_parts.append(answer_preview[:1200])
        context_parts.append("")

    for index, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue

        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        content = str(
            item.get("content")
            or item.get("raw_content")
            or ""
        ).strip()

        if not title and not url and not content:
            continue

        content = content[
            : min(
                int(live_web_fallback_max_content_chars or 3200),
                int(live_web_source_content_max_chars or 1200),
            )
        ]

        sources.append(
            {
                "title": title,
                "url": url,
                "content": content[:500],
            }
        )

        context_parts.append(f"Sumber {index}: {title or 'Tanpa judul'}")
        if url:
            context_parts.append(f"URL: {url}")
        if content:
            context_parts.append(f"Isi ringkas: {content}")
        context_parts.append("")

    if not sources and not answer_preview:
        return {
            "ok": False,
            "error": "Tavily tidak mengembalikan hasil yang bisa dipakai.",
            "context": "",
            "sources": [],
            "cache_hit": False,
        }

    context_parts.append(
        "Instruksi penggunaan: jawab hanya berdasarkan konteks live web di atas untuk bagian info terkini. "
        "Jangan memakai Knowledge Base lama jika bertentangan dengan konteks live web."
    )

    context = "\n".join(context_parts).strip()

    try:
        write_live_cache(
            power_db_path,
            query=query_clean,
            provider=live_web_fallback_provider or "tavily",
            answer=answer_preview,
            sources=sources,
            ttl_seconds=int(live_web_fallback_ttl_hours or 24) * 3600,
        )
    except Exception:
        pass

    return {
        "ok": True,
        "error": "",
        "context": context,
        "sources": sources,
        "answer_preview": answer_preview,
        "cache_hit": False,
    }


def build_current_info_memory_context(
    user_query: str,
    runtime_options: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """Buat memory context khusus untuk pertanyaan info terkini."""
    if not runtime_options.get("current_info_mode"):
        base_memory = build_memory_text(limit=12)
        kb_v2_context, kb_v2_meta = build_kb_v2_context_for_prompt(
            user_query,
            limit=int(kb_v2_retrieval_limit or 5),
        )

        merged_sections = [
            section
            for section in [
                base_memory,
                kb_v2_context,
            ]
            if str(section or "").strip()
        ]

        return "\n\n".join(merged_sections), {
            "live_context_used": False,
            "live_context_error": "",
            "live_sources": [],
            **kb_v2_meta,
        }

    tavily_result = fetch_tavily_live_context(
        user_query,
        max_results=int(live_web_fallback_max_results or 4),
    )

    if tavily_result.get("ok"):
        return str(tavily_result.get("context") or ""), {
            "live_context_used": True,
            "live_context_error": "",
            "live_sources": tavily_result.get("sources") or [],
            "live_answer_preview": tavily_result.get("answer_preview", ""),
            "live_cache_hit": bool(tavily_result.get("cache_hit")),
        }

    return (
        "MODE INFO TERKINI AKTIF, tetapi Tavily/live web belum berhasil mengambil sumber terbaru.\n"
        f"Error: {tavily_result.get('error', 'Tidak diketahui')}\n"
        "Jika menjawab, jelaskan bahwa informasi terkini belum bisa diverifikasi.",
        {
            "live_context_used": False,
            "live_context_error": str(tavily_result.get("error") or ""),
            "live_sources": [],
        },
    )



def render_public_status_summary() -> None:
    stats = _get_public_stats()
    active_blocks = _runtime_block_store()

    blocked_count = len(active_blocks)
    total_questions = int(stats.get("total_questions", 0) or 0)
    hidden_errors = int(stats.get("public_errors_hidden", 0) or 0)
    block_class = "danger" if blocked_count else "ok"

    st.markdown(
        f"""
        <div class="production-status-card">
            <span class="production-pill ok">Publik: {total_questions} pertanyaan</span>
            <span class="production-pill {block_class}">Model diblokir: {blocked_count}</span>
            <span class="production-pill">Error disamarkan: {hidden_errors}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_admin_production_dashboard() -> None:
    stats = _get_public_stats()
    active_blocks = _runtime_block_store()

    st.subheader("🛡️ Produksi & Stabilitas")

    (
        col_a,
        col_b,
        col_c,
        col_d,
    ) = st.columns(4)

    with col_a:
        st.metric(
            "Pertanyaan publik",
            int(stats.get("total_questions", 0) or 0),
        )

    with col_b:
        st.metric(
            "Kena rate limit",
            int(stats.get("blocked_by_rate_limit", 0) or 0),
        )

    with col_c:
        st.metric(
            "Error disamarkan",
            int(stats.get("public_errors_hidden", 0) or 0),
        )

    with col_d:
        st.metric(
            "Model diblokir aktif",
            len(active_blocks),
        )

    if active_blocks:
        rows = []

        for model_name, info in active_blocks.items():
            rows.append(
                {
                    "model": model_name,
                    "alasan": info.get("reason", ""),
                    "sampai": _timestamp_to_wib_text(info.get("until", 0)),
                    "dibuat": info.get("created_at", ""),
                }
            )

        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True,
        )

    last_error = str(stats.get("last_error_summary") or "").strip()

    if last_error:
        with st.expander("Detail error terakhir admin"):
            st.code(
                last_error,
                language="text",
            )

    st.caption(
        "Rate limit, penyamaran error, block model, dan ranking performa berjalan otomatis untuk halaman publik."
    )

    perf_stats = get_model_performance_stats()
    if perf_stats:
        perf_rows = []
        for model_name, info in perf_stats.items():
            requests_count = int(info.get("requests", 0) or 0)
            if requests_count <= 0:
                continue
            success_count = int(info.get("success", 0) or 0)
            perf_rows.append(
                {
                    "model": model_name,
                    "request": requests_count,
                    "sukses": success_count,
                    "success_rate": f"{(success_count / max(1, requests_count)) * 100:.1f}%",
                    "avg_latency_ms": info.get("avg_latency_ms"),
                    "timeout": info.get("timeouts", 0),
                    "error_terakhir": str(info.get("last_error") or "")[:120],
                }
            )
        perf_rows.sort(
            key=lambda row: (
                -float(str(row["success_rate"]).replace("%", "") or 0),
                float(row.get("avg_latency_ms") or 999999),
            )
        )
        with st.expander("Ranking performa model", expanded=False):
            st.dataframe(perf_rows[:25], use_container_width=True, hide_index=True)

    reset_col_a, reset_col_b = st.columns(2)
    with reset_col_a:
        if st.button(
            "♻️ Reset model block",
            use_container_width=True,
            key="reset_runtime_blocks_btn",
        ):
            st.session_state.model_runtime_blocks = {}
            st.success("Model block aktif sudah direset.")
            st.rerun()
    with reset_col_b:
        if st.button(
            "🧹 Reset performa model",
            use_container_width=True,
            key="reset_model_perf_btn",
        ):
            reset_model_runtime_and_performance()
            st.success("Log performa model sudah direset.")
            st.rerun()



def should_use_realtime_streaming(
    user_text: str,
    runtime_options: Dict[str, Any],
) -> bool:
    """Direct API streaming dimatikan agar stabil di provider yang tidak konsisten.

    Jawaban tetap dibuat bertahap di UI lewat `render_answer_typewriter_display()`
    setelah jalur `safe_generate_power_answer()` selesai.
    """
    return False


def render_answer_typewriter_display(
    placeholder: Any,
    answer_text: str,
    chunk_size: int = 22,
    delay_seconds: float = 0.012,
    is_error: bool = False,
) -> None:
    """Tampilkan jawaban dengan mode aman.

    Default: tanpa typewriter/time.sleep agar frontend tidak menerima banyak update
    beruntun yang dapat memicu React error #185.
    """
    answer = str(answer_text or "")

    if is_error:
        placeholder.warning(answer)
        return

    if bool(frontend_ultra_safe_mode) or not bool(typewriter_enabled):
        placeholder.markdown(answer)
        return

    if not answer.strip():
        placeholder.markdown(answer)
        return

    if len(answer) > 3500:
        chunk_size = 56
        delay_seconds = 0.003
    elif len(answer) > 1600:
        chunk_size = 38
        delay_seconds = 0.006

    shown_parts: List[str] = []

    for index in range(
        0,
        len(answer),
        max(1, int(chunk_size or 22)),
    ):
        shown_parts.append(answer[index:index + chunk_size])
        placeholder.markdown("".join(shown_parts) + "▌")

        if delay_seconds > 0:
            time.sleep(max(0.0, float(delay_seconds)))

    placeholder.markdown(answer)

def render_realtime_stream_answer(
    placeholder: Any,
    cfg: Dict[str, Any],
    route: Dict[str, Any],
    runtime_options: Dict[str, Any],
    user_input: str,
) -> Tuple[str, Dict[str, Any]]:
    """Kompatibilitas lama.

    Direct API streaming sengaja tidak dipakai karena sebagian provider
    OpenAI-compatible tidak stabil saat `stream=True`.
    """
    raise RuntimeError(
        "Direct API streaming disabled; using stable typewriter display."
    )



def normalize_telegram_model_mode(
    mode: Any = "",
) -> str:
    """Map mode UI/app ke mode Telegram: auto, cheap, expensive."""
    raw = str(mode or "").strip().lower()

    if raw in {
        "cheap",
        "murah",
        "hemat",
        "cepat",
        "normal",
    }:
        return "cheap"

    if raw in {
        "expensive",
        "mahal",
        "medium",
        "menengah",
        "maksimal",
        "pintar",
        "capable",
    }:
        return "expensive"

    return "auto"


def build_telegram_config_payload(
    route: Dict[str, Any],
    cfg: Dict[str, Any],
    persona_text: str,
) -> Dict[str, Any]:
    """Bangun config Telegram sesuai format `telegram_service.py`.

    File Telegram memakai banyak key eksplisit untuk router, health-check,
    model switch, GitHub update, RAG, quality control, dan runtime state.
    Helper ini menjaga agar Start Bot manual dan auto-start memakai format
    config yang sama.
    """
    telegram_model_mode = normalize_telegram_model_mode(
        st.session_state.get(
            "telegram_model_mode",
            telegram_model_mode_default,
        )
    )

    active_expensive_models = route.get(
        "active_expensive_models",
        [],
    )

    active_cheap_models = route.get(
        "active_cheap_models",
        [],
    )

    fast_cheap_models = route.get(
        "fast_cheap_models",
        [],
    )

    config_payload = {
        "telegram_token": telegram_token,
        "telegram_status_test_cache_ttl_seconds": telegram_status_test_cache_ttl_seconds,
        "telegram_status_test_timeout_seconds": telegram_status_test_timeout_seconds,
        "telegram_admin_chat_ids": telegram_admin_chat_ids,
        "admin_chat_ids": telegram_admin_chat_ids,
        "telegram_runtime_state_file": telegram_runtime_state_file,
        "telegram_model_mode": telegram_model_mode,
        "auto_rotate_on_model_error": bool(telegram_auto_rotate_on_model_error),
        "github_actions_token": github_actions_token,
        "github_repo": github_repo,
        "github_workflow_file": github_workflow_file,
        "github_branch": github_branch,
        "github_update_source_limit": github_update_source_limit,
        "github_update_max_items": github_update_max_items,
        "allow_unrestricted_model_commands": bool(
            allow_unrestricted_model_commands
        ),
        "slashai_api_key": api_key,
        "slashai_api_url": api_url,
        "slashai_model": route["primary_model"],
        "persona": persona_text,
        "memory_file": memory_file,
        "fallback_models": route["cheap_fallback_models"],
        "expensive_fallback_models": route["expensive_fallback_models"],
        "allow_expensive_fallback": route["allow_expensive_fallback"],
        "max_expensive_models": route["max_expensive_models"],
        "show_model_info": telegram_show_model_info,
        "temperature": float(cfg["temperature"]),
        "max_completion_tokens": int(cfg["max_completion_tokens"]),
        "timeout": 60,
        "drop_pending_updates": drop_pending_updates,
        "send_processing_message": send_processing_message,
        # File Telegram memaksa plain text, key ini tetap dikirim untuk kompatibilitas.
        "telegram_parse_mode": "",
        "lock_file": telegram_lock_file,
        "allow_memory_commands": False,
        "smart_model_router": bool(cfg["smart_model_router"]),
        "return_to_primary": route["return_to_primary"],
        "max_smart_models": route["max_smart_models"],
        "thinking_model_router": bool(
            st.session_state.get(
                "active_thinking_model_router",
                True,
            )
        ),
        "thinking_min_chars": int(
            st.session_state.get(
                "active_thinking_min_chars",
                thinking_min_chars_default,
            )
            or 180
        ),
        "thinking_capable_model": thinking_capable_model_override,
        "thinking_capable_models": active_expensive_models,
        "capable_models": active_expensive_models,
        "fast_normal_model_router": bool(
            st.session_state.get(
                "active_fast_normal_model_router",
                True,
            )
        ),
        "fastest_cheap_model": route.get(
            "fastest_cheap_primary_model",
            "",
        ),
        "fast_cheap_models": fast_cheap_models,
        "active_cheap_models": active_cheap_models,
        "active_expensive_models": active_expensive_models,
        "all_cheap_models": CHEAP_MODEL_OPTIONS,
        "all_expensive_models": EXPENSIVE_MODEL_OPTIONS,
        "all_model_candidates": unique_models(
            MODEL_OPTIONS
            + TOP_USAGE_MODEL_CANDIDATES
            + (st.session_state.get("dynamic_api_models") or [])
        ),
        "model_discovery_enabled": bool(model_discovery_enabled),
        "models_api_url": models_api_url,
        "model_discovery_timeout": int(model_discovery_timeout or 12),
        "model_health_timeout": int(model_health_timeout or 12),
        "model_health_workers": model_health_workers,
        "model_health_retries": model_health_retries,
        "model_health_midnight_only": bool(model_health_midnight_only),
        "model_health_hour_wib": int(model_health_hour_wib or 0),
        "model_health_window_minutes": int(model_health_window_minutes or 60),
        "power_features_enabled": bool(power_features_enabled),
        "power_db_path": power_db_path,
        "power_rag_enabled": bool(power_rag_enabled),
        "power_rag_top_k": int(power_rag_top_k),
        "power_strict_rag_mode": bool(power_strict_rag_mode),
        "power_anti_hallucination_enabled": bool(
            power_anti_hallucination_enabled
        ),
        "power_anti_hallucination_auto_strict": bool(
            power_anti_hallucination_auto_strict
        ),
        "power_anti_hallucination_min_sources": int(
            power_anti_hallucination_min_sources
        ),
        "power_anti_hallucination_min_quality": float(
            power_anti_hallucination_min_quality
        ),
        "power_anti_hallucination_min_freshness": float(
            power_anti_hallucination_min_freshness
        ),
        "power_anti_hallucination_append_sources": bool(
            power_anti_hallucination_append_sources
        ),
        "power_rag_min_sources": int(power_rag_min_sources),
        "power_rag_min_score": float(power_rag_min_score),
        "power_persistent_memory_enabled": bool(
            power_persistent_memory_enabled
        ),
        "power_prompt_templates_enabled": bool(
            power_prompt_templates_enabled
        ),
        "power_self_verification_enabled": bool(
            power_self_verification_enabled
        ),
        "power_quality_control_enabled": bool(
            power_quality_control_enabled
        ),
        "power_quality_verifier_enabled": bool(
            power_quality_verifier_enabled
        ),
        "power_quality_verifier_model": power_quality_verifier_model,
        "power_quality_min_score": float(power_quality_min_score),
        "power_quality_append_footer": bool(power_quality_append_footer),
        "power_hide_kb_sources_for_casual": bool(
            power_hide_kb_sources_for_casual
        ),
        "power_disable_rag_for_casual": bool(power_disable_rag_for_casual),
        "power_performance_optimizer_enabled": bool(
            power_performance_optimizer_enabled
        ),
        "power_query_rewriter_enabled": bool(power_query_rewriter_enabled),
        "power_reranker_enabled": bool(power_reranker_enabled),
        "power_semantic_cache_enabled": bool(power_semantic_cache_enabled),
        "power_semantic_cache_threshold": float(
            power_semantic_cache_threshold
        ),
        "power_semantic_cache_ttl_seconds": int(
            power_semantic_cache_ttl_seconds
        ),
        "power_latency_budget_enabled": bool(power_latency_budget_enabled),
        "power_retrieval_eval_enabled": bool(power_retrieval_eval_enabled),
        "live_music_chart_enabled": bool(live_music_chart_enabled),
        "live_music_chart_limit": int(live_music_chart_limit),
        "live_music_chart_timeout_seconds": int(
            live_music_chart_timeout_seconds
        ),
        "live_web_fallback_enabled": bool(live_web_fallback_enabled),
        "live_web_fallback_provider": live_web_fallback_provider,
        "tavily_api_key": tavily_api_key,
        "live_web_fallback_max_results": int(
            live_web_fallback_max_results
        ),
        "live_web_fallback_timeout_seconds": int(
            live_web_fallback_timeout_seconds
        ),
        "live_web_fallback_min_sources": int(
            live_web_fallback_min_sources
        ),
        "live_web_fallback_include_raw_content": bool(
            live_web_fallback_include_raw_content
        ),
        "live_web_fallback_max_content_chars": int(
            live_web_fallback_max_content_chars
        ),
        "live_web_fallback_auto_save_to_kb": bool(
            live_web_fallback_auto_save_to_kb
        ),
        "live_web_fallback_ttl_hours": int(live_web_fallback_ttl_hours),
        "live_web_fallback_force_for_current": bool(
            live_web_fallback_force_for_current
        ),
        "live_web_fallback_topic": live_web_fallback_topic,
        "auto_live_scraping_enabled": bool(auto_live_scraping_enabled),
        "auto_live_scraping_show_status": bool(auto_live_scraping_show_status),
        "power_default_answer_mode": power_default_answer_mode,
        "daily_cost_limit_idr": float(daily_cost_limit_idr),
        "max_expensive_calls_per_day": int(max_expensive_calls_per_day),
        "speed_update_code": telegram_speed_update_code,
        "model_circuit_max_failures": int(model_circuit_max_failures),
        "model_circuit_cooldown_seconds": int(
            model_circuit_cooldown_seconds
        ),
    }

    return config_payload


# =========================
# Runtime config
# =========================
def get_runtime_config() -> Dict[str, Any]:
    return {
        "api_url": api_url,
        "api_key": api_key,
        "model": st.session_state.active_model or default_model,
        "persona": st.session_state.active_persona or persona_from_secret,
        "temperature": float(st.session_state.active_temperature),
        "max_completion_tokens": int(st.session_state.active_max_tokens),
        "memory_file": memory_file,
        "telegram_token": telegram_token,
        "telegram_admin_chat_ids": telegram_admin_chat_ids,
        "github_actions_token": github_actions_token,
        "github_repo": github_repo,
        "github_workflow_file": github_workflow_file,
        "github_branch": github_branch,
        "github_update_source_limit": github_update_source_limit,
        "github_update_max_items": github_update_max_items,
        "allow_unrestricted_model_commands": bool(allow_unrestricted_model_commands),
        "smart_model_router": bool(st.session_state.active_smart_router),
        "return_to_primary": bool(st.session_state.active_return_to_primary),
        "max_smart_models": int(st.session_state.active_max_smart_models),
        "allow_expensive_fallback": bool(st.session_state.allow_expensive_fallback),
        "max_expensive_models": int(st.session_state.max_expensive_models),
        "default_memory_context": str(st.session_state.active_default_memory),
        "use_streamlit_cache_memory": bool(
            st.session_state.active_use_streamlit_cache_memory
        ),
        "thinking_model_router": bool(st.session_state.active_thinking_model_router),
        "fast_normal_model_router": bool(
            st.session_state.active_fast_normal_model_router
        ),
        "power_features_enabled": bool(power_features_enabled),
        "power_db_path": power_db_path,
        "power_rag_enabled": bool(power_rag_enabled),
        "power_rag_top_k": int(power_rag_top_k),
        "power_kb_max_file_mb": int(power_kb_max_file_mb),
        "power_persistent_memory_enabled": bool(power_persistent_memory_enabled),
        "power_prompt_templates_enabled": bool(power_prompt_templates_enabled),
        "power_self_verification_enabled": bool(power_self_verification_enabled),
        "power_quality_control_enabled": bool(power_quality_control_enabled),
        "power_quality_verifier_enabled": bool(power_quality_verifier_enabled),
        "power_quality_verifier_model": power_quality_verifier_model,
        "power_quality_min_score": float(power_quality_min_score),
        "power_quality_append_footer": bool(power_quality_append_footer),
        "power_default_answer_mode": power_default_answer_mode,
        "daily_cost_limit_idr": float(daily_cost_limit_idr),
        "max_expensive_calls_per_day": int(max_expensive_calls_per_day),
        "power_response_cache_enabled": bool(power_response_cache_enabled),
        "power_response_cache_ttl_seconds": int(power_response_cache_ttl_seconds),
        "power_adaptive_scoring_enabled": bool(power_adaptive_scoring_enabled),
        "power_circuit_breaker_enabled": bool(power_circuit_breaker_enabled),
        "model_circuit_max_failures": int(model_circuit_max_failures),
        "model_circuit_cooldown_seconds": int(model_circuit_cooldown_seconds),
        "operation_mode": str(
            st.session_state.get("active_operation_mode", ai_operation_mode_default)
            or "Seimbang"
        ),
        "maintenance_lock_file": maintenance_lock_file,
        "maintenance_access_key_file": maintenance_access_key_file,
        "maintenance_access_key_max_questions": maintenance_access_key_max_questions,
        "akses_terbatas_auto_on_boot": bool(akses_terbatas_auto_on_boot),
        "akses_terbatas_boot_guard_file": akses_terbatas_boot_guard_file,
        "akses_terbatas_boot_reason": akses_terbatas_boot_reason,
        "maintenance_message": maintenance_default_message,
        "maintenance_locked": bool(is_maintenance_locked()),
        "maintenance_auto_check_interval_seconds": maintenance_auto_check_interval_seconds,
        "maintenance_auto_refresh_enabled": bool(maintenance_auto_refresh_enabled),
        "maintenance_auto_refresh_interval_seconds": maintenance_auto_refresh_interval_seconds,
        "maintenance_auto_refresh_when_unlocked": bool(maintenance_auto_refresh_when_unlocked),
        "maintenance_hide_chat_when_locked": bool(maintenance_hide_chat_when_locked),
        "maintenance_fragment_enabled": bool(maintenance_fragment_enabled),
        "maintenance_browser_reload_enabled": bool(maintenance_browser_reload_enabled),
        "frontend_ultra_safe_mode": bool(frontend_ultra_safe_mode),
        "message_effects_enabled": bool(message_effects_enabled),
        "custom_components_enabled": bool(custom_components_enabled),
        "auto_scroll_enabled": bool(auto_scroll_enabled),
        "answer_sound_enabled": bool(answer_sound_enabled),
        "typewriter_enabled": bool(typewriter_enabled),
        "animated_loading_enabled": bool(animated_loading_enabled),
        "model_status_fragment_enabled": bool(model_status_fragment_enabled),
    }


def start_telegram_if_needed() -> None:
    cfg = get_runtime_config()

    if auto_start and telegram_token and api_key and not service.status()["running"]:
        route = build_model_routing_plan(advance_rotation=True)
        bot_config = build_telegram_config_payload(
            route=route,
            cfg=cfg,
            persona_text=persona_with_default_memory(cfg["persona"]),
        )
        service.start(bot_config)
        restore_active_model_to_cheap(route.get("primary_model"))



def render_admin_status() -> None:
    cfg = get_runtime_config()
    price = model_price(cfg["model"])
    last_meta = st.session_state.get("last_answer_meta", {}) or {}
    last_model = (
        last_meta.get("active_model_final")
        or last_meta.get("model_requested")
        or last_meta.get("model")
        or cfg["model"]
    )
    exp_used = "ya" if last_meta.get("expensive_fallback_used") else "tidak"
    telegram_verified = get_telegram_verified_status(force=False)
    telegram_status = telegram_verified_status_label(telegram_verified)
    health_cache = st.session_state.get("model_health_cache") or {}
    active_count = sum(1 for item in health_cache.values() if item.get("active"))
    admin_route_preview = build_model_routing_plan(user_text="halo")
    readiness = get_model_readiness_state(admin_route_preview)
    readiness_label = sanitize_model_readiness_text(
        readiness.get("label") or "Perlu cek model"
    )
    readiness_next_model = str(readiness.get("next_model") or cfg["model"])
    checked_at = "belum pernah"
    if st.session_state.get("model_health_checked_at"):
        checked_at = _timestamp_to_wib_text(st.session_state.model_health_checked_at)

    st.markdown("#### Status Sistem")
    st.caption("Chat publik aktif. Setting hanya untuk admin.")
    status_text = (
        f"Model utama: {cfg['model']}\n\n"
        f"Tier: {model_cost_tier(cfg['model'])} | Rp{price.get('input', 0):,}/Rp{price.get('output', 0):,}\n\n"
        f"Jawaban terakhir: {last_model}\n\n"
        f"Model mahal dipakai: {exp_used}\n\n"
        f"Telegram: {telegram_status}\n"
        f"Telegram tested: {telegram_verified.get('checked_at_text') or '-'}\n"
        f"Telegram detail: {telegram_verified.get('caption') or '-'}\n\n"
        f"Rotasi murah: {'ON' if st.session_state.get('active_rotate_cheap_primary', True) else 'OFF'}\n\n"
        f"Thinking router: {'ON' if st.session_state.get('active_thinking_model_router', True) else 'OFF'}\n\n"
        f"Fast normal: {'ON' if st.session_state.get('active_fast_normal_model_router', True) else 'OFF'}\n\n"
        f"Mode operasi: {st.session_state.get('active_operation_mode', ai_operation_mode_default)}\n\n"
        f"Status kesiapan nyata: {readiness_label}\n\n"
        f"Model route berikutnya: {readiness_next_model}\n\n"
        f"Model aktif terdeteksi: {active_count}\n\n"
        f"Cek model terakhir: {checked_at}\n\n"
        f"Auto-refresh status terakhir: {st.session_state.get('model_status_auto_refresh_last_text') or '-'}"
    ).replace(",", ".")
    st.info(status_text)

    with st.expander("💬 Status Telegram Terverifikasi", expanded=False):
        render_telegram_verified_status_card(
            force_button_key="telegram_verified_status_card_test_btn",
        )

    with st.expander("🛠️ Akses Terbatas", expanded=bool(is_maintenance_locked())):
        maintenance_state = read_maintenance_lock_state()
        locked_now = bool(maintenance_state.get("locked"))
        status_label = "Akses terbatas" if locked_now else "Akses dibuka"
        st.markdown(
            f"""
            <div class="maintenance-admin-card">
                <div class="maintenance-lock-icon">{"🛠️" if locked_now else "✅"}</div>
                <div>
                    <div class="maintenance-lock-title">{_html_escape(status_label)}</div>
                    <div class="maintenance-lock-text">
                        {"Chat publik dan Telegram non-admin sedang dikunci. Admin tetap dapat menggunakan Adioranye." if locked_now else "Chat publik dan Telegram dapat digunakan normal."}
                    </div>
                    <div class="maintenance-lock-meta">
                        Update: {_html_escape(maintenance_state.get("updated_at") or "-")} •
                        Oleh: {_html_escape(maintenance_state.get("updated_by") or "-")} •
                        Channel: {_html_escape(maintenance_state.get("channel") or "-")}
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        reason_value = st.text_input(
            "Catatan akses terbatas",
            value=str(maintenance_state.get("reason") or ""),
            placeholder="Contoh: update model, maintenance database, deploy fitur baru",
            key="maintenance_lock_reason",
        )
        col_lock, col_unlock = st.columns(2)
        with col_lock:
            if st.button(
                "🔒 Aktifkan akses terbatas",
                use_container_width=True,
                disabled=locked_now,
                key="maintenance_lock_button",
            ):
                set_maintenance_lock(
                    True,
                    updated_by=admin_username,
                    channel="web-admin",
                    reason=reason_value,
                )
                st.success("Akses terbatas aktif. Publik dan Telegram non-admin dikunci.")
                st.rerun()

        with col_unlock:
            if st.button(
                "🔓 Buka akses",
                use_container_width=True,
                disabled=not locked_now,
                key="maintenance_unlock_button",
            ):
                set_maintenance_lock(
                    False,
                    updated_by=admin_username,
                    channel="web-admin",
                    reason=reason_value,
                )
                st.success("Akses terbatas dibuka. Publik dan Telegram dapat digunakan lagi.")
                st.rerun()

        st.divider()
        st.markdown("##### 🔑 Access key akses terbatas")
        st.caption(
            f"User yang punya key tetap bisa chat saat akses terbatas aktif, maksimal {maintenance_access_key_max_questions} pertanyaan per key."
        )
        key_note = st.text_input(
            "Catatan key",
            value="",
            placeholder="Contoh: akses sementara untuk user A",
            key="maintenance_access_key_note",
        )
        col_key_a, col_key_b, col_key_c = st.columns(3)
        with col_key_a:
            if st.button("Generate access key", use_container_width=True, key="generate_maintenance_access_key_btn"):
                new_key_record = generate_maintenance_access_key(
                    note=key_note,
                    created_by=admin_username,
                    max_questions=maintenance_access_key_max_questions,
                )
                st.session_state.latest_maintenance_access_key = new_key_record.get("key", "")
                st.success("Access key berhasil dibuat.")
                st.rerun()
        summary = maintenance_access_key_summary()
        with col_key_b:
            st.metric("Key aktif", summary.get("active", 0))
        with col_key_c:
            st.metric("Key total", summary.get("total", 0))

        latest_key = str(st.session_state.get("latest_maintenance_access_key") or "")
        if latest_key:
            st.caption("Key terakhir dibuat:")
            st.code(latest_key)

        access_state = read_maintenance_access_key_state()
        active_records = []
        for record in (access_state.get("keys") or {}).values():
            if not isinstance(record, dict):
                continue
            max_uses = int(record.get("max_uses") or maintenance_access_key_max_questions or 5)
            used = int(record.get("used") or 0)
            if bool(record.get("active", True)) and used < max_uses:
                active_records.append(record)

        if active_records:
            with st.expander("Daftar access key aktif", expanded=False):
                for idx, record in enumerate(sorted(active_records, key=lambda item: str(item.get("created_at") or ""), reverse=True)[:20]):
                    key = str(record.get("key") or "")
                    used = int(record.get("used") or 0)
                    max_uses = int(record.get("max_uses") or maintenance_access_key_max_questions or 5)
                    note = str(record.get("note") or "")
                    st.markdown(f"**{_html_escape(key)}** — {used}/{max_uses} terpakai" + (f" — {_html_escape(note)}" if note else ""))
                    if st.button("Nonaktifkan", key=f"revoke_maintenance_key_{idx}_{key}", use_container_width=True):
                        revoke_maintenance_access_key(key, revoked_by=admin_username)
                        st.success(f"Key {key} dinonaktifkan.")
                        st.rerun()
        else:
            st.caption("Belum ada access key aktif.")

        st.caption(
            "Saat akses terbatas aktif, hanya admin web, chat ID Telegram admin, dan user dengan access key yang dapat menggunakan Adioranye."
        )
        st.caption(
            f"Auto akses terbatas setelah server reboot: {'ON' if akses_terbatas_auto_on_boot else 'OFF'}."
        )
        st.caption(
            f"Frontend ultra-safe: {'ON' if frontend_ultra_safe_mode else 'OFF'}; "
            f"message effects: {'ON' if message_effects_enabled else 'OFF'}; "
            f"sound: {'ON' if answer_sound_enabled else 'OFF'}; "
            f"auto-scroll: {'ON' if auto_scroll_enabled else 'OFF'}; "
            f"refresh lock: {'ON' if maintenance_auto_refresh_enabled else 'OFF'}."
        )

    with st.expander("Cache pertanyaan sering muncul"):
        cache_stats = frequent_question_cache_stats()
        col_cache_a, col_cache_b, col_cache_c, col_cache_d = st.columns(4)

        col_cache_a.metric(
            "Cache aktif",
            cache_stats.get("active", 0),
        )
        col_cache_b.metric(
            "Expired",
            cache_stats.get("expired", 0),
        )
        col_cache_c.metric(
            "Total",
            cache_stats.get("total", 0),
        )
        col_cache_d.metric(
            "Total hit",
            cache_stats.get("total_hits", 0),
        )

        rows = export_frequent_question_cache_rows()

        if rows:
            st.dataframe(
                rows[:20],
                use_container_width=True,
                hide_index=True,
            )

        col_cache_reset, col_cache_export = st.columns(2)

        with col_cache_reset:
            if st.button(
                "🧹 Reset cache pertanyaan",
                use_container_width=True,
                key="frequent_question_cache_reset",
            ):
                deleted = clear_frequent_question_cache()
                st.success(f"Cache pertanyaan dihapus: {deleted} item.")
                st.rerun()

        with col_cache_export:
            st.download_button(
                "⬇️ Export cache pertanyaan",
                data=json.dumps(
                    rows,
                    ensure_ascii=False,
                    indent=2,
                ),
                file_name="frequent_question_cache.json",
                mime="application/json",
                use_container_width=True,
                key="frequent_question_cache_export",
            )

    with st.expander("Retry otomatis jika model gangguan"):
        col_retry_a, col_retry_b, col_retry_c = st.columns(3)
        col_retry_a.metric(
            "Retry error model",
            "ON"
            if parse_bool(
                get_secret("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", True),
                default=True,
            )
            else "OFF",
        )
        col_retry_b.metric(
            "Max percobaan",
            int(get_secret("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", 2) or 2),
        )
        col_retry_c.metric(
            "Timeout retry",
            f"{int(get_secret('AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS', 35) or 35)} detik",
        )
        st.caption(
            "Jika jawaban awal adalah pesan gangguan koneksi/model, sistem akan mencoba ulang "
            "pertanyaan yang sama memakai model aktif lain dari hasil health check."
        )

    with st.expander("Auto-refresh status model"):
        col_auto_a, col_auto_b, col_auto_c = st.columns(3)
        col_auto_a.metric(
            "Auto-refresh",
            "ON" if model_status_auto_refresh_enabled else "OFF",
        )
        col_auto_b.metric(
            "Interval",
            f"{int(model_status_auto_refresh_interval_seconds or 90)} detik",
        )
        col_auto_c.metric(
            "Terakhir",
            st.session_state.get("model_status_auto_refresh_last_text") or "-",
        )
        st.caption(
            "Auto-refresh hanya menjalankan quick health check berkala dan menyimpan hasilnya. "
            "Tidak memaksa reload halaman, sehingga chat tetap aman."
        )
        if st.button(
            "🔄 Jalankan auto-refresh status sekarang",
            use_container_width=True,
            key="manual_auto_refresh_model_status_now",
        ):
            result = maybe_auto_refresh_model_status(
                reason="admin-manual-auto-refresh",
            )
            if result.get("ran"):
                st.success(
                    f"Auto-refresh selesai dalam {result.get('duration_ms', 0)} ms."
                )
            else:
                st.info(f"Auto-refresh tidak dijalankan: {result.get('reason')}")
            st.rerun()

    with st.expander("Kontrol status model"):
        st.caption(
            "Health check hemat token aktif: prompt pendek, output kecil, retry default 0, dan cache dipertahankan."
        )
        col_health_saver_a, col_health_saver_b, col_health_saver_c, col_health_saver_d = st.columns(4)
        col_health_saver_a.metric("Probe token", int(model_health_probe_max_tokens or 2))
        col_health_saver_b.metric("GPT probe token", int(model_health_probe_gpt5_max_tokens or 8))
        col_health_saver_c.metric("Quick limit", int(model_health_quick_limit or 6))
        col_health_saver_d.metric("Retry", int(model_health_retries or 0))

        col_ready_a, col_ready_b = st.columns(2)
        with col_ready_a:
            if st.button(
                "🔁 Cek model sekarang",
                use_container_width=True,
                key="real_status_quick_check_now",
            ):
                refresh_model_health_if_needed(
                    force=True,
                    scope="quick",
                )
                st.success("Quick health check selesai dan status disimpan.")
                st.rerun()
        with col_ready_b:
            if st.button(
                "🧽 Reset status model tersimpan",
                use_container_width=True,
                key="real_status_clear_saved",
            ):
                st.session_state.model_health_cache = {}
                st.session_state.model_health_checked_at = 0.0
                try:
                    if os.path.exists(model_readiness_state_file):
                        os.remove(model_readiness_state_file)
                except Exception:
                    pass
                st.success("Status model tersimpan direset.")
                st.rerun()

        if st.button(
            "✅ Cari dan jadikan model sehat sebagai utama",
            use_container_width=True,
            key="auto_promote_healthy_primary_now",
        ):
            result = apply_healthy_primary_model(
                reason="admin-manual-promote",
            )
            chosen = str(result.get("model") or "").strip()
            if chosen:
                st.success(f"Model utama sehat aktif sekarang: {chosen}")
                st.rerun()
            else:
                st.warning("Belum ada model sehat yang bisa dijadikan model utama.")


def render_mode_selector() -> None:
    st.markdown("#### Mode Operasional AI")
    current = str(
        st.session_state.get("active_operation_mode", ai_operation_mode_default)
        or "Seimbang"
    )
    options = ["Hemat", "Seimbang", "Maksimal"]
    if current not in options:
        current = "Seimbang"
    selected = st.radio(
        "Pilih mode",
        options,
        index=options.index(current),
        horizontal=True,
        help="Hemat menahan model mahal. Seimbang murah dulu lalu mahal jika perlu. Maksimal lebih agresif memakai model capable.",
    )
    st.session_state.active_operation_mode = selected
    if selected == "Hemat":
        st.info(
            "Mode Hemat: memprioritaskan model murah/cepat dan menahan fallback menengah/mahal."
        )
    elif selected == "Maksimal":
        st.warning(
            "Mode Maksimal: lebih cepat memakai model capable. Biaya bisa lebih tinggi."
        )
    else:
        st.success(
            "Mode Seimbang: murah dulu, naik ke model capable hanya jika diperlukan."
        )


def render_secrets_validator_panel() -> None:
    st.markdown("#### Validator Secrets")
    st.caption(
        "Panel ini membantu mengecek konfigurasi tanpa menampilkan token/API key penuh."
    )
    rows = validate_runtime_secrets()
    st.dataframe(rows, use_container_width=True, hide_index=True)
    required_missing = [r for r in rows if r["status"].startswith("❌")]
    warnings = [r for r in rows if r["status"].startswith("⚠️")]
    if required_missing:
        st.error(
            "Ada secret wajib yang belum terisi. Chat/model bisa gagal sampai ini diperbaiki."
        )
    elif warnings:
        st.warning(
            "Konfigurasi utama aman, tetapi ada beberapa saran keamanan/operasional."
        )
    else:
        st.success("Konfigurasi utama terlihat aman.")

    st.markdown("#### Dependency Knowledge Base")
    deps = [
        {
            "fitur": "PDF",
            "module": "pypdf",
            "status": (
                "✅ tersedia" if check_optional_dependency("pypdf") else "⚠️ belum ada"
            ),
        },
        {
            "fitur": "DOCX",
            "module": "docx",
            "status": (
                "✅ tersedia" if check_optional_dependency("docx") else "⚠️ belum ada"
            ),
        },
        {
            "fitur": "XLSX",
            "module": "openpyxl",
            "status": (
                "✅ tersedia"
                if check_optional_dependency("openpyxl")
                else "⚠️ belum ada"
            ),
        },
        {
            "fitur": "DataFrame",
            "module": "pandas",
            "status": (
                "✅ tersedia" if check_optional_dependency("pandas") else "⚠️ belum ada"
            ),
        },
    ]
    st.dataframe(deps, use_container_width=True, hide_index=True)


def render_ai_health_center() -> None:
    st.markdown("#### AI Health Center")
    route = build_model_routing_plan(user_text="halo")
    status = service.status()
    usage = {}
    db_info = {}
    try:
        usage = power_store.usage_summary(days=1) if power_features_enabled else {}
        db_info = power_store.database_overview() if power_features_enabled else {}
    except Exception as exc:
        st.warning(f"Power Features belum bisa dibaca: {exc}")
    health_cache = st.session_state.get("model_health_cache") or {}
    active_total = sum(1 for item in health_cache.values() if item.get("active"))
    checked_at = (
        _timestamp_to_wib_text(st.session_state.model_health_checked_at)
        if st.session_state.get("model_health_checked_at")
        else "belum pernah"
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Mode", route.get("operation_mode", "Seimbang"))
    c2.metric("Model aktif terdeteksi", active_total)
    c3.metric("Request 24 jam", int((usage or {}).get("requests") or 0))
    c4.metric("Biaya 24 jam", f"Rp{float((usage or {}).get('cost_idr') or 0):.2f}")

    st.markdown("##### Status Ringkas")
    rows = [
        {
            "komponen": "SlashAI API Key",
            "status": "✅ siap" if api_key else "❌ kosong",
            "detail": mask_secret_value(api_key),
        },
        {
            "komponen": "Telegram",
            "status": "✅ running" if status.get("running") else "⚪ off",
            "detail": status.get("last_error") or status.get("worker_id") or "-",
        },
        {
            "komponen": "Primary berikutnya",
            "status": route.get("primary_model", "-"),
            "detail": model_price_label(route.get("primary_model", "")),
        },
        {
            "komponen": "Model murah aktif",
            "status": str(len(route.get("active_cheap_models") or [])),
            "detail": ", ".join((route.get("active_cheap_models") or [])[:3]),
        },
        {
            "komponen": "Model capable aktif",
            "status": str(len(route.get("active_expensive_models") or [])),
            "detail": ", ".join((route.get("active_expensive_models") or [])[:3]),
        },
        {
            "komponen": "Cek model terakhir",
            "status": checked_at,
            "detail": st.session_state.get("last_model_health_error", "") or "-",
        },
        {
            "komponen": "Knowledge Base",
            "status": f"{db_info.get('documents', 0)} dokumen" if db_info else "-",
            "detail": f"{db_info.get('chunks', 0)} chunks | {db_info.get('db_size', '-') if db_info else '-'}",
        },
        {
            "komponen": "Response Cache",
            "status": f"{db_info.get('response_cache', 0)} item" if db_info else "-",
            "detail": "SQLite persistent cache",
        },
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button(
            "🔁 Cek model sekarang",
            use_container_width=True,
            disabled=False,
            key="auto_btn_2532",
        ):
            refresh_model_health_if_needed(force=True, scope="quick")
            st.success("Quick health check selesai.")
            st.rerun()
    with col_b:
        if st.button(
            "🧪 Tes jawaban cepat",
            use_container_width=True,
            disabled=not bool(api_key),
            key="auto_btn_2537",
        ):
            try:
                test_route = build_model_routing_plan(
                    advance_rotation=True, user_text="halo"
                )
                ans, meta = generate_answer(
                    api_url=api_url,
                    api_key=api_key,
                    model=test_route["primary_model"],
                    system_prompt=st.session_state.active_persona,
                    user_text="Jawab satu kata: aktif",
                    memory_text="",
                    recent_messages=[],
                    fallback_models=test_route["cheap_fallback_models"],
                    expensive_fallback_models=test_route["expensive_fallback_models"],
                    allow_expensive_fallback=test_route["allow_expensive_fallback"],
                    max_expensive_models=test_route["max_expensive_models"],
                    temperature=0,
                    max_completion_tokens=120,
                    timeout=30,
                    smart_model_router=True,
                    return_to_primary=test_route["return_to_primary"],
                    max_smart_models=test_route["max_smart_models"],
                )
                st.success(f"Tes berhasil: {ans[:160]}")
                st.caption(
                    f"Model: {(meta or {}).get('active_model_final') or (meta or {}).get('model_requested') or test_route['primary_model']}"
                )
            except Exception as exc:
                st.error(f"Tes gagal: {exc}")


def render_maintenance_tools() -> None:
    st.markdown("#### Backup, Restore, dan Perawatan")
    db_path = str(power_db_path or ".adioranye_power.db")
    st.caption(f"Database power: {db_path} | ukuran: {file_size_label(db_path)}")

    db_bytes = read_file_bytes_safe(db_path)
    st.download_button(
        "⬇️ Download backup database SQLite",
        data=db_bytes or b"",
        file_name=f"adioranye-power-backup-{datetime.now(WIB_TZ).strftime('%Y%m%d-%H%M%S')}.db",
        mime="application/octet-stream",
        use_container_width=True,
        disabled=not bool(db_bytes),
    )

    uploaded_db = st.file_uploader(
        "Restore database dari file .db",
        type=["db", "sqlite", "sqlite3"],
        key="restore_power_db",
    )
    confirm_restore = st.checkbox(
        "Saya paham restore akan menimpa database power saat ini",
        key="confirm_restore_power_db",
    )
    if st.button(
        "♻️ Restore database",
        use_container_width=True,
        disabled=not bool(uploaded_db and confirm_restore),
        key="auto_btn_2582",
    ):
        try:
            backup_path = db_path + f".before-restore-{int(time.time())}.bak"
            if os.path.exists(db_path):
                shutil.copyfile(db_path, backup_path)
            with open(db_path, "wb") as f:
                f.write(uploaded_db.read())
            st.success(f"Restore berhasil. Backup database lama: {backup_path}")
            st.rerun()
        except Exception as exc:
            st.error(f"Restore gagal: {exc}")

    st.markdown("#### Auto-clean data lama")
    c1, c2, c3 = st.columns(3)
    with c1:
        log_days = st.number_input(
            "Simpan usage log (hari)", 1, 365, int(power_log_retention_days), 1
        )
    with c2:
        cache_days = st.number_input(
            "Simpan response cache (hari)", 1, 90, int(power_cache_retention_days), 1
        )
    with c3:
        bench_days = st.number_input(
            "Simpan benchmark (hari)", 1, 180, int(power_benchmark_retention_days), 1
        )
    if st.button(
        "🧹 Bersihkan data lama", use_container_width=True, key="auto_btn_2602"
    ):
        try:
            deleted = power_store.cleanup_old_data(
                int(log_days), int(cache_days), int(bench_days)
            )
            st.success(f"Cleanup selesai: {deleted}")
        except Exception as exc:
            st.error(f"Cleanup gagal: {exc}")

    st.markdown("#### Reset terarah")
    confirm_reset = st.checkbox(
        "Aktifkan tombol reset berisiko", key="confirm_dangerous_resets"
    )
    r1, r2, r3, r4 = st.columns(4)
    with r1:
        if st.button(
            "Reset usage",
            use_container_width=True,
            disabled=not confirm_reset,
            key="auto_btn_2613",
        ):
            st.warning(f"Usage dihapus: {power_store.clear_usage_logs()}")
            st.rerun()
    with r2:
        if st.button(
            "Reset cache",
            use_container_width=True,
            disabled=not confirm_reset,
            key="auto_btn_2617",
        ):
            st.warning(f"Cache dihapus: {power_store.clear_response_cache()}")
            st.rerun()
    with r3:
        if st.button(
            "Reset KB",
            use_container_width=True,
            disabled=not confirm_reset,
            key="auto_btn_2621",
        ):
            st.warning(f"Knowledge base dihapus: {power_store.clear_knowledge_base()}")
            st.rerun()
    with r4:
        if st.button(
            "Reset memory",
            use_container_width=True,
            disabled=not confirm_reset,
            key="auto_btn_2625",
        ):
            st.warning(f"Memory permanen dihapus: {power_store.clear_memories_all()}")
            st.rerun()




def render_admin_custom_css() -> None:
    """CSS khusus agar halaman admin lebih rapi, konsisten light/dark, dan mobile-friendly."""
    st.markdown(
        """
        <style>
        .admin-page-shell {
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }

        .admin-route-bar {
            margin-bottom: 0.75rem !important;
        }

        .admin-route-hero {
            position: relative;
            overflow: hidden;
            align-items: stretch !important;
            gap: 1rem !important;
            margin-bottom: 0.9rem !important;
            border: 1px solid var(--ui-border, rgba(120,120,128,0.24)) !important;
            box-shadow: 0 18px 48px rgba(15,23,42,0.10) !important;
        }

        .admin-route-hero::after {
            content: "";
            position: absolute;
            inset: auto -12% -50% 36%;
            height: 140px;
            pointer-events: none;
            background:
                radial-gradient(circle at 30% 50%, rgba(10,132,255,0.20), transparent 34%),
                radial-gradient(circle at 70% 55%, rgba(52,199,89,0.16), transparent 32%);
            filter: blur(18px);
            opacity: 0.72;
        }

        .admin-hero-copy {
            position: relative;
            z-index: 1;
            flex: 1;
            min-width: 0;
        }

        .admin-hero-kicker {
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.22rem 0.52rem;
            margin-bottom: 0.52rem;
            border-radius: 999px;
            border: 1px solid rgba(10,132,255,0.24);
            background: rgba(10,132,255,0.10);
            color: var(--ui-text, inherit);
            font-size: 0.72rem;
            font-weight: 850;
        }

        .admin-hero-actions {
            position: relative;
            z-index: 1;
            display: flex;
            flex-wrap: wrap;
            align-content: center;
            justify-content: flex-end;
            gap: 0.5rem;
            min-width: 190px;
        }

        .admin-pill {
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            padding: 0.34rem 0.62rem;
            border-radius: 999px;
            border: 1px solid var(--ui-border, rgba(120,120,128,0.24));
            background: var(--ui-surface, rgba(255,255,255,0.55));
            color: var(--ui-text, inherit);
            font-size: 0.74rem;
            font-weight: 800;
            white-space: nowrap;
            backdrop-filter: blur(14px);
            -webkit-backdrop-filter: blur(14px);
        }

        .admin-overview-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.72rem;
            margin: 0.8rem 0 0.75rem;
        }

        .admin-overview-card,
        .admin-login-card,
        .admin-section-card {
            border: 1px solid var(--ui-border, rgba(120,120,128,0.24));
            border-radius: 22px;
            background:
                radial-gradient(circle at 18% 12%, rgba(255,255,255,0.30), transparent 28%),
                var(--ui-surface, rgba(255,255,255,0.54));
            color: var(--ui-text, inherit);
            box-shadow: 0 14px 36px rgba(15,23,42,0.08);
            backdrop-filter: blur(18px);
            -webkit-backdrop-filter: blur(18px);
        }

        .admin-overview-card {
            padding: 0.86rem 0.92rem;
            min-height: 116px;
        }

        .admin-overview-label {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.5rem;
            margin-bottom: 0.55rem;
            color: var(--ui-muted, rgba(100,116,139,0.92));
            font-size: 0.76rem;
            font-weight: 820;
        }

        .admin-overview-value {
            color: var(--ui-text-strong, var(--ui-text, inherit));
            font-size: 1.05rem;
            line-height: 1.2;
            font-weight: 920;
            word-break: break-word;
        }

        .admin-overview-caption {
            margin-top: 0.44rem;
            color: var(--ui-muted, rgba(100,116,139,0.92));
            font-size: 0.72rem;
            line-height: 1.35;
        }

        .admin-section-card {
            padding: 0.95rem;
            margin: 0.65rem 0 0.85rem;
        }

        .admin-section-title {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            margin: 0 0 0.32rem;
            color: var(--ui-text-strong, var(--ui-text, inherit));
            font-size: 1rem;
            font-weight: 900;
        }

        .admin-section-desc {
            margin: 0;
            color: var(--ui-muted, rgba(100,116,139,0.92));
            font-size: 0.82rem;
            line-height: 1.45;
        }

        .admin-login-card {
            max-width: 520px;
            margin: 1.05rem auto 0.7rem;
            padding: 1.25rem;
        }

        .admin-login-title {
            display: flex;
            align-items: center;
            gap: 0.55rem;
            margin-bottom: 0.35rem;
            color: var(--ui-text-strong, var(--ui-text, inherit));
            font-size: 1.14rem;
            font-weight: 930;
        }

        .admin-login-subtitle {
            color: var(--ui-muted, rgba(100,116,139,0.92));
            font-size: 0.84rem;
            margin-bottom: 0.9rem;
            line-height: 1.45;
        }

        div[data-testid="stTabs"] [role="tablist"] {
            gap: 0.42rem;
            padding: 0.28rem;
            border-radius: 18px;
            border: 1px solid var(--ui-border, rgba(120,120,128,0.22));
            background: rgba(120,120,128,0.08);
            overflow-x: auto;
        }

        div[data-testid="stTabs"] button[role="tab"] {
            min-height: 38px;
            border-radius: 14px !important;
            padding: 0.42rem 0.72rem !important;
            color: var(--ui-muted, rgba(100,116,139,0.95)) !important;
            font-weight: 850 !important;
        }

        div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
            background:
                linear-gradient(135deg, rgba(10,132,255,0.16), rgba(52,199,89,0.11)) !important;
            color: var(--ui-text-strong, inherit) !important;
            box-shadow: inset 0 0 0 1px rgba(10,132,255,0.22);
        }

        div[data-testid="stExpander"] {
            border-radius: 18px !important;
            border: 1px solid var(--ui-border, rgba(120,120,128,0.22)) !important;
            overflow: hidden !important;
            background: var(--ui-surface, rgba(255,255,255,0.48)) !important;
            box-shadow: 0 8px 26px rgba(15,23,42,0.05);
        }

        div[data-testid="stMetric"] {
            border-radius: 18px;
            padding: 0.62rem 0.68rem;
            border: 1px solid var(--ui-border, rgba(120,120,128,0.18));
            background: rgba(120,120,128,0.07);
        }

        div[data-testid="stButton"] button,
        div[data-testid="stDownloadButton"] button,
        button[kind="primary"],
        button[kind="secondary"] {
            border-radius: 14px !important;
            min-height: 39px !important;
            font-weight: 850 !important;
        }

        .admin-divider-soft {
            height: 1px;
            margin: 0.85rem 0;
            background: linear-gradient(90deg, transparent, var(--ui-border, rgba(120,120,128,0.28)), transparent);
        }

        .maintenance-lock-banner,
        .maintenance-admin-card {
            display: flex;
            gap: 0.72rem;
            align-items: flex-start;
            padding: 0.92rem 1rem;
            margin: 0.72rem 0 0.9rem;
            border-radius: 22px;
            border: 1px solid rgba(255,149,0,0.28);
            background:
                radial-gradient(circle at 12% 20%, rgba(255,204,0,0.22), transparent 28%),
                linear-gradient(135deg, rgba(255,149,0,0.14), rgba(120,120,128,0.08));
            color: var(--ui-text, inherit);
            box-shadow: 0 14px 34px rgba(255,149,0,0.08);
        }

        .maintenance-lock-icon {
            width: 42px;
            height: 42px;
            min-width: 42px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 16px;
            background: rgba(255,149,0,0.18);
            font-size: 1.15rem;
        }

        .maintenance-lock-title {
            color: var(--ui-text-strong, inherit);
            font-size: 1rem;
            font-weight: 940;
            margin-bottom: 0.2rem;
        }

        .maintenance-lock-text,
        .maintenance-lock-meta {
            color: var(--ui-muted, rgba(100,116,139,0.92));
            font-size: 0.82rem;
            line-height: 1.45;
        }

        .maintenance-lock-meta {
            margin-top: 0.36rem;
            font-size: 0.74rem;
            font-weight: 760;
        }

        .maintenance-refresh-note {
            display: inline-flex;
            align-items: center;
            gap: 0.42rem;
            margin: 0.35rem 0 0.75rem;
            padding: 0.36rem 0.62rem;
            border-radius: 999px;
            border: 1px solid var(--ui-border, rgba(120,120,128,0.22));
            background: rgba(120,120,128,0.08);
            color: var(--ui-muted, rgba(100,116,139,0.92));
            font-size: 0.73rem;
            font-weight: 780;
        }

        .maintenance-live-dot {
            display: inline-block;
            width: 7px;
            height: 7px;
            margin-right: 0.35rem;
            border-radius: 999px;
            background: #ff9500;
            box-shadow: 0 0 10px rgba(255,149,0,0.68);
            animation: onlinePulse 1.6s ease-in-out infinite;
        }

        @media (max-width: 980px) {
            .admin-overview-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }

            .admin-route-hero {
                flex-direction: column !important;
            }

            .admin-hero-actions {
                justify-content: flex-start;
            }
        }

        @media (max-width: 620px) {
            .admin-overview-grid {
                grid-template-columns: 1fr;
            }

            .admin-overview-card {
                min-height: unset;
            }

            .admin-login-card {
                margin: 0.6rem 0 0.7rem;
                padding: 1rem;
            }

            div[data-testid="stTabs"] [role="tablist"] {
                flex-wrap: nowrap;
            }

            div[data-testid="stTabs"] button[role="tab"] {
                min-width: max-content;
                font-size: 0.80rem !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _admin_status_badge(value: Any) -> str:
    return _html_escape(str(value or "-"))



def get_telegram_verified_status(
    force: bool = False,
) -> Dict[str, Any]:
    """Cek status Telegram dari API, bukan hanya status worker lokal.

    Menggunakan service.diagnose() yang memanggil getMe + getWebhookInfo.
    Hasil dicache singkat agar halaman admin tidak spam API setiap rerun.
    """
    now = time.time()
    ttl = max(5, int(telegram_status_test_cache_ttl_seconds or 60))
    cached = st.session_state.get("telegram_verified_status_cache") or {}

    if (
        not force
        and isinstance(cached, dict)
        and cached.get("checked_at_ts")
        and now - float(cached.get("checked_at_ts") or 0) < ttl
    ):
        return cached

    local_status = service.status()
    result: Dict[str, Any] = {
        "ok": False,
        "api_ok": False,
        "running": bool(local_status.get("running")),
        "label": "TOKEN BELUM DIISI",
        "caption": "TELEGRAM_BOT_TOKEN belum diisi.",
        "bot_username": "",
        "bot_id": "",
        "webhook_url": "",
        "pending_update_count": None,
        "last_error": "",
        "checked_at_ts": now,
        "checked_at_text": _wib_now_text(),
        "source": "diagnose",
    }

    if not str(telegram_token or "").strip():
        st.session_state.telegram_verified_status_cache = result
        return result

    try:
        diag_config = {
            "telegram_token": telegram_token,
            "telegram_status_test_timeout_seconds": int(
                telegram_status_test_timeout_seconds or 12
            ),
        }
        diag = service.diagnose(diag_config)

        result.update(
            {
                "ok": bool(diag.get("ok")),
                "api_ok": bool(diag.get("ok")),
                "running": bool(service.status().get("running")),
                "bot_username": str(diag.get("bot_username") or ""),
                "bot_id": str(diag.get("bot_id") or ""),
                "webhook_url": str(diag.get("webhook_url") or ""),
                "pending_update_count": diag.get("pending_update_count"),
                "last_error": str(diag.get("last_error") or ""),
            }
        )

        if result["api_ok"] and result["running"]:
            result["label"] = "API OK + WORKER ON"
        elif result["api_ok"]:
            result["label"] = "API OK / WORKER OFF"
        else:
            result["label"] = "API ERROR"

        bot_label = (
            f"@{result['bot_username']}"
            if result.get("bot_username")
            else "bot terdeteksi"
            if result.get("api_ok")
            else "bot belum terverifikasi"
        )
        worker_label = "worker berjalan" if result.get("running") else "worker mati"
        pending_label = (
            f"pending {result.get('pending_update_count')}"
            if result.get("pending_update_count") is not None
            else "pending -"
        )

        result["caption"] = (
            f"{bot_label} • {worker_label} • {pending_label} • "
            f"tes: {result['checked_at_text']}"
        )

        if result.get("webhook_url"):
            result["caption"] += " • webhook aktif"

        if not result["api_ok"] and result.get("last_error"):
            result["caption"] = f"API gagal • {result['last_error'][:160]}"

    except Exception as exc:
        result["ok"] = False
        result["api_ok"] = False
        result["running"] = bool(service.status().get("running"))
        result["label"] = "TEST ERROR"
        result["last_error"] = str(exc)[:1200]
        result["caption"] = f"Test Telegram gagal • {result['last_error'][:160]}"

    st.session_state.telegram_verified_status_cache = result
    return result


def telegram_verified_status_label(
    status: Dict[str, Any] | None = None,
) -> str:
    status = status or get_telegram_verified_status(force=False)
    return str(status.get("label") or "UNKNOWN")


def render_telegram_verified_status_card(
    force_button_key: str = "telegram_verified_status_test_btn",
) -> Dict[str, Any]:
    """Render kartu test Telegram terverifikasi di admin."""
    status = get_telegram_verified_status(force=False)
    label = telegram_verified_status_label(status)
    api_ok = bool(status.get("api_ok"))
    running = bool(status.get("running"))

    if api_ok and running:
        st.success(f"Telegram terverifikasi: {label}")
    elif api_ok:
        st.warning(f"Telegram API OK, tetapi worker belum berjalan: {label}")
    else:
        st.error(f"Telegram belum terverifikasi: {label}")

    st.caption(str(status.get("caption") or "-"))

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("API Telegram", "OK" if api_ok else "ERROR")
    col_b.metric("Worker", "ON" if running else "OFF")
    col_c.metric(
        "Pending",
        status.get("pending_update_count")
        if status.get("pending_update_count") is not None
        else "-",
    )

    if status.get("bot_username"):
        st.caption(f"Bot: @{status.get('bot_username')} | ID: {status.get('bot_id') or '-'}")

    if status.get("webhook_url"):
        st.warning(
            "Webhook masih aktif. Untuk mode polling, klik Reset koneksi Telegram agar webhook dihapus."
        )
        st.caption(f"Webhook: {status.get('webhook_url')}")

    if status.get("last_error") and not api_ok:
        with st.expander("Detail error test Telegram"):
            st.code(str(status.get("last_error"))[:3000])

    if st.button(
        "🧪 Test ulang status Telegram",
        use_container_width=True,
        key=force_button_key,
    ):
        refreshed = get_telegram_verified_status(force=True)
        if refreshed.get("api_ok"):
            bot_user = refreshed.get("bot_username") or "tidak diketahui"
            st.success(f"Test Telegram OK. Bot: @{bot_user}")
        else:
            st.error("Test Telegram gagal.")
            st.code(str(refreshed.get("last_error") or "Tidak ada detail error.")[:3000])
        st.rerun()

    return status


def render_admin_overview_cards() -> None:
    """Ringkasan cepat admin tanpa mengganggu logika kontrol."""
    try:
        cfg = get_runtime_config()
    except Exception:
        cfg = {
            "model": st.session_state.get("active_model", default_model),
        }

    model_name = str(cfg.get("model") or st.session_state.get("active_model") or default_model)
    tier = model_cost_tier(model_name)
    telegram_verified = get_telegram_verified_status(force=False)
    telegram_status = telegram_verified_status_label(telegram_verified)
    telegram_caption = str(
        telegram_verified.get("caption")
        or "Status Telegram dites dari API getMe/getWebhookInfo."
    )

    try:
        health_cache = st.session_state.get("model_health_cache") or {}
        active_count = sum(
            1
            for item in health_cache.values()
            if isinstance(item, dict) and item.get("active")
        )
    except Exception:
        active_count = 0

    try:
        cache_stats = frequent_question_cache_stats()
        cache_value = cache_stats.get("active", 0)
        cache_caption = f"{cache_stats.get('total_hits', 0)} hit total"
    except Exception:
        cache_value = "-"
        cache_caption = "cache belum terbaca"

    checked_at = "-"
    if st.session_state.get("model_health_checked_at"):
        checked_at = _timestamp_to_wib_text(st.session_state.model_health_checked_at)

    st.markdown(
        f"""
        <div class="admin-overview-grid">
            <div class="admin-overview-card">
                <div class="admin-overview-label"><span>🤖 Model aktif</span><span>{_admin_status_badge(tier)}</span></div>
                <div class="admin-overview-value">{_html_escape(model_name)}</div>
                <div class="admin-overview-caption">Dipakai sebagai model utama/routing awal.</div>
            </div>
            <div class="admin-overview-card">
                <div class="admin-overview-label"><span>✅ Model sehat</span><span>health</span></div>
                <div class="admin-overview-value">{_html_escape(active_count)}</div>
                <div class="admin-overview-caption">Cek terakhir: {_html_escape(checked_at)}</div>
            </div>
            <div class="admin-overview-card">
                <div class="admin-overview-label"><span>💬 Telegram</span><span>tested</span></div>
                <div class="admin-overview-value">{_html_escape(telegram_status)}</div>
                <div class="admin-overview-caption">{_html_escape(telegram_caption)}</div>
            </div>
            <div class="admin-overview-card">
                <div class="admin-overview-label"><span>⚡ Cache</span><span>FAQ</span></div>
                <div class="admin-overview-value">{_html_escape(cache_value)}</div>
                <div class="admin-overview-caption">{_html_escape(cache_caption)}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )



def render_admin_login() -> None:
    """Render login admin untuk halaman /admin."""
    st.markdown(
        """
        <div class="admin-login-card">
            <div class="admin-login-title">🔐 Masuk Admin</div>
            <div class="admin-login-subtitle">
                Akses khusus untuk mengatur model, Telegram, Knowledge Base, live web, cache, dan maintenance.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.form(
        "admin_login_form",
        clear_on_submit=False,
    ):
        username = st.text_input(
            "Username",
            value="",
            placeholder="Masukkan username admin",
            key="admin_login_username",
        )

        password = st.text_input(
            "Password",
            value="",
            placeholder="Masukkan password admin",
            type="password",
            key="admin_login_password",
        )

        submitted = st.form_submit_button(
            "Masuk Admin",
            use_container_width=True,
        )

    if submitted:
        valid_username = safe_compare(
            username,
            admin_username,
        )
        valid_password = safe_compare(
            password,
            admin_password,
        )

        if valid_username and valid_password:
            st.session_state.admin_authenticated = True
            st.success("Login berhasil. Membuka panel admin...")
            st.rerun()
        else:
            st.session_state.admin_authenticated = False
            st.error("Username atau password admin salah.")

    st.caption(
        "Jika lupa password, ubah ADMIN_USERNAME dan ADMIN_PASSWORD di Streamlit Secrets, lalu reboot app."
    )

def render_admin_settings() -> None:
    st.markdown(
        f"""
        <div class="admin-section-card">
            <div class="admin-section-title">⚙️ Pusat Kontrol Admin</div>
            <p class="admin-section-desc">
                Login sebagai <strong>{_html_escape(admin_username)}</strong>. Atur model, Telegram, memory, Knowledge Base,
                live web, cache, optimizer, dan maintenance dari panel ini.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_admin_overview_cards()
    render_admin_status()

    logout_col, spacer_col = st.columns([1, 3])
    with logout_col:
        if st.button("🚪 Logout Admin", use_container_width=True, key="auto_btn_2646"):
            st.session_state.admin_authenticated = False
            st.rerun()

    st.markdown('<div class="admin-divider-soft"></div>', unsafe_allow_html=True)

    tab_ai, tab_bot, tab_memory, tab_health, tab_maint, tab_setup = st.tabs(
        ["🤖 AI", "💬 Telegram", "🧠 Memory", "✅ Health", "🧹 Akses Terbatas", "🔧 Setup"]
    )

    with tab_ai:
        st.markdown("#### 🤖 Model & Persona")
        render_mode_selector()
        filter_choice = st.radio(
            "Tampilan model",
            ["Hemat saja", "Hemat + menengah/mahal"],
            horizontal=False,
            index=0,
        )
        model_list = (
            CHEAP_MODEL_OPTIONS if filter_choice == "Hemat saja" else MODEL_OPTIONS
        )
        current_model = (
            st.session_state.active_model
            if st.session_state.active_model in model_list
            else default_model
        )
        if current_model not in model_list:
            current_model = model_list[0]

        st.session_state.active_model = st.selectbox(
            "Model utama aktif",
            model_list,
            index=model_list.index(current_model),
            format_func=model_price_label,
        )
        tier = model_cost_tier(st.session_state.active_model)
        price = model_price(st.session_state.active_model)
        st.info(
            f"Model utama: {st.session_state.active_model} | tier: {tier} | input Rp{price.get('input', 0):,}/1M, output Rp{price.get('output', 0):,}/1M".replace(
                ",", "."
            )
        )
        current_health = (st.session_state.get("model_health_cache") or {}).get(
            st.session_state.active_model,
            {},
        )
        if current_health and not current_health.get("active"):
            st.warning(
                "Model utama pilihan admin belum sehat. Gunakan tombol di bawah untuk mengganti ke model sehat otomatis."
            )
            if st.button(
                "Ganti ke model sehat sekarang",
                use_container_width=True,
                key="admin_ai_promote_healthy_primary",
            ):
                result = apply_healthy_primary_model(
                    selected_model=st.session_state.active_model,
                    reason="admin-ai-tab-promote",
                )
                chosen = str(result.get("model") or "").strip()
                if chosen:
                    st.success(f"Model utama diganti ke: {chosen}")
                    st.rerun()
                else:
                    st.error("Belum ada model sehat yang tersedia.")
        st.session_state.active_temperature = st.slider(
            "Temperature",
            0.0,
            1.0,
            float(st.session_state.active_temperature),
            0.1,
        )
        st.session_state.active_max_tokens = st.slider(
            "Max output tokens",
            800,
            5000,
            int(st.session_state.active_max_tokens),
            100,
        )
        st.session_state.active_rotate_cheap_primary = st.toggle(
            "Rotate model murah sebagai model utama",
            value=bool(st.session_state.active_rotate_cheap_primary),
            help="Jika aktif, setiap request memakai model murah aktif berikutnya secara bergiliran. Model mahal tetap hanya cadangan saat murah gagal/kurang cukup.",
        )
        if st.session_state.active_rotate_cheap_primary:
            cheap_for_sync, _ = get_prioritized_fallback_models()
            next_rotation_model = (
                get_rotating_cheap_primary(cheap_for_sync, advance=False)
                if cheap_for_sync
                else ""
            )
            st.caption(
                f"Model murah berikutnya: {next_rotation_model or 'belum ada model murah aktif'}"
            )
            if st.button(
                "Mulai rotasi dari model yang dipilih",
                use_container_width=True,
                key="auto_btn_2698",
            ):
                sync_rotation_index_to_selected_model(cheap_for_sync)
                st.success(
                    "Titik awal rotasi disesuaikan dengan model murah yang dipilih."
                )

        st.session_state.active_thinking_model_router = st.toggle(
            "Gunakan model capable untuk pertanyaan thinking",
            value=bool(st.session_state.active_thinking_model_router),
            help="Jika aktif, pertanyaan analitis, teknis, panjang, debugging, riset, atau multi-langkah langsung diarahkan ke model menengah/mahal aktif. Setelah selesai, default kembali ke model murah aktif.",
        )
        st.session_state.active_thinking_min_chars = st.slider(
            "Minimal panjang konteks untuk dianggap thinking",
            80,
            500,
            int(st.session_state.active_thinking_min_chars),
            20,
            disabled=not st.session_state.active_thinking_model_router,
        )
        st.session_state.active_fast_normal_model_router = st.toggle(
            "Untuk pertanyaan ringan, pakai model murah tercepat",
            value=bool(st.session_state.active_fast_normal_model_router),
            help="Jika aktif, pertanyaan non-thinking tidak memakai rotasi, tetapi langsung memakai model murah aktif dengan latency health check paling rendah.",
        )
        if st.session_state.active_fast_normal_model_router:
            route_preview = build_model_routing_plan(user_text="halo")
            st.caption(
                f"Model cepat untuk pertanyaan ringan: {route_preview.get('fastest_cheap_primary_model') or 'belum ada model murah aktif'}"
            )
        st.session_state.active_persona = st.text_area(
            "System persona",
            value=st.session_state.active_persona,
            height=170,
        )
        st.session_state.show_debug = st.toggle(
            "Tampilkan debug respons di chat", value=st.session_state.show_debug
        )
        st.markdown("#### Router Cepat & Akurat")
        st.caption(
            "Algoritma baru: pertanyaan thinking langsung memakai model capable aktif. Pertanyaan ringan/non-thinking memakai model murah aktif tercepat. Jika fast-normal dimatikan, sistem kembali memakai rotasi model murah. Jika jawaban kosong/kurang kuat/gagal, sistem mencoba backup sesuai jalur, lalu kembali ke model murah aktif setelah selesai."
        )
        st.session_state.active_smart_router = st.toggle(
            "Aktifkan router hanya jika jawaban kurang kuat",
            value=bool(st.session_state.active_smart_router),
        )
        st.session_state.active_return_to_primary = st.toggle(
            "Setelah konsultasi, susun ulang jawaban dengan model utama",
            value=bool(st.session_state.active_return_to_primary),
        )
        st.session_state.active_max_smart_models = st.slider(
            "Maksimal model hemat yang dikonsultasikan",
            1,
            3,
            int(st.session_state.active_max_smart_models),
            1,
        )
        st.session_state.allow_expensive_fallback = st.toggle(
            "Izinkan model menengah/mahal hanya jika model hemat tidak cukup",
            value=bool(st.session_state.allow_expensive_fallback),
        )
        st.session_state.max_expensive_models = st.slider(
            "Maksimal model menengah/mahal yang boleh dipanggil",
            1,
            2,
            int(st.session_state.max_expensive_models),
            1,
            disabled=not st.session_state.allow_expensive_fallback,
        )
        with st.expander("Daftar jalur model"):
            route = build_model_routing_plan()
            st.markdown("**Primary berikutnya:**")
            st.code(model_price_label(route["primary_model"]))
            st.caption(
                f"Fast normal: {'ON' if route.get('fast_normal_model_router') else 'OFF'} | Rotasi murah: {'ON' if route.get('rotate_cheap_primary') else 'OFF'} | thinking router: {'ON' if st.session_state.get('active_thinking_model_router', True) else 'OFF'} | indeks berikutnya: {route.get('cheap_rotation_index', 0)}"
            )
            st.caption(
                f"Model murah tercepat: {route.get('fastest_cheap_primary_model') or 'belum ada'}"
            )
            st.markdown("**Model hemat aktif/prioritas backup:**")
            st.code(
                "\n".join(model_price_label(m) for m in route["active_cheap_models"])
                or "Belum ada model hemat aktif"
            )
            st.markdown(
                "**Model menengah/mahal aktif otomatis jika model hemat tidak cukup / pertanyaan thinking:**"
            )
            st.code(
                "\n".join(
                    model_price_label(m) for m in route["active_expensive_models"]
                )
                or "Belum ada model menengah/mahal aktif"
            )
            capable_preview = get_capable_primary_model(
                route["active_expensive_models"],
                st.session_state.get("model_health_cache") or {},
            )
            st.caption(
                f"Model capable untuk thinking: {capable_preview or 'belum ada model capable aktif'}"
            )
            if route["direct_to_expensive"]:
                st.warning(
                    "Tidak ada model hemat aktif. Request berikutnya langsung memakai model menengah/mahal aktif, lalu sistem akan kembali ke model hemat saat sudah aktif lagi."
                )

        st.markdown("#### Cek Berkala Model")
        health_window_open = True
        st.caption(
            "Health check model aktif kapan saja. "
            "Klik tombol cek model untuk ping/test model langsung tanpa menunggu jam tertentu. "
            "Urutan default: thinking → model capable aktif; non-thinking → model hemat aktif tercepat → backup hemat aktif lain → model menengah/mahal jika semua hemat gagal/kurang cukup → kembali ke model hemat aktif."
        )
        col_health_check, col_health_full, col_health_info = st.columns([1, 1, 2])
        with col_health_check:
            if st.button(
                "⚡ Quick check",
                use_container_width=True,
                disabled=False,
                key="model_health_quick_check_btn",
            ):
                st.session_state.active_health_check_scope = "quick"
                refresh_model_health_if_needed(force=True, scope="quick")
                st.success("Quick health check selesai.")
            st.caption("Cek model prioritas saja.")
        with col_health_full:
            if st.button(
                "🔁 Full check",
                use_container_width=True,
                disabled=False,
                key="model_health_full_check_btn",
            ):
                st.session_state.active_health_check_scope = "full"
                refresh_model_health_if_needed(force=True, scope="full")
                st.success("Full health check selesai.")
            st.caption("Cek semua kandidat model.")
        with col_health_info:
            cheap_active, expensive_active = get_prioritized_fallback_models()
            st.info(
                f"Backup hemat aktif: {len(cheap_active)} | Backup menengah/mahal aktif: {len(expensive_active)}"
            )
        with st.expander("Detail status semua model"):
            render_model_health_table()

        col_test, col_reset = st.columns(2)
        with col_test:
            if st.button("🧪 Tes AI", use_container_width=True, key="auto_btn_2795"):
                try:
                    route = build_model_routing_plan(advance_rotation=True)
                    answer, meta = generate_answer(
                        api_url=api_url,
                        api_key=api_key,
                        model=route["primary_model"],
                        system_prompt=st.session_state.active_persona,
                        user_text="Jawab singkat: apakah kamu aktif?",
                        memory_text=build_memory_text(limit=8),
                        recent_messages=[],
                        fallback_models=route["cheap_fallback_models"],
                        expensive_fallback_models=route["expensive_fallback_models"],
                        allow_expensive_fallback=route["allow_expensive_fallback"],
                        max_expensive_models=route["max_expensive_models"],
                        temperature=float(st.session_state.active_temperature),
                        max_completion_tokens=int(st.session_state.active_max_tokens),
                        timeout=int(
                            get_secret("AI_TEST_TIMEOUT_SECONDS", 60) or 60
                        ),
                        smart_model_router=bool(st.session_state.active_smart_router),
                        return_to_primary=route["return_to_primary"],
                        max_smart_models=route["max_smart_models"],
                    )
                    restore_active_model_to_cheap(route.get("primary_model"))
                    st.success(answer)
                    st.caption(
                        f"Model: {meta.get('model') or meta.get('model_requested')}"
                    )
                except Exception as exc:
                    st.error(str(exc))
        with col_reset:
            if st.button(
                "↩️ Reset dari Secrets", use_container_width=True, key="auto_btn_2823"
            ):
                st.session_state.active_model = default_model
                st.session_state.active_persona = persona_from_secret
                st.session_state.active_default_memory = (
                    default_memory_context_from_secret
                )
                st.session_state.active_temperature = 0.3
                st.session_state.active_max_tokens = 2600
                st.session_state.show_debug = False
                st.session_state.active_smart_router = smart_model_router_default
                st.session_state.active_return_to_primary = return_to_primary_default
                st.session_state.active_max_smart_models = max_smart_models_default
                st.session_state.allow_expensive_fallback = parse_bool(
                    get_secret("ALLOW_EXPENSIVE_FALLBACK", True), default=True
                )
                st.session_state.max_expensive_models = int(
                    get_secret("MAX_EXPENSIVE_MODELS", 1) or 1
                )
                st.session_state.model_health_cache = {}
                st.session_state.model_health_checked_at = 0.0
                st.session_state.active_cheap_fallback_models = (
                    DEFAULT_CHEAP_FALLBACK_MODELS.copy()
                )
                st.session_state.active_expensive_fallback_models = (
                    DEFAULT_EXPENSIVE_FALLBACK_MODELS.copy()
                )
                st.session_state.last_model_health_error = ""
                st.session_state.active_rotate_cheap_primary = (
                    rotate_cheap_primary_default
                )
                st.session_state.active_use_streamlit_cache_memory = (
                    use_streamlit_cache_memory_default
                )
                st.session_state.active_thinking_model_router = (
                    thinking_model_router_default
                )
                st.session_state.active_thinking_min_chars = thinking_min_chars_default
                st.session_state.active_fast_normal_model_router = (
                    fast_normal_model_router_default
                )
                st.session_state.cheap_model_rotation_index = 0
                st.session_state.last_rotated_primary_model = ""
                st.rerun()

    with tab_bot:
        st.markdown("#### Kontrol Bot Telegram")
        format_token_status("TELEGRAM_BOT_TOKEN", telegram_token)
        format_token_status("SLASHAI_API_KEY", api_key)
        st.warning(
            "Mode aman aktif: TELEGRAM_AUTO_START disarankan FALSE. Jalankan bot hanya dari tombol admin agar Streamlit Online tidak membuat beberapa poller saat app rerun/restart."
        )
        st.info(
            "Lock OS aktif untuk mencegah lebih dari satu worker dalam container yang sama. Jika tetap double/triple, berarti token bot masih hidup di deployment lama/lokal/VPS lain."
        )
        st.caption(
            "Telegram dikirim sebagai plain text secara default agar kode/XML seperti <uses-permission> tidak dianggap tag HTML."
        )
        st.caption(
            f"Perintah admin Telegram: /speed {telegram_speed_update_code} untuk cek ulang model kapan saja dan memakai hanya model yang hidup."
        )
        st.info(
            "Kontrol admin juga tersedia dari Telegram: /admin, /status, /telegramtest, /health, /router auto|murah|mahal, /lock, /unlock, /key generate, /keys, /akses, /update, /reset_runtime, /reset_telegram."
        )

        status = service.status()
        st.write("Status bot:", "🟢 Berjalan" if status["running"] else "🔴 Mati")
        st.caption(f"Pesan diproses: {status.get('processed', 0)}")
        if status.get("started_at"):
            st.caption(f"Mulai: {_to_wib_display_text(status['started_at'])}")
        if status.get("worker_id"):
            st.caption(f"Worker: {status['worker_id']}")
        st.caption(f"Duplikat dicegah: {status.get('duplicates_skipped', 0)}")
        if status.get("runtime_primary_model"):
            st.caption(
                f"Primary runtime Telegram: {status.get('runtime_primary_model')}"
            )
        if status.get("model_health_checked_at"):
            st.caption(
                f"Update model Telegram terakhir: {_to_wib_display_text(status.get('model_health_checked_at'))} | aktif: {status.get('model_health_active_count', 0)}"
            )

        with st.expander("💬 Test status Telegram dari API", expanded=True):
            render_telegram_verified_status_card(
                force_button_key="telegram_diagnose_connection_btn",
            )

        cfg = get_runtime_config()
        route = build_model_routing_plan()
        bot_config = build_telegram_config_payload(
            route=route,
            cfg=cfg,
            persona_text=persona_with_default_memory(
                st.session_state.active_persona
            ),
        )

        st.markdown("#### Mode model Telegram")
        current_telegram_mode = normalize_telegram_model_mode(
            st.session_state.get(
                "telegram_model_mode",
                telegram_model_mode_default,
            )
        )
        mode_options = {
            "auto": "Otomatis",
            "cheap": "Murah/Cepat",
            "expensive": "Medium/Mahal",
        }
        selected_label = st.radio(
            "Mode routing bot",
            options=list(mode_options.keys()),
            format_func=lambda value: mode_options.get(value, value),
            index=list(mode_options.keys()).index(current_telegram_mode)
            if current_telegram_mode in mode_options
            else 0,
            horizontal=True,
            key="telegram_model_mode",
            help="Sesuai format telegram_service.py: auto, cheap, atau expensive.",
        )
        bot_config["telegram_model_mode"] = normalize_telegram_model_mode(
            selected_label
        )

        col_start, col_stop = st.columns(2)
        with col_start:
            if st.button("▶️ Start Bot", use_container_width=True, key="auto_btn_2932"):
                start_route = build_model_routing_plan(advance_rotation=True)
                bot_config.update(
                    {
                        "slashai_model": start_route["primary_model"],
                        "fallback_models": start_route["cheap_fallback_models"],
                        "expensive_fallback_models": start_route[
                            "expensive_fallback_models"
                        ],
                        "allow_expensive_fallback": start_route[
                            "allow_expensive_fallback"
                        ],
                        "max_expensive_models": start_route["max_expensive_models"],
                        "return_to_primary": start_route["return_to_primary"],
                        "max_smart_models": start_route["max_smart_models"],
                        "fastest_cheap_model": start_route.get(
                            "fastest_cheap_primary_model", ""
                        ),
                        "fast_cheap_models": start_route.get("fast_cheap_models", []),
                        "active_cheap_models": start_route.get(
                            "active_cheap_models", []
                        ),
                        "thinking_capable_models": start_route.get(
                            "active_expensive_models", []
                        ),
                        "capable_models": start_route.get(
                            "active_expensive_models", []
                        ),
                        "active_expensive_models": start_route.get(
                            "active_expensive_models", []
                        ),
                        "telegram_model_mode": normalize_telegram_model_mode(
                            st.session_state.get(
                                "telegram_model_mode",
                                telegram_model_mode_default,
                            )
                        ),
                        "telegram_runtime_state_file": telegram_runtime_state_file,
                        "auto_rotate_on_model_error": bool(
                            telegram_auto_rotate_on_model_error
                        ),
                    }
                )
                started = service.start(bot_config)
                restore_active_model_to_cheap(start_route.get("primary_model"))
                if started:
                    st.session_state.telegram_verified_status_cache = {}
                    st.success(
                        f"Bot Telegram dijalankan dengan primary: {start_route['primary_model']}"
                    )
                else:
                    latest_status = service.status()
                    if latest_status.get("running"):
                        st.info("Bot sudah berjalan.")
                    else:
                        st.error("Bot Telegram gagal dijalankan.")
                        detail_error = str(
                            latest_status.get("last_error")
                            or "Tidak ada detail error dari worker."
                        )
                        st.code(detail_error[:3000])
                        st.info(
                            "Langkah cepat: klik Tes koneksi Telegram. Jika token OK tetapi start gagal, "
                            "klik Reset koneksi Telegram lalu Force reset lokal. Jika muncul 409 Conflict, "
                            "token masih dipakai instance lain; revoke token di BotFather dan masukkan token baru."
                        )
        with col_stop:
            if st.button("⏹️ Stop Bot", use_container_width=True, key="auto_btn_2954"):
                service.stop()
                st.session_state.telegram_verified_status_cache = {}
                st.warning("Bot Telegram dihentikan pada instance Streamlit ini.")

        if st.button(
            "🧯 Reset koneksi Telegram / hapus pending update",
            use_container_width=True,
            key="auto_btn_2958",
        ):
            result = service.reset_telegram_session(bot_config)
            st.session_state.telegram_verified_status_cache = {}
            st.warning(result)

        if st.button(
            "🛠️ Force reset lokal worker Telegram",
            use_container_width=True,
            key="telegram_force_local_reset_btn",
        ):
            st.session_state.telegram_verified_status_cache = {}
            st.warning(service.force_local_reset())

        st.caption(
            "Penting: tombol Stop hanya mematikan worker pada app ini. Jika ada deploy lama/laptop/VPS lain dengan token yang sama, revoke token dari BotFather lalu masukkan token baru di Secrets."
        )

        if status.get("last_update"):
            with st.expander("Update terakhir"):
                st.code(status["last_update"])
        if status.get("last_error"):
            with st.expander("Error terakhir"):
                st.code(status["last_error"][:2000])

    with tab_memory:
        st.markdown("#### Memory Default Aktif")
        st.caption(
            "Memory default ini selalu ikut dikirim ke AI, baik ada memory cache maupun belum ada."
        )
        st.session_state.active_default_memory = st.text_area(
            "Memory default",
            value=st.session_state.active_default_memory,
            height=220,
        )

        st.markdown("#### Memory Cache Online")
        st.info(
            "Memory cache disimpan di RAM/cache online. Memory ini bertahan saat rerun dan selama container app masih hidup, "
            "tetapi bisa hilang saat app sleep, restart, clear cache, atau redeploy. Cocok untuk memory cepat di hosting online."
        )
        st.session_state.active_use_streamlit_cache_memory = st.toggle(
            "Aktifkan memory cache online untuk jawaban AI",
            value=bool(st.session_state.active_use_streamlit_cache_memory),
        )

        cache_memory_text = streamlit_cache_memory_list_text(limit=80)
        if cache_memory_text:
            st.code(cache_memory_text)
        else:
            st.write("Belum ada memory di cache online.")

        new_cache_memory = st.text_area(
            "Tambah memory ke cache online",
            value="",
            height=90,
            placeholder="Contoh: User ingin jawaban yang ringkas, jelas, dan langsung bisa dipakai.",
        )
        col_cache_save, col_cache_save_both = st.columns(2)
        with col_cache_save:
            if st.button(
                "Simpan ke cache online", use_container_width=True, key="auto_btn_3004"
            ):
                saved = add_streamlit_cache_memory(
                    new_cache_memory, source="streamlit-admin-cache"
                )
                if saved:
                    st.success("Memory disimpan ke cache online.")
                else:
                    st.info("Memory kosong atau sudah ada di cache.")
                st.rerun()
        with col_cache_save_both:
            if st.button(
                "Simpan ke cache + file lokal",
                use_container_width=True,
                key="auto_btn_3012",
            ):
                saved_cache = add_streamlit_cache_memory(
                    new_cache_memory, source="streamlit-admin-cache"
                )
                if new_cache_memory.strip():
                    memory.add(new_cache_memory.strip(), source="streamlit-admin-file")
                if saved_cache or new_cache_memory.strip():
                    st.success("Memory disimpan ke cache online dan file lokal.")
                else:
                    st.info("Memory kosong atau sudah ada.")
                st.rerun()

        forget_cache_keyword = st.text_input("Hapus memory cache yang mengandung kata")
        col_cache_forget, col_cache_reset = st.columns(2)
        with col_cache_forget:
            if st.button(
                "Hapus dari cache berdasarkan kata",
                use_container_width=True,
                key="auto_btn_3025",
            ):
                count = forget_streamlit_cache_memory_contains(forget_cache_keyword)
                st.warning(f"{count} memory cache dihapus.")
                st.rerun()
        with col_cache_reset:
            if st.button(
                "Reset semua memory cache",
                use_container_width=True,
                key="auto_btn_3030",
            ):
                count = reset_streamlit_cache_memory()
                st.warning(f"{count} memory cache dihapus.")
                st.rerun()

        st.markdown("#### Memory Tambahan File Lokal")
        st.caption(
            "Opsional. File lokal dapat hilang di hosting online saat app restart/redeploy, tetapi tetap dipertahankan untuk kompatibilitas fitur lama."
        )
        current_memory = memory.list_text(limit=80)
        if current_memory:
            st.code(current_memory)
        else:
            st.write("Belum ada memory file lokal.")

        new_file_memory = st.text_input("Tambah memory ke file lokal")
        if st.button(
            "Simpan ke file lokal", use_container_width=True, key="auto_btn_3044"
        ):
            if new_file_memory.strip():
                memory.add(new_file_memory.strip(), source="streamlit-admin-file")
                st.success("Memory disimpan ke file lokal.")
                st.rerun()
            else:
                st.info("Memory masih kosong.")

        forget_keyword = st.text_input("Hapus memory file lokal yang mengandung kata")
        col_forget, col_reset_memory = st.columns(2)
        with col_forget:
            if st.button(
                "Hapus file lokal berdasarkan kata",
                use_container_width=True,
                key="auto_btn_3055",
            ):
                count = memory.forget_contains(forget_keyword)
                st.warning(f"{count} memory file lokal dihapus.")
                st.rerun()
        with col_reset_memory:
            if st.button(
                "Reset semua memory file lokal",
                use_container_width=True,
                key="auto_btn_3060",
            ):
                memory.reset()
                st.warning("Semua memory file lokal dihapus.")
                st.rerun()

    with tab_health:
        render_secrets_validator_panel()
        render_ai_health_center()

    with tab_maint:
        render_maintenance_tools()

    with tab_setup:
        st.markdown("#### Secrets Aplikasi")
        st.write(
            "Masukkan konfigurasi berikut di menu **Dashboard Aplikasi → Settings → Secrets**."
        )
        st.code(
            '''ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "GANTI_PASSWORD_ADMIN_YANG_KUAT"

TELEGRAM_BOT_TOKEN = "ISI_TOKEN_BOT_DARI_BOTFATHER"
SLASHAI_API_KEY = "ISI_API_KEY_SLASHAI_KAMU"
SLASHAI_API_URL = "https://api.slashai.my.id/v1/chat/completions"
SLASHAI_MODEL = "slashai/gpt-5-nano"

ASSISTANT_PERSONA = "Nama kamu adalah adioranye. Kamu adalah asisten pribadi yang sangat cerdas, ramah, teliti, detail, cepat memahami konteks, dan mampu membantu berbagai kebutuhan pengguna secara praktis. Jawab dalam bahasa Indonesia yang natural, jelas, sopan, dan mudah dipahami. Untuk pertanyaan sederhana, jawab singkat dan langsung. Untuk pertanyaan teknis, akademik, bisnis, coding, atau analisis, jawab lebih detail, bertahap, dan berikan contoh bila membantu. Jangan mengarang fakta. Jika informasi tidak pasti, jelaskan keterbatasannya dan berikan saran langkah aman."

DEFAULT_MEMORY_CONTEXT = """
Memory default Adioranye:
- Adioranye dapat membantu menjawab pertanyaan umum, akademik, teknis, bisnis, kreatif, penulisan, coding, analisis data, strategi konten, dan kebutuhan praktis sehari-hari.
- Prioritaskan jawaban yang akurat, jelas, ramah, detail secukupnya, dan langsung bisa dipakai.
- Untuk pertanyaan akademik, bantu dengan struktur rapi, bahasa natural, contoh, dan penjelasan yang mudah dipahami.
- Untuk pertanyaan coding atau aplikasi, berikan langkah perbaikan yang praktis, kode yang siap ditempel, dan jelaskan letak perubahan penting.
- Untuk kode, tulis vertikal ke bawah dengan line break rapi. Pecah parameter, list, dictionary, command, dan fungsi panjang ke beberapa baris.
- Untuk pertanyaan bisnis, pemasaran, desain, konten, atau promosi, berikan ide yang ringkas, menarik, dan mudah dieksekusi.
- Untuk pertanyaan yang membutuhkan data terbaru, hukum, medis, keuangan, atau keputusan berisiko, jangan mengarang. Jelaskan bahwa data perlu diverifikasi dan berikan arahan aman.
- Jika pengguna meminta format tertentu, ikuti format tersebut. Jika tidak, gunakan struktur yang paling mudah dibaca.
- Jika permintaan kurang jelas, tetap berikan jawaban terbaik berdasarkan konteks yang ada dan sebutkan asumsi yang digunakan.
"""

MEMORY_FILE = "assistant_memory.json"

# true = bot Telegram otomatis start saat app Streamlit dibuka/aktif
TELEGRAM_AUTO_START = false
TELEGRAM_DROP_PENDING_UPDATES = true
TELEGRAM_SEND_PROCESSING_MESSAGE = false
TELEGRAM_LOCK_FILE = ".telegram_bot_worker.lock"
TELEGRAM_SHOW_MODEL_INFO = true
TELEGRAM_SPEED_UPDATE_CODE = "4321"
TELEGRAM_ADMIN_CHAT_IDS = ""
ALLOW_UNRESTRICTED_MODEL_COMMANDS = false

# Opsional
TEMPERATURE = 0.3
MAX_COMPLETION_TOKENS = 2600
SMART_MODEL_ROUTER = true
RETURN_TO_PRIMARY_MODEL = true
MAX_SMART_MODELS = 2
ROTATE_CHEAP_PRIMARY = true
FAST_NORMAL_MODEL_ROUTER = true
USE_STREAMLIT_CACHE_MEMORY = true
STREAMLIT_CACHE_MEMORY_LIMIT = 200

# Jika pertanyaan kompleks/thinking, langsung pakai model capable aktif.
THINKING_MODEL_ROUTER = true
THINKING_MIN_CHARS = 180
# Opsional: paksa model capable tertentu jika aktif, contoh:
# THINKING_CAPABLE_MODEL = "slashai/gpt-5.5"

# Default aktif: pertanyaan ringan/non-thinking memakai model murah aktif tercepat.
# Jika FAST_NORMAL_MODEL_ROUTER dimatikan, model murah bisa dipakai bergiliran lewat ROTATE_CHEAP_PRIMARY.
# Jika semua model murah gagal/kurang cukup, naik otomatis ke model menengah/mahal.
# Setelah request selesai, aplikasi kembali memilih model murah aktif sebagai default.
ALLOW_EXPENSIVE_FALLBACK = true
MAX_EXPENSIVE_MODELS = 1
MODEL_HEALTH_CHECK_INTERVAL_SECONDS = 90000
MODEL_HEALTH_TIMEOUT_SECONDS = 12
MODEL_HEALTH_MIDNIGHT_ONLY = false
MODEL_HEALTH_HOUR_WIB = 0
MODEL_HEALTH_WINDOW_MINUTES = 60

# Power Features / Knowledge Base
POWER_FEATURES_ENABLED = true
POWER_DB_PATH = ".adioranye_power.db"
POWER_RAG_ENABLED = true
POWER_RAG_TOP_K = 5
POWER_KB_MAX_FILE_MB = 12
POWER_RESPONSE_CACHE_ENABLED = true
POWER_ADAPTIVE_SCORING_ENABLED = true
POWER_CIRCUIT_BREAKER_ENABLED = true
AI_OPERATION_MODE = "Seimbang"
POWER_LOG_RETENTION_DAYS = 30
POWER_CACHE_RETENTION_DAYS = 7
POWER_BENCHMARK_RETENTION_DAYS = 14

# Daily Knowledge Base Auto Update
KB_SCRAPER_SOURCES_FILE = "kb_sources.json"
KB_SCRAPER_STATE_FILE = ".adioranye_kb_scrape_state.json"
KB_SCRAPER_MAX_ITEMS_PER_SOURCE = 5
KB_SCRAPER_TIMEOUT = 20''',
            language="toml",
        )
        st.markdown("""
            **Catatan:** Chat AI di halaman utama tidak perlu login. Password admin hanya melindungi pengaturan, kontrol Telegram, memory, dan debug.
            Untuk bot Telegram 24 jam nonstop, VPS tetap lebih stabil karena Streamlit Online bisa sleep saat tidak aktif.
            """.strip())



# =========================
# Animated Adioranye AI brand
# =========================
st.markdown(
    """
    <style>
    .adioranye-brand-title {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 7px;
        min-width: 0;
        line-height: 1;
        white-space: nowrap;
    }

    .adioranye-brand-mark {
        position: relative;
        width: 18px;
        height: 18px;
        flex: 0 0 18px;
        border-radius: 8px;
        background:
            radial-gradient(circle at 32% 26%, rgba(255,255,255,0.96), transparent 25%),
            linear-gradient(135deg, #ff8a3d, #34c759 52%, #0a84ff);
        box-shadow:
            0 0 0 1px rgba(255,255,255,0.34) inset,
            0 7px 18px rgba(10,132,255,0.22),
            0 0 18px rgba(52,199,89,0.20);
        animation: adioranyeBrandFloat 3.6s ease-in-out infinite;
    }

    .adioranye-brand-mark::before,
    .adioranye-brand-mark::after {
        content: "";
        position: absolute;
        width: 3px;
        height: 3px;
        top: 7px;
        border-radius: 999px;
        background: rgba(255,255,255,0.96);
        box-shadow: 0 0 4px rgba(255,255,255,0.88);
        animation: adioranyeBlink 3.2s ease-in-out infinite;
    }

    .adioranye-brand-mark::before {
        left: 5px;
    }

    .adioranye-brand-mark::after {
        right: 5px;
    }

    .adioranye-brand-word {
        font-weight: 850;
        letter-spacing: -0.03em;
        background:
            linear-gradient(
                100deg,
                var(--mac-text),
                #ff8a3d 28%,
                #34c759 55%,
                #0a84ff 78%,
                var(--mac-text)
            );
        background-size: 260% 100%;
        -webkit-background-clip: text;
        background-clip: text;
        color: transparent;
        animation: adioranyeTextFlow 4.8s ease-in-out infinite;
    }

    .adioranye-brand-ai {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 24px;
        padding: 3px 6px;
        border-radius: 999px;
        border: 1px solid rgba(10,132,255,0.34);
        background:
            radial-gradient(circle at 20% 20%, rgba(255,255,255,0.50), transparent 32%),
            linear-gradient(135deg, rgba(10,132,255,0.20), rgba(52,199,89,0.16));
        color: var(--mac-text);
        font-size: 0.66rem;
        font-weight: 900;
        letter-spacing: 0.01em;
        box-shadow:
            inset 0 1px 0 rgba(255,255,255,0.28),
            0 6px 16px rgba(10,132,255,0.12);
        animation: adioranyeChipPulse 2.8s ease-in-out infinite;
    }

    .app-hero.adioranye-hero-motion {
        isolation: isolate;
    }

    .app-hero.adioranye-hero-motion::before {
        content: "";
        position: absolute;
        inset: -40% -28%;
        z-index: 0;
        pointer-events: none;
        background:
            radial-gradient(circle at 18% 35%, rgba(255,138,61,0.14), transparent 22%),
            radial-gradient(circle at 68% 24%, rgba(52,199,89,0.13), transparent 24%),
            radial-gradient(circle at 82% 80%, rgba(10,132,255,0.12), transparent 26%);
        opacity: 0.92;
        animation: adioranyeAuraMove 10s ease-in-out infinite alternate;
    }

    .app-logo.adioranye-logo-motion {
        position: relative;
        overflow: visible;
        background:
            radial-gradient(circle at 30% 20%, rgba(255,255,255,0.82), transparent 26%),
            linear-gradient(145deg, rgba(255,138,61,0.26), rgba(52,199,89,0.20) 52%, rgba(10,132,255,0.22)) !important;
        animation: adioranyeBotFloat 3.4s ease-in-out infinite;
    }

    .app-logo.adioranye-logo-motion::before,
    .app-logo.adioranye-logo-motion::after {
        content: "✦";
        position: absolute;
        color: #ff8a3d;
        font-size: 0.64rem;
        text-shadow: 0 0 10px rgba(255,138,61,0.55);
        opacity: 0.82;
        animation: adioranyeSparkle 2.6s ease-in-out infinite;
    }

    .app-logo.adioranye-logo-motion::before {
        top: -8px;
        right: -5px;
    }

    .app-logo.adioranye-logo-motion::after {
        left: -7px;
        bottom: 4px;
        color: #34c759;
        animation-delay: 0.9s;
    }

    .adioranye-mini-bot {
        position: relative;
        width: 38px;
        height: 34px;
        border-radius: 14px 14px 12px 12px;
        background:
            linear-gradient(180deg, rgba(255,255,255,0.98), rgba(238,247,255,0.88));
        border: 1px solid rgba(15,23,42,0.10);
        box-shadow:
            inset 0 1px 0 rgba(255,255,255,0.86),
            0 10px 22px rgba(15,23,42,0.14);
    }

    .adioranye-mini-bot::before {
        content: "";
        position: absolute;
        left: 50%;
        top: -10px;
        width: 2px;
        height: 10px;
        border-radius: 999px;
        background: linear-gradient(180deg, #0a84ff, #34c759);
        transform: translateX(-50%);
        transform-origin: bottom center;
        animation: adioranyeAntenna 1.9s ease-in-out infinite;
    }

    .adioranye-mini-bot::after {
        content: "";
        position: absolute;
        left: 50%;
        top: -14px;
        width: 7px;
        height: 7px;
        border-radius: 999px;
        background: #34c759;
        box-shadow:
            0 0 0 4px rgba(52,199,89,0.14),
            0 0 15px rgba(52,199,89,0.65);
        transform: translateX(-50%);
        animation: adioranyeSignalPulse 1.9s ease-in-out infinite;
    }

    .adioranye-eye {
        position: absolute;
        top: 13px;
        width: 6px;
        height: 6px;
        border-radius: 999px;
        background: #0f172a;
        box-shadow: 0 0 0 2px rgba(10,132,255,0.08);
        animation: adioranyeBlink 3.2s ease-in-out infinite;
    }

    .adioranye-eye.left {
        left: 10px;
    }

    .adioranye-eye.right {
        right: 10px;
    }

    .adioranye-smile {
        position: absolute;
        left: 50%;
        bottom: 8px;
        width: 14px;
        height: 7px;
        border-bottom: 2px solid rgba(255,138,61,0.90);
        border-radius: 0 0 999px 999px;
        transform: translateX(-50%);
        animation: adioranyeSmile 2.4s ease-in-out infinite;
    }

    .adioranye-hero-content {
        position: relative;
        z-index: 1;
    }

    .app-title.adioranye-hero-title {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 8px;
        margin-bottom: 0.35rem !important;
    }

    .adioranye-hero-word {
        background:
            linear-gradient(
                100deg,
                var(--mac-text),
                #ff8a3d 25%,
                #34c759 54%,
                #0a84ff 78%,
                var(--mac-text)
            );
        background-size: 260% 100%;
        -webkit-background-clip: text;
        background-clip: text;
        color: transparent;
        animation: adioranyeTextFlow 4.8s ease-in-out infinite;
    }

    .adioranye-ai-chip-large {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 5px 10px;
        border-radius: 999px;
        border: 1px solid rgba(10,132,255,0.32);
        background:
            linear-gradient(135deg, rgba(10,132,255,0.16), rgba(52,199,89,0.14));
        color: var(--mac-text);
        font-size: 0.88rem;
        font-weight: 900;
        letter-spacing: 0.03em;
        box-shadow: 0 8px 20px rgba(10,132,255,0.10);
        animation: adioranyeChipPulse 2.8s ease-in-out infinite;
    }

    .adioranye-hero-kicker {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        margin-bottom: 8px;
        padding: 5px 9px;
        border-radius: 999px;
        border: 1px solid rgba(52,199,89,0.26);
        background: rgba(52,199,89,0.09);
        color: var(--mac-muted);
        font-size: 0.78rem;
        font-weight: 800;
    }

    .adioranye-hero-kicker::before {
        content: "";
        width: 7px;
        height: 7px;
        border-radius: 999px;
        background: #34c759;
        box-shadow: 0 0 12px rgba(52,199,89,0.64);
        animation: onlinePulse 1.65s ease-in-out infinite;
    }

    .online-status.status-ready,
    .adioranye-hero-kicker.status-ready {
        border-color: rgba(52,199,89,0.34);
        background: rgba(52,199,89,0.10);
    }

    .online-status.status-fallback,
    .adioranye-hero-kicker.status-fallback {
        border-color: rgba(10,132,255,0.34);
        background: rgba(10,132,255,0.10);
    }

    .online-status.status-checking,
    .adioranye-hero-kicker.status-checking {
        border-color: rgba(255,204,0,0.38);
        background: rgba(255,204,0,0.12);
    }

    .online-status.status-warning,
    .adioranye-hero-kicker.status-warning {
        border-color: rgba(255,149,0,0.42);
        background: rgba(255,149,0,0.12);
    }

    .online-status.status-offline,
    .adioranye-hero-kicker.status-offline {
        border-color: rgba(255,69,58,0.42);
        background: rgba(255,69,58,0.12);
    }

    .online-status.status-fallback .online-dot,
    .adioranye-hero-kicker.status-fallback::before {
        background: #0a84ff;
        box-shadow: 0 0 12px rgba(10,132,255,0.72);
    }

    .online-status.status-checking .online-dot,
    .adioranye-hero-kicker.status-checking::before {
        background: #ffcc00;
        box-shadow: 0 0 12px rgba(255,204,0,0.72);
    }

    .online-status.status-warning .online-dot,
    .adioranye-hero-kicker.status-warning::before {
        background: #ff9500;
        box-shadow: 0 0 12px rgba(255,149,0,0.72);
    }

    .online-status.status-offline .online-dot,
    .adioranye-hero-kicker.status-offline::before {
        background: #ff453a;
        box-shadow: 0 0 12px rgba(255,69,58,0.72);
    }

    .model-auto-refresh-panel {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 0.46rem;
        margin: 0.45rem 0 0.2rem 0;
        padding: 0.48rem 0.62rem;
        border-radius: 999px;
        border: 1px solid rgba(120,120,128,0.22);
        background: rgba(120,120,128,0.08);
        color: var(--mac-text);
        font-size: 0.76rem;
        line-height: 1.2;
    }

    .model-auto-refresh-panel span {
        color: inherit;
    }

    .model-auto-dot {
        width: 8px;
        height: 8px;
        border-radius: 999px;
        background: #ffcc00;
        box-shadow: 0 0 12px rgba(255,204,0,0.68);
        animation: onlinePulse 1.65s ease-in-out infinite;
    }

    .model-auto-refresh-panel.status-ready,
    .model-auto-refresh-panel.status-fallback {
        border-color: rgba(52,199,89,0.30);
        background: rgba(52,199,89,0.09);
    }

    .model-auto-refresh-panel.status-ready .model-auto-dot,
    .model-auto-refresh-panel.status-fallback .model-auto-dot {
        background: #34c759;
        box-shadow: 0 0 12px rgba(52,199,89,0.72);
    }

    .model-auto-refresh-panel.status-checking {
        border-color: rgba(255,204,0,0.35);
        background: rgba(255,204,0,0.10);
    }

    .model-auto-refresh-panel.status-warning {
        border-color: rgba(255,149,0,0.38);
        background: rgba(255,149,0,0.11);
    }

    .model-auto-refresh-panel.status-warning .model-auto-dot {
        background: #ff9500;
        box-shadow: 0 0 12px rgba(255,149,0,0.72);
    }

    .model-auto-refresh-panel.status-offline {
        border-color: rgba(255,69,58,0.38);
        background: rgba(255,69,58,0.10);
    }

    .model-auto-refresh-panel.status-offline .model-auto-dot {
        background: #ff453a;
        box-shadow: 0 0 12px rgba(255,69,58,0.72);
    }

    @keyframes adioranyeTextFlow {
        0%, 100% {
            background-position: 0% 50%;
        }
        50% {
            background-position: 100% 50%;
        }
    }

    @keyframes adioranyeBotFloat {
        0%, 100% {
            transform: translateY(0) rotate(-1deg);
        }
        50% {
            transform: translateY(-5px) rotate(1.2deg);
        }
    }

    @keyframes adioranyeBrandFloat {
        0%, 100% {
            transform: translateY(0) rotate(-3deg) scale(1);
        }
        50% {
            transform: translateY(-2px) rotate(4deg) scale(1.04);
        }
    }

    @keyframes adioranyeBlink {
        0%, 86%, 100% {
            transform: scaleY(1);
        }
        90%, 94% {
            transform: scaleY(0.12);
        }
    }

    @keyframes adioranyeSmile {
        0%, 100% {
            transform: translateX(-50%) scaleX(1);
        }
        50% {
            transform: translateX(-50%) scaleX(1.14);
        }
    }

    @keyframes adioranyeAntenna {
        0%, 100% {
            transform: translateX(-50%) rotate(-4deg);
        }
        50% {
            transform: translateX(-50%) rotate(4deg);
        }
    }

    @keyframes adioranyeSignalPulse {
        0%, 100% {
            transform: translateX(-50%) scale(0.92);
            opacity: 0.82;
        }
        50% {
            transform: translateX(-50%) scale(1.16);
            opacity: 1;
        }
    }

    @keyframes adioranyeChipPulse {
        0%, 100% {
            transform: translateY(0);
            box-shadow: 0 8px 20px rgba(10,132,255,0.08);
        }
        50% {
            transform: translateY(-1px);
            box-shadow: 0 12px 26px rgba(52,199,89,0.13);
        }
    }

    @keyframes adioranyeAuraMove {
        0% {
            transform: translate3d(-2%, -1%, 0) rotate(0deg);
        }
        100% {
            transform: translate3d(2%, 2%, 0) rotate(4deg);
        }
    }

    @keyframes adioranyeSparkle {
        0%, 100% {
            transform: scale(0.78) rotate(0deg);
            opacity: 0.38;
        }
        50% {
            transform: scale(1.18) rotate(16deg);
            opacity: 1;
        }
    }

    @media (max-width: 760px) {
        .adioranye-brand-title {
            gap: 5px;
        }

        .adioranye-brand-mark {
            width: 15px;
            height: 15px;
            flex-basis: 15px;
            border-radius: 6px;
        }

        .adioranye-brand-ai {
            min-width: 20px;
            padding: 2px 5px;
            font-size: 0.58rem;
        }

        .adioranye-mini-bot {
            width: 32px;
            height: 29px;
            border-radius: 12px 12px 10px 10px;
        }

        .adioranye-eye {
            top: 11px;
            width: 5px;
            height: 5px;
        }

        .adioranye-eye.left {
            left: 8px;
        }

        .adioranye-eye.right {
            right: 8px;
        }

        .adioranye-smile {
            width: 12px;
            bottom: 7px;
        }

        .app-title.adioranye-hero-title {
            gap: 6px;
        }

        .adioranye-ai-chip-large {
            padding: 4px 8px;
            font-size: 0.72rem;
        }

        .adioranye-hero-kicker {
            margin-bottom: 7px;
            padding: 4px 8px;
            font-size: 0.70rem;
        }
    }

    @media (prefers-reduced-motion: reduce) {
        .adioranye-brand-mark,
        .adioranye-brand-mark::before,
        .adioranye-brand-mark::after,
        .adioranye-brand-word,
        .adioranye-brand-ai,
        .app-logo.adioranye-logo-motion,
        .app-logo.adioranye-logo-motion::before,
        .app-logo.adioranye-logo-motion::after,
        .adioranye-mini-bot::before,
        .adioranye-mini-bot::after,
        .adioranye-eye,
        .adioranye-smile,
        .adioranye-hero-word,
        .adioranye-ai-chip-large,
        .adioranye-hero-kicker::before,
        .app-hero.adioranye-hero-motion::before {
            animation: none !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================
# Production safety UI styling
# =========================
st.markdown(
    """
    <style>
    .production-status-card {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 0.42rem;
        width: 100%;
        margin: 0.35rem 0 0.75rem;
        padding: 0.46rem 0.58rem;
        border: 1px solid var(--mac-border);
        border-radius: 18px;
        background: var(--mac-panel-soft);
        color: var(--mac-muted) !important;
        font-size: 0.72rem;
        font-weight: 750;
        line-height: 1.45;
        backdrop-filter: var(--mac-blur);
        -webkit-backdrop-filter: var(--mac-blur);
        box-shadow: 0 8px 24px rgba(15, 23, 42, 0.06);
    }

    .production-pill {
        display: inline-flex;
        align-items: center;
        gap: 0.34rem;
        padding: 0.20rem 0.46rem;
        border-radius: 999px;
        background: var(--mac-blue-soft);
        color: var(--mac-text) !important;
        font-size: 0.68rem;
        font-weight: 820;
        white-space: nowrap;
    }

    .production-pill.danger {
        background: rgba(255, 69, 58, 0.14);
    }

    .production-pill.ok {
        background: var(--mac-green-soft);
    }

    .mini-toggle-wrap {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        margin: -0.15rem 0 0.55rem;
    }

    .mini-toggle-wrap div[data-testid="stCheckbox"] label {
        min-height: 24px !important;
        gap: 0.34rem !important;
    }

    .mini-toggle-wrap div[data-testid="stCheckbox"] p {
        font-size: 0.72rem !important;
        color: var(--mac-muted) !important;
        font-weight: 820 !important;
    }

    @media (max-width: 760px) {
        .production-status-card {
            font-size: 0.66rem;
            padding: 0.38rem 0.44rem;
        }

        .production-pill {
            font-size: 0.62rem;
            padding: 0.16rem 0.34rem;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================
# Page Router
# =========================
def render_public_page() -> None:
    # =========================
    # Public Chat UI
    # =========================
    cfg = get_runtime_config()
    st.session_state.sound_enabled = bool(
        message_effects_enabled
        and answer_sound_enabled
    )
    render_sound_unlock_script()
    hydrate_model_readiness_from_file()
    if parse_bool(get_secret("MODEL_READINESS_AUTO_QUICK_CHECK", True), default=True):
        maybe_auto_refresh_model_status(
            reason="public-page-load",
        )

    public_route_preview = build_model_routing_plan(user_text="halo")
    cheap_active = public_route_preview.get("active_cheap_models") or []
    expensive_active = public_route_preview.get("active_expensive_models") or []
    public_readiness = get_model_readiness_state(public_route_preview)
    public_status_class = str(public_readiness.get("class") or "checking")
    public_status_label = sanitize_model_readiness_text(
        public_readiness.get("label") or "Perlu cek model"
    )
    public_status_kicker = sanitize_model_readiness_text(
        public_readiness.get("kicker") or "status model belum dicek"
    )
    public_status_subtitle = sanitize_model_readiness_text(
        public_readiness.get("subtitle") or ""
    )
    st.markdown(
        f"""
        <div class="mac-windowbar">
            <div class="mac-traffic">
                <span class="mac-close"></span>
                <span class="mac-min"></span>
                <span class="mac-max"></span>
            </div>
            <div class="mac-window-title">
                <span class="adioranye-brand-title" aria-label="adioranye AI">
                    <span class="adioranye-brand-mark" aria-hidden="true"></span>
                    <span class="adioranye-brand-word">adioranye</span>
                    <span class="adioranye-brand-ai">AI</span>
                </span>
            </div>
            <div class="mac-window-actions online-status status-{_html_escape(public_status_class)}" aria-label="Status AI model">
                <span class="online-dot" aria-hidden="true"></span>
                <span class="online-text">{_html_escape(public_status_label)}</span>
                <span class="online-wave" aria-hidden="true">
                    <span></span>
                    <span></span>
                    <span></span>
                </span>
            </div>
        </div>
        <div class="app-hero adioranye-hero-motion">
            <div class="app-logo adioranye-logo-motion" aria-hidden="true">
                <div class="adioranye-mini-bot">
                    <span class="adioranye-eye left"></span>
                    <span class="adioranye-eye right"></span>
                    <span class="adioranye-smile"></span>
                </div>
            </div>
            <div class="adioranye-hero-content">
                <div class="adioranye-hero-kicker status-{_html_escape(public_status_class)}">{_html_escape(public_status_kicker)}</div>
                <h3 class="app-title adioranye-hero-title">
                    <span class="adioranye-hero-word">Adioranye</span>
                    <span class="adioranye-ai-chip-large">AI</span>
                </h3>
                <p class="app-subtitle">Asisten AI yang rapi, cepat, dan mudah dibaca di mode terang maupun gelap. Status model: {_html_escape(public_status_subtitle)} Router otomatis memilih {len(cheap_active)} model utama dan {len(expensive_active)} model kuat sesuai tingkat kesulitan pertanyaan.</p>
            </div>
        </div>
        <div class="developer-credit"><span>Developed by Galuh Adi Insani</span></div>
        """,
        unsafe_allow_html=True,
    )

    render_auto_model_status_refresh_panel()

    maintenance_state = read_maintenance_lock_state()
    public_locked = bool(
        maintenance_state.get("locked")
        and not st.session_state.get("admin_authenticated", False)
    )
    maintenance_access_status = get_current_maintenance_access_key_status()
    maintenance_access_allowed = bool(public_locked and maintenance_access_status.get("valid"))

    if maintenance_access_allowed:
        render_maintenance_access_key_active_notice(maintenance_access_status)
    else:
        maintenance_state = render_maintenance_realtime_status(maintenance_state)

    render_maintenance_safe_meta_refresh(
        maintenance_state,
        is_admin=bool(st.session_state.get("admin_authenticated", False)),
    )

    if st.session_state.get("admin_authenticated", False):
        render_public_status_summary()

    if public_locked and not maintenance_access_allowed:
        render_maintenance_access_key_form(maintenance_state)
        render_maintenance_locked_public_guard(maintenance_state)
        st.markdown(
            '<div class="auto-scroll-anchor"></div>'
            '<div class="chat-input-safe-space"></div>',
            unsafe_allow_html=True,
        )
        return

    if not api_key:
        st.warning(
            "SLASHAI_API_KEY belum diisi. Chat belum bisa digunakan sampai admin mengisi Secrets di halaman /admin."
        )

    col_new_chat, col_info = st.columns([1, 4])
    with col_new_chat:
        if st.button("🧹 Chat baru", use_container_width=True, key="auto_btn_3209"):
            st.session_state.chat_messages = []
            st.session_state.pending_prompt = ""
            st.rerun()
    with col_info:
        st.markdown(
            f'<div class="ios-chat-meta">💬 {len(st.session_state.chat_messages)} pesan</div>',
            unsafe_allow_html=True,
        )

    if st.session_state.chat_messages:
        transcript_parts = []
        for item in st.session_state.chat_messages:
            role_label = "Pengguna" if item.get("role") == "user" else "Adioranye"
            transcript_parts.append(f"{role_label}:\n{item.get('content', '')}")
        st.download_button(
            "⬇️ Download jawaban ini ke PDF",
            data=make_answer_pdf_bytes(
                "\n\n".join(transcript_parts), title="Riwayat Chat Adioranye AI"
            ),
            file_name=f"riwayat-chat-adioranye-{datetime.now(WIB_TZ).strftime('%Y%m%d-%H%M%S')}.pdf",
            mime="application/pdf",
            key="download_pdf_full_chat",
            use_container_width=True,
        )

    st.divider()

    for idx, msg in enumerate(st.session_state.chat_messages):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("role") == "assistant":
                msg_meta = msg.get("meta") or {}
                answer_pdf_download_button(
                    msg.get("content", ""),
                    key=f"download_pdf_history_{idx}",
                    model_name=get_answer_model_name(msg_meta),
                )
                render_answer_model_caption(
                    msg_meta,
                    fallback="",
                    admin_detail=bool(st.session_state.admin_authenticated),
                )
                render_feedback_controls(
                    msg_meta,
                    msg.get("content", ""),
                    key_prefix=f"history_feedback_{idx}",
                )

    # Spacer is rendered at the very end so it also protects newly generated messages.
    typed_input = st.chat_input(
        "Tulis pertanyaan, minta ringkasan, analisis dokumen, atau perbaiki kode..."
    )
    user_input = st.session_state.pending_prompt or typed_input
    if st.session_state.pending_prompt:
        st.session_state.pending_prompt = ""

    if user_input:
        maintenance_question_access_status: Dict[str, Any] = {}
        if is_maintenance_locked() and not st.session_state.get("admin_authenticated", False):
            current_access = get_current_maintenance_access_key_status()
            if not current_access.get("valid"):
                with st.chat_message("assistant"):
                    st.warning("Akses terbatas sedang aktif. Masukkan access key valid untuk tetap chat.")
                return

            consumed_access = consume_maintenance_access_question(
                current_access.get("key"),
                used_by="web-public",
            )
            if not consumed_access.get("allowed"):
                st.session_state.maintenance_access_key = ""
                with st.chat_message("assistant"):
                    st.warning("Access key sudah habis atau tidak aktif. Minta key baru ke admin.")
                return

            maintenance_question_access_status = consumed_access
            st.session_state.maintenance_access_key_status = consumed_access

        # Public chat: memory commands are disabled unless admin is logged in.
        # This prevents random visitors from changing global memory.
        with st.chat_message("user"):
            st.markdown(user_input)

        render_auto_scroll_script(
            target="latest",
            delay_ms=80,
        )

        st.session_state.chat_messages.append({"role": "user", "content": user_input})

        if is_maintenance_locked() and not st.session_state.get("admin_authenticated", False):
            if maintenance_question_access_status:
                remaining_after_access = int(maintenance_question_access_status.get("remaining") or 0)
                st.caption(f"Access key akses terbatas: sisa {remaining_after_access} pertanyaan setelah ini.")

        allowed_request, rate_limit_message = check_public_rate_limit(user_input)

        if not allowed_request:
            answer = rate_limit_message
            meta = {
                "rate_limited": True,
                "public_safe_message": True,
            }

            with st.chat_message("assistant"):
                st.markdown(answer)

            st.session_state.chat_messages.append(
                {
                    "role": "assistant",
                    "content": answer,
                    "meta": meta,
                }
            )

            st.markdown(
                '<div class="auto-scroll-anchor"></div>'
                '<div class="chat-input-safe-space"></div>',
                unsafe_allow_html=True,
            )

            return

        local_reply = ""
        local_meta: Dict[str, Any] = {}

        greeting_reply, greeting_meta = build_indonesia_time_greeting_reply(
            user_input
        )

        if greeting_reply:
            local_reply = greeting_reply
            local_meta = greeting_meta

        if not local_reply:
            # Permintaan tertentu yang aman dan sering gagal karena thinking/capable
            # boleh dijawab lokal agar tidak memunculkan pesan gangguan.
            local_safe_answer, local_safe_meta = build_local_safe_fallback_answer(
                user_input,
                failure_reason="pre_model_safe_template",
            )
            if local_safe_answer and local_safe_meta.get("local_safe_fallback_type") == "horse_ration":
                local_reply = local_safe_answer
                local_meta = local_safe_meta

        if not local_reply:
            cached_reply, cached_meta = get_frequent_question_cached_answer(
                user_input
            )

            if cached_reply:
                local_reply = cached_reply
                local_meta = cached_meta

        if not local_reply and st.session_state.admin_authenticated:
            local_reply = handle_local_memory_command(user_input, memory)
            if not local_reply and power_features_enabled:
                local_reply = handle_power_command(
                    user_input, power_store, user_id="web-admin", is_admin=True
                )

        if local_reply:
            answer = local_reply
            meta = local_meta or {}
            st.session_state.last_answer_meta = meta
        else:
            try:
                with st.chat_message("assistant"):
                    placeholder = st.empty()
                    placeholder.markdown(
                        render_loading_animation_html(),
                        unsafe_allow_html=True,
                    )
                    render_auto_scroll_script(
                        target="loading",
                        delay_ms=90,
                    )
                    route = build_model_routing_plan(
                        advance_rotation=True,
                        user_text=user_input,
                    )
                    runtime_options = choose_dynamic_runtime_options(
                        user_text=user_input,
                        route=route,
                        cfg=cfg,
                    )
                    runtime_options["streaming_preview_enabled"] = False
                    if runtime_options.get("auto_live_scraping_needed"):
                        loading_title = "Adioranye sedang mengecek info terkini"
                        loading_subtitle = (
                            "Mode info terkini aktif. Adioranye sedang mengecek sumber luar dan cache terbaru."
                        )
                    elif route.get("thinking_direct_to_capable") or route.get("thinking_mode"):
                        loading_title = "Adioranye sedang thinking dan mengetik jawaban"
                        loading_subtitle = (
                            "Mode thinking aktif. Adioranye sedang menganalisis konteks lebih teliti sebelum menyusun jawaban."
                        )
                    elif route.get("normal_fast_mode"):
                        loading_title = "Adioranye sedang mengetik jawaban cepat"
                        loading_subtitle = (
                            "Mode cepat aktif. Robot kecilnya sedang mengetik jawaban ringkas."
                        )
                    else:
                        loading_title = "Adioranye sedang mengetik jawaban"
                        loading_subtitle = (
                            "Adioranye memilih jalur terbaik, lalu merapikan jawaban untuk Anda."
                        )
                    placeholder.markdown(
                        render_loading_animation_html(
                            title=loading_title,
                            subtitle=loading_subtitle,
                        ),
                        unsafe_allow_html=True,
                    )
                    render_auto_scroll_script(
                        target="loading",
                        delay_ms=120,
                    )
                    current_info_memory_text, current_info_meta = build_current_info_memory_context(
                        user_input,
                        runtime_options,
                    )
                    if (
                        runtime_options.get("current_info_mode")
                        and not current_info_meta.get("live_context_used")
                    ):
                        placeholder.markdown(
                            render_loading_animation_html(
                                subtitle=(
                                    "Mode info terkini aktif, tetapi Tavily belum memberi sumber. "
                                    "Adioranye akan menjawab dengan pemberitahuan verifikasi."
                                ),
                            ),
                            unsafe_allow_html=True,
                        )

                    request_started_at = time.time()
                    answer, meta = safe_generate_power_answer(
                        api_url=api_url,
                        api_key=api_key,
                        model=route["primary_model"],
                        system_prompt=(
                            cfg["persona"]
                            + (
                                "\n\nMODE INFO TERKINI AKTIF:\n"
                                "- Untuk pertanyaan yang meminta info terbaru/hari ini/sekarang, prioritaskan sumber live web/Tavily.\n"
                                "- Jangan menjawab berdasarkan Knowledge Base lama jika sumber live web tersedia.\n"
                                "- Hasil live web/Tavily telah dimasukkan ke konteks. Gunakan konteks tersebut sebagai sumber utama.\n"
                                "- Jika konteks live web kosong/gagal, jangan mengarang info terbaru; jelaskan bahwa info terkini belum dapat diverifikasi."
                                if runtime_options.get("current_info_mode")
                                else ""
                            )
                        ),
                        user_text=(
                            user_input
                            + (
                                "\n\nInstruksi internal: ini pertanyaan info terkini. Gunakan hasil live web/Tavily sebagai sumber utama; abaikan KB lama 2025 jika tidak relevan."
                                if runtime_options.get("current_info_mode")
                                else ""
                            )
                        ),
                        base_memory_text=current_info_memory_text,
                        recent_messages=st.session_state.chat_messages[:-1][-6:],
                        fallback_models=route["cheap_fallback_models"],
                        expensive_fallback_models=route["expensive_fallback_models"],
                        allow_expensive_fallback=route["allow_expensive_fallback"],
                        max_expensive_models=route["max_expensive_models"],
                        temperature=float(cfg["temperature"]),
                        max_completion_tokens=int(runtime_options["max_completion_tokens"]),
                        timeout=int(runtime_options.get("timeout") or request_timeout_seconds or 45),
                        smart_model_router=bool(cfg["smart_model_router"]),
                        return_to_primary=route["return_to_primary"],
                        max_smart_models=route["max_smart_models"],
                        store=power_store,
                        user_id=(
                            "web-admin"
                            if st.session_state.get("admin_authenticated", False)
                            else "web-public"
                        ),
                        channel="web",
                        enable_rag=bool(
                            runtime_options["enable_rag"]
                            and not runtime_options.get("current_info_mode")
                        ),
                        rag_top_k=int(runtime_options["rag_top_k"]),
                        enable_persistent_memory=bool(
                            power_features_enabled and power_persistent_memory_enabled
                        ),
                        enable_prompt_templates=bool(
                            power_features_enabled and power_prompt_templates_enabled
                        ),
                        enable_self_verification=bool(
                            runtime_options["enable_self_verification"]
                        ),
                        daily_cost_limit_idr=float(daily_cost_limit_idr),
                        max_expensive_calls_per_day=int(max_expensive_calls_per_day),
                        enable_response_cache=bool(
                            power_response_cache_enabled
                            and not runtime_options.get("current_info_mode")
                        ),
                        response_cache_ttl_seconds=int(
                            runtime_options["response_cache_ttl_seconds"]
                        ),
                        enable_adaptive_scoring=bool(power_adaptive_scoring_enabled),
                        enable_circuit_breaker=bool(power_circuit_breaker_enabled),
                        circuit_max_failures=int(model_circuit_max_failures),
                        circuit_cooldown_seconds=int(model_circuit_cooldown_seconds),
                        anti_hallucination_enabled=bool(
                            power_anti_hallucination_enabled
                        ),
                        anti_hallucination_auto_strict=bool(
                            power_anti_hallucination_auto_strict
                        ),
                        anti_hallucination_min_sources=int(
                            power_anti_hallucination_min_sources
                        ),
                        anti_hallucination_min_quality=float(
                            power_anti_hallucination_min_quality
                        ),
                        anti_hallucination_min_freshness=float(
                            power_anti_hallucination_min_freshness
                        ),
                        anti_hallucination_append_sources=bool(
                            power_anti_hallucination_append_sources
                        ),
                        strict_rag_mode=bool(
                            power_strict_rag_mode
                            and not runtime_options.get("current_info_mode")
                        ),
                        rag_min_sources=int(power_rag_min_sources),
                        rag_min_score=float(power_rag_min_score),
                        quality_control_enabled=bool(
                            runtime_options["quality_control_enabled"]
                        ),
                        quality_verifier_enabled=bool(
                            runtime_options["quality_verifier_enabled"]
                        ),
                        quality_verifier_model=power_quality_verifier_model,
                        quality_min_score=float(power_quality_min_score),
                        answer_mode=(
                            "current"
                            if runtime_options.get("current_info_mode")
                            else power_default_answer_mode
                        ),
                        append_quality_footer=bool(power_quality_append_footer),
                        hide_kb_sources_for_casual=bool(
                            power_hide_kb_sources_for_casual
                        ),
                        disable_rag_for_casual=bool(
                            power_disable_rag_for_casual
                            or runtime_options.get("current_info_mode")
                        ),
                        performance_optimizer_enabled=bool(
                            power_performance_optimizer_enabled
                        ),
                        query_rewriter_enabled=bool(
                            runtime_options["query_rewriter_enabled"]
                        ),
                        reranker_enabled=bool(runtime_options["reranker_enabled"]),
                        semantic_cache_enabled=bool(
                            runtime_options["semantic_cache_enabled"]
                            and not runtime_options.get("current_info_mode")
                        ),
                        semantic_cache_threshold=float(power_semantic_cache_threshold),
                        semantic_cache_ttl_seconds=int(
                            power_semantic_cache_ttl_seconds
                        ),
                        latency_budget_enabled=bool(power_latency_budget_enabled),
                        retrieval_eval_enabled=bool(power_retrieval_eval_enabled),
                        live_music_chart_enabled=bool(live_music_chart_enabled),
                        live_music_chart_limit=int(live_music_chart_limit),
                        live_music_chart_timeout_seconds=int(
                            live_music_chart_timeout_seconds
                        ),
                        live_web_fallback_enabled=bool(live_web_fallback_enabled),
                        live_web_fallback_provider=live_web_fallback_provider,
                        tavily_api_key=tavily_api_key,
                        live_web_fallback_max_results=int(
                            live_web_fallback_max_results
                        ),
                        live_web_fallback_timeout_seconds=int(
                            live_web_fallback_timeout_seconds
                        ),
                        live_web_fallback_min_sources=int(
                            live_web_fallback_min_sources
                        ),
                        live_web_fallback_include_raw_content=bool(
                            live_web_fallback_include_raw_content
                        ),
                        live_web_fallback_max_content_chars=int(
                            live_web_fallback_max_content_chars
                        ),
                        live_web_fallback_auto_save_to_kb=bool(
                            live_web_fallback_auto_save_to_kb
                        ),
                        live_web_fallback_ttl_hours=int(live_web_fallback_ttl_hours),
                        live_web_fallback_force_for_current=bool(
                            live_web_fallback_force_for_current
                            and runtime_options.get("auto_live_scraping_needed")
                        ),
                        live_web_fallback_topic=(
                            runtime_options.get("auto_live_scraping_topic")
                            if runtime_options.get("auto_live_scraping_needed")
                            else live_web_fallback_topic
                        ),
                    )
                    if not isinstance(meta, dict):
                        meta = {}
                    request_latency_seconds = time.time() - request_started_at
                    record_model_performance_from_meta(
                        route=route,
                        meta=meta,
                        latency_seconds=request_latency_seconds,
                        answer_text=answer,
                    )
                    answer = sanitize_public_ai_answer(
                        answer,
                        meta,
                        show_technical_detail=bool(
                            st.session_state.admin_authenticated
                            and st.session_state.show_debug
                        ),
                    )
                    if isinstance(meta, dict):
                        meta["route_reason"] = route.get("routing_reason", "")
                        meta["route_complexity_score"] = route.get(
                            "complexity_score", 0
                        )
                        meta["route_complexity_signals"] = route.get(
                            "complexity_signals", []
                        )
                        meta["runtime_strategy"] = runtime_options.get(
                            "strategy_label", ""
                        )
                        meta["runtime_rag_enabled"] = bool(
                            runtime_options.get("enable_rag")
                        )
                        meta["runtime_quality_verifier_enabled"] = bool(
                            runtime_options.get("quality_verifier_enabled")
                        )
                        meta["streaming_preview_enabled"] = bool(
                            runtime_options.get("streaming_preview_enabled")
                        )
                        meta["stable_typewriter_display"] = True
                        meta["auto_retry_on_model_error_enabled"] = parse_bool(
                            get_secret("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", True),
                            default=True,
                        )
                        meta["token_saver_enabled"] = bool(token_saver_enabled)
                        meta["token_saver_mode"] = _token_saver_mode()
                        meta["token_saver_max_completion_tokens"] = int(
                            runtime_options.get("max_completion_tokens", 0) or 0
                        )
                        meta["ui_loading_status_title"] = loading_title
                        meta["ui_loading_status_thinking"] = bool(
                            route.get("thinking_direct_to_capable")
                            or route.get("thinking_mode")
                        )
                        meta["auto_live_scraping_needed"] = bool(
                            runtime_options.get("auto_live_scraping_needed")
                        )
                        meta["auto_live_scraping_reason"] = runtime_options.get(
                            "auto_live_scraping_reason",
                            "",
                        )
                        meta["auto_live_scraping_topic"] = runtime_options.get(
                            "auto_live_scraping_topic",
                            "auto",
                        )
                        meta["current_info_mode"] = bool(
                            runtime_options.get("current_info_mode")
                        )
                        meta["kb_disabled_for_current_info"] = bool(
                            runtime_options.get("current_info_mode")
                        )
                        meta["cache_disabled_for_current_info"] = bool(
                            runtime_options.get("current_info_mode")
                        )
                        meta["direct_tavily_context_used"] = bool(
                            current_info_meta.get("live_context_used")
                        )
                        meta["direct_tavily_error"] = current_info_meta.get(
                            "live_context_error",
                            "",
                        )
                        meta["direct_tavily_sources"] = current_info_meta.get(
                            "live_sources",
                            [],
                        )
                        meta["direct_tavily_cache_hit"] = bool(
                            current_info_meta.get("live_cache_hit")
                        )
                        meta["kb_v2_used"] = bool(
                            current_info_meta.get("kb_v2_used")
                        )
                        meta["kb_v2_sources"] = current_info_meta.get(
                            "kb_v2_sources",
                            [],
                        )
                        meta["kb_v2_error"] = current_info_meta.get(
                            "kb_v2_error",
                            "",
                        )
                    restore_active_model_to_cheap(route.get("primary_model"))
                    answer = sanitize_public_answer(
                        answer,
                        meta=meta,
                        route=route,
                    )
                    is_public_error_answer = is_public_connection_error_answer(
                        answer,
                        meta=meta,
                    )

                    if (
                        bool(st.session_state.get("sound_enabled", False))
                        and not is_public_error_answer
                    ):
                        render_answer_ready_sound_script(
                            sound_key=f"normal-{len(st.session_state.chat_messages)}",
                        )

                    render_answer_typewriter_display(
                        placeholder,
                        answer,
                        is_error=is_public_error_answer,
                    )
                    render_auto_scroll_script(
                        target="latest",
                        delay_ms=120,
                    )
                    if (
                        bool(st.session_state.get("sound_enabled", False))
                        and not is_public_error_answer
                    ):
                        render_answer_ready_sound_script(
                            sound_key=f"normal-{len(st.session_state.chat_messages)}",
                        )
                    st.session_state.last_answer_meta = meta or {}
                    final_model = (
                        (meta or {}).get("active_model_final")
                        or (meta or {}).get("model_requested")
                        or cfg["model"]
                    )
                    answer_pdf_download_button(
                        answer, key="download_pdf_latest_answer", model_name=final_model
                    )
                    caption_text = f"Model aktif: {final_model}"
                    if (meta or {}).get("power_intent"):
                        caption_text += f" • intent: {(meta or {}).get('power_intent')}"
                    if (meta or {}).get("self_verified_by"):
                        caption_text += (
                            f" • self-check: {(meta or {}).get('self_verified_by')}"
                        )
                    route_reason = str(route.get("routing_reason") or "")
                    if route_reason:
                        caption_text += f" • rute: {route_reason}"
                    strategy_label = str(runtime_options.get("strategy_label") or "")
                    if strategy_label and st.session_state.admin_authenticated:
                        caption_text += f" • strategi: {strategy_label}"
                    if st.session_state.admin_authenticated:
                        caption_text += (
                            f" • skor kompleksitas: {route.get('complexity_score', 0)}"
                        )
                    if (meta or {}).get("auto_model_retry_success"):
                        caption_text += (
                            f" • retry model: {(meta or {}).get('auto_model_retry_final_model')}"
                        )
                    if (meta or {}).get("power_kb_sources") and (
                        bool((meta or {}).get("show_kb_sources", False))
                        or st.session_state.admin_authenticated
                    ):
                        kb_sources = (meta or {}).get("power_kb_sources") or []
                        caption_text += f" • KB: {len(kb_sources)} sumber"
                        if st.session_state.admin_authenticated:
                            titles = [
                                str(item.get("citation") or item.get("title") or "")[
                                    :70
                                ]
                                for item in kb_sources[:3]
                                if str(
                                    item.get("citation") or item.get("title") or ""
                                ).strip()
                            ]
                            if titles:
                                caption_text += " • sumber: " + "; ".join(titles)
                    if st.session_state.admin_authenticated:
                        consulted = (meta or {}).get("consulted_models") or []
                        expensive_used = (meta or {}).get(
                            "expensive_fallback_used", False
                        )
                        if consulted:
                            caption_text += " • konsultasi: " + ", ".join(
                                str(item) for item in consulted[:4]
                            )
                        if route.get("thinking_direct_to_capable"):
                            caption_text += " • thinking mode: memakai model capable"
                        elif expensive_used:
                            caption_text += " • model menengah/mahal dipakai karena jawaban hemat kurang cukup"
                    st.caption(caption_text)
                    render_feedback_controls(meta, answer, key_prefix="latest_feedback")
            except Exception as exc:
                try:
                    record_model_performance_from_meta(
                        route=locals().get("route", {}),
                        meta={},
                        latency_seconds=time.time() - float(locals().get("request_started_at", time.time())),
                        error_text=str(exc),
                    )
                except Exception:
                    pass

                route_for_retry = locals().get("route", {}) or {}
                runtime_for_retry = locals().get("runtime_options", {}) or {}
                current_info_memory_for_retry = locals().get("current_info_memory_text", "")

                retry_answer, retry_meta = retry_power_answer_with_active_models(
                    make_public_ai_error_message(),
                    {
                        "public_error_sanitized": True,
                        "error_class": exc.__class__.__name__,
                        "hidden_public_error_detail": str(exc)[:5000],
                        "retry_trigger": "outer_exception",
                    },
                    {
                        "api_url": api_url,
                        "api_key": api_key,
                        "model": route_for_retry.get("primary_model") or cfg.get("model"),
                        "system_prompt": cfg["persona"],
                        "user_text": user_input,
                        "base_memory_text": current_info_memory_for_retry,
                        "recent_messages": compact_recent_messages_for_token_saver(
                            st.session_state.chat_messages,
                            limit=web_history_limit,
                            recent_full=web_history_recent_full,
                        ) if "compact_recent_messages_for_token_saver" in globals() else st.session_state.chat_messages[:-1][-4:],
                        "fallback_models": route_for_retry.get("cheap_fallback_models", []),
                        "expensive_fallback_models": route_for_retry.get("expensive_fallback_models", []),
                        "allow_expensive_fallback": True,
                        "max_expensive_models": route_for_retry.get("max_expensive_models", 1),
                        "temperature": float(cfg.get("temperature", 0.3)),
                        "max_completion_tokens": int(runtime_for_retry.get("max_completion_tokens") or cfg.get("max_completion_tokens", 1200)),
                        "timeout": int(runtime_for_retry.get("timeout") or request_timeout_seconds or 45),
                        "smart_model_router": bool(cfg.get("smart_model_router", True)),
                        "return_to_primary": False,
                        "max_smart_models": route_for_retry.get("max_smart_models", 2),
                        "store": power_store,
                        "user_id": "web-admin" if st.session_state.get("admin_authenticated", False) else "web-public",
                        "channel": "web",
                    },
                    retry_depth=0,
                )

                if not is_retryable_model_error_answer(
                    retry_answer,
                    meta=retry_meta,
                ):
                    answer = retry_answer
                    meta = retry_meta if isinstance(retry_meta, dict) else {}
                    meta["outer_exception_retry_success"] = True
                else:
                    local_fallback_answer, local_fallback_meta = build_local_safe_fallback_answer(
                        user_input,
                        failure_reason=str(exc)[:500],
                    )
                    if local_fallback_answer:
                        answer = local_fallback_answer
                        meta = retry_meta if isinstance(retry_meta, dict) else {}
                        meta.update(local_fallback_meta)
                        meta["outer_exception_local_fallback"] = True
                    else:
                        meta = retry_meta if isinstance(retry_meta, dict) else {
                            "public_error_sanitized": True,
                            "error_class": exc.__class__.__name__,
                            "hidden_public_error_detail": str(exc)[:5000],
                        }
                        answer = make_public_ai_error_message()

                st.session_state.last_answer_meta = meta

                existing_placeholder = locals().get("placeholder")

                if existing_placeholder is not None:
                    if is_public_connection_error_answer(answer, meta=meta):
                        existing_placeholder.warning(answer)
                    else:
                        existing_placeholder.markdown(answer)
                else:
                    with st.chat_message("assistant"):
                        if is_public_connection_error_answer(answer, meta=meta):
                            st.warning(answer)
                        else:
                            st.markdown(answer)

                if not is_public_connection_error_answer(answer, meta=meta):
                    render_answer_ready_sound_script(
                        sound_key=f"model-{len(st.session_state.chat_messages)}-{int(time.time() * 1000)}",
                    )
                    render_auto_scroll_script(
                        target="latest",
                        delay_ms=80,
                    )

        if local_reply:
            with st.chat_message("assistant"):
                st.markdown(answer)
                render_sound_unlock_script()
                if (
                    bool(st.session_state.get("sound_enabled", False))
                    and not is_public_connection_error_answer(answer, meta=meta)
                ):
                    render_answer_ready_sound_script(
                        sound_key=f"local-{len(st.session_state.chat_messages)}-{int(time.time() * 1000)}",
                    )
                    render_auto_scroll_script(
                        target="latest",
                        delay_ms=80,
                    )
                answer_pdf_download_button(answer, key="download_pdf_local_reply")

        if not local_reply:
            cache_saved = save_frequent_question_cached_answer(
                user_input,
                answer,
                meta or {},
            )

            if cache_saved and isinstance(meta, dict):
                meta["frequent_question_cache_saved"] = True

        st.session_state.chat_messages.append(
            {"role": "assistant", "content": answer, "meta": meta or {}}
        )

        if (
            meta
            and st.session_state.admin_authenticated
            and st.session_state.show_debug
        ):
            with st.expander("Debug response admin"):
                st.json(meta)

    # Ruang aman terakhir agar input floating tidak menutupi pesan terakhir, termasuk pesan yang baru dibuat.
    st.markdown(
        '<div class="auto-scroll-anchor"></div>'
        '<div class="chat-input-safe-space"></div>',
        unsafe_allow_html=True,
    )

    # Tiny refresh delay for hosting online stability
    time.sleep(0.03)


def render_power_features_admin_panel() -> None:
    # =========================
    # Power Features Admin Panel
    # =========================
    if st.session_state.get("admin_authenticated", False):
        render_admin_production_dashboard()

    if power_features_enabled and st.session_state.get("admin_authenticated", False):
        try:
            with st.expander(
                "⚡ Pusat Fitur Pintar: Knowledge Base, Memory, Biaya, Optimizer",
                expanded=True,
            ):
                st.caption(
                    "Kelola fitur pintar dari satu tempat. Mulai dari Upload File untuk knowledge base, lalu pantau Usage dan Optimizer agar model tetap hemat dan stabil."
                )
                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    st.metric("RAG", "ON" if power_rag_enabled else "OFF")
                with col_b:
                    st.metric(
                        "SQLite Memory",
                        "ON" if power_persistent_memory_enabled else "OFF",
                    )
                with col_c:
                    st.metric(
                        "Self-check", "ON" if power_self_verification_enabled else "OFF"
                    )

                tabs_power = st.tabs(
                    [
                        "📚 Knowledge Base",
                        "🧠 Memory",
                        "💰 Usage",
                        "🛠️ Optimizer",
                        "🧪 Benchmark",
                        "🧠 Learning Loop",
                        "✅ Quality Control",
                        "⚡ Performance",
                    ]
                )
                with tabs_power[0]:
                    kb_stats = power_store.knowledge_stats()
                    c1, c2, c3, c4, c5 = st.columns(5)
                    c1.metric("Dokumen", kb_stats.get("documents", 0))
                    c2.metric("Chunks", kb_stats.get("chunks", 0))
                    c3.metric("Koleksi", kb_stats.get("collections", 0))
                    c4.metric("Pinned", kb_stats.get("pinned", 0))
                    c5.metric("Karakter", kb_stats.get("characters", 0))

                    st.caption(
                        "Knowledge base premium: koleksi/workspace, tags, deduplikasi dokumen, hybrid search, sitasi sumber, dan pin dokumen penting."
                    )
                    kb_upload_tabs = st.tabs(
                        [
                            "Upload File",
                            "Tambah Manual",
                            "Cari",
                            "Koleksi",
                            "Kelola",
                            "Auto Update",
                        ]
                    )

                    with kb_upload_tabs[0]:
                        uploaded_kb = st.file_uploader(
                            "Upload file ke knowledge base",
                            type=[
                                "txt",
                                "md",
                                "markdown",
                                "csv",
                                "json",
                                "jsonl",
                                "pdf",
                                "docx",
                                "xlsx",
                                "xlsm",
                                "log",
                                "py",
                                "js",
                                "ts",
                                "html",
                                "css",
                                "xml",
                            ],
                            accept_multiple_files=True,
                            help="PDF/DOCX/XLSX membutuhkan library terkait. Jika tidak tersedia, sistem akan memberi pesan gagal ekstrak tanpa membuat app crash.",
                        )
                        source_label = st.text_input(
                            "Label sumber",
                            value="streamlit-upload",
                            key="kb_source_label",
                        )
                        upload_collection = st.text_input(
                            "Koleksi/workspace",
                            value="Default",
                            key="kb_upload_collection",
                        )
                        upload_tags = st.text_input(
                            "Tags",
                            value="",
                            placeholder="contoh: sop, produk, skripsi",
                            key="kb_upload_tags",
                        )
                        col_up1, col_up2 = st.columns(2)
                        with col_up1:
                            upload_replace = st.checkbox(
                                "Replace jika dokumen sama",
                                value=False,
                                key="kb_upload_replace",
                            )
                        with col_up2:
                            upload_pinned = st.checkbox(
                                "Pin/prioritaskan dokumen",
                                value=False,
                                key="kb_upload_pinned",
                            )
                        if uploaded_kb and st.button(
                            "➕ Masukkan file ke Knowledge Base",
                            use_container_width=True,
                            key="auto_btn_3286",
                        ):
                            added = []
                            max_bytes = (
                                max(1, int(power_kb_max_file_mb or 12)) * 1024 * 1024
                            )
                            for up in uploaded_kb:
                                try:
                                    raw_bytes = up.read()
                                    if len(raw_bytes) > max_bytes:
                                        added.append(
                                            f"{up.name}: dilewati, ukuran > {power_kb_max_file_mb} MB"
                                        )
                                        continue
                                    content, kind = extract_text_from_file_bytes(
                                        up.name, raw_bytes
                                    )
                                    if not str(content or "").strip():
                                        added.append(
                                            f"{up.name}: gagal, tidak ada teks yang bisa diambil"
                                        )
                                        continue
                                    doc_id, chunks = power_store.add_document(
                                        title=up.name,
                                        text=content,
                                        source=f"{source_label}:{kind}",
                                        collection=upload_collection,
                                        tags=upload_tags,
                                        metadata={
                                            "filename": up.name,
                                            "kind": kind,
                                            "size_bytes": len(raw_bytes),
                                        },
                                        replace_existing=bool(upload_replace),
                                        pinned=bool(upload_pinned),
                                    )
                                    if chunks == 0:
                                        added.append(
                                            f"{up.name}: sudah ada sebagai doc {doc_id} (tidak diduplikasi)"
                                        )
                                    else:
                                        added.append(
                                            f"{up.name}: doc {doc_id}, {chunks} chunk, tipe {kind}"
                                        )
                                except Exception as exc:
                                    added.append(f"{up.name}: gagal - {exc}")
                            st.success("\n".join(added))

                    with kb_upload_tabs[1]:
                        manual_title = st.text_input(
                            "Judul dokumen manual", key="kb_manual_title"
                        )
                        manual_source = st.text_input(
                            "Sumber manual",
                            value="streamlit-manual",
                            key="kb_manual_source",
                        )
                        manual_collection = st.text_input(
                            "Koleksi/workspace",
                            value="Default",
                            key="kb_manual_collection",
                        )
                        manual_tags = st.text_input(
                            "Tags", value="", key="kb_manual_tags"
                        )
                        manual_pinned = st.checkbox(
                            "Pin/prioritaskan dokumen manual",
                            value=False,
                            key="kb_manual_pinned",
                        )
                        manual_text = st.text_area(
                            "Isi dokumen/manual knowledge",
                            height=220,
                            key="kb_manual_text",
                        )
                        if (
                            st.button(
                                "💾 Simpan manual ke Knowledge Base",
                                use_container_width=True,
                                key="auto_btn_3309",
                            )
                            and manual_text.strip()
                        ):
                            doc_id, chunks = power_store.add_document(
                                title=manual_title.strip() or "Catatan manual",
                                text=manual_text,
                                source=manual_source.strip() or "streamlit-manual",
                                collection=manual_collection,
                                tags=manual_tags,
                                pinned=bool(manual_pinned),
                            )
                            if chunks:
                                st.success(
                                    f"Tersimpan. Doc ID: {doc_id}, chunks: {chunks}"
                                )
                            else:
                                st.warning("Tidak ada teks yang bisa disimpan.")

                    with kb_upload_tabs[2]:
                        collection_rows = power_store.knowledge_collections()
                        collection_options = [""] + [
                            str(row.get("collection") or "Default")
                            for row in collection_rows
                        ]
                        kb_query = st.text_input(
                            "Cari isi knowledge base", key="power_kb_query"
                        )
                        kb_collection = st.selectbox(
                            "Filter koleksi",
                            collection_options,
                            format_func=lambda x: "Semua koleksi" if not x else x,
                            key="kb_search_collection",
                        )
                        kb_limit = st.slider(
                            "Jumlah hasil",
                            3,
                            15,
                            int(power_rag_top_k or 5),
                            key="kb_search_limit",
                        )
                        if kb_query:
                            results = power_store.search_documents(
                                kb_query, limit=kb_limit, collection=kb_collection
                            )
                            if not results:
                                st.info("Belum ada potongan knowledge base yang cocok.")
                            for item in results:
                                citation = (
                                    item.get("citation")
                                    or f"{item.get('title')} · chunk {item.get('chunk_index')}"
                                )
                                with st.expander(
                                    f"{citation} · score {item.get('score')}"
                                ):
                                    st.caption(
                                        f"Koleksi: {item.get('collection') or 'Default'} | Sumber: {item.get('source')} | Tags: {item.get('tags') or '-'}"
                                    )
                                    st.write(str(item.get("content") or "")[:1800])

                    with kb_upload_tabs[3]:
                        st.caption(
                            "Koleksi membantu memisahkan dokumen seperti SOP, produk, skripsi, e-learning, dan referensi internal."
                        )
                        collections = power_store.knowledge_collections()
                        st.dataframe(
                            collections, use_container_width=True, hide_index=True
                        )

                    with kb_upload_tabs[4]:
                        manage_cols = power_store.knowledge_collections()
                        manage_options = [""] + [
                            str(row.get("collection") or "Default")
                            for row in manage_cols
                        ]
                        manage_collection = st.selectbox(
                            "Tampilkan koleksi",
                            manage_options,
                            format_func=lambda x: "Semua koleksi" if not x else x,
                            key="kb_manage_collection",
                        )
                        docs = power_store.list_documents(
                            limit=100, collection=manage_collection
                        )
                        st.dataframe(docs, use_container_width=True, hide_index=True)
                        col_a, col_b = st.columns(2)
                        with col_a:
                            delete_id = st.text_input(
                                "Hapus Doc ID", key="kb_delete_doc_id"
                            )
                            if (
                                st.button(
                                    "🗑️ Hapus dokumen",
                                    use_container_width=True,
                                    key="auto_btn_3338",
                                )
                                and delete_id.strip()
                            ):
                                ok = (
                                    power_store.delete_document(int(delete_id))
                                    if delete_id.strip().isdigit()
                                    else False
                                )
                                (
                                    st.success(f"Dokumen ID {delete_id} dihapus.")
                                    if ok
                                    else st.error(
                                        "Doc ID tidak ditemukan/gagal dihapus."
                                    )
                                )
                        with col_b:
                            detail_id = st.text_input(
                                "Preview Doc ID", key="kb_detail_doc_id"
                            )
                            if (
                                st.button(
                                    "👁️ Preview dokumen",
                                    use_container_width=True,
                                    key="auto_btn_3343",
                                )
                                and detail_id.strip()
                            ):
                                doc = (
                                    power_store.get_document(
                                        int(detail_id), max_chars=6000
                                    )
                                    if detail_id.strip().isdigit()
                                    else {}
                                )
                                if doc:
                                    st.markdown(f"**{doc.get('title')}**")
                                    st.caption(
                                        f"Source: {doc.get('source')} | Chunks: {doc.get('chunks')}"
                                    )
                                    st.text_area(
                                        "Preview",
                                        value=str(doc.get("preview") or ""),
                                        height=260,
                                    )
                                else:
                                    st.error("Doc ID tidak ditemukan.")
                        st.markdown("**Metadata & Prioritas**")
                        col_m1, col_m2, col_m3 = st.columns(3)
                        with col_m1:
                            meta_id = st.text_input(
                                "Doc ID metadata", key="kb_meta_doc_id"
                            )
                        with col_m2:
                            meta_collection = st.text_input(
                                "Koleksi baru",
                                value="Default",
                                key="kb_meta_collection",
                            )
                        with col_m3:
                            meta_tags = st.text_input(
                                "Tags baru", value="", key="kb_meta_tags"
                            )
                        col_pin1, col_pin2, col_pin3 = st.columns(3)
                        with col_pin1:
                            if (
                                st.button(
                                    "🏷️ Update metadata",
                                    use_container_width=True,
                                    key="kb_update_metadata_btn",
                                )
                                and meta_id.strip()
                            ):
                                ok = (
                                    power_store.update_document_metadata(
                                        int(meta_id),
                                        collection=meta_collection,
                                        tags=meta_tags,
                                    )
                                    if meta_id.strip().isdigit()
                                    else False
                                )
                                (
                                    st.success("Metadata diperbarui.")
                                    if ok
                                    else st.error("Doc ID tidak ditemukan/gagal.")
                                )
                        with col_pin2:
                            if (
                                st.button(
                                    "📌 Pin dokumen",
                                    use_container_width=True,
                                    key="kb_pin_doc_btn",
                                )
                                and meta_id.strip()
                            ):
                                ok = (
                                    power_store.set_document_pinned(int(meta_id), True)
                                    if meta_id.strip().isdigit()
                                    else False
                                )
                                (
                                    st.success("Dokumen diprioritaskan.")
                                    if ok
                                    else st.error("Doc ID tidak ditemukan/gagal.")
                                )
                        with col_pin3:
                            if (
                                st.button(
                                    "📍 Unpin dokumen",
                                    use_container_width=True,
                                    key="kb_unpin_doc_btn",
                                )
                                and meta_id.strip()
                            ):
                                ok = (
                                    power_store.set_document_pinned(int(meta_id), False)
                                    if meta_id.strip().isdigit()
                                    else False
                                )
                                (
                                    st.success("Prioritas dilepas.")
                                    if ok
                                    else st.error("Doc ID tidak ditemukan/gagal.")
                                )
                        if st.button(
                            "🔁 Rebuild index Knowledge Base",
                            use_container_width=True,
                            key="auto_btn_3351",
                        ):
                            docs_count, chunks_count = (
                                power_store.rebuild_knowledge_index()
                            )
                            st.success(
                                f"Index dibangun ulang. Dokumen: {docs_count}, chunks: {chunks_count}"
                            )

                    with kb_upload_tabs[5]:
                        st.caption(
                            "Ambil informasi terbaru dari RSS/HTML publik lalu simpan otomatis ke SQLite Knowledge Base. Cocok dijalankan manual dari admin atau harian via GitHub Actions."
                        )
                        st.code(
                            f"Sources: {kb_scraper_sources_file}\nState: {kb_scraper_state_file}\nDB: {power_db_path}"
                        )

                        st.markdown("##### KB Manager v2: incremental, hash, summary, audit log")
                        try:
                            kb_overview = kb_manager_overview(power_db_path)
                            kb_m1, kb_m2, kb_m3, kb_m4 = st.columns(4)
                            kb_m1.metric("Doc aktif v2", kb_overview.get("documents_active", 0))
                            kb_m2.metric("Doc archived", kb_overview.get("documents_archived", 0))
                            kb_m3.metric("Chunks v2", kb_overview.get("chunks_active", 0))
                            kb_m4.metric("Live cache", kb_overview.get("live_cache", 0))

                            recent_logs = kb_overview.get("recent_logs") or []
                            if recent_logs:
                                with st.expander("Audit log KB terbaru"):
                                    st.dataframe(
                                        recent_logs,
                                        use_container_width=True,
                                        hide_index=True,
                                    )
                        except Exception as exc:
                            st.warning(f"KB Manager v2 belum bisa dibaca: {exc}")

                        st.markdown("##### Maintenance KB v2")
                        col_kbv2_a, col_kbv2_b, col_kbv2_c = st.columns(3)

                        with col_kbv2_a:
                            if st.button(
                                "🧹 Reset live cache",
                                use_container_width=True,
                                key="kb_v2_clear_live_cache",
                            ):
                                try:
                                    deleted = clear_live_cache(power_db_path)
                                    st.success(f"Live cache dihapus: {deleted} baris.")
                                except Exception as exc:
                                    st.error(f"Gagal reset live cache: {exc}")

                        with col_kbv2_b:
                            if st.button(
                                "🗑️ Hapus archived v2",
                                use_container_width=True,
                                key="kb_v2_delete_archived",
                            ):
                                try:
                                    result = delete_archived_documents(power_db_path)
                                    st.success(
                                        f"Archived terhapus. Dokumen: {result.get('documents_deleted', 0)}, "
                                        f"chunks: {result.get('chunks_deleted', 0)}"
                                    )
                                except Exception as exc:
                                    st.error(f"Gagal hapus archived: {exc}")

                        with col_kbv2_c:
                            audit_rows = export_kb_audit_log(power_db_path, limit=500)
                            st.download_button(
                                "⬇️ Export audit log",
                                data=json.dumps(
                                    audit_rows,
                                    ensure_ascii=False,
                                    indent=2,
                                ),
                                file_name="kb_audit_log_v2.json",
                                mime="application/json",
                                use_container_width=True,
                                key="kb_v2_export_audit_log",
                            )

                        with st.expander("Archive dokumen aktif berdasarkan source_id"):
                            archive_source_id = st.text_input(
                                "source_id yang akan di-archive",
                                value="",
                                key="kb_v2_archive_source_id",
                            )
                            if st.button(
                                "Archive source_id",
                                use_container_width=True,
                                key="kb_v2_archive_source_button",
                            ) and archive_source_id.strip():
                                try:
                                    result = archive_documents_by_source(
                                        power_db_path,
                                        archive_source_id.strip(),
                                    )
                                    st.success(
                                        f"Diarsipkan. Dokumen: {result.get('documents_archived', 0)}, "
                                        f"chunks: {result.get('chunks_archived', 0)}"
                                    )
                                except Exception as exc:
                                    st.error(f"Gagal archive source: {exc}")


                        if st.button(
                            "🧩 Buat/isi kb_sources.json relevan",
                            use_container_width=True,
                            key="kb_manager_write_sources",
                        ):
                            created = ensure_kb_sources_file(
                                kb_scraper_sources_file,
                                json.loads(DEFAULT_RELEVANT_KB_SOURCES_JSON)["sources"],
                            )
                            if created:
                                st.success("kb_sources.json dibuat dari sumber relevan default.")
                            else:
                                st.info("kb_sources.json sudah ada, tidak ditimpa.")

                        try:
                            scraper_sources = load_kb_scraper_sources(
                                kb_scraper_sources_file
                            )
                        except Exception as exc:
                            scraper_sources = []
                            st.error(f"Gagal membaca sources JSON: {exc}")

                        if scraper_sources:
                            preview_rows = []
                            for item in scraper_sources:
                                preview_rows.append(
                                    {
                                        "aktif": bool(item.get("enabled", True)),
                                        "nama": item.get("name") or item.get("url"),
                                        "tipe": item.get("type", "rss"),
                                        "koleksi": item.get(
                                            "collection", "Auto Update"
                                        ),
                                        "max_items": item.get("max_items", 5),
                                        "url": item.get("url", ""),
                                    }
                                )
                            st.dataframe(
                                preview_rows, use_container_width=True, hide_index=True
                            )
                        else:
                            st.warning(
                                "Belum ada sumber. Buat file kb_sources.json di root repo. Contohnya sudah tersedia di paket ZIP."
                            )

                        col_auto1, col_auto2, col_auto3 = st.columns(3)
                        with col_auto1:
                            auto_max_items = st.number_input(
                                "Max item/sumber",
                                min_value=1,
                                max_value=30,
                                value=max(1, int(kb_scraper_max_items_per_source or 5)),
                                step=1,
                                key="kb_auto_max_items",
                            )
                        with col_auto2:
                            auto_dry_run = st.checkbox(
                                "Dry run saja", value=False, key="kb_auto_dry_run"
                            )
                        with col_auto3:
                            auto_force = st.checkbox(
                                "Force ingest ulang", value=False, key="kb_auto_force"
                            )

                        if st.button(
                            "🌐 Update Knowledge Base dari sumber online sekarang",
                            use_container_width=True,
                            key="kb_auto_update_now",
                        ):
                            try:
                                report = run_daily_kb_update(
                                    db_path=power_db_path,
                                    sources_path=kb_scraper_sources_file,
                                    state_path=kb_scraper_state_file,
                                    max_items_per_source=int(auto_max_items),
                                    timeout=int(kb_scraper_timeout or 20),
                                    dry_run=bool(auto_dry_run),
                                    force=bool(auto_force),
                                )
                                st.success(
                                    f"Selesai. Dokumen baru: {report.get('added_documents', 0)}, "
                                    f"chunks baru: {report.get('added_chunks', 0)}, "
                                    f"skip existing: {report.get('skipped_existing', 0)}, "
                                    f"error: {report.get('errors', 0)}"
                                )
                                items = report.get("items") or []
                                if items:
                                    st.dataframe(
                                        items, use_container_width=True, hide_index=True
                                    )
                            except Exception as exc:
                                st.error(f"Auto update gagal: {exc}")

                        if st.button(
                            "🧠 Incremental update v2: hash + summary + mirror ke KB",
                            use_container_width=True,
                            key="kb_manager_incremental_update_now",
                        ):
                            try:
                                report = advanced_incremental_kb_update(
                                    db_path=power_db_path,
                                    sources_path=kb_scraper_sources_file,
                                    power_store=power_store,
                                    max_items_per_source=int(auto_max_items),
                                    timeout=int(kb_scraper_timeout or 20),
                                    dry_run=bool(auto_dry_run),
                                    force=bool(auto_force),
                                    max_chunk_chars=1800,
                                )
                                st.success(
                                    f"KB v2 selesai. Updated: {report.get('documents_updated', 0)}, "
                                    f"skipped: {report.get('documents_skipped', 0)}, "
                                    f"chunks: {report.get('chunks_added', 0)}, "
                                    f"error: {report.get('errors', 0)}"
                                )
                                items = report.get("items") or []
                                if items:
                                    st.dataframe(
                                        items,
                                        use_container_width=True,
                                        hide_index=True,
                                    )
                            except Exception as exc:
                                st.error(f"Incremental KB v2 gagal: {exc}")

                with tabs_power[1]:
                    mem_text = st.text_area(
                        "Tambah memory permanen SQLite",
                        height=100,
                        placeholder="Contoh: User ingin jawaban profesional, praktis, dan kode siap tempel.",
                    )
                    if (
                        st.button(
                            "💾 Simpan memory permanen",
                            use_container_width=True,
                            key="auto_btn_3357",
                        )
                        and mem_text.strip()
                    ):
                        mem_id = power_store.add_memory(
                            mem_text, user_id="global", tags="streamlit-admin"
                        )
                        st.success(f"Memory tersimpan. ID: {mem_id}")
                    mem_query = st.text_input("Cari memory", key="power_mem_query")
                    if mem_query:
                        st.dataframe(
                            power_store.search_memories(
                                mem_query, user_id="global", limit=20
                            ),
                            use_container_width=True,
                            hide_index=True,
                        )

                with tabs_power[2]:
                    usage = power_store.usage_summary(days=1)
                    st.metric(
                        "Estimasi biaya 24 jam", f"Rp{usage.get('cost_idr', 0):.2f}"
                    )
                    st.metric("Request 24 jam", usage.get("requests", 0))
                    st.dataframe(
                        usage.get("by_model", []),
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.caption(
                        f"Limit harian: Rp{daily_cost_limit_idr:.0f} | Max expensive calls/hari: {max_expensive_calls_per_day}"
                    )

                with tabs_power[3]:
                    st.caption(
                        "Optimizer memakai data nyata: success rate, latency, quality score, biaya, dan circuit breaker."
                    )
                    opt_intent = st.selectbox(
                        "Lihat skor untuk intent",
                        [
                            "",
                            "quick_chat",
                            "coding",
                            "academic",
                            "livestock",
                            "health",
                            "calculation",
                            "document_question",
                            "research",
                            "creative",
                            "deep_reasoning",
                            "general",
                        ],
                        format_func=lambda x: "semua intent" if x == "" else x,
                        key="power_optimizer_intent",
                    )
                    st.dataframe(
                        power_store.model_score_rows(
                            intent=opt_intent or None, limit=120
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )
                    with st.expander("Circuit breaker / model yang sedang dikarantina"):
                        st.dataframe(
                            power_store.circuit_breaker_status(limit=120),
                            use_container_width=True,
                            hide_index=True,
                        )
                    st.caption(
                        f"Response cache: {'ON' if power_response_cache_enabled else 'OFF'} | TTL {power_response_cache_ttl_seconds}s | "
                        f"Adaptive scoring: {'ON' if power_adaptive_scoring_enabled else 'OFF'} | Circuit breaker: {'ON' if power_circuit_breaker_enabled else 'OFF'}"
                    )

                with tabs_power[4]:
                    route_preview = build_model_routing_plan()
                    bench_models = unique_models(
                        [route_preview.get("primary_model", "")]
                        + route_preview.get("active_cheap_models", [])[:4]
                        + route_preview.get("active_expensive_models", [])[:4]
                    )
                    st.write("Model yang akan dites:")
                    st.code(
                        "\n".join(bench_models[:benchmark_max_models])
                        or "Belum ada model aktif"
                    )
                    if st.button(
                        "🧪 Jalankan benchmark ringan",
                        use_container_width=True,
                        disabled=not bool(api_key and bench_models),
                        key="auto_btn_3392",
                    ):
                        results = run_model_benchmark(
                            store=power_store,
                            api_url=api_url,
                            api_key=api_key,
                            models=bench_models,
                            system_prompt=st.session_state.active_persona,
                            timeout=45,
                            max_models=benchmark_max_models,
                        )
                        st.dataframe(results, use_container_width=True, hide_index=True)
                    with st.expander("Riwayat benchmark"):
                        st.dataframe(
                            power_store.latest_benchmarks(limit=80),
                            use_container_width=True,
                            hide_index=True,
                        )

                with tabs_power[5]:
                    st.caption(
                        "Learning Loop menyimpan feedback, knowledge gap, pertanyaan berulang, dan template jawaban agar AI makin relevan."
                    )
                    dash_days = st.slider(
                        "Rentang dashboard", 1, 60, 14, key="learning_dash_days"
                    )
                    dashboard = power_store.learning_dashboard(days=int(dash_days))
                    f1, f2, f3 = st.columns(3)
                    fb = dashboard.get("feedback", {})
                    f1.metric("Feedback", fb.get("total", 0))
                    f2.metric("Positif", fb.get("positive", 0))
                    f3.metric("Negatif", fb.get("negative", 0))
                    st.markdown("**Intent paling sering**")
                    st.dataframe(
                        dashboard.get("intents", []),
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.markdown("**Pertanyaan berulang**")
                    st.dataframe(
                        dashboard.get("repeated_questions", []),
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.markdown("**Knowledge gap terbuka**")
                    gaps = power_store.list_knowledge_gaps(status="open", limit=100)
                    st.dataframe(gaps, use_container_width=True, hide_index=True)
                    col_gap1, col_gap2 = st.columns(2)
                    with col_gap1:
                        done_gap_id = st.text_input(
                            "Tandai gap selesai ID", key="learning_gap_done_id"
                        )
                    with col_gap2:
                        if (
                            st.button(
                                "✅ Tandai selesai",
                                use_container_width=True,
                                key="learning_gap_done_btn",
                            )
                            and done_gap_id.strip()
                        ):
                            ok = (
                                power_store.update_knowledge_gap_status(
                                    int(done_gap_id), status="done"
                                )
                                if done_gap_id.strip().isdigit()
                                else False
                            )
                            (
                                st.success("Gap selesai.")
                                if ok
                                else st.error("Gap ID tidak ditemukan.")
                            )
                    with st.expander("Interaksi terbaru"):
                        only_neg = st.checkbox(
                            "Tampilkan yang feedback negatif saja",
                            value=False,
                            key="learning_only_negative",
                        )
                        st.dataframe(
                            power_store.recent_interactions(
                                limit=100, only_negative=only_neg
                            ),
                            use_container_width=True,
                            hide_index=True,
                        )
                    with st.expander("Simpan template jawaban manual"):
                        tmpl_title = st.text_input(
                            "Judul template", key="learning_template_title"
                        )
                        tmpl_trigger = st.text_input(
                            "Trigger/contoh pertanyaan", key="learning_template_trigger"
                        )
                        tmpl_intent = st.selectbox(
                            "Intent template",
                            [
                                "general",
                                "quick_chat",
                                "coding",
                                "academic",
                                "livestock",
                                "health",
                                "research",
                                "creative",
                                "deep_reasoning",
                                "document_question",
                            ],
                            key="learning_template_intent",
                        )
                        tmpl_body = st.text_area(
                            "Isi template jawaban",
                            height=180,
                            key="learning_template_body",
                        )
                        if (
                            st.button(
                                "💾 Simpan template",
                                use_container_width=True,
                                key="learning_save_template_btn",
                            )
                            and tmpl_body.strip()
                        ):
                            tid = power_store.save_answer_template(
                                title=tmpl_title or "Template manual",
                                trigger_query=tmpl_trigger,
                                answer=tmpl_body,
                                intent=tmpl_intent,
                                tags="manual",
                            )
                            st.success(f"Template tersimpan #{tid}")

                with tabs_power[6]:
                    st.caption(
                        "Quality Control memantau skor jawaban, mode jawaban, verifier model, export/import KB, dan evaluasi mingguan."
                    )
                    q_days = st.slider(
                        "Rentang Quality Control", 1, 60, 14, key="quality_dash_days"
                    )
                    qdash = power_store.quality_dashboard(days=int(q_days))
                    q1, q2, q3, q4 = st.columns(4)
                    q1.metric("Jawaban dinilai", qdash.get("total", 0))
                    q2.metric("Skor rata-rata", qdash.get("avg_score", 0))
                    q3.metric("Skor rendah", qdash.get("low_count", 0))
                    q4.metric("Diverifikasi", qdash.get("verified_count", 0))

                    st.markdown("**Kualitas per mode**")
                    st.dataframe(
                        qdash.get("by_mode", []),
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.markdown("**Kualitas per intent**")
                    st.dataframe(
                        qdash.get("by_intent", []),
                        use_container_width=True,
                        hide_index=True,
                    )
                    with st.expander(
                        "Jawaban skor rendah / perlu perbaikan", expanded=False
                    ):
                        st.dataframe(
                            qdash.get("low_quality", []),
                            use_container_width=True,
                            hide_index=True,
                        )

                    st.markdown("#### Evaluasi mingguan")
                    if st.button(
                        "📊 Buat laporan evaluasi 7 hari",
                        use_container_width=True,
                        key="quality_weekly_eval_btn",
                    ):
                        report = power_store.weekly_quality_evaluation(
                            days=7, save=True
                        )
                        st.text_area(
                            "Laporan evaluasi",
                            value=report,
                            height=320,
                            key="quality_weekly_report_text",
                        )

                    st.markdown("#### Export / Import Knowledge Base")
                    ex1, ex2 = st.columns(2)
                    with ex1:
                        kb_jsonl = power_store.export_knowledge_base_jsonl(limit=5000)
                        st.download_button(
                            "⬇️ Export KB JSONL",
                            data=kb_jsonl.encode("utf-8"),
                            file_name=f"adioranye-kb-export-{datetime.now(WIB_TZ).strftime('%Y%m%d-%H%M%S')}.jsonl",
                            mime="application/jsonl",
                            use_container_width=True,
                        )
                    with ex2:
                        inter_jsonl = power_store.export_interactions_jsonl(
                            days=30, limit=5000
                        )
                        st.download_button(
                            "⬇️ Export log interaksi JSONL",
                            data=inter_jsonl.encode("utf-8"),
                            file_name=f"adioranye-interactions-{datetime.now(WIB_TZ).strftime('%Y%m%d-%H%M%S')}.jsonl",
                            mime="application/jsonl",
                            use_container_width=True,
                        )

                    import_file = st.file_uploader(
                        "Import KB dari JSONL",
                        type=["jsonl", "txt"],
                        key="quality_import_kb_jsonl",
                    )
                    if import_file and st.button(
                        "⬆️ Import JSONL ke Knowledge Base",
                        use_container_width=True,
                        key="quality_import_kb_btn",
                    ):
                        try:
                            text = import_file.read().decode("utf-8", "ignore")
                            result = power_store.import_knowledge_base_jsonl(
                                text, collection_prefix="Imported"
                            )
                            st.success(f"Import selesai: {result}")
                        except Exception as exc:
                            st.error(f"Import gagal: {exc}")

                    st.markdown("#### Mode jawaban default")
                    st.info(
                        f"Default dari secrets: {power_default_answer_mode}. Pengguna Telegram dapat mengubah mode sendiri dengan /mode hemat|pintar|riset|kritis|auto."
                    )
                    st.caption(
                        f"Quality Control: {'ON' if power_quality_control_enabled else 'OFF'} | Verifier: {'ON' if power_quality_verifier_enabled else 'OFF'} | Min score: {power_quality_min_score}"
                    )

                with tabs_power[7]:
                    st.caption(
                        "Performance Optimizer memantau retrieval, reranker, semantic cache, latency, dan maintenance SQLite."
                    )
                    perf_days = st.slider(
                        "Rentang Performance", 1, 60, 14, key="perf_dash_days"
                    )
                    perf = power_store.performance_dashboard(days=int(perf_days))
                    retrieval = perf.get("retrieval") or {}
                    p1, p2, p3, p4 = st.columns(4)
                    p1.metric("Retrieval eval", retrieval.get("total", 0))
                    p2.metric(
                        "Precision avg",
                        f"{float(retrieval.get('avg_precision') or 0):.2f}",
                    )
                    p3.metric(
                        "Similarity avg",
                        f"{float(retrieval.get('avg_similarity') or 0):.2f}",
                    )
                    p4.metric("Semantic cache", perf.get("semantic_cache_active", 0))

                    st.markdown("**Latency per intent**")
                    st.dataframe(
                        perf.get("top_intents_latency", []),
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.markdown("**Retrieval terbaru**")
                    st.dataframe(
                        perf.get("recent_retrieval", []),
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.markdown("**Sumber lambat/bermasalah**")
                    st.dataframe(
                        perf.get("slow_sources", []),
                        use_container_width=True,
                        hide_index=True,
                    )

                    st.markdown("#### Maintenance")
                    m1, m2, m3 = st.columns(3)
                    with m1:
                        if st.button(
                            "⚡ PRAGMA optimize + ANALYZE",
                            use_container_width=True,
                            key="perf_optimize_btn",
                        ):
                            st.json(power_store.optimize_database(vacuum=False))
                    with m2:
                        if st.button(
                            "🧹 Bersihkan cache respons",
                            use_container_width=True,
                            key="perf_clear_cache_btn",
                        ):
                            st.success(
                                f"Cache dihapus: {power_store.clear_response_cache()}"
                            )
                    with m3:
                        if st.button(
                            "🧱 VACUUM DB",
                            use_container_width=True,
                            key="perf_vacuum_btn",
                        ):
                            st.json(power_store.optimize_database(vacuum=True))

                    st.markdown("#### Konfigurasi aktif")
                    st.code(
                        f"PERFORMANCE={power_performance_optimizer_enabled} | REWRITE={power_query_rewriter_enabled} | "
                        f"RERANK={power_reranker_enabled} | SEMANTIC_CACHE={power_semantic_cache_enabled} | "
                        f"THRESHOLD={power_semantic_cache_threshold} | LATENCY_BUDGET={power_latency_budget_enabled}",
                        language="text",
                    )

        except Exception as exc:
            st.error("Power Features gagal dimuat, tetapi chat utama tetap aktif.")
            st.code(str(exc)[:2000])


def render_admin_page() -> None:
    render_admin_custom_css()
    st.markdown(
        """
        <div class="admin-page-shell">
            <div class="mac-windowbar admin-route-bar">
                <div class="mac-traffic">
                    <span class="mac-close"></span>
                    <span class="mac-min"></span>
                    <span class="mac-max"></span>
                </div>
                <div class="mac-window-title">adioranye admin</div>
                <div class="mac-window-actions">Private</div>
            </div>
            <div class="app-hero admin-route-hero">
                <div class="app-logo">🔐</div>
                <div class="admin-hero-copy">
                    <div class="admin-hero-kicker">Admin workspace</div>
                    <h3 class="app-title">Panel Kontrol Adioranye</h3>
                    <p class="app-subtitle">Kelola model, Telegram, Knowledge Base, cache, health check, live web, optimizer, dan maintenance dari satu halaman yang lebih rapi.</p>
                </div>
                <div class="admin-hero-actions">
                    <span class="admin-pill">🔒 Private</span>
                    <span class="admin-pill">⚙️ System Control</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.session_state.admin_authenticated:
        render_admin_settings()
        render_power_features_admin_panel()
    else:
        render_admin_login()
        st.info(
            "Gunakan URL dengan akhiran /admin untuk membuka halaman admin. Halaman utama tetap bersih untuk chat publik."
        )


def run_adioranye_router() -> None:
    """Route app pages without exposing admin controls on the public chat page.

    - /       : chat publik
    - /admin  : login dan panel admin

    st.Page/st.navigation dipakai karena Streamlit mendukung URL pathname untuk page,
    sehingga admin bisa dibuka langsung lewat /admin tanpa menampilkan menu sidebar.
    """
    apply_auto_akses_terbatas_on_boot()

    if hasattr(st, "Page") and hasattr(st, "navigation"):
        public_page = st.Page(
            render_public_page,
            title="Adioranye AI",
            icon="🤖",
            default=True,
        )
        admin_page = st.Page(
            render_admin_page,
            title="Admin",
            icon="🔐",
            url_path="admin",
            visibility="hidden",
        )
        selected_page = st.navigation([public_page, admin_page], position="hidden")
        selected_page.run()
        return

    # Fallback untuk Streamlit lama: chat tetap jalan, tetapi /admin membutuhkan
    # upgrade Streamlit agar route pathname tersedia.
    render_public_page()


run_adioranye_router()
