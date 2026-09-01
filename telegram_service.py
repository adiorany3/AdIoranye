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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Untuk ransum final, sebaiknya konsultasi dengan dokter hewan atau ahli nutrisi kuda."""
        return answer, {
            "telegram_local_safe_fallback_used": True,
            "telegram_local_safe_fallback_type": "horse_ration",
            "model_skipped_after_failure": True,
            "failure_reason": failure_reason[:500],
        }

    if normalized and len(tokens) <= 14:
        has_question_shape = (
            text.endswith("?")
            or any(normalized.startswith(starter) for starter in question_starters)
        )
        asks_current_info = any(marker in lower for marker in current_info_markers)
        risky_domain = any(marker in lower for marker in risky_domain_markers)

        if has_question_shape and not asks_current_info and not risky_domain:
            return (
                "Model sedang tidak stabil. Kirim ulang pertanyaan dengan topik lebih spesifik agar saya jawab langsung secara lokal, misalnya definisi, fungsi, cara kerja, perbedaan, atau contoh singkat.",
                {
                    "telegram_local_safe_fallback_used": True,
                    "telegram_local_safe_fallback_type": "general_question_redirect",
                    "model_skipped_after_failure": True,
                    "failure_reason": failure_reason[:500],
                },
            )

    if any(marker in lower for marker in ["buatkan", "buat ", "susun", "rancang", "contoh"]):
        return (
            "Model sedang tidak stabil, jadi saya buatkan draft awal secara lokal agar pekerjaan tetap bisa lanjut. "
            "Kirim detail tambahan jika ingin hasilnya disesuaikan.",
            {
                "telegram_local_safe_fallback_used": True,
                "telegram_local_safe_fallback_type": "generic_draft",
                "model_skipped_after_failure": True,
                "failure_reason": failure_reason[:500],
            },
        )

    return "", {}


def retry_telegram_power_answer_with_active_models(
    original_answer: str,
    original_meta: Dict[str, Any] | None,
    original_kwargs: Dict[str, Any],
    retry_depth: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """Retry Telegram answer with another active/fallback model if first result failed."""
    enabled = telegram_parse_bool(
        original_kwargs.get("auto_retry_on_model_error_enabled")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_ENABLED", "true"),
        default=True,
    )

    if not enabled:
        return original_answer, original_meta or {}

    if retry_depth > 0:
        return original_answer, original_meta or {}

    if not telegram_looks_like_model_error(
        original_answer,
        meta=original_meta,
    ):
        return original_answer, original_meta or {}

    max_attempts = max(
        1,
        min(
            telegram_safe_int(
                original_kwargs.get("auto_retry_on_model_error_max_attempts")
                or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_MAX_ATTEMPTS", "3"),
                3,
            ),
            5,
        ),
    )
    timeout_seconds = telegram_safe_int(
        original_kwargs.get("auto_retry_on_model_error_timeout_seconds")
        or os.getenv("AUTO_RETRY_ON_MODEL_ERROR_TIMEOUT_SECONDS", "35"),
        35,
    )

    original_model = str(original_kwargs.get("model") or "").strip()
    failed_models = [original_model] if original_model else []
    candidates = telegram_get_retry_candidates(
        original_kwargs,
        failed_models=failed_models,
    )

    if not candidates:
        meta_data = original_meta or {}
        meta_data["telegram_auto_model_retry_enabled"] = True
        meta_data["telegram_auto_model_retry_success"] = False
        meta_data["telegram_auto_model_retry_reason"] = "no-candidate"
        meta_data["telegram_auto_model_retry_attempts"] = 0
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
        retry_kwargs["return_to_primary"] = False
        retry_kwargs["timeout"] = min(
            telegram_safe_int(
                retry_kwargs.get("timeout"),
                timeout_seconds,
            ),
            timeout_seconds,
        )
        retry_kwargs["_telegram_auto_retry_depth"] = retry_depth + 1

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

        if not telegram_looks_like_model_error(
            retry_answer,
            meta=retry_meta,
        ):
            retry_meta["telegram_auto_model_retry_success"] = True
            retry_meta["telegram_auto_model_retry_attempts"] = len(tried_models)
            retry_meta["telegram_auto_model_retry_models"] = tried_models
            retry_meta["telegram_auto_model_retry_from_model"] = original_model
            retry_meta["telegram_auto_model_retry_final_model"] = (
                retry_meta.get("active_model_final")
                or retry_meta.get("model_requested")
                or candidate_model
            )
            retry_meta["telegram_auto_model_retry_errors"] = retry_errors
            return retry_answer, retry_meta

        retry_errors.append(
            f"{candidate_model}: retry returned public/model error"
        )

    meta_data = original_meta or {}
    meta_data["telegram_auto_model_retry_enabled"] = True
    meta_data["telegram_auto_model_retry_success"] = False
    meta_data["telegram_auto_model_retry_attempts"] = len(tried_models)
    meta_data["telegram_auto_model_retry_models"] = tried_models
    meta_data["telegram_auto_model_retry_errors"] = retry_errors

    local_fallback_answer, local_fallback_meta = build_telegram_local_safe_fallback_answer(
        str(original_kwargs.get("user_text") or ""),
        failure_reason="; ".join(retry_errors[-3:]) or str(meta_data.get("hidden_telegram_error_detail", "")),
    )
    if local_fallback_answer:
        meta_data.update(local_fallback_meta)
        meta_data["telegram_auto_model_retry_local_fallback"] = True
        return local_fallback_answer, meta_data

    return original_answer, meta_data


def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper plus Telegram retry with active model alternatives.

    If the first model path returns a public model/connection failure, Telegram
    retries the same question with another model candidate before sending the
    failure message.
    """
    retry_depth = telegram_safe_int(
        kwargs.pop("_telegram_auto_retry_depth", 0),
        0,
    )
    original_kwargs = dict(kwargs)

    try:
        answer, meta = call_generate_power_answer_compat(kwargs)

        return retry_telegram_power_answer_with_active_models(
            answer,
            meta,
            original_kwargs,
            retry_depth=retry_depth,
        )
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)

        if match:
            bad_key = match.group(1)

            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                retry_kwargs["_telegram_auto_retry_depth"] = retry_depth
                answer, meta = safe_generate_power_answer(**retry_kwargs)

                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])

                    if bad_key not in dropped:
                        dropped.append(bad_key)

                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)

                return answer, meta

        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

        raise
    except Exception as exc:
        if retry_depth <= 0:
            answer, meta = retry_telegram_power_answer_with_active_models(
                TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE,
                {
                    "telegram_public_error_sanitized": True,
                    "error_class": exc.__class__.__name__,
                    "hidden_telegram_error_detail": str(exc)[:5000],
                    "retry_trigger": "generic_exception",
                },
                original_kwargs,
                retry_depth=retry_depth,
            )

            if not telegram_looks_like_model_error(
                answer,
                meta=meta,
            ):
                return answer, meta

            if isinstance(meta, dict):
                meta["telegram_public_error_sanitized"] = True
                return TELEGRAM_PUBLIC_MODEL_ERROR_MESSAGE, meta

        raise


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
        "real time",
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
- Sesuaikan dengan bobot, umur, aktivitas, kondisi tubuh, dan kual