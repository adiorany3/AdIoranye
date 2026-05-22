import os
import inspect
import json
import re
import threading
import fcntl
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional, Deque, Set

import requests

from ai_core import (
    ALL_SLASHAI_MODELS,
    ALL_CHEAP_MODELS,
    ALL_CAPABLE_MODELS,
    TOP_USAGE_MODEL_CANDIDATES,
    discover_available_models_from_api,
    DEFAULT_CHEAP_FALLBACK_MODELS,
    DEFAULT_EXPENSIVE_FALLBACK_MODELS,
    generate_answer,
    model_cost_tier,
    model_price,
)
from memory_store import MemoryStore, handle_local_memory_command
from power_features import get_power_store, handle_power_command, generate_power_answer


TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
DEFAULT_LOCK_FILE = "/tmp/adioranye_telegram_bot_worker.lock"
DEFAULT_RUNTIME_STATE_FILE = ".telegram_runtime_state.json"
LOCK_STALE_SECONDS = 180
WIB_TZ = ZoneInfo("Asia/Jakarta")




def safe_generate_power_answer(**kwargs: Any) -> tuple[str, Dict[str, Any]]:
    """Compatibility wrapper for generate_power_answer keyword changes.

    Telegram should keep answering even if app.py/telegram_service.py is newer
    than power_features.py during deploy or merge. Unsupported kwargs are ignored.
    """
    try:
        signature = inspect.signature(generate_power_answer)
        parameters = signature.parameters
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values())
        if accepts_kwargs:
            return generate_power_answer(**kwargs)
        filtered_kwargs = {key: value for key, value in kwargs.items() if key in parameters}
        dropped_keys = sorted(set(kwargs) - set(filtered_kwargs))
        answer, meta = generate_power_answer(**filtered_kwargs)
        if dropped_keys and isinstance(meta, dict):
            meta["power_answer_compat_dropped_kwargs"] = dropped_keys
        return answer, meta
    except TypeError as exc:
        message = str(exc)
        match = re.search(r"unexpected keyword argument '([^']+)'", message)
        if match:
            bad_key = match.group(1)
            if bad_key in kwargs:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop(bad_key, None)
                answer, meta = safe_generate_power_answer(**retry_kwargs)
                if isinstance(meta, dict):
                    dropped = list(meta.get("power_answer_compat_dropped_kwargs") or [])
                    if bad_key not in dropped:
                        dropped.append(bad_key)
                    meta["power_answer_compat_dropped_kwargs"] = sorted(dropped)
                return answer, meta
        raise

def _wib_now_text() -> str:
    return datetime.now(WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")


def split_telegram_message(text: str, max_len: int = 3900) -> List[str]:
    """Split Telegram messages without cutting words/code lines when possible."""
    text = str(text or "")
    if len(text) <= max_len:
        return [text]

    chunks: List[str] = []
    remaining = text
    while len(remaining) > max_len:
        # Prefer paragraph, then line, then whitespace boundaries.
        cut = remaining.rfind("\n\n", 0, max_len)
        if cut < max_len * 0.45:
            cut = remaining.rfind("\n", 0, max_len)
        if cut < max_len * 0.45:
            cut = remaining.rfind(" ", 0, max_len)
        if cut < max_len * 0.45:
            cut = max_len
        chunk = remaining[:cut].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks or [""]


def normalize_telegram_text(text: str) -> str:
    """Send AI output to Telegram as safe, readable plain text.

    We intentionally do not use parse_mode so code/XML/HTML snippets never break
    Telegram parsing. This formatter only cleans control characters, trims noisy
    whitespace, and keeps user-facing content intact.
    """
    text = str(text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def _as_string_list(value: Any) -> List[str]:
    """Normalize config values into a clean list of model names."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = str(value).replace("\n", ",").split(",")
    result: List[str] = []
    for item in raw_items:
        item_text = str(item or "").strip()
        if item_text and item_text not in result:
            result.append(item_text)
    return result


def _contains_any(text: str, keywords: List[str]) -> bool:
    lowered = f" {str(text or '').lower()} "
    return any(keyword in lowered for keyword in keywords)


def is_thinking_telegram_question(text: str, history: Optional[List[Dict[str, str]]] = None, min_chars: int = 180) -> bool:
    """Detect Telegram questions that should use a more capable model.

    Conservative routing: short/simple chat remains on the cheap model path,
    while analytical, coding, debugging, academic, strategic, or long multi-step
    prompts are routed directly to the capable model path.
    """
    prompt = str(text or "").strip()
    if not prompt:
        return False

    lowered = prompt.lower()
    word_count = len(prompt.split())
    try:
        min_chars = int(min_chars or 180)
    except Exception:
        min_chars = 180

    strong_keywords = [
        "thinking", "reasoning", "berpikir", "nalar", "logika", "analisis", "analisa",
        "evaluasi", "bandingkan", "pertimbangkan", "strategi", "arsitektur", "algoritma",
        "debug", "error", "traceback", "exception", "bug", "refactor", "optimasi",
        "optimize", "perbaiki kode", "cek kode", "skripsi", "tesis", "jurnal", "riset",
        "metodologi", "smartpls", "statistik", "regresi", "sentimen", "indobert",
        "buatkan alur", "bagan alur", "step by step", "langkah-langkah", "kenapa", "mengapa",
        "apa penyebab", "solusi terbaik", "rekomendasi terbaik", "prioritaskan",
        "model yang capable", "jawaban mendalam", "berpikir dalam", "jelaskan detail",
    ]
    code_or_log_markers = [
        "```", "def ", "class ", "import ", "from ", "return ", "npm ", "vercel",
        "status code", "response:", "build failed", "failed", "unauthorized", "creditsdepleted",
        "<html", "<script", "streamlit", "session_state", "generate_answer", "telegram_service",
    ]

    if _contains_any(lowered, strong_keywords):
        return True
    if _contains_any(lowered, code_or_log_markers):
        return True
    if len(prompt) >= min_chars and word_count >= 24:
        return True
    if prompt.count("?") >= 2 and word_count >= 18:
        return True
    if any(token in lowered for token in ["1.", "2.", "3.", "- "]) and word_count >= 25:
        return True

    # If the current message is short but follows a technical/analytical exchange,
    # keep using the capable route for follow-up questions such as "lanjut" or "patch itu".
    history = history or []
    recent_context = "\n".join(str(item.get("content", "")) for item in history[-4:]).lower()
    followup_markers = {"lanjut", "patch", "perbaiki", "ubah", "tambahkan", "error", "kode"}
    if word_count <= 12 and any(marker in lowered for marker in followup_markers):
        if _contains_any(recent_context, strong_keywords + code_or_log_markers):
            return True

    return False


def pick_telegram_capable_model(
    primary_model: str,
    expensive_fallback_models: List[str],
    config: Dict[str, Any],
) -> str:
    """Pick a capable model for Telegram thinking mode.

    Priority:
    1) THINKING_CAPABLE_MODEL / config['thinking_capable_model'] if provided.
    2) Any explicit thinking_capable_models list.
    3) Active expensive fallback models already passed by app.py.
    4) Primary model as last resort.
    """
    candidates: List[str] = []
    override = str(config.get("thinking_capable_model") or "").strip()
    if override:
        candidates.append(override)

    candidates.extend(_as_string_list(config.get("thinking_capable_models")))
    candidates.extend(_as_string_list(config.get("capable_models")))
    candidates.extend(_as_string_list(expensive_fallback_models))

    for candidate in candidates:
        if candidate and candidate != primary_model:
            return candidate

    return primary_model


def pick_fastest_telegram_normal_model(
    primary_model: str,
    fallback_models: List[str],
    config: Dict[str, Any],
) -> str:
    """Pick the fastest cheap/normal model for lightweight Telegram questions.

    app.py passes fast_cheap_models already sorted by measured health-check latency.
    If that list is unavailable, this falls back to the current primary model and
    cheap fallback order so the bot remains compatible with older app.py files.
    """
    candidates: List[str] = []

    explicit_fastest = str(config.get("fastest_cheap_model") or "").strip()
    if explicit_fastest:
        candidates.append(explicit_fastest)

    candidates.extend(_as_string_list(config.get("fast_cheap_models")))
    candidates.extend(_as_string_list(config.get("active_cheap_models")))
    candidates.append(primary_model)
    candidates.extend(_as_string_list(fallback_models))

    for candidate in candidates:
        if candidate:
            return candidate

    return primary_model


def resolve_answering_model(meta: Any, fallback_model: str) -> str:
    """Return the best available model name that actually answered.

    ai_core versions may use different meta keys. Prefer final/active keys first,
    then fall back to the model requested for this Telegram message.
    """
    if not isinstance(meta, dict):
        return str(fallback_model or "tidak diketahui")

    candidate_keys = [
        "active_model_final",
        "final_model",
        "model_final",
        "model_used",
        "selected_model",
        "active_model",
        "model",
        "telegram_model_requested",
        "model_requested",
    ]
    for key in candidate_keys:
        value = str(meta.get(key) or "").strip()
        if value:
            return value
    return str(fallback_model or "tidak diketahui")


TELEGRAM_THIN_SEPARATOR = "\n\n━━━━━━━━━━━━\n"


def _telegram_mode_label(meta: Any) -> str:
    if not isinstance(meta, dict):
        return "normal"
    forced_mode = str(meta.get("telegram_forced_model_mode") or "").lower()
    if forced_mode == "expensive":
        return "mahal"
    if forced_mode == "cheap":
        return "murah"
    if meta.get("telegram_auto_rotated_after_error"):
        return "auto-rotate"
    if meta.get("telegram_rotate_mode"):
        return "rotate"
    if meta.get("telegram_thinking_mode"):
        return "thinking"
    if meta.get("telegram_fast_normal_mode"):
        return "cepat"
    return "normal"


def build_telegram_help_text(is_admin: bool = False) -> str:
    """Build readable /help text for Telegram."""
    lines = [
        "🤖 Adioranye AI",
        "Kirim pertanyaan langsung untuk dijawab AI.",
        "",
        "Perintah umum:",
        "• /start — mulai bot",
        "• /help — bantuan",
        "• /mode — pilih mode jawaban",
    ]
    if is_admin:
        lines.extend([
            "",
            "Model & router:",
            "• /speed 4321 — cek model aktif dan pilih tercepat",
            "• /rotate — ganti ke model aktif terbaik saat ini",
            "• /ubah murah — pakai model murah/cepat",
            "• /ubah mahal — pakai model medium/mahal",
            "• /model skor — lihat skor model adaptif",
            "• /circuit — lihat model yang dikarantina",
            "",
            "Knowledge Base:",
            "• /update — jalankan update Knowledge Base sekarang",
            "• /kb bantuan — daftar perintah KB",
            "• /kb statistik — statistik dokumen",
            "• /kb list — daftar dokumen terakhir",
            "• /kb cari <query> — cari isi dokumen",
            "• /kb tambah <judul> lalu baris baru isi dokumen",
            "",
            "Critical current:",
            "• /briefing — ringkasan isu/fakta terkini dari KB",
            "• /cek isu <topik> — cek isu dengan klaim + dokumen pendukung",
            "• /pantau <topik> — tambah topik watchlist",
            "• /pantau list — daftar topik watchlist",
            "",
            "Quality Control:",
            "• /mode — lihat/pilih mode jawaban",
            "• /mode hemat|pintar|riset|kritis|auto",
            "• /kualitas — statistik kualitas jawaban",
            "• /laporan mingguan — evaluasi kualitas 7 hari",
            "",
            "Performance:",
            "• /performa — statistik retrieval/cache/latency",
            "• /optimasi db — optimasi SQLite ringan",
            "",
            "Memory & biaya:",
            "• /ingat <teks> — simpan memory permanen",
            "• /lupa <keyword> — hapus memory sesuai keyword",
            "• /biaya — ringkasan biaya 24 jam",
        ])
    else:
        lines.extend([
            "",
            "Catatan: perintah admin disembunyikan. Hubungi admin bot untuk pengaturan model/KB.",
        ])
    return "\n".join(lines)


def build_telegram_model_note(
    meta: Any,
    requested_model: str,
    default_model: str,
) -> str:
    """Build a compact footer appended below every Telegram answer."""
    answering_model = resolve_answering_model(meta, requested_model or default_model)
    mode_label = _telegram_mode_label(meta)
    footer_parts = [f"Model: {answering_model}", f"Mode: {mode_label}"]

    if isinstance(meta, dict):
        requested = str(meta.get("telegram_model_requested") or requested_model or "").strip()
        if requested and requested != answering_model:
            footer_parts.append(f"Awal: {requested}")

        intent = str(meta.get("power_intent") or meta.get("intent") or "").strip()
        if intent:
            footer_parts.append(f"Intent: {intent}")

        answer_mode = str(meta.get("answer_mode") or "").strip()
        if answer_mode:
            footer_parts.append(f"Jawab: {answer_mode}")

        quality = meta.get("answer_quality_after_verifier") or meta.get("answer_quality") or {}
        if isinstance(quality, dict) and quality.get("score") is not None:
            try:
                footer_parts.append(f"QC: {float(quality.get('score') or 0):.2f}")
            except Exception:
                pass
        if meta.get("quality_verified_by"):
            footer_parts.append(f"Verifier: {meta.get('quality_verified_by')}")

        if meta.get("power_response_cache_hit") or meta.get("cache_hit"):
            footer_parts.append("Cache: hit")

        rag_sources = meta.get("power_kb_sources") or meta.get("power_rag_sources") or meta.get("rag_sources") or []
        try:
            rag_count = len(rag_sources)
        except Exception:
            rag_count = 0
        if rag_count and bool(meta.get("show_kb_sources", False)):
            footer_parts.append(f"KB: {rag_count} sumber")

        consulted = meta.get("consulted_models") or []
        if consulted:
            short_consulted = ", ".join(str(item) for item in consulted[:3])
            footer_parts.append(f"Konsultasi: {short_consulted}")

        if meta.get("telegram_auto_rotated_after_error"):
            footer_parts.append("Retry: auto-rotate")
        elif meta.get("expensive_fallback_used"):
            footer_parts.append("Fallback: capable")

    return TELEGRAM_THIN_SEPARATOR + "ℹ️ " + " | ".join(footer_parts)


def _model_tier_rank(model: str) -> int:
    """Sort models by cost tier: cheap -> medium -> expensive -> ultra -> unknown."""
    try:
        tier = str(model_cost_tier(model) or "").lower()
    except Exception:
        tier = ""
    if tier == "cheap":
        return 0
    if tier in {"medium", "menengah"}:
        return 1
    if tier in {"expensive", "mahal"}:
        return 2
    if tier in {"ultra", "ultra_expensive", "ultra mahal"}:
        return 3
    return 4


def _model_output_price(model: str) -> int:
    try:
        return int((model_price(model) or {}).get("output", 999999999))
    except Exception:
        return 999999999


def _prioritize_active_telegram_models(models: List[str], health_cache: Dict[str, Dict[str, Any]]) -> List[str]:
    active = [model for model in _as_string_list(models) if health_cache.get(model, {}).get("active")]
    return sorted(
        active,
        key=lambda item: (
            _model_tier_rank(item),
            _model_output_price(item),
            float(health_cache.get(item, {}).get("latency_ms") or 999999),
            item,
        ),
    )


def _prioritize_fastest_telegram_models(models: List[str], health_cache: Dict[str, Dict[str, Any]]) -> List[str]:
    active = [model for model in _as_string_list(models) if health_cache.get(model, {}).get("active")]
    return sorted(
        active,
        key=lambda item: (
            float(health_cache.get(item, {}).get("latency_ms") or 999999),
            _model_output_price(item),
            item,
        ),
    )




TRANSIENT_HEALTH_HTTP_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


def _is_gpt5_health_model(model: str) -> bool:
    return "gpt-5" in str(model or "").lower()


def _extract_health_content(data: Any) -> str:
    """Extract assistant text from common OpenAI-compatible response shapes."""
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


def _build_health_payload(model: str) -> Dict[str, Any]:
    """Use a tiny safe prompt that proves the model can return assistant content."""
    max_tokens = 64 if _is_gpt5_health_model(model) else 16
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Balas hanya satu kata: OK."},
            {"role": "user", "content": "OK?"},
        ],
        "temperature": 0,
        "max_completion_tokens": max_tokens,
        "stream": False,
    }
    if _is_gpt5_health_model(model):
        # GPT-5-family models can spend completion budget on reasoning. Minimal reasoning
        # avoids false negatives where HTTP 200 returns choices but no visible content.
        payload["reasoning_effort"] = "minimal"
    return payload


def _candidate_tier(model: str) -> str:
    try:
        return str(model_cost_tier(model) or "unknown").lower()
    except Exception:
        return "unknown"


def _split_candidates_by_tier(current_model: str, config: Dict[str, Any]) -> Dict[str, List[str]]:
    """Build robust model pools from defaults, config, and dynamically classified candidates."""
    default_cheap = _as_string_list(ALL_CHEAP_MODELS) or _as_string_list(DEFAULT_CHEAP_FALLBACK_MODELS)
    default_expensive = _as_string_list(ALL_CAPABLE_MODELS) or _as_string_list(DEFAULT_EXPENSIVE_FALLBACK_MODELS)

    declared_cheap = []
    declared_cheap.extend(_as_string_list(config.get("all_cheap_models")) or default_cheap)
    declared_cheap.extend(_as_string_list(config.get("fallback_models")))
    declared_cheap.extend(_as_string_list(config.get("fast_cheap_models")))
    declared_cheap.extend(_as_string_list(config.get("active_cheap_models")))

    declared_capable = []
    declared_capable.extend(_as_string_list(config.get("all_expensive_models")) or default_expensive)
    declared_capable.extend(_as_string_list(config.get("expensive_fallback_models")))
    declared_capable.extend(_as_string_list(config.get("thinking_capable_models")))
    declared_capable.extend(_as_string_list(config.get("capable_models")))
    capable_override = str(config.get("thinking_capable_model") or "").strip()
    if capable_override:
        declared_capable.append(capable_override)

    extra = _as_string_list(config.get("all_model_candidates"))
    extra.extend(_as_string_list(TOP_USAGE_MODEL_CANDIDATES))
    extra.extend(_as_string_list(ALL_SLASHAI_MODELS))
    all_candidates = _as_string_list([current_model] + declared_cheap + declared_capable + extra)

    dynamic_cheap: List[str] = []
    dynamic_capable: List[str] = []
    dynamic_unknown: List[str] = []
    for candidate in all_candidates:
        tier = _candidate_tier(candidate)
        if tier == "cheap":
            dynamic_cheap.append(candidate)
        elif tier in {"medium", "expensive", "ultra"}:
            dynamic_capable.append(candidate)
        else:
            dynamic_unknown.append(candidate)

    return {
        "cheap": _as_string_list(declared_cheap + dynamic_cheap),
        "capable": _as_string_list(declared_capable + dynamic_capable),
        "unknown": _as_string_list(dynamic_unknown),
        "all": all_candidates,
    }


def _select_primary_by_mode(
    preferred_mode: str,
    current_model: str,
    active_cheap_fast: List[str],
    active_capable: List[str],
    active_unknown: List[str],
    active_all: List[str],
) -> str:
    """Select primary model from live health-check results."""
    mode = str(preferred_mode or "auto").strip().lower()
    if mode in {"cheap", "murah"}:
        if active_cheap_fast:
            return active_cheap_fast[0]
        if active_capable:
            return active_capable[0]
    elif mode in {"expensive", "mahal", "medium", "menengah"}:
        if active_capable:
            return active_capable[0]
        if active_cheap_fast:
            return active_cheap_fast[0]
    else:
        if active_cheap_fast:
            return active_cheap_fast[0]
        if active_capable:
            return active_capable[0]

    if active_unknown:
        return active_unknown[0]
    if active_all:
        return active_all[0]
    return current_model

def is_speed_update_command(text: str, expected_code: str = "4321") -> bool:
    """Return True only for the protected /speed command.

    Supports:
    - /speed 4321
    - /speed@NamaBot 4321
    """
    raw = str(text or "").strip()
    parts = raw.split()
    if len(parts) != 2:
        return False
    command = parts[0].lower()
    code = parts[1].strip()
    if not command.startswith("/speed"):
        return False
    if "@" in command:
        command = command.split("@", 1)[0]
    return command == "/speed" and code == str(expected_code or "4321")


def parse_model_switch_command(text: str) -> str:
    """Parse protected Telegram model switch command.

    Returns:
    - "expensive" for /ubah mahal
    - "cheap" for /ubah murah
    - "" for non-switch commands

    Supports bot mentions such as /ubah@NamaBot mahal.
    """
    raw = str(text or "").strip()
    parts = raw.split()
    if len(parts) != 2:
        return ""

    command = parts[0].lower()
    if "@" in command:
        command = command.split("@", 1)[0]

    if command != "/ubah":
        return ""

    target = parts[1].strip().lower()
    if target in {"mahal", "medium", "menengah", "capable"}:
        return "expensive"
    if target in {"murah", "cheap", "cepat", "normal"}:
        return "cheap"
    return ""


def build_model_switch_summary(mode: str, model: str, cheap_models: List[str], capable_models: List[str]) -> str:
    """Build Telegram confirmation text after /ubah command."""
    if mode == "expensive":
        selected = pick_telegram_capable_model(
            primary_model=model,
            expensive_fallback_models=capable_models,
            config={"thinking_capable_models": capable_models},
        )
        lines = [
            "✅ Mode model diubah ke: MEDIUM/MAHAL.",
            "",
            "Mulai sekarang pertanyaan Telegram akan diarahkan ke model medium/mahal yang sedang aktif.",
            f"Model utama mode mahal: {selected or model}",
        ]
        if capable_models:
            lines.append("Cadangan medium/mahal: " + ", ".join(str(item) for item in capable_models[:6]))
        else:
            lines.append("Catatan: daftar model medium/mahal belum tersedia. Jalankan /speed 4321 jika ingin cek model aktif terlebih dahulu.")
        return "\n".join(lines)

    if mode == "cheap":
        selected = pick_fastest_telegram_normal_model(
            primary_model=model,
            fallback_models=cheap_models,
            config={"fast_cheap_models": cheap_models, "active_cheap_models": cheap_models},
        )
        lines = [
            "✅ Mode model diubah ke: MURAH/CEPAT.",
            "",
            "Mulai sekarang pertanyaan Telegram akan diarahkan ke model murah/cepat yang sedang aktif.",
            f"Model utama mode murah: {selected or model}",
        ]
        if cheap_models:
            lines.append("Cadangan murah: " + ", ".join(str(item) for item in cheap_models[:8]))
        else:
            lines.append("Catatan: daftar model murah belum tersedia. Jalankan /speed 4321 jika ingin cek model aktif terlebih dahulu.")
        return "\n".join(lines)

    return "Format perintah: /ubah mahal atau /ubah murah"




def is_rotate_model_command(text: str) -> bool:
    """Return True for /rotate command.

    Supports:
    - /rotate
    - /rotate@NamaBot
    """
    raw = str(text or "").strip()
    parts = raw.split()
    if len(parts) != 1:
        return False
    command = parts[0].lower()
    if "@" in command:
        command = command.split("@", 1)[0]
    return command == "/rotate"




def is_update_command(text: str) -> bool:
    """Return True for /update command.

    Supports:
    - /update
    - /update@NamaBot
    """
    raw = str(text or "").strip()
    parts = raw.split()
    if len(parts) != 1:
        return False
    command = parts[0].lower()
    if "@" in command:
        command = command.split("@", 1)[0]
    return command == "/update"


def trigger_github_kb_update(config: Dict[str, Any], chat_id: int) -> str:
    """Trigger GitHub Actions workflow_dispatch untuk update Knowledge Base.

    Perintah ini hanya memicu workflow. Proses scraping/update tetap dijalankan
    oleh GitHub Actions agar tidak membebani worker Streamlit/Telegram.
    """
    github_token = str(config.get("github_actions_token") or os.getenv("GITHUB_ACTIONS_TOKEN", "") or "").strip()
    repo = str(config.get("github_repo") or os.getenv("GITHUB_REPO", "") or "").strip()
    workflow_file = str(config.get("github_workflow_file") or os.getenv("GITHUB_WORKFLOW_FILE", "daily-kb-update.yml") or "daily-kb-update.yml").strip()
    branch = str(config.get("github_branch") or os.getenv("GITHUB_BRANCH", "main") or "main").strip()

    if not github_token:
        return (
            "❌ GITHUB_ACTIONS_TOKEN belum diisi.\n\n"
            "Tambahkan secret ini di Streamlit/hosting app:\n"
            "GITHUB_ACTIONS_TOKEN = \"token_github_kamu\""
        )

    if not repo or "/" not in repo:
        return (
            "❌ GITHUB_REPO belum benar.\n\n"
            "Format yang benar:\n"
            "GITHUB_REPO = \"username/nama-repo\""
        )

    workflow_file = workflow_file or "daily-kb-update.yml"
    branch = branch or "main"
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches"

    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }

    requested_at = _wib_now_text()
    # Safe defaults for GitHub-hosted runner with 20-minute limit.
    # The workflow also has hard caps, so even if these values are changed
    # accidentally, update remains small and sequential.
    update_source_limit = str(config.get("github_update_source_limit") or os.getenv("GITHUB_UPDATE_SOURCE_LIMIT", "8") or "8").strip()
    update_max_items = str(config.get("github_update_max_items") or os.getenv("GITHUB_UPDATE_MAX_ITEMS", "1") or "1").strip()
    payload_with_inputs = {
        "ref": branch,
        "inputs": {
            "source": "telegram",
            "chat_id": str(chat_id),
            "requested_at": requested_at,
            "source_limit": update_source_limit,
            "max_items": update_max_items,
        },
    }
    payload_without_inputs = {"ref": branch}

    def _dispatch(payload: Dict[str, Any]) -> requests.Response:
        return requests.post(url, headers=headers, json=payload, timeout=30)

    try:
        response = _dispatch(payload_with_inputs)

        # GitHub mengembalikan HTTP 422 jika workflow_dispatch belum mendefinisikan
        # input source/chat_id/requested_at. Agar command /update tetap berjalan
        # pada workflow lama, ulangi request tanpa inputs.
        if response.status_code == 422 and "Unexpected inputs" in response.text:
            response = _dispatch(payload_without_inputs)
    except requests.Timeout:
        return "❌ Gagal trigger update KB: request ke GitHub timeout."
    except requests.RequestException as exc:
        return f"❌ Gagal trigger update KB: {exc}"

    if response.status_code in {200, 201, 202, 204}:
        return (
            "✅ Perintah update Knowledge Base diterima.\n\n"
            f"Repo: {repo}\n"
            f"Workflow: {workflow_file}\n"
            f"Branch: {branch}\n"
            f"Batch sumber: {update_source_limit}\n"
            f"Item/sumber: {update_max_items}\n"
            f"Waktu: {requested_at}\n\n"
            "GitHub Actions sedang menjalankan update sekuensial kecil. "
            "Jika workflow sudah memakai notifikasi Telegram, kamu akan mendapat pesan lagi saat selesai."
        )

    detail = response.text[:1200]
    return (
        "❌ GitHub menolak perintah update KB.\n\n"
        f"HTTP: {response.status_code}\n"
        f"Repo: {repo}\n"
        f"Workflow: {workflow_file}\n"
        f"Branch: {branch}\n\n"
        "Penyebab umum:\n"
        "1. Workflow file belum ada di branch tersebut.\n"
        "2. workflow_dispatch belum aktif.\n"
        "3. GITHUB_ACTIONS_TOKEN belum punya permission Actions: Read and write.\n"
        "4. Nama workflow file di secret GITHUB_WORKFLOW_FILE tidak sama.\n\n"
        f"Detail:\n{detail}"
    )


def select_rotated_runtime_model(result: Dict[str, Any], current_mode: str, current_model: str) -> Dict[str, Any]:
    """Select the best currently-active model according to current routing mode.

    - cheap mode: use fastest active cheap model; fall back to capable if no cheap model is alive.
    - expensive mode: use active medium/expensive model; fall back to cheap if no capable model is alive.
    - auto mode: use refresh_telegram_runtime_models' primary model.
    """
    mode = str(current_mode or "auto").lower()
    fast_cheap = _as_string_list(result.get("fast_cheap_models"))
    active_expensive = _as_string_list(result.get("active_expensive_models"))
    active_cheap = _as_string_list(result.get("active_cheap_models")) or fast_cheap
    primary = str(result.get("primary_model") or current_model or "").strip()

    selected_mode = mode if mode in {"auto", "cheap", "expensive"} else "auto"
    selected_model = primary
    fallback_models = _as_string_list(result.get("fallback_models"))
    expensive_fallback_models = _as_string_list(result.get("expensive_fallback_models"))
    allow_expensive = bool(expensive_fallback_models or active_expensive)

    if selected_mode == "cheap":
        if fast_cheap:
            selected_model = fast_cheap[0]
            fallback_models = [item for item in fast_cheap if item != selected_model]
            expensive_fallback_models = []
            allow_expensive = False
        elif active_expensive:
            # Safety fallback: if every cheap model is down, keep bot alive using capable model.
            selected_model = active_expensive[0]
            fallback_models = []
            expensive_fallback_models = [item for item in active_expensive if item != selected_model]
            allow_expensive = True
            selected_mode = "expensive"
    elif selected_mode == "expensive":
        if active_expensive:
            selected_model = active_expensive[0]
            fallback_models = []
            expensive_fallback_models = [item for item in active_expensive if item != selected_model]
            allow_expensive = True
        elif fast_cheap:
            # Safety fallback: if every capable model is down, keep bot alive using cheap model.
            selected_model = fast_cheap[0]
            fallback_models = [item for item in fast_cheap if item != selected_model]
            expensive_fallback_models = []
            allow_expensive = False
            selected_mode = "cheap"
    else:
        selected_model = primary
        if fast_cheap and selected_model in fast_cheap:
            fallback_models = [item for item in fast_cheap if item != selected_model]
            expensive_fallback_models = active_expensive
            allow_expensive = bool(active_expensive)
        elif active_expensive and selected_model in active_expensive:
            fallback_models = []
            expensive_fallback_models = [item for item in active_expensive if item != selected_model]
            allow_expensive = True
        else:
            fallback_models = [item for item in fast_cheap if item != selected_model]
            expensive_fallback_models = [item for item in active_expensive if item != selected_model]
            allow_expensive = bool(expensive_fallback_models)

    if not selected_model:
        selected_model = current_model

    return {
        "selected_model": selected_model,
        "selected_mode": selected_mode,
        "fallback_models": fallback_models,
        "expensive_fallback_models": expensive_fallback_models,
        "allow_expensive_fallback": allow_expensive,
        "active_cheap_models": active_cheap,
        "fast_cheap_models": fast_cheap,
        "active_expensive_models": active_expensive,
    }


def build_rotate_summary(result: Dict[str, Any], rotation: Dict[str, Any], previous_model: str) -> str:
    """Human-readable Telegram summary after /rotate command."""
    selected_model = rotation.get("selected_model") or "tidak ada"
    selected_mode = str(rotation.get("selected_mode") or "auto")
    fast_cheap = rotation.get("fast_cheap_models") or []
    active_expensive = rotation.get("active_expensive_models") or []
    health_cache = result.get("health_cache") or {}

    mode_label = {
        "auto": "OTOMATIS",
        "cheap": "MURAH/CEPAT",
        "expensive": "MEDIUM/MAHAL",
    }.get(selected_mode, selected_mode.upper())

    lines = [
        "✅ Rotate model selesai.",
        "",
        f"Mode aktif: {mode_label}",
        f"Model sebelumnya: {previous_model or 'tidak diketahui'}",
        f"Model sekarang: {selected_model}",
        f"Total dicek: {result.get('checked_total', 0)} | Hidup: {result.get('active_total', 0)} | Sementara error: {result.get('transient_total', 0)} | Mati: {result.get('dead_total', 0)}",
        f"Metode cek: paralel {result.get('health_workers', 1)} worker | retry {result.get('health_retries', 0)}x",
    ]

    if selected_model in health_cache:
        latency = health_cache.get(selected_model, {}).get("latency_ms")
        status_code = health_cache.get(selected_model, {}).get("status_code")
        lines.append(f"Status model terpilih: aktif | {latency} ms | HTTP {status_code}")

    if fast_cheap:
        lines.append("")
        lines.append("⚡ Murah aktif tercepat:")
        for model_name in fast_cheap[:5]:
            latency = health_cache.get(model_name, {}).get("latency_ms")
            lines.append(f"- {model_name} ({latency} ms)")

    if active_expensive:
        lines.append("")
        lines.append("🧠 Medium/mahal aktif:")
        for model_name in active_expensive[:5]:
            latency = health_cache.get(model_name, {}).get("latency_ms")
            lines.append(f"- {model_name} ({latency} ms)")

    if not fast_cheap and not active_expensive:
        lines.append("")
        lines.append("Peringatan: tidak ada model yang lolos health check. Bot tetap memakai model terakhir agar error tetap terlihat.")
    elif previous_model == selected_model:
        lines.append("")
        lines.append("Catatan: model tidak berubah karena model ini masih menjadi pilihan terbaik yang aktif saat ini.")

    return "\n".join(lines)

def check_telegram_single_model_health(api_url: str, api_key: str, model: str, timeout: int = 12, retries: int = 1) -> Dict[str, Any]:
    """Check whether one model is truly usable right now.

    A model is marked active only when it returns HTTP 200, has at least one
    choice, and produces non-empty assistant content. Temporary provider errors
    such as 429/5xx are retried once and reported as transient instead of being
    treated as a confirmed dead model.
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
                json=_build_health_payload(model),
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
                    "tier": _candidate_tier(model),
                    "error": last_error,
                }

            try:
                data = response.json()
            except Exception:
                last_error = (response.text or "")[:500]
                return {
                    "active": False,
                    "health_status": "dead",
                    "error_class": "invalid_json",
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                    "attempts": attempt,
                    "checked_at": _wib_now_text(),
                    "tier": _candidate_tier(model),
                    "error": "Respons 200 tetapi bukan JSON valid: " + last_error,
                }

            choices = data.get("choices") or [] if isinstance(data, dict) else []
            content = _extract_health_content(data)
            finish_reason = ""
            if choices and isinstance(choices[0], dict):
                finish_reason = str(choices[0].get("finish_reason") or "")

            if choices and content:
                return {
                    "active": True,
                    "health_status": "active",
                    "error_class": "",
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                    "attempts": attempt,
                    "checked_at": _wib_now_text(),
                    "tier": _candidate_tier(model),
                    "finish_reason": finish_reason,
                    "sample": content[:80],
                    "error": "",
                }

            # HTTP 200 but no readable assistant content should not be considered active.
            usage = data.get("usage") if isinstance(data, dict) else None
            details = (usage or {}).get("completion_tokens_details") if isinstance(usage, dict) else None
            reasoning_tokens = (details or {}).get("reasoning_tokens") if isinstance(details, dict) else None
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
                "tier": _candidate_tier(model),
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
                "tier": _candidate_tier(model),
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
                "tier": _candidate_tier(model),
                "error": last_error,
            }
        except Exception as exc:
            last_error = str(exc)[:500]
            return {
                "active": False,
                "health_status": "dead",
                "error_class": "unexpected_error",
                "status_code": last_status,
                "latency_ms": round((time.time() - started) * 1000, 1),
                "attempts": attempt,
                "checked_at": _wib_now_text(),
                "tier": _candidate_tier(model),
                "error": last_error,
            }

    return {
        "active": False,
        "health_status": "dead",
        "error_class": "unknown",
        "status_code": last_status,
        "latency_ms": round((time.time() - started) * 1000, 1),
        "attempts": attempts,
        "checked_at": _wib_now_text(),
        "tier": _candidate_tier(model),
        "error": last_error or "Health check gagal tanpa detail.",
    }


def refresh_telegram_runtime_models(
    api_url: str,
    api_key: str,
    current_model: str,
    config: Dict[str, Any],
    timeout: int = 12,
    preferred_mode: str = "auto",
) -> Dict[str, Any]:
    """Refresh Telegram runtime routing so only currently-active models are used.

    Improvements over the previous checker:
    - candidate pools are deduplicated and classified by actual price tier;
    - checks run in parallel with bounded workers;
    - temporary 429/5xx/timeouts are retried and reported separately;
    - active means the model returned non-empty assistant content, not merely HTTP 200.
    """
    if not api_url or not api_key:
        raise RuntimeError("SLASHAI_API_URL atau SLASHAI_API_KEY belum tersedia.")

    discovery_meta: Dict[str, Any] = {"ok": False, "models": [], "source_url": "", "error": ""}
    if bool(config.get("model_discovery_enabled", True)):
        discovery_meta = discover_available_models_from_api(
            api_url=api_url,
            api_key=api_key,
            models_api_url=str(config.get("models_api_url") or ""),
            timeout=int(config.get("model_discovery_timeout", timeout) or timeout),
        )
        discovered_models = _as_string_list(discovery_meta.get("models") or [])
        if discovered_models:
            config = dict(config)
            config["all_model_candidates"] = _as_string_list(config.get("all_model_candidates")) + discovered_models + _as_string_list(TOP_USAGE_MODEL_CANDIDATES)

    pools = _split_candidates_by_tier(current_model=current_model, config=config)
    cheap_candidates = pools["cheap"]
    capable_candidates = pools["capable"]
    unknown_candidates = pools["unknown"]
    candidates = pools["all"]

    if not candidates:
        candidates = _as_string_list([current_model] + TOP_USAGE_MODEL_CANDIDATES + ALL_SLASHAI_MODELS + DEFAULT_CHEAP_FALLBACK_MODELS + DEFAULT_EXPENSIVE_FALLBACK_MODELS)

    max_workers = int(config.get("model_health_workers", 6) or 6)
    max_workers = max(1, min(max_workers, len(candidates), 10))
    retries = int(config.get("model_health_retries", 1) or 1)
    retries = max(0, min(retries, 2))

    health_cache: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                check_telegram_single_model_health,
                api_url,
                api_key,
                candidate,
                timeout,
                retries,
            ): candidate
            for candidate in candidates
        }
        for future in as_completed(future_map):
            candidate = future_map[future]
            try:
                health_cache[candidate] = future.result()
            except Exception as exc:
                health_cache[candidate] = {
                    "active": False,
                    "health_status": "dead",
                    "error_class": "future_error",
                    "status_code": None,
                    "latency_ms": None,
                    "attempts": 0,
                    "checked_at": _wib_now_text(),
                    "tier": _candidate_tier(candidate),
                    "error": str(exc)[:500],
                }

    active_cheap_priority = _prioritize_active_telegram_models(cheap_candidates, health_cache)
    active_cheap_fast = _prioritize_fastest_telegram_models(cheap_candidates, health_cache)
    active_capable = _prioritize_active_telegram_models(capable_candidates, health_cache)
    active_unknown = _prioritize_fastest_telegram_models(unknown_candidates, health_cache)
    active_all = [model for model in candidates if health_cache.get(model, {}).get("active")]

    primary_model = _select_primary_by_mode(
        preferred_mode=preferred_mode,
        current_model=current_model,
        active_cheap_fast=active_cheap_fast,
        active_capable=active_capable,
        active_unknown=active_unknown,
        active_all=active_all,
    )

    if primary_model in active_cheap_fast:
        fallback_models = [model for model in active_cheap_fast if model != primary_model]
        expensive_fallback_models = active_capable
    elif primary_model in active_capable:
        fallback_models = active_cheap_fast
        expensive_fallback_models = [model for model in active_capable if model != primary_model]
    else:
        fallback_models = active_cheap_fast
        expensive_fallback_models = active_capable

    transient_total = sum(1 for item in health_cache.values() if item.get("health_status") == "transient")
    dead_total = sum(1 for item in health_cache.values() if item.get("health_status") == "dead")

    return {
        "primary_model": primary_model,
        "preferred_mode": preferred_mode,
        "active_cheap_models": active_cheap_priority,
        "fast_cheap_models": active_cheap_fast,
        "fallback_models": fallback_models,
        "active_expensive_models": active_capable,
        "expensive_fallback_models": expensive_fallback_models,
        "thinking_capable_models": active_capable,
        "active_unknown_models": active_unknown,
        "health_cache": health_cache,
        "api_model_discovery": discovery_meta,
        "api_discovered_model_count": len(discovery_meta.get("models") or []),
        "active_total": len(active_all),
        "transient_total": transient_total,
        "dead_total": dead_total,
        "checked_total": len(candidates),
        "health_workers": max_workers,
        "health_retries": retries,
    }


def build_speed_update_summary(result: Dict[str, Any]) -> str:
    """Human-readable Telegram summary after /speed or /rotate command."""
    primary = result.get("primary_model") or "tidak ada"
    fast_cheap = result.get("fast_cheap_models") or []
    active_expensive = result.get("active_expensive_models") or []
    health_cache = result.get("health_cache") or {}
    discovery = result.get("api_model_discovery") or {}

    lines = [
        "✅ Update model selesai",
        "",
        f"Model utama: {primary}",
        f"Aktif: {result.get('active_total', 0)}/{result.get('checked_total', 0)} model",
        f"Murah aktif: {len(fast_cheap)} | Capable aktif: {len(active_expensive)}",
        f"Transient: {result.get('transient_total', 0)} | Mati: {result.get('dead_total', 0)}",
        f"Cek: {result.get('health_workers', 1)} worker | retry {result.get('health_retries', 0)}x",
    ]
    if discovery.get("ok"):
        lines.append(f"Discovery API: {len(discovery.get('models') or [])} model")

    if fast_cheap:
        lines.append(TELEGRAM_THIN_SEPARATOR.strip())
        lines.append("⚡ Model murah tercepat")
        for idx, model_name in enumerate(fast_cheap[:8], start=1):
            latency = health_cache.get(model_name, {}).get("latency_ms")
            latency_text = f"{latency} ms" if latency is not None else "-"
            lines.append(f"{idx}. {model_name} — {latency_text}")

    if active_expensive:
        lines.append(TELEGRAM_THIN_SEPARATOR.strip())
        lines.append("🧠 Model capable aktif")
        for idx, model_name in enumerate(active_expensive[:8], start=1):
            latency = health_cache.get(model_name, {}).get("latency_ms")
            latency_text = f"{latency} ms" if latency is not None else "-"
            lines.append(f"{idx}. {model_name} — {latency_text}")

    if not fast_cheap and active_expensive:
        lines.append("\nCatatan: tidak ada model murah aktif, jadi bot memakai model capable.")
    elif not fast_cheap and not active_expensive:
        lines.append("\nPeringatan: tidak ada model lolos health check. Bot tetap memakai model terakhir agar error terlihat.")
    return "\n".join(lines)


class TelegramBotService:
    """Singleton polling service for Streamlit.

    Streamlit reruns app.py frequently. This class prevents multiple polling
    workers from being created in the same process and also uses a lightweight
    lock file to reduce duplicate bot instances across reloads.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._running = False
        self._last_error = ""
        self._last_update = ""
        self._processed = 0
        self._duplicates_skipped = 0
        self._started_at = ""
        self._worker_id = f"{os.getpid()}-{int(time.time())}"
        self._histories: Dict[str, List[Dict[str, str]]] = {}
        self._seen_queue: Deque[int] = deque(maxlen=500)
        self._seen_set: Set[int] = set()
        self._lock_file = DEFAULT_LOCK_FILE
        self._has_file_lock = False
        self._lock_fd = None
        self._model_health_cache: Dict[str, Dict[str, Any]] = {}
        self._model_health_checked_at = ""
        self._last_model_update_source = ""
        self._runtime_primary_model = ""
        self._forced_model_mode = "auto"

    def status(self) -> Dict[str, Any]:
        alive = self._thread is not None and self._thread.is_alive() and self._running
        return {
            "running": alive,
            "last_error": self._last_error,
            "last_update": self._last_update,
            "processed": self._processed,
            "duplicates_skipped": self._duplicates_skipped,
            "started_at": self._started_at,
            "worker_id": self._worker_id if alive else "",
            "runtime_primary_model": self._runtime_primary_model,
            "telegram_forced_model_mode": self._forced_model_mode,
            "last_model_update_source": self._last_model_update_source,
            "model_health_checked_at": self._model_health_checked_at,
            "model_health_active_count": sum(1 for item in self._model_health_cache.values() if item.get("active")),
        }

    def _runtime_state_path(self, config: Dict[str, Any]) -> str:
        """Return the file path used to persist Telegram model routing state."""
        raw_path = str(config.get("telegram_runtime_state_file") or DEFAULT_RUNTIME_STATE_FILE).strip()
        return os.path.abspath(raw_path or DEFAULT_RUNTIME_STATE_FILE)

    def _load_runtime_state(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Load persisted runtime model state from disk.

        The app can restart frequently on Streamlit/Vercel-like environments. This
        keeps /ubah, /rotate, and the last known active model from disappearing on
        every rerun. Invalid/corrupt state is ignored safely.
        """
        path = self._runtime_state_path(config)
        try:
            if not os.path.exists(path):
                return {}
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return {}
            return data
        except Exception as exc:
            self._last_error = f"Gagal membaca runtime state Telegram: {exc}"
            return {}

    def _save_runtime_state(self, config: Dict[str, Any], state: Dict[str, Any]) -> None:
        """Persist runtime model state atomically enough for a single-process app."""
        path = self._runtime_state_path(config)
        try:
            safe_state = dict(state or {})
            safe_state["saved_at"] = _wib_now_text()
            tmp_path = path + ".tmp"
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(safe_state, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception as exc:
            self._last_error = f"Gagal menyimpan runtime state Telegram: {exc}"

    def _admin_chat_ids(self, config: Dict[str, Any]) -> Set[str]:
        """Read admin chat IDs from config or environment.

        Supported keys/env:
        - telegram_admin_chat_ids
        - admin_chat_ids
        - TELEGRAM_ADMIN_CHAT_IDS
        - ADMIN_CHAT_IDS
        """
        raw_values: List[str] = []
        raw_values.extend(_as_string_list(config.get("telegram_admin_chat_ids")))
        raw_values.extend(_as_string_list(config.get("admin_chat_ids")))
        raw_values.extend(_as_string_list(os.getenv("TELEGRAM_ADMIN_CHAT_IDS", "")))
        raw_values.extend(_as_string_list(os.getenv("ADMIN_CHAT_IDS", "")))
        return {str(item).strip() for item in raw_values if str(item).strip()}

    def _is_admin_chat(self, chat_id: Any, config: Dict[str, Any]) -> bool:
        """Return True when the chat can run model-management commands.

        Secure default: if no admin IDs are configured, model-management commands
        are blocked. Set allow_unrestricted_model_commands=True only for private
        testing.
        """
        admin_ids = self._admin_chat_ids(config)
        if not admin_ids:
            return bool(config.get("allow_unrestricted_model_commands", False))
        return str(chat_id).strip() in admin_ids

    def _send_admin_required(self, token: str, chat_id: int, config: Dict[str, Any]) -> None:
        admin_ids = self._admin_chat_ids(config)
        if admin_ids:
            msg = "Perintah ini hanya untuk admin bot."
        else:
            msg = (
                "Perintah model hanya untuk admin, tetapi admin_chat_ids belum diatur.\n\n"
                "Tambahkan salah satu konfigurasi ini:\n"
                "- telegram_admin_chat_ids\n"
                "- admin_chat_ids\n"
                "- TELEGRAM_ADMIN_CHAT_IDS\n\n"
                f"Chat ID Anda: {chat_id}"
            )
        self._send_message(token, chat_id, msg)

    def force_local_reset(self) -> str:
        """Reset worker/lock state only for the current Streamlit process/container.

        This cannot stop another deployment/laptop/VPS that uses the same bot token,
        but it helps when a previous Streamlit rerun left local state inconsistent.
        """
        try:
            self.stop()
            lock_path = os.path.abspath(self._lock_file or DEFAULT_LOCK_FILE)
            if lock_path.startswith("/tmp/") and os.path.exists(lock_path):
                try:
                    os.remove(lock_path)
                except OSError:
                    pass
            self._last_error = ""
            return "Reset lokal selesai. Coba Start Bot lagi."
        except Exception as exc:
            self._last_error = f"Gagal reset lokal Telegram: {exc}"
            return self._last_error

    def start(self, config: Dict[str, Any]) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._running = True
                return False

            token = str(config.get("telegram_token") or "").strip()
            if not token:
                self._last_error = "TELEGRAM_BOT_TOKEN belum diisi."
                self._running = False
                return False

            # Validate the token before creating the worker. This prevents the UI
            # from showing a bot as started while the polling thread immediately dies.
            try:
                self._telegram_post(token, "getMe", {}, timeout=12)
                # Polling bot must not have an active webhook. Do this before the worker starts
                # so failures are visible in the admin UI, not hidden inside the thread.
                self._telegram_post(
                    token,
                    "deleteWebhook",
                    {"drop_pending_updates": bool(config.get("drop_pending_updates", True))},
                    timeout=20,
                )
            except Exception as exc:
                self._last_error = f"Gagal validasi/koneksi Telegram: {exc}"
                self._running = False
                return False

            self._lock_file = str(config.get("lock_file") or DEFAULT_LOCK_FILE)
            if not self._acquire_file_lock():
                self._running = False
                return False

            self._stop_event.clear()
            self._last_error = ""
            self._started_at = _wib_now_text()
            self._worker_id = f"{os.getpid()}-{int(time.time())}"
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(config,),
                daemon=True,
                name="adioranye-telegram-bot-singleton",
            )
            self._thread.start()
            self._running = True
            return True

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            self._running = False
            self._release_file_lock()

    def _acquire_file_lock(self) -> bool:
        """Acquire an OS-level lock for the polling worker.

        The default lock path is in /tmp because Streamlit Cloud deployments can
        have stricter permissions in the source directory. If a custom lock path
        fails because of permissions, the service falls back to /tmp automatically.
        """
        candidate_paths: List[str] = []
        configured = str(self._lock_file or DEFAULT_LOCK_FILE).strip() or DEFAULT_LOCK_FILE
        candidate_paths.append(configured)
        if configured != DEFAULT_LOCK_FILE:
            candidate_paths.append(DEFAULT_LOCK_FILE)
        if "/tmp/" not in configured:
            candidate_paths.append("/tmp/adioranye_telegram_bot_worker.lock")

        last_exc = ""
        for candidate_path in _as_string_list(candidate_paths):
            try:
                lock_path = os.path.abspath(candidate_path)
                os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
                self._lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.ftruncate(self._lock_fd, 0)
                os.write(
                    self._lock_fd,
                    json.dumps({
                        "worker_id": self._worker_id,
                        "pid": os.getpid(),
                        "started_at": time.time(),
                        "lock_path": lock_path,
                    }).encode("utf-8"),
                )
                self._lock_file = lock_path
                self._has_file_lock = True
                return True
            except BlockingIOError:
                last_exc = (
                    "Bot Telegram sudah aktif di proses/container lain. "
                    "Jika Anda yakin tidak ada bot lain, klik Reset koneksi Telegram, "
                    "lalu Force reset lokal. Jika masih gagal, revoke token di BotFather."
                )
                try:
                    if self._lock_fd is not None:
                        os.close(self._lock_fd)
                except OSError:
                    pass
                self._lock_fd = None
                break
            except Exception as exc:
                last_exc = f"Gagal membuat lock bot Telegram di {candidate_path}: {exc}"
                try:
                    if self._lock_fd is not None:
                        os.close(self._lock_fd)
                except OSError:
                    pass
                self._lock_fd = None
                continue

        self._last_error = last_exc or "Gagal membuat lock bot Telegram."
        return False

    def _heartbeat_lock(self) -> None:
        # Lock is held by an open file descriptor; no heartbeat needed.
        return

    def _release_file_lock(self) -> None:
        if not self._has_file_lock:
            return
        try:
            if self._lock_fd is not None:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
                self._lock_fd = None
        except OSError:
            pass
        self._has_file_lock = False

    def reset_telegram_session(self, config: Dict[str, Any]) -> str:
        """Reset webhook and pending updates for this bot token.

        This cannot kill old deployments that are still running elsewhere, but it
        clears Telegram-side pending updates and helps after a redeploy/reboot.
        """
        token = config.get("telegram_token", "")
        if not token:
            return "TELEGRAM_BOT_TOKEN belum diisi."
        self.stop()
        try:
            self._telegram_post(token, "deleteWebhook", {"drop_pending_updates": True}, timeout=20)
            data = self._telegram_post(token, "getUpdates", {"offset": -1, "limit": 1, "timeout": 1}, timeout=10)
            return "Sesi Telegram direset. Pending update dibersihkan. Jika masih double/triple, revoke token di BotFather karena masih ada instance lama di luar app ini."
        except Exception as exc:
            return f"Gagal reset sesi Telegram: {exc}"
        self._lock_fd = None

    def _remember_update(self, update_id: int) -> bool:
        """Return True if update is new, False if duplicate."""
        if update_id in self._seen_set:
            self._duplicates_skipped += 1
            return False
        if len(self._seen_queue) == self._seen_queue.maxlen:
            old = self._seen_queue.popleft()
            self._seen_set.discard(old)
        self._seen_queue.append(update_id)
        self._seen_set.add(update_id)
        return True

    def _telegram_error_message(self, method: str, data: Dict[str, Any], status_code: int | None = None) -> str:
        """Build a clear Telegram API error message without exposing the bot token."""
        error_code = data.get("error_code") if isinstance(data, dict) else status_code
        description = str((data or {}).get("description") or "").strip()
        prefix = f"Telegram API error {method}"

        if error_code == 401 or "unauthorized" in description.lower():
            return (
                f"{prefix}: token bot tidak valid/expired/revoked. "
                "Buat token baru di BotFather lalu update TELEGRAM_BOT_TOKEN di Secrets."
            )
        if error_code == 404 or "not found" in description.lower():
            return (
                f"{prefix}: endpoint bot tidak ditemukan. "
                "Periksa format TELEGRAM_BOT_TOKEN; jangan ada spasi, kutip tambahan, atau karakter tersembunyi."
            )
        if error_code == 409 or "conflict" in description.lower():
            return (
                f"{prefix}: 409 Conflict. Token bot sedang dipakai oleh instance lain/getUpdates lain. "
                "Matikan deploy/laptop/VPS lama, klik Reset koneksi Telegram, atau revoke token di BotFather lalu pakai token baru."
            )
        return f"{prefix}: {data}"

    def _is_fatal_telegram_error(self, error_text: str) -> bool:
        lower = str(error_text or "").lower()
        fatal_markers = [
            "token bot tidak valid",
            "unauthorized",
            "not found",
            "409 conflict",
            "sedang dipakai oleh instance lain",
        ]
        return any(marker in lower for marker in fatal_markers)

    def _telegram_post(self, token: str, method: str, payload: Dict[str, Any], timeout: int = 35) -> Dict[str, Any]:
        if not str(token or "").strip():
            raise RuntimeError("TELEGRAM_BOT_TOKEN belum diisi.")

        url = TELEGRAM_API.format(token=str(token).strip(), method=method)
        try:
            resp = requests.post(url, json=payload, timeout=timeout)
        except requests.Timeout:
            raise RuntimeError(f"Telegram API timeout saat menjalankan {method}. Coba ulangi atau cek koneksi Streamlit Cloud.")
        except requests.RequestException as exc:
            raise RuntimeError(f"Telegram API request gagal saat {method}: {exc}")

        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"Telegram response bukan JSON saat {method}. HTTP {resp.status_code}: {resp.text[:1000]}")

        if resp.status_code != 200 or not data.get("ok"):
            raise RuntimeError(self._telegram_error_message(method, data, status_code=resp.status_code))
        return data

    def diagnose(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Run lightweight Telegram diagnostics for the admin panel."""
        token = str(config.get("telegram_token") or "").strip()
        result: Dict[str, Any] = {
            "ok": False,
            "bot_username": "",
            "bot_id": "",
            "webhook_url": "",
            "pending_update_count": None,
            "last_error": "",
            "running": self.status().get("running", False),
        }
        if not token:
            result["last_error"] = "TELEGRAM_BOT_TOKEN belum diisi."
            return result
        try:
            me = self._telegram_post(token, "getMe", {}, timeout=12).get("result") or {}
            result["bot_username"] = str(me.get("username") or "")
            result["bot_id"] = str(me.get("id") or "")

            webhook = self._telegram_post(token, "getWebhookInfo", {}, timeout=12).get("result") or {}
            result["webhook_url"] = str(webhook.get("url") or "")
            result["pending_update_count"] = webhook.get("pending_update_count")
            result["ok"] = True
            return result
        except Exception as exc:
            result["last_error"] = str(exc)[:1200]
            self._last_error = result["last_error"]
            return result

    def _send_message(self, token: str, chat_id: int, text: str, parse_mode: str = "") -> None:
        """Send Telegram message as strict plain text.

        IMPORTANT: this method intentionally ignores any parse_mode from
        secrets/config. Telegram errors such as:
        - Unsupported start tag "uses-permission"
        - Unsupported start tag "ip-server"
        happen when AI output contains XML/HTML-looking text and Telegram tries
        to parse it as HTML. For an AI assistant, answers often contain code,
        XML, HTML, AndroidManifest, nginx config, etc., so the safest behavior
        is to never send parse_mode at all.
        """
        safe_text = normalize_telegram_text(text)
        for chunk in split_telegram_message(safe_text):
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
                # Do not include parse_mode under any condition.
                # Plain text allows <ip-server>, <uses-permission>, <div>, etc.
            }
            self._telegram_post(token, "sendMessage", payload, timeout=20)

    def _send_typing(self, token: str, chat_id: int) -> None:
        try:
            self._telegram_post(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
        except Exception:
            # Typing indicator is optional.
            pass

    def _run_loop(self, config: Dict[str, Any]) -> None:
        token = config.get("telegram_token", "")
        api_key = config.get("slashai_api_key", "")
        api_url = config.get("slashai_api_url", "")
        model = config.get("slashai_model", "slashai/gpt-5-nano")
        persona = config.get("persona", "")
        memory_file = config.get("memory_file", "assistant_memory.json")
        fallback_models = config.get("fallback_models") or []
        expensive_fallback_models = config.get("expensive_fallback_models") or []
        allow_expensive_fallback = bool(config.get("allow_expensive_fallback", True))
        max_expensive_models = int(config.get("max_expensive_models", 1) or 1)
        drop_pending_updates = bool(config.get("drop_pending_updates", True))
        send_processing_message = bool(config.get("send_processing_message", False))
        allow_memory_commands = bool(config.get("allow_memory_commands", False))
        telegram_parse_mode = ""  # Force plain text; ignore TELEGRAM_PARSE_MODE to prevent HTML parse errors
        smart_model_router = bool(config.get("smart_model_router", True))
        return_to_primary = bool(config.get("return_to_primary", True))
        max_smart_models = int(config.get("max_smart_models", 2) or 2)
        thinking_model_router = bool(config.get("thinking_model_router", True))
        thinking_min_chars = int(config.get("thinking_min_chars", 180) or 180)
        fast_normal_model_router = bool(config.get("fast_normal_model_router", True))
        speed_update_code = str(config.get("speed_update_code") or "4321").strip()
        model_health_timeout = int(config.get("model_health_timeout", 12) or 12)
        power_features_enabled = bool(config.get("power_features_enabled", True))
        power_db_path = str(config.get("power_db_path") or ".adioranye_power.db")
        power_rag_enabled = bool(config.get("power_rag_enabled", True))
        power_rag_top_k = int(config.get("power_rag_top_k", 5) or 5)
        power_strict_rag_mode = bool(config.get("power_strict_rag_mode", False))
        power_anti_hallucination_enabled = bool(config.get("power_anti_hallucination_enabled", True))
        power_anti_hallucination_auto_strict = bool(config.get("power_anti_hallucination_auto_strict", True))
        power_anti_hallucination_min_sources = int(config.get("power_anti_hallucination_min_sources", 1) or 1)
        power_anti_hallucination_min_quality = float(config.get("power_anti_hallucination_min_quality", 0) or 0)
        power_anti_hallucination_min_freshness = float(config.get("power_anti_hallucination_min_freshness", 0) or 0)
        power_anti_hallucination_append_sources = bool(config.get("power_anti_hallucination_append_sources", True))
        power_rag_min_sources = int(config.get("power_rag_min_sources", 1) or 1)
        power_rag_min_score = float(config.get("power_rag_min_score", 0) or 0)
        power_persistent_memory_enabled = bool(config.get("power_persistent_memory_enabled", True))
        power_prompt_templates_enabled = bool(config.get("power_prompt_templates_enabled", True))
        power_self_verification_enabled = bool(config.get("power_self_verification_enabled", False))
        power_quality_control_enabled = bool(config.get("power_quality_control_enabled", True))
        power_quality_verifier_enabled = bool(config.get("power_quality_verifier_enabled", True))
        power_quality_verifier_model = str(config.get("power_quality_verifier_model") or "").strip()
        power_quality_min_score = float(config.get("power_quality_min_score", 0.72) or 0.72)
        power_quality_append_footer = bool(config.get("power_quality_append_footer", False))
        power_hide_kb_sources_for_casual = bool(config.get("power_hide_kb_sources_for_casual", True))
        power_disable_rag_for_casual = bool(config.get("power_disable_rag_for_casual", True))
        power_performance_optimizer_enabled = bool(config.get("power_performance_optimizer_enabled", True))
        power_query_rewriter_enabled = bool(config.get("power_query_rewriter_enabled", True))
        power_reranker_enabled = bool(config.get("power_reranker_enabled", True))
        power_semantic_cache_enabled = bool(config.get("power_semantic_cache_enabled", True))
        power_semantic_cache_threshold = float(config.get("power_semantic_cache_threshold", 0.78) or 0.78)
        power_semantic_cache_ttl_seconds = int(config.get("power_semantic_cache_ttl_seconds", 86400) or 86400)
        power_latency_budget_enabled = bool(config.get("power_latency_budget_enabled", True))
        power_retrieval_eval_enabled = bool(config.get("power_retrieval_eval_enabled", True))
        live_music_chart_enabled = bool(config.get("live_music_chart_enabled", True))
        live_music_chart_limit = int(config.get("live_music_chart_limit", 10) or 10)
        live_music_chart_timeout_seconds = int(config.get("live_music_chart_timeout_seconds", 8) or 8)
        live_web_fallback_enabled = bool(config.get("live_web_fallback_enabled", True))
        live_web_fallback_provider = str(config.get("live_web_fallback_provider") or "tavily")
        tavily_api_key = str(config.get("tavily_api_key") or "")
        live_web_fallback_max_results = int(config.get("live_web_fallback_max_results", 4) or 4)
        live_web_fallback_timeout_seconds = int(config.get("live_web_fallback_timeout_seconds", 10) or 10)
        live_web_fallback_min_sources = int(config.get("live_web_fallback_min_sources", 1) or 1)
        live_web_fallback_include_raw_content = bool(config.get("live_web_fallback_include_raw_content", True))
        live_web_fallback_max_content_chars = int(config.get("live_web_fallback_max_content_chars", 3200) or 3200)
        live_web_fallback_auto_save_to_kb = bool(config.get("live_web_fallback_auto_save_to_kb", True))
        live_web_fallback_ttl_hours = int(config.get("live_web_fallback_ttl_hours", 24) or 24)
        live_web_fallback_force_for_current = bool(config.get("live_web_fallback_force_for_current", True))
        live_web_fallback_topic = str(config.get("live_web_fallback_topic") or "auto")
        power_default_answer_mode = str(config.get("power_default_answer_mode") or "auto").strip().lower()
        daily_cost_limit_idr = float(config.get("daily_cost_limit_idr", 0) or 0)
        max_expensive_calls_per_day = int(config.get("max_expensive_calls_per_day", 0) or 0)
        power_response_cache_enabled = bool(config.get("power_response_cache_enabled", True))
        power_response_cache_ttl_seconds = int(config.get("power_response_cache_ttl_seconds", 1800) or 1800)
        power_adaptive_scoring_enabled = bool(config.get("power_adaptive_scoring_enabled", True))
        power_circuit_breaker_enabled = bool(config.get("power_circuit_breaker_enabled", True))
        model_circuit_max_failures = int(config.get("model_circuit_max_failures", 3) or 3)
        model_circuit_cooldown_seconds = int(config.get("model_circuit_cooldown_seconds", 1800) or 1800)
        fast_cheap_models_runtime = _as_string_list(config.get("fast_cheap_models"))
        thinking_capable_models_runtime = _as_string_list(config.get("thinking_capable_models"))
        forced_model_mode = str(config.get("telegram_model_mode") or "auto").strip().lower()
        if forced_model_mode not in {"auto", "cheap", "expensive"}:
            forced_model_mode = "auto"

        # Restore last runtime routing state after Streamlit/App restart.
        runtime_state = self._load_runtime_state(config)
        if runtime_state:
            model = str(runtime_state.get("primary_model") or runtime_state.get("slashai_model") or model).strip() or model
            fallback_models = _as_string_list(runtime_state.get("fallback_models")) or fallback_models
            expensive_fallback_models = _as_string_list(runtime_state.get("expensive_fallback_models")) or expensive_fallback_models
            fast_cheap_models_runtime = _as_string_list(runtime_state.get("fast_cheap_models")) or fast_cheap_models_runtime
            thinking_capable_models_runtime = _as_string_list(runtime_state.get("thinking_capable_models")) or thinking_capable_models_runtime
            restored_mode = str(runtime_state.get("telegram_model_mode") or forced_model_mode).strip().lower()
            if restored_mode in {"auto", "cheap", "expensive"}:
                forced_model_mode = restored_mode
            allow_expensive_fallback = bool(runtime_state.get("allow_expensive_fallback", bool(expensive_fallback_models)))
            self._model_health_checked_at = str(runtime_state.get("model_health_checked_at") or "")
            self._last_model_update_source = str(runtime_state.get("last_model_update_source") or "runtime_state")
            config.update({
                "slashai_model": model,
                "telegram_model_mode": forced_model_mode,
                "fallback_models": fallback_models,
                "expensive_fallback_models": expensive_fallback_models,
                "fast_cheap_models": fast_cheap_models_runtime,
                "fastest_cheap_model": fast_cheap_models_runtime[0] if fast_cheap_models_runtime else "",
                "thinking_capable_models": thinking_capable_models_runtime,
                "allow_expensive_fallback": allow_expensive_fallback,
            })

        self._runtime_primary_model = model
        self._forced_model_mode = forced_model_mode

        if not token:
            self._last_error = "TELEGRAM_BOT_TOKEN belum diisi."
            self._running = False
            self._release_file_lock()
            return

        memory = MemoryStore(memory_file)
        power_store = get_power_store(power_db_path)
        offset = None

        def persist_current_runtime_state(source: str) -> None:
            self._save_runtime_state(config, {
                "primary_model": model,
                "slashai_model": model,
                "telegram_model_mode": forced_model_mode,
                "fallback_models": fallback_models,
                "expensive_fallback_models": expensive_fallback_models,
                "active_cheap_models": config.get("active_cheap_models") or [],
                "fast_cheap_models": fast_cheap_models_runtime,
                "thinking_capable_models": thinking_capable_models_runtime,
                "active_expensive_models": thinking_capable_models_runtime,
                "allow_expensive_fallback": bool(allow_expensive_fallback),
                "model_health_checked_at": self._model_health_checked_at,
                "last_model_update_source": source,
            })

        def apply_rotation_result(rotation_result: Dict[str, Any], rotation: Dict[str, Any], source: str) -> None:
            nonlocal model, fallback_models, expensive_fallback_models, fast_cheap_models_runtime
            nonlocal thinking_capable_models_runtime, allow_expensive_fallback, max_smart_models
            nonlocal forced_model_mode, thinking_model_router, fast_normal_model_router

            model = rotation.get("selected_model") or model
            # Keep the requested mode stable. select_rotated_runtime_model may use a
            # temporary safety fallback, but /ubah murah should still mean cheap-first
            # on the next rotate when cheap models return.
            selected_mode = str(rotation.get("requested_mode") or rotation.get("selected_mode") or forced_model_mode or "auto").lower()
            if selected_mode in {"auto", "cheap", "expensive"}:
                forced_model_mode = selected_mode

            fallback_models = rotation.get("fallback_models") or []
            expensive_fallback_models = rotation.get("expensive_fallback_models") or []
            fast_cheap_models_runtime = rotation.get("fast_cheap_models") or []
            thinking_capable_models_runtime = rotation.get("active_expensive_models") or []
            allow_expensive_fallback = bool(rotation.get("allow_expensive_fallback"))
            max_smart_models = max(int(max_smart_models or 1), len(fallback_models), 1)

            if forced_model_mode == "cheap":
                thinking_model_router = False
                fast_normal_model_router = True
            elif forced_model_mode == "expensive":
                thinking_model_router = False
                fast_normal_model_router = False
            else:
                thinking_model_router = bool(config.get("thinking_model_router", True))
                fast_normal_model_router = bool(config.get("fast_normal_model_router", True))

            config.update({
                "slashai_model": model,
                "telegram_model_mode": forced_model_mode,
                "fallback_models": fallback_models,
                "expensive_fallback_models": expensive_fallback_models,
                "active_cheap_models": rotation.get("active_cheap_models") or [],
                "fast_cheap_models": fast_cheap_models_runtime,
                "fastest_cheap_model": fast_cheap_models_runtime[0] if fast_cheap_models_runtime else "",
                "thinking_capable_models": thinking_capable_models_runtime,
                "allow_expensive_fallback": allow_expensive_fallback,
            })
            self._model_health_cache = rotation_result.get("health_cache") or {}
            self._model_health_checked_at = _wib_now_text()
            self._last_model_update_source = source
            self._runtime_primary_model = model
            self._forced_model_mode = forced_model_mode
            persist_current_runtime_state(source)

        try:
            # drop_pending_updates=True prevents old messages from being answered twice
            # after Streamlit restarts or wakes from sleep.
            self._telegram_post(
                token,
                "deleteWebhook",
                {"drop_pending_updates": drop_pending_updates},
                timeout=20,
            )
        except Exception as exc:
            self._last_error = f"Gagal deleteWebhook: {exc}"

        try:
            while not self._stop_event.is_set():
                self._heartbeat_lock()
                try:
                    payload = {"timeout": 25, "limit": 10, "allowed_updates": ["message"]}
                    if offset is not None:
                        payload["offset"] = offset

                    data = self._telegram_post(token, "getUpdates", payload, timeout=35)
                    updates = data.get("result", [])

                    for update in updates:
                        update_id = int(update.get("update_id", 0))
                        offset = update_id + 1

                        if not self._remember_update(update_id):
                            continue

                        message = update.get("message") or {}
                        chat = message.get("chat") or {}
                        chat_id = chat.get("id")
                        text = (message.get("text") or "").strip()

                        if not chat_id or not text:
                            continue

                        self._last_update = f"Chat {chat_id}: {text[:120]}"
                        self._processed += 1

                        text_lower = text.lower()
                        if text_lower in {"/start", "start"}:
                            self._send_message(
                                token,
                                chat_id,
                                "Halo, saya adioranye.\n\nKirim pertanyaan langsung untuk dijawab AI.\nKetik /help untuk melihat bantuan.",
                                parse_mode=telegram_parse_mode,
                            )
                            continue

                        if text_lower in {"/help", "help"}:
                            self._send_message(
                                token,
                                chat_id,
                                build_telegram_help_text(is_admin=self._is_admin_chat(chat_id, config)),
                                parse_mode=telegram_parse_mode,
                            )
                            continue

                        if is_speed_update_command(text, expected_code=speed_update_code):
                            if not self._is_admin_chat(chat_id, config):
                                self._send_admin_required(token, chat_id, config)
                                continue
                            self._send_message(
                                token,
                                chat_id,
                                "⏳ Mengecek semua model. Setelah selesai, hanya model yang hidup yang akan dipakai...",
                                parse_mode=telegram_parse_mode,
                            )
                            try:
                                speed_result = refresh_telegram_runtime_models(
                                    api_url=api_url,
                                    api_key=api_key,
                                    current_model=model,
                                    config=config,
                                    timeout=model_health_timeout,
                                    preferred_mode="auto",
                                )
                                model = speed_result.get("primary_model") or model
                                fallback_models = speed_result.get("fallback_models") or []
                                expensive_fallback_models = speed_result.get("expensive_fallback_models") or []
                                fast_cheap_models_runtime = speed_result.get("fast_cheap_models") or []
                                thinking_capable_models_runtime = speed_result.get("thinking_capable_models") or []
                                allow_expensive_fallback = bool(expensive_fallback_models)
                                max_smart_models = max(int(max_smart_models or 1), len(fallback_models), 1)

                                config["slashai_model"] = model
                                config["fallback_models"] = fallback_models
                                config["expensive_fallback_models"] = expensive_fallback_models
                                config["active_cheap_models"] = speed_result.get("active_cheap_models") or []
                                config["fast_cheap_models"] = fast_cheap_models_runtime
                                config["fastest_cheap_model"] = fast_cheap_models_runtime[0] if fast_cheap_models_runtime else ""
                                config["thinking_capable_models"] = thinking_capable_models_runtime

                                self._model_health_cache = speed_result.get("health_cache") or {}
                                self._model_health_checked_at = _wib_now_text()
                                self._last_model_update_source = "speed"
                                self._runtime_primary_model = model
                                config["allow_expensive_fallback"] = allow_expensive_fallback
                                persist_current_runtime_state("speed")
                                self._send_message(token, chat_id, build_speed_update_summary(speed_result), parse_mode=telegram_parse_mode)
                            except Exception as exc:
                                self._last_error = str(exc)
                                self._send_message(
                                    token,
                                    chat_id,
                                    "Gagal update model.\n\nDetail ringkas:\n" + str(exc)[:1200],
                                    parse_mode=telegram_parse_mode,
                                )
                            continue

                        if text_lower.startswith("/speed"):
                            self._send_message(
                                token,
                                chat_id,
                                "Kode /speed salah. Gunakan format: /speed 4321",
                                parse_mode=telegram_parse_mode,
                            )
                            continue

                        if is_rotate_model_command(text):
                            if not self._is_admin_chat(chat_id, config):
                                self._send_admin_required(token, chat_id, config)
                                continue
                            self._send_message(
                                token,
                                chat_id,
                                "⏳ Rotate model: mengecek kondisi model aktif saat ini...",
                                parse_mode=telegram_parse_mode,
                            )
                            try:
                                previous_model = model
                                rotate_result = refresh_telegram_runtime_models(
                                    api_url=api_url,
                                    api_key=api_key,
                                    current_model=model,
                                    config=config,
                                    timeout=model_health_timeout,
                                    preferred_mode=forced_model_mode,
                                )
                                rotation = select_rotated_runtime_model(
                                    result=rotate_result,
                                    current_mode=forced_model_mode,
                                    current_model=model,
                                )

                                rotation["requested_mode"] = forced_model_mode
                                apply_rotation_result(rotate_result, rotation, "rotate")
                                self._send_message(
                                    token,
                                    chat_id,
                                    build_rotate_summary(rotate_result, rotation, previous_model),
                                    parse_mode=telegram_parse_mode,
                                )
                            except Exception as exc:
                                self._last_error = str(exc)
                                self._send_message(
                                    token,
                                    chat_id,
                                    "Gagal rotate model.\n\nDetail ringkas:\n" + str(exc)[:1200],
                                    parse_mode=telegram_parse_mode,
                                )
                            continue

                        if text_lower.startswith("/rotate"):
                            self._send_message(
                                token,
                                chat_id,
                                "Format perintah salah. Gunakan: /rotate",
                                parse_mode=telegram_parse_mode,
                            )
                            continue

                        switch_mode = parse_model_switch_command(text)
                        if switch_mode:
                            if not self._is_admin_chat(chat_id, config):
                                self._send_admin_required(token, chat_id, config)
                                continue
                            self._send_message(
                                token,
                                chat_id,
                                "⏳ Mengubah mode dan mengecek model aktif saat ini...",
                                parse_mode=telegram_parse_mode,
                            )
                            try:
                                previous_model = model
                                switch_result = refresh_telegram_runtime_models(
                                    api_url=api_url,
                                    api_key=api_key,
                                    current_model=model,
                                    config=config,
                                    timeout=model_health_timeout,
                                    preferred_mode=switch_mode,
                                )
                                rotation = select_rotated_runtime_model(
                                    result=switch_result,
                                    current_mode=switch_mode,
                                    current_model=model,
                                )
                                rotation["requested_mode"] = switch_mode
                                apply_rotation_result(switch_result, rotation, "ubah")

                                cheap_pool = fast_cheap_models_runtime or _as_string_list(config.get("fast_cheap_models")) or _as_string_list(config.get("fallback_models"))
                                capable_pool = thinking_capable_models_runtime or _as_string_list(config.get("thinking_capable_models")) or _as_string_list(config.get("expensive_fallback_models"))
                                message = build_model_switch_summary(switch_mode, model, cheap_pool, capable_pool)
                                message += "\n\n" + build_rotate_summary(switch_result, rotation, previous_model)
                                self._send_message(token, chat_id, message, parse_mode=telegram_parse_mode)
                            except Exception as exc:
                                self._last_error = str(exc)
                                self._send_message(
                                    token,
                                    chat_id,
                                    "Gagal mengubah mode model.\n\nDetail ringkas:\n" + str(exc)[:1200],
                                    parse_mode=telegram_parse_mode,
                                )
                            continue

                        if text_lower.startswith("/ubah"):
                            self._send_message(
                                token,
                                chat_id,
                                "Format perintah salah. Gunakan: /ubah mahal atau /ubah murah",
                                parse_mode=telegram_parse_mode,
                            )
                            continue

                        if is_update_command(text):
                            if not self._is_admin_chat(chat_id, config):
                                self._send_admin_required(token, chat_id, config)
                                continue
                            self._send_message(
                                token,
                                chat_id,
                                "⏳ Memulai update Knowledge Base via GitHub Actions...",
                                parse_mode=telegram_parse_mode,
                            )
                            try:
                                update_reply = trigger_github_kb_update(config, chat_id)
                            except Exception as exc:
                                update_reply = "❌ Gagal menjalankan /update.\n\nDetail ringkas:\n" + str(exc)[:1200]
                            self._send_message(token, chat_id, update_reply, parse_mode=telegram_parse_mode)
                            continue

                        if text_lower.startswith("/update"):
                            self._send_message(
                                token,
                                chat_id,
                                "Format perintah salah. Gunakan: /update",
                                parse_mode=telegram_parse_mode,
                            )
                            continue

                        local_reply = handle_local_memory_command(text, memory) if allow_memory_commands else ""
                        if not local_reply and power_features_enabled:
                            local_reply = handle_power_command(
                                text,
                                power_store,
                                user_id=str(chat_id),
                                is_admin=self._is_admin_chat(chat_id, config),
                            )
                        if local_reply:
                            self._send_message(token, chat_id, local_reply, parse_mode=telegram_parse_mode)
                            continue

                        key = str(chat_id)
                        history = self._histories.setdefault(key, [])
                        memory_text = memory.as_prompt_text(limit=20)

                        if send_processing_message:
                            self._send_message(token, chat_id, "⏳ Sedang diproses...", parse_mode=telegram_parse_mode)
                        else:
                            self._send_typing(token, chat_id)

                        try:
                            manual_mode = str(forced_model_mode or "auto").lower()
                            thinking_mode = (
                                manual_mode == "auto"
                                and bool(thinking_model_router)
                                and is_thinking_telegram_question(
                                    text,
                                    history=history,
                                    min_chars=thinking_min_chars,
                                )
                            )
                            request_model = model
                            request_fallback_models = list(fallback_models or [])
                            request_expensive_fallback_models = list(expensive_fallback_models or [])
                            request_allow_expensive = allow_expensive_fallback
                            request_return_to_primary = return_to_primary

                            fast_normal_mode = False

                            if manual_mode == "expensive":
                                capable_pool = thinking_capable_models_runtime or request_expensive_fallback_models
                                capable_model = pick_telegram_capable_model(
                                    primary_model=model,
                                    expensive_fallback_models=capable_pool,
                                    config=config,
                                )
                                if capable_model:
                                    request_model = capable_model
                                    request_fallback_models = []
                                    request_expensive_fallback_models = [
                                        item for item in capable_pool if item != request_model
                                    ]
                                request_allow_expensive = True
                                request_return_to_primary = False
                            elif manual_mode == "cheap":
                                fast_model = pick_fastest_telegram_normal_model(
                                    primary_model=model,
                                    fallback_models=request_fallback_models,
                                    config=config,
                                )
                                if fast_model:
                                    fast_pool = fast_cheap_models_runtime or _as_string_list(config.get("fast_cheap_models")) or request_fallback_models
                                    if fast_model not in fast_pool:
                                        fast_pool = [fast_model] + fast_pool
                                    request_model = fast_model
                                    request_fallback_models = [item for item in fast_pool if item != request_model]
                                    fast_normal_mode = True
                                request_expensive_fallback_models = []
                                request_allow_expensive = False
                                request_return_to_primary = False
                            elif thinking_mode:
                                capable_model = pick_telegram_capable_model(
                                    primary_model=model,
                                    expensive_fallback_models=thinking_capable_models_runtime or request_expensive_fallback_models,
                                    config=config,
                                )
                                if capable_model:
                                    request_model = capable_model
                                    # For thinking prompts, do not route back down to cheap models first.
                                    # Use capable/expensive models as the main path, then return to cheap on the next message.
                                    request_fallback_models = []
                                    request_expensive_fallback_models = [
                                        item for item in request_expensive_fallback_models if item != request_model
                                    ]
                                    request_allow_expensive = True
                                    request_return_to_primary = True
                            elif fast_normal_model_router:
                                fast_model = pick_fastest_telegram_normal_model(
                                    primary_model=model,
                                    fallback_models=request_fallback_models,
                                    config=config,
                                )
                                if fast_model:
                                    fast_pool = fast_cheap_models_runtime or _as_string_list(config.get("fast_cheap_models")) or request_fallback_models
                                    if fast_model not in fast_pool:
                                        fast_pool = [fast_model] + fast_pool
                                    request_model = fast_model
                                    request_fallback_models = [item for item in fast_pool if item != request_model]
                                    fast_normal_mode = True

                            answer, meta = safe_generate_power_answer(
                                api_url=api_url,
                                api_key=api_key,
                                model=request_model,
                                system_prompt=persona,
                                user_text=text,
                                base_memory_text=memory_text,
                                recent_messages=history,
                                fallback_models=request_fallback_models,
                                expensive_fallback_models=request_expensive_fallback_models,
                                allow_expensive_fallback=request_allow_expensive,
                                max_expensive_models=max_expensive_models,
                                temperature=float(config.get("temperature", 0.3)),
                                max_completion_tokens=int(config.get("max_completion_tokens", 1800)),
                                timeout=int(config.get("timeout", 60)),
                                smart_model_router=smart_model_router,
                                return_to_primary=request_return_to_primary,
                                max_smart_models=max_smart_models,
                                store=power_store,
                                user_id=str(chat_id),
                                channel="telegram",
                                enable_rag=bool(power_features_enabled and power_rag_enabled),
                                rag_top_k=int(power_rag_top_k),
                                enable_persistent_memory=bool(power_features_enabled and power_persistent_memory_enabled),
                                enable_prompt_templates=bool(power_features_enabled and power_prompt_templates_enabled),
                                enable_self_verification=bool(power_features_enabled and power_self_verification_enabled),
                                daily_cost_limit_idr=float(daily_cost_limit_idr),
                                max_expensive_calls_per_day=int(max_expensive_calls_per_day),
                                enable_response_cache=bool(power_response_cache_enabled),
                                response_cache_ttl_seconds=int(power_response_cache_ttl_seconds),
                                enable_adaptive_scoring=bool(power_adaptive_scoring_enabled),
                                enable_circuit_breaker=bool(power_circuit_breaker_enabled),
                                circuit_max_failures=int(model_circuit_max_failures),
                                circuit_cooldown_seconds=int(model_circuit_cooldown_seconds),
                                anti_hallucination_enabled=bool(power_anti_hallucination_enabled),
                                anti_hallucination_auto_strict=bool(power_anti_hallucination_auto_strict),
                                anti_hallucination_min_sources=int(power_anti_hallucination_min_sources),
                                anti_hallucination_min_quality=float(power_anti_hallucination_min_quality),
                                anti_hallucination_min_freshness=float(power_anti_hallucination_min_freshness),
                                anti_hallucination_append_sources=bool(power_anti_hallucination_append_sources),
                                strict_rag_mode=bool(power_strict_rag_mode),
                                rag_min_sources=int(power_rag_min_sources),
                                rag_min_score=float(power_rag_min_score),
                                quality_control_enabled=bool(power_quality_control_enabled),
                                quality_verifier_enabled=bool(power_quality_verifier_enabled),
                                quality_verifier_model=power_quality_verifier_model,
                                quality_min_score=float(power_quality_min_score),
                                answer_mode=power_default_answer_mode,
                                append_quality_footer=bool(power_quality_append_footer),
                                hide_kb_sources_for_casual=bool(power_hide_kb_sources_for_casual),
                                disable_rag_for_casual=bool(power_disable_rag_for_casual),
                                performance_optimizer_enabled=bool(power_performance_optimizer_enabled),
                                query_rewriter_enabled=bool(power_query_rewriter_enabled),
                                reranker_enabled=bool(power_reranker_enabled),
                                semantic_cache_enabled=bool(power_semantic_cache_enabled),
                                semantic_cache_threshold=float(power_semantic_cache_threshold),
                                semantic_cache_ttl_seconds=int(power_semantic_cache_ttl_seconds),
                                latency_budget_enabled=bool(power_latency_budget_enabled),
                                retrieval_eval_enabled=bool(power_retrieval_eval_enabled),
                                live_music_chart_enabled=bool(live_music_chart_enabled),
                                live_music_chart_limit=int(live_music_chart_limit),
                                live_music_chart_timeout_seconds=int(live_music_chart_timeout_seconds),
                                live_web_fallback_enabled=bool(live_web_fallback_enabled),
                                live_web_fallback_provider=live_web_fallback_provider,
                                tavily_api_key=tavily_api_key,
                                live_web_fallback_max_results=int(live_web_fallback_max_results),
                                live_web_fallback_timeout_seconds=int(live_web_fallback_timeout_seconds),
                                live_web_fallback_min_sources=int(live_web_fallback_min_sources),
                                live_web_fallback_include_raw_content=bool(live_web_fallback_include_raw_content),
                                live_web_fallback_max_content_chars=int(live_web_fallback_max_content_chars),
                                live_web_fallback_auto_save_to_kb=bool(live_web_fallback_auto_save_to_kb),
                                live_web_fallback_ttl_hours=int(live_web_fallback_ttl_hours),
                                live_web_fallback_force_for_current=bool(live_web_fallback_force_for_current),
                                live_web_fallback_topic=live_web_fallback_topic,
                            )

                            if isinstance(meta, dict):
                                meta["telegram_thinking_mode"] = thinking_mode
                                meta["telegram_fast_normal_mode"] = fast_normal_mode
                                meta["telegram_forced_model_mode"] = manual_mode
                                meta["telegram_rotate_mode"] = manual_mode == "auto" and self._last_model_update_source == "rotate"
                                meta["telegram_model_requested"] = request_model
                                if self._model_health_checked_at:
                                    meta["telegram_speed_updated_at"] = self._model_health_checked_at

                            history.append({"role": "user", "content": text})
                            history.append({"role": "assistant", "content": answer})
                            self._histories[key] = history[-8:]
                            # Keterangan model ditampilkan di bawah setiap jawaban Telegram
                            # agar admin/pengguna tahu model mana yang benar-benar menjawab.
                            show_model = bool(config.get("show_model_info", True))
                            if show_model:
                                answer_to_send = answer + build_telegram_model_note(
                                    meta=meta,
                                    requested_model=request_model,
                                    default_model=model,
                                )
                            else:
                                answer_to_send = answer
                            self._send_message(token, chat_id, answer_to_send, parse_mode=telegram_parse_mode)

                        except Exception as exc:
                            original_error = str(exc)
                            self._last_error = original_error

                            if bool(config.get("auto_rotate_on_model_error", True)):
                                try:
                                    self._send_typing(token, chat_id)
                                    retry_result = refresh_telegram_runtime_models(
                                        api_url=api_url,
                                        api_key=api_key,
                                        current_model=model,
                                        config=config,
                                        timeout=model_health_timeout,
                                        preferred_mode=manual_mode,
                                    )
                                    retry_rotation = select_rotated_runtime_model(
                                        result=retry_result,
                                        current_mode=manual_mode,
                                        current_model=model,
                                    )
                                    retry_rotation["requested_mode"] = manual_mode
                                    retry_previous_model = model
                                    apply_rotation_result(retry_result, retry_rotation, "auto_retry")

                                    retry_answer, retry_meta = safe_generate_power_answer(
                                        api_url=api_url,
                                        api_key=api_key,
                                        model=model,
                                        system_prompt=persona,
                                        user_text=text,
                                        base_memory_text=memory_text,
                                        recent_messages=history,
                                        fallback_models=fallback_models,
                                        expensive_fallback_models=expensive_fallback_models,
                                        allow_expensive_fallback=allow_expensive_fallback,
                                        max_expensive_models=max_expensive_models,
                                        temperature=float(config.get("temperature", 0.3)),
                                        max_completion_tokens=int(config.get("max_completion_tokens", 1800)),
                                        timeout=int(config.get("timeout", 60)),
                                        smart_model_router=smart_model_router,
                                        return_to_primary=False,
                                        max_smart_models=max_smart_models,
                                        store=power_store,
                                        user_id=str(chat_id),
                                        channel="telegram",
                                        enable_rag=bool(power_features_enabled and power_rag_enabled),
                                        rag_top_k=int(power_rag_top_k),
                                        enable_persistent_memory=bool(power_features_enabled and power_persistent_memory_enabled),
                                        enable_prompt_templates=bool(power_features_enabled and power_prompt_templates_enabled),
                                        enable_self_verification=bool(power_features_enabled and power_self_verification_enabled),
                                        daily_cost_limit_idr=float(daily_cost_limit_idr),
                                        max_expensive_calls_per_day=int(max_expensive_calls_per_day),
                                        enable_response_cache=bool(power_response_cache_enabled),
                                        response_cache_ttl_seconds=int(power_response_cache_ttl_seconds),
                                        enable_adaptive_scoring=bool(power_adaptive_scoring_enabled),
                                        enable_circuit_breaker=bool(power_circuit_breaker_enabled),
                                        circuit_max_failures=int(model_circuit_max_failures),
                                        circuit_cooldown_seconds=int(model_circuit_cooldown_seconds),
                                        anti_hallucination_enabled=bool(power_anti_hallucination_enabled),
                                        anti_hallucination_auto_strict=bool(power_anti_hallucination_auto_strict),
                                        anti_hallucination_min_sources=int(power_anti_hallucination_min_sources),
                                        anti_hallucination_min_quality=float(power_anti_hallucination_min_quality),
                                        anti_hallucination_min_freshness=float(power_anti_hallucination_min_freshness),
                                        anti_hallucination_append_sources=bool(power_anti_hallucination_append_sources),
                                        strict_rag_mode=bool(power_strict_rag_mode),
                                        rag_min_sources=int(power_rag_min_sources),
                                        rag_min_score=float(power_rag_min_score),
                                        quality_control_enabled=bool(power_quality_control_enabled),
                                        quality_verifier_enabled=bool(power_quality_verifier_enabled),
                                        quality_verifier_model=power_quality_verifier_model,
                                        quality_min_score=float(power_quality_min_score),
                                        answer_mode=power_default_answer_mode,
                                        append_quality_footer=bool(power_quality_append_footer),
                                        hide_kb_sources_for_casual=bool(power_hide_kb_sources_for_casual),
                                        disable_rag_for_casual=bool(power_disable_rag_for_casual),
                                        performance_optimizer_enabled=bool(power_performance_optimizer_enabled),
                                        query_rewriter_enabled=bool(power_query_rewriter_enabled),
                                        reranker_enabled=bool(power_reranker_enabled),
                                        semantic_cache_enabled=bool(power_semantic_cache_enabled),
                                        semantic_cache_threshold=float(power_semantic_cache_threshold),
                                        semantic_cache_ttl_seconds=int(power_semantic_cache_ttl_seconds),
                                        latency_budget_enabled=bool(power_latency_budget_enabled),
                                        retrieval_eval_enabled=bool(power_retrieval_eval_enabled),
                                        live_music_chart_enabled=bool(live_music_chart_enabled),
                                        live_music_chart_limit=int(live_music_chart_limit),
                                        live_music_chart_timeout_seconds=int(live_music_chart_timeout_seconds),
                                        live_web_fallback_enabled=bool(live_web_fallback_enabled),
                                        live_web_fallback_provider=live_web_fallback_provider,
                                        tavily_api_key=tavily_api_key,
                                        live_web_fallback_max_results=int(live_web_fallback_max_results),
                                        live_web_fallback_timeout_seconds=int(live_web_fallback_timeout_seconds),
                                        live_web_fallback_min_sources=int(live_web_fallback_min_sources),
                                        live_web_fallback_include_raw_content=bool(live_web_fallback_include_raw_content),
                                        live_web_fallback_max_content_chars=int(live_web_fallback_max_content_chars),
                                        live_web_fallback_auto_save_to_kb=bool(live_web_fallback_auto_save_to_kb),
                                        live_web_fallback_ttl_hours=int(live_web_fallback_ttl_hours),
                                        live_web_fallback_force_for_current=bool(live_web_fallback_force_for_current),
                                        live_web_fallback_topic=live_web_fallback_topic,
                                    )

                                    if isinstance(retry_meta, dict):
                                        retry_meta["telegram_auto_rotated_after_error"] = True
                                        retry_meta["telegram_previous_error"] = original_error[:500]
                                        retry_meta["telegram_previous_model"] = retry_previous_model
                                        retry_meta["telegram_forced_model_mode"] = manual_mode
                                        retry_meta["telegram_model_requested"] = model
                                        retry_meta["telegram_speed_updated_at"] = self._model_health_checked_at

                                    history.append({"role": "user", "content": text})
                                    history.append({"role": "assistant", "content": retry_answer})
                                    self._histories[key] = history[-8:]
                                    show_model = bool(config.get("show_model_info", True))
                                    if show_model:
                                        retry_answer_to_send = retry_answer + build_telegram_model_note(
                                            meta=retry_meta,
                                            requested_model=model,
                                            default_model=model,
                                        )
                                        retry_answer_to_send += "\n♻️ Catatan: model awal gagal, lalu bot otomatis rotate ke model aktif dan mengulang jawaban."
                                    else:
                                        retry_answer_to_send = retry_answer
                                    self._send_message(token, chat_id, retry_answer_to_send, parse_mode=telegram_parse_mode)
                                    continue
                                except Exception as retry_exc:
                                    self._last_error = f"{original_error} | Auto-rotate retry gagal: {retry_exc}"

                            self._send_message(
                                token,
                                chat_id,
                                "Maaf, bot belum bisa menjawab.\n\nDetail ringkas:\n" + self._last_error[:900],
                                parse_mode=telegram_parse_mode,
                            )

                    if not updates:
                        time.sleep(0.5)

                except Exception as exc:
                    self._last_error = str(exc)
                    if self._is_fatal_telegram_error(self._last_error):
                        # Do not keep looping forever for invalid token or 409 conflict.
                        self._stop_event.set()
                        break
                    time.sleep(4)
        finally:
            self._running = False
            self._release_file_lock()


_service = TelegramBotService()


def get_telegram_service() -> TelegramBotService:
    return _service