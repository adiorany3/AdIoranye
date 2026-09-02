import base64
import os
import inspect
import json
import re
import sqlite3
import threading
import traceback
import fcntl
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional, Deque, Set, Tuple

import requests

from ai_core import (
    ALL_SLASHAI_MODELS,
    ALL_CHEAP_MODELS,
    ALL_CAPABLE_MODELS,
    TOP_USAGE_MODEL_CANDIDATES,
    discover_available_models_from_api,
    DEFAULT_CHEAP_FALLBACK_MODELS,
    DEFAULT_EXPENSIVE_FALLBACK_MODELS,
    MODEL_PRICE_IDR,
    generate_answer,
    model_cost_tier,
    model_price,
)
from memory_store import MemoryStore, handle_local_memory_command
from power_features import get_power_store, handle_power_command, generate_power_answer
from daily_kb_scraper import run_daily_kb_update

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
DEFAULT_LOCK_FILE = "/tmp/adioranye_telegram_bot_worker.lock"
DEFAULT_RUNTIME_STATE_FILE = ".telegram_runtime_state.json"
DEFAULT_TELEGRAM_KB_UPDATE_LOCK_FILE = ".telegram_kb_update.lock"
LOCK_STALE_SECONDS = 180
WIB_TZ = ZoneInfo("Asia/Jakarta")
WITA_TZ = ZoneInfo("Asia/Makassar")
WIT_TZ = ZoneInfo("Asia/Jayapura")





TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE = (
    "Maaf, Adioranye sedang mengalami gangguan koneksi/model. "
    "Silakan coba lagi beberapa saat lagi."
)

TELEGRAM_TECHNICAL_ERROR_PATTERNS = [
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
    "creditsdepleted",
    "quota",
    "billing",
]


def telegram_parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    return str(value).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


def telegram_safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def telegram_parse_admin_chat_ids(value: Any) -> Set[str]:
    raw = str(value or "").strip()
    if not raw:
        return set()

    return {
        token.strip()
        for token in re.split(r"[,\s]+", raw)
        if token.strip()
    }

def telegram_unique_models(models: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []

    for model in models:
        model_name = str(model or "").strip()

        if not model_name or model_name in seen:
            continue

        seen.add(model_name)
        result.append(model_name)

    return result


def telegram_model_tier_rank(model: str) -> int:
    tier = model_cost_tier(model)
    if tier == "cheap":
        return 0
    if tier in {"medium", "menengah"}:
        return 1
    return 2



def telegram_model_is_free(model: str) -> bool:
    model_name = str(model or "").strip()
    lower_name = model_name.lower()

    if "free" in lower_name:
        return True

    explicit_price = MODEL_PRICE_IDR.get(model_name) or MODEL_PRICE_IDR.get(lower_name)
    if not isinstance(explicit_price, dict):
        return False

    return int(explicit_price.get("input", 0) or 0) == 0 and int(explicit_price.get("output", 0) or 0) == 0


def telegram_model_is_nano(model: str) -> bool:
    return "nano" in str(model or "").lower()


def telegram_free_nano_priority_rank(model: str) -> int:
    if telegram_model_is_free(model):
        return 0
    if telegram_model_is_nano(model):
        return 1
    if telegram_model_tier_rank(model) == 0:
        return 2
    if telegram_model_tier_rank(model) == 1:
        return 3
    if telegram_model_tier_rank(model) == 2:
        return 4
    return 5


def telegram_sort_models_for_simple_chat(
    models: List[str],
    health_cache: Dict[str, Dict[str, Any]] | None = None,
) -> List[str]:
    """Percakapan sederhana: free -> nano -> cheap, lalu latency/harga."""
    health_cache = health_cache or {}
    unique = telegram_unique_models(models)

    def sort_key(model: str) -> tuple[int, float, int, int, str]:
        latency = 999999.0
        try:
            value = (health_cache.get(model, {}) or {}).get("latency_ms")
            if value is not None:
                latency = float(value)
        except Exception:
            latency = 999999.0

        price = model_price(model)
        return (
            telegram_free_nano_priority_rank(model),
            latency,
            int(price.get("output", 999999999) or 0),
            int(price.get("input", 999999999) or 0),
            model,
        )

    return sorted(unique, key=sort_key)

def telegram_looks_like_model_error(
    answer_text: Any,
    meta: Dict[str, Any] | None = None,
) -> bool:
    answer = str(answer_text or "").strip()
    lowered = answer.lower()
    meta_data = meta or {}

    if not answer:
        return True

    if answer == TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE:
        return True

    if bool(
        meta_data.get("public_error_sanitized")
        or meta_data.get("public_error_hidden")
        or meta_data.get("public_safe_message")
        or meta_data.get("telegram_public_error_sanitized")
    ):
        return True

    return any(
        pattern in lowered
        for pattern in TELEGRAM_TECHNICAL_ERROR_PATTERNS
    )


def telegram_get_retry_candidates(
    kwargs: Dict[str, Any],
    failed_models: List[str] | None = None,
) -> List[str]:
    failed_set = {
        str(model or "").strip()
        for model in (failed_models or [])
        if str(model or "").strip()
    }

    medium_candidates = [
        model
        for model in telegram_unique_models(
            (ALL_CAPABLE_MODELS or [])
            + (TOP_USAGE_MODEL_CANDIDATES or [])
        )
        if telegram_model_tier_rank(model) == 1
    ]

    candidates: List[str] = []
    candidates.extend(kwargs.get("active_health_models") or [])
    candidates.extend(DEFAULT_CHEAP_FALLBACK_MODELS)
    candidates.extend(ALL_CHEAP_MODELS or [])
    candidates.extend(kwargs.get("fallback_models") or [])
    candidates.extend(medium_candidates)
    candidates.extend(kwargs.get("expensive_fallback_models") or [])
    candidates.extend(DEFAULT_EXPENSIVE_FALLBACK_MODELS)
    candidates.extend(ALL_CAPABLE_MODELS or [])
    candidates.extend(TOP_USAGE_MODEL_CANDIDATES)

    unique = telegram_sort_models_for_simple_chat(
        candidates,
        kwargs.get("health_cache") or {},
    )

    return [
        model
        for model in unique
        if model not in failed_set
    ]


TELEGRAM_INTERNAL_POWER_KWARGS = {
    "active_health_models",
    "health_cache",
    "auto_retry_on_model_error_enabled",
    "auto_retry_on_model_error_max_attempts",
    "auto_retry_on_model_error_timeout_seconds",
    "_telegram_auto_retry_depth",
}


def call_generate_power_answer_compat(
    kwargs: Dict[str, Any],
) -> tuple[str, Dict[str, Any]]:
    """Call generate_power_answer while dropping unsupported/internal kwargs.

    Some versions of power_features accept **kwargs, but internal Telegram-only
    options should still not be forwarded because older implementations may pass
    them deeper and break the worker thread.
    """
    safe_kwargs = {
        key: value
        for key, value in dict(kwargs or {}).items()
        if key not in TELEGRAM_INTERNAL_POWER_KWARGS
    }

    signature = inspect.signature(generate_power_answer)
    parameters = signature.parameters
    accepts_kwargs = any(
        param.kind == inspect.Parameter.VAR_KEYWORD
        for param in parameters.values()
    )

    if accepts_kwargs:
        answer, meta = generate_power_answer(**safe_kwargs)
        meta = meta if isinstance(meta, dict) else {}
        dropped_internal = sorted(set(kwargs or {}) - set(safe_kwargs))
        if dropped_internal:
            meta["telegram_internal_dropped_kwargs"] = dropped_internal
        return str(answer or ""), meta

    filtered_kwargs = {
        key: value
        for key, value in safe_kwargs.items()
        if key in parameters
    }
    dropped_keys = sorted(set(kwargs or {}) - set(filtered_kwargs))
    answer, meta = generate_power_answer(**filtered_kwargs)

    if not isinstance(meta, dict):
        meta = {}

    if dropped_keys:
        meta["power_answer_compat_dropped_kwargs"] = dropped_keys

    return str(answer or ""), meta



def telegram_normalize_short_greeting_text(text: str) -> str:
    value = str(text or "").strip().lower()
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def build_telegram_local_safe_fallback_answer(
    user_text: str,
    failure_reason: str = "",
) -> tuple[str, Dict[str, Any]]:
    text = str(user_text or "").strip()
    lower = text.lower()
    normalized = telegram_normalize_short_greeting_text(text)
    tokens = normalized.split()

    question_starters = {
        "apa",
        "apakah",
        "siapa",
        "kapan",
        "di mana",
        "dimana",
        "mengapa",
        "kenapa",
        "bagaimana",
        "jelaskan",
        "arti",
        "definisi",
        "fungsi",
        "manfaat",
        "bedanya",
        "perbedaan",
        "contoh",
        "cara",
    }
    current_info_markers = {
        "hari ini",
        "terbaru",
        "update",
        "news",
        "berita",
        "harga",
        "kurs",
        "cuaca",
        "jadwal",
        "skor",
        "hasil pertandingan",
        "live",
        "real-time",
        "realtime",
    }
    risky_domain_markers = {
        "diagnosis",
        "obat",
        "dosis",
        "resep",
        "penyakit",
        "investasi",
        "saham",
        "crypto",
        "kripto",
        "trading",
        "legal",
        "hukum",
        "kontrak",
    }

    if "ransum" in lower and ("kuda" in lower or "horse" in lower):
        answer = """Berikut contoh draft ransum kuda sebagai acuan awal.

Contoh kuda dewasa ±400 kg, kerja ringan:

1. Hijauan utama
- Rumput/hay ±6–8 kg per hari.
- Berikan bertahap dalam beberapa kali pemberian.
- Hijauan sebaiknya menjadi porsi terbesar.

2. Konsentrat/energi
- Dedak/bekatul ±0,5–1 kg per hari.
- Jagung giling/oat ±0,5–1 kg per hari.
- Naikkan porsi secara bertahap, jangan mendadak.

3. Protein tambahan
- Bungkil kedelai/sumber protein lain ±0,2–0,4 kg per hari.

4. Mineral dan air
- Garam mineral/block mineral tersedia bebas atau ±30–50 gram per hari.
- Air bersih harus selalu tersedia.

Pola sederhana:
- Pagi: rumput/hay + sedikit konsentrat.
- Siang: rumput/hay.
- Sore/malam: rumput/hay + konsentrat.

Catatan:
- Total pakan kering umumnya sekitar 1,5–2,5% dari bobot badan per hari.
- Sesuaikan dengan bobot, umur, aktivitas, kondisi tubuh, dan kualitas hijauan.
- Untuk kebutuhan presisi, konsultasikan dengan dokter hewan atau nutrisionis ternak.
"""
        return {
            "should_answer": True,
            "answer": answer,
            "reason": "safe_domain_specific_local_answer",
        }

    hidden_detail = str(failure_reason or "")[:500]

    if not text:
        return (
            "Halo. Tulis pertanyaan atau kebutuhan Anda, nanti saya bantu jawab sejelas mungkin.",
            {
                "public_safe_message": True,
                "telegram_public_error_sanitized": True,
                "fallback_reason": "empty_input_local_fallback",
                "hidden_telegram_error_detail": hidden_detail,
            },
        )

    greeting_tokens = {
        "halo", "hai", "hi", "hello", "pagi", "siang", "sore", "malam",
        "permisi", "bro", "sis", "min", "admin", "adioranye",
    }
    thanks_tokens = {"makasih", "terima kasih", "thanks", "thank you", "thx"}

    if normalized in greeting_tokens or any(token in greeting_tokens for token in tokens[:2]):
        return (
            "Halo juga. Kirim pertanyaan Anda, saya bantu jawab singkat dan jelas.",
            {
                "public_safe_message": True,
                "telegram_public_error_sanitized": True,
                "fallback_reason": "short_greeting_local_fallback",
                "hidden_telegram_error_detail": hidden_detail,
            },
        )

    if normalized in thanks_tokens or "terima kasih" in lower or "makasih" in lower:
        return (
            "Sama-sama. Jika masih ada yang ingin ditanyakan, lanjutkan saja.",
            {
                "public_safe_message": True,
                "telegram_public_error_sanitized": True,
                "fallback_reason": "thanks_local_fallback",
                "hidden_telegram_error_detail": hidden_detail,
            },
        )

    if any(marker in lower for marker in current_info_markers):
        return (
            "Maaf, data real-time belum bisa diambil saat ini. Coba lagi beberapa saat lagi, atau kirim pertanyaan yang tidak butuh info terbaru.",
            {
                "public_safe_message": True,
                "telegram_public_error_sanitized": True,
                "fallback_reason": "current_info_local_fallback",
                "hidden_telegram_error_detail": hidden_detail,
            },
        )

    if any(marker in lower for marker in risky_domain_markers):
        return (
            "Maaf, untuk topik sensitif seperti medis, dosis, hukum, atau investasi, kirim konteks lebih lengkap agar jawaban bisa lebih aman dan terarah.",
            {
                "public_safe_message": True,
                "telegram_public_error_sanitized": True,
                "fallback_reason": "risky_domain_local_fallback",
                "hidden_telegram_error_detail": hidden_detail,
            },
        )

    if tokens and (tokens[0] in question_starters or text.endswith("?")):
        return (
            "Maaf, layanan sedang terbatas. Coba kirim ulang pertanyaan dengan versi lebih singkat atau beberapa saat lagi.",
            {
                "public_safe_message": True,
                "telegram_public_error_sanitized": True,
                "fallback_reason": "question_local_fallback",
                "hidden_telegram_error_detail": hidden_detail,
            },
        )

    return (
        "Maaf, Adioranye sedang mengalami gangguan koneksi/model. Silakan coba lagi beberapa saat lagi.",
        {
            "public_safe_message": True,
            "telegram_public_error_sanitized": True,
            "fallback_reason": "default_public_fallback",
            "hidden_telegram_error_detail": hidden_detail,
        },
    )


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Minimal safe wrapper after truncated file recovery."""
    try:
        answer, meta = call_generate_power_answer_compat(kwargs)
        return str(answer or ""), meta if isinstance(meta, dict) else {}
    except Exception as exc:
        fallback_answer, fallback_meta = build_telegram_local_safe_fallback_answer(
            str(kwargs.get("user_text") or ""),
            failure_reason=str(exc),
        )
        fallback_meta["error_class"] = exc.__class__.__name__
        return fallback_answer, fallback_meta


class TelegramService:
    def __init__(self) -> None:
        self._running = False
        self._processed = 0
        self._started_at = ""
        self._worker_id = ""
        self._last_update = ""
        self._last_error = ""
        self._duplicates_skipped = 0
        self._runtime_primary_model = ""
        self._model_health_checked_at = ""
        self._model_health_active_count = 0
        self._poll_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._offset = 0
        self._token = ""
        self._config: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            thread_alive = bool(self._poll_thread and self._poll_thread.is_alive())
            running = bool(self._running and thread_alive)
            return {
                "running": running,
                "processed": self._processed,
                "started_at": self._started_at,
                "worker_id": self._worker_id,
                "last_update": self._last_update,
                "last_error": self._last_error,
                "duplicates_skipped": self._duplicates_skipped,
                "runtime_primary_model": self._runtime_primary_model,
                "model_health_checked_at": self._model_health_checked_at,
                "model_health_active_count": self._model_health_active_count,
            }

    def _telegram_request(
        self,
        method: str,
        payload: Dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        if not self._token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN kosong.")
        response = requests.post(
            TELEGRAM_API.format(token=self._token, method=method),
            json=payload or {},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(str(data))
        return data

    def _build_answer(self, text: str) -> tuple[str, Dict[str, Any]]:
        answer, meta = safe_generate_power_answer(
            api_url=str(self._config.get("slashai_api_url") or self._config.get("api_url") or ""),
            api_key=str(self._config.get("slashai_api_key") or self._config.get("api_key") or ""),
            model=str(self._config.get("slashai_model") or self._config.get("model") or "tamandata"),
            system_prompt=str(self._config.get("persona_text") or self._config.get("system_prompt") or ""),
            user_text=text,
            memory_text="",
            recent_messages=[],
            fallback_models=list(self._config.get("fallback_models") or []),
            expensive_fallback_models=list(self._config.get("expensive_fallback_models") or []),
            allow_expensive_fallback=bool(self._config.get("allow_expensive_fallback", True)),
            max_expensive_models=telegram_safe_int(self._config.get("max_expensive_models"), 1),
            temperature=float(self._config.get("temperature") or 0.3),
            max_completion_tokens=telegram_safe_int(self._config.get("max_completion_tokens"), 1200),
            timeout=telegram_safe_int(self._config.get("timeout"), 90),
            max_smart_models=telegram_safe_int(self._config.get("max_smart_models"), 1),
            return_to_primary=bool(self._config.get("return_to_primary", False)),
        )
        return str(answer or "").strip(), meta if isinstance(meta, dict) else {}

    def _admin_chat_ids(self) -> Set[str]:
        return telegram_parse_admin_chat_ids(
            self._config.get("telegram_admin_chat_ids")
            or self._config.get("admin_chat_ids")
            or os.getenv("TELEGRAM_ADMIN_CHAT_IDS")
            or ""
        )

    def _is_admin_chat(self, chat_id: Any) -> bool:
        return str(chat_id or "").strip() in self._admin_chat_ids()

    def _maintenance_lock_file(self) -> str:
        path = str(
            self._config.get("maintenance_lock_file")
            or ".adioranye_maintenance_lock.json"
        ).strip()
        return path or ".adioranye_maintenance_lock.json"

    def _maintenance_default_message(self) -> str:
        message = str(
            self._config.get("maintenance_message")
            or "Adioranye sedang dalam mode akses terbatas. Silakan coba lagi setelah admin membuka akses."
        ).strip()
        return message or "Adioranye sedang dalam mode akses terbatas. Silakan coba lagi setelah admin membuka akses."

    def _read_maintenance_state(self) -> Dict[str, Any]:
        state = {
            "locked": False,
            "status": "unlocked",
            "message": self._maintenance_default_message(),
            "reason": "",
            "updated_at": "",
            "updated_by": "",
            "channel": "",
        }
        path = self._maintenance_lock_file()

        try:
            if not os.path.exists(path):
                return state
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, dict):
                state.update(data)
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)[:1200]

        state["locked"] = bool(state.get("locked"))
        state["status"] = "locked" if state.get("locked") else "unlocked"
        state["message"] = str(state.get("message") or self._maintenance_default_message()).strip()
        return state

    def _write_maintenance_state(self, locked: bool, updated_by: str, reason: str) -> Dict[str, Any]:
        state = self._read_maintenance_state()
        state.update(
            {
                "locked": bool(locked),
                "status": "locked" if locked else "unlocked",
                "message": self._maintenance_default_message(),
                "reason": str(reason or "").strip(),
                "updated_at": datetime.now(WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB"),
                "updated_by": str(updated_by or "telegram-admin"),
                "channel": "telegram-admin",
            }
        )

        with open(self._maintenance_lock_file(), "w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)

        return state

    def _handle_admin_command(self, chat_id: Any, text: str) -> Optional[str]:
        command = str(text or "").strip().lower().split()[0]

        if command not in {"/helpadmin", "/webstatus", "/lockweb", "/unlockweb"}:
            return None

        if not self._is_admin_chat(chat_id):
            return "Perintah admin ditolak. Chat ID ini tidak terdaftar sebagai admin Telegram."

        if command == "/helpadmin":
            return (
                "Command admin Telegram:\n"
                "/helpadmin - daftar command admin\n"
                "/webstatus - lihat status web chat\n"
                "/lockweb - kunci web chat\n"
                "/unlockweb - buka web chat lagi"
            )

        if command == "/webstatus":
            state = self._read_maintenance_state()
            bot_mode = str(
                self._config.get("telegram_model_mode")
                or self._config.get("model_mode")
                or "auto"
            ).strip() or "auto"
            runtime_primary_model = str(self.status().get("runtime_primary_model") or self._config.get("slashai_model") or self._config.get("model") or "-").strip() or "-"
            active_count = int(self.status().get("model_health_active_count") or 0)
            return (
                f"Web chat: {'LOCKED' if state.get('locked') else 'UNLOCKED'}\n"
                f"Updated: {state.get('updated_at') or '-'}\n"
                f"By: {state.get('updated_by') or '-'}\n"
                f"Reason: {state.get('reason') or '-'}\n"
                f"Telegram bot: {'RUNNING' if self.status().get('running') else 'STOPPED'}\n"
                f"Telegram mode: {bot_mode}\n"
                f"Primary model: {runtime_primary_model}\n"
                f"Model aktif: {active_count}"
            )

        if command == "/lockweb":
            state = self._write_maintenance_state(
                True,
                updated_by="telegram-admin",
                reason="manual_web_chat_lock",
            )
            return (
                "Web chat dikunci.\n"
                f"Status: {'LOCKED' if state.get('locked') else 'UNLOCKED'}\n"
                f"Updated: {state.get('updated_at') or '-'}"
            )

        state = self._write_maintenance_state(
            False,
            updated_by="telegram-admin",
            reason="manual_web_chat_unlock",
        )
        return (
            "Web chat dibuka lagi.\n"
            f"Status: {'LOCKED' if state.get('locked') else 'UNLOCKED'}\n"
            f"Updated: {state.get('updated_at') or '-'}"
        )

    def _handle_message(self, message: Dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = str(message.get("text") or "").strip()
        update_id = message.get("update_id")

        if not chat_id or not text:
            return

        with self._lock:
            self._last_update = f"update_id={update_id} chat_id={chat_id} text={text[:120]}"

        admin_reply = self._handle_admin_command(chat_id, text)
        if admin_reply is not None:
            try:
                self._telegram_request(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": admin_reply[:4000],
                    },
                    timeout=telegram_safe_int(self._config.get("telegram_send_timeout_seconds"), 60),
                )
                with self._lock:
                    self._processed += 1
            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)[:1200]
            return

        try:
            answer, _meta = self._build_answer(text)
            if not answer:
                answer, _meta = build_telegram_local_safe_fallback_answer(text, failure_reason="empty_answer")
            self._telegram_request(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": answer[:4000],
                },
                timeout=telegram_safe_int(self._config.get("telegram_send_timeout_seconds"), 60),
            )
            with self._lock:
                self._processed += 1
        except Exception as exc:
            fallback_answer, _ = build_telegram_local_safe_fallback_answer(text, failure_reason=str(exc))
            try:
                self._telegram_request(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": fallback_answer[:4000],
                    },
                    timeout=telegram_safe_int(self._config.get("telegram_send_timeout_seconds"), 60),
                )
            except Exception as send_exc:
                with self._lock:
                    self._last_error = str(send_exc)[:1200]
            else:
                with self._lock:
                    self._processed += 1
                    self._last_error = str(exc)[:1200]

    def _poll_loop(self) -> None:
        timeout_seconds = telegram_safe_int(self._config.get("telegram_poll_timeout_seconds"), 30)
        while not self._stop_event.is_set():
            try:
                payload = {
                    "timeout": timeout_seconds,
                    "offset": self._offset,
                    "allowed_updates": ["message"],
                }
                data = self._telegram_request(
                    "getUpdates",
                    payload,
                    timeout=timeout_seconds + 10,
                )
                for item in data.get("result") or []:
                    update_id = int(item.get("update_id") or 0)
                    self._offset = max(self._offset, update_id + 1)
                    message = dict(item.get("message") or {})
                    message["update_id"] = update_id
                    self._handle_message(message)
            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)[:1200]
                if self._stop_event.wait(3):
                    break

        with self._lock:
            self._running = False
            self._poll_thread = None
            self._last_update = "Telegram worker berhenti."

    def start(self, config: Dict[str, Any] | None = None) -> bool:
        config = config or {}
        token = str(config.get("telegram_token") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        api_key = str(config.get("slashai_api_key") or config.get("api_key") or "").strip()
        api_url = str(config.get("slashai_api_url") or config.get("api_url") or "").strip()

        if not token:
            with self._lock:
                self._last_error = "TELEGRAM_BOT_TOKEN kosong."
            return False

        if not api_key or not api_url:
            with self._lock:
                self._last_error = "Konfigurasi AI belum lengkap. Isi API key dan API URL."
            return False

        with self._lock:
            if self._poll_thread and self._poll_thread.is_alive():
                self._running = True
                return True
            self._config = dict(config)
            self._token = token
            self._stop_event.clear()
            self._running = True
            self._started_at = datetime.utcnow().isoformat()
            self._worker_id = f"local-{int(time.time())}"
            self._runtime_primary_model = str(config.get("slashai_model") or config.get("model") or "")
            self._model_health_checked_at = datetime.utcnow().isoformat()
            self._model_health_active_count = len(config.get("active_cheap_models") or []) + len(config.get("active_expensive_models") or [])
            self._last_error = ""
            self._last_update = "Telegram worker mulai polling."
            self._poll_thread = threading.Thread(target=self._poll_loop, name="adioranye-telegram-poll", daemon=True)
            self._poll_thread.start()
        return True

    def stop(self) -> None:
        thread = None
        with self._lock:
            self._running = False
            self._stop_event.set()
            thread = self._poll_thread
            self._last_update = "Telegram worker dihentikan."
        if thread and thread.is_alive():
            thread.join(timeout=2)

    def diagnose(self, config: Dict[str, Any] | None = None) -> Dict[str, Any]:
        config = config or {}
        token = str(config.get("telegram_token") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        timeout = telegram_safe_int(config.get("telegram_status_test_timeout_seconds"), 12)

        result = {
            "ok": False,
            "bot_username": "",
            "bot_id": "",
            "webhook_url": "",
            "pending_update_count": None,
            "last_error": "",
        }

        if not token:
            result["last_error"] = "TELEGRAM_BOT_TOKEN kosong."
            return result

        try:
            get_me = requests.get(
                TELEGRAM_API.format(token=token, method="getMe"),
                timeout=timeout,
            )
            get_me.raise_for_status()
            me_payload = get_me.json()
            if not me_payload.get("ok"):
                raise RuntimeError(str(me_payload))

            bot_info = me_payload.get("result") or {}
            result["bot_username"] = str(bot_info.get("username") or "")
            result["bot_id"] = str(bot_info.get("id") or "")

            webhook_resp = requests.get(
                TELEGRAM_API.format(token=token, method="getWebhookInfo"),
                timeout=timeout,
            )
            webhook_resp.raise_for_status()
            webhook_payload = webhook_resp.json()
            webhook_info = webhook_payload.get("result") or {}
            result["webhook_url"] = str(webhook_info.get("url") or "")
            result["pending_update_count"] = webhook_info.get("pending_update_count")
            result["ok"] = True
            return result
        except Exception as exc:
            result["last_error"] = str(exc)[:1200]
            with self._lock:
                self._last_error = result["last_error"]
            return result

    def reset_telegram_session(self, config: Dict[str, Any] | None = None) -> str:
        config = config or {}
        token = str(config.get("telegram_token") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        timeout = telegram_safe_int(config.get("telegram_status_test_timeout_seconds"), 12)

        if not token:
            return "Reset dibatalkan: TELEGRAM_BOT_TOKEN kosong."

        messages: List[str] = []
        try:
            resp = requests.get(
                TELEGRAM_API.format(token=token, method="deleteWebhook") + "?drop_pending_updates=true",
                timeout=timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("ok"):
                messages.append("Webhook dihapus dan pending update dibersihkan.")
            else:
                messages.append(f"deleteWebhook tidak OK: {payload}")
        except Exception as exc:
            messages.append(f"Reset Telegram gagal: {exc}")
            with self._lock:
                self._last_error = str(exc)[:1200]

        return " ".join(messages) or "Reset Telegram selesai."

    def force_local_reset(self) -> str:
        with self._lock:
            self._running = False
            self._processed = 0
            self._worker_id = ""
            self._last_update = "Force reset lokal selesai."
            self._last_error = ""
        return "Force reset lokal worker Telegram selesai."


_TELEGRAM_SERVICE_SINGLETON: TelegramService | None = None


def get_telegram_service(*args: Any, **kwargs: Any) -> TelegramService:
    del args, kwargs
    global _TELEGRAM_SERVICE_SINGLETON
    if _TELEGRAM_SERVICE_SINGLETON is None:
        _TELEGRAM_SERVICE_SINGLETON = TelegramService()
    return _TELEGRAM_SERVICE_SINGLETON


assert callable(get_telegram_service)
