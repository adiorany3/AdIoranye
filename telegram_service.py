import os
import json
import threading
import fcntl
import time
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional, Deque, Set

import requests

from ai_core import (
    DEFAULT_CHEAP_FALLBACK_MODELS,
    DEFAULT_EXPENSIVE_FALLBACK_MODELS,
    generate_answer,
    model_cost_tier,
    model_price,
)
from memory_store import MemoryStore, handle_local_memory_command


TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
DEFAULT_LOCK_FILE = ".telegram_bot_worker.lock"
LOCK_STALE_SECONDS = 180
WIB_TZ = ZoneInfo("Asia/Jakarta")


def _wib_now_text() -> str:
    return datetime.now(WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")


def split_telegram_message(text: str, max_len: int = 3900) -> List[str]:
    text = text or ""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks


def normalize_telegram_text(text: str) -> str:
    """Send AI output to Telegram as safe plain text.

    AI answers may contain XML/HTML/Android tags such as <uses-permission>.
    If Telegram receives that while parse_mode=HTML, sendMessage fails with
    `can't parse entities`. The safest default is plain text without parse_mode.
    This function only removes unsupported control characters and keeps < > intact.
    """
    text = str(text or "")
    # Telegram can reject some ASCII control characters. Keep newlines/tabs.
    return "".join(ch for ch in text if ch in "\n\r\t" or ord(ch) >= 32)


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


def build_telegram_model_note(
    meta: Any,
    requested_model: str,
    default_model: str,
) -> str:
    """Build a clear model note appended below every Telegram answer."""
    answering_model = resolve_answering_model(meta, requested_model or default_model)
    info_lines = [
        "",
        "—",
        f"🤖 Dijawab oleh model: {answering_model}",
    ]

    if isinstance(meta, dict):
        requested = str(meta.get("telegram_model_requested") or requested_model or "").strip()
        if requested and requested != answering_model:
            info_lines.append(f"🚦 Model awal: {requested}")

        forced_mode = str(meta.get("telegram_forced_model_mode") or "").lower()
        if forced_mode == "expensive":
            info_lines.append("💎 Mode: paksa medium/mahal (/ubah mahal)")
        elif forced_mode == "cheap":
            info_lines.append("💸 Mode: paksa murah/cepat (/ubah murah)")
        elif meta.get("telegram_thinking_mode"):
            info_lines.append("🧠 Mode: thinking/capable")
        elif meta.get("telegram_fast_normal_mode"):
            info_lines.append("⚡ Mode: normal cepat/model tercepat aktif")
        else:
            info_lines.append("🧭 Mode: normal/model murah aktif")

        if meta.get("telegram_speed_updated_at"):
            info_lines.append(f"🧪 Update model terakhir: {meta.get('telegram_speed_updated_at')}")

        consulted = meta.get("consulted_models") or []
        if consulted:
            info_lines.append("🔁 Konsultasi model: " + ", ".join(str(item) for item in consulted[:4]))

        if meta.get("expensive_fallback_used"):
            info_lines.append("⚠️ Fallback: model menengah/mahal dipakai karena model murah belum cukup.")
    else:
        info_lines.append("🧭 Mode: normal")

    return "\n".join(info_lines)



def _model_tier_rank(model: str) -> int:
    """Cheap first, then medium, then expensive/unknown."""
    try:
        tier = str(model_cost_tier(model) or "").lower()
    except Exception:
        tier = ""
    if tier == "cheap":
        return 0
    if tier in {"medium", "menengah"}:
        return 1
    return 2


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
            "Mulai sekarang pertanyaan Telegram akan diarahkan ke model capable terlebih dahulu.",
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
            "Mulai sekarang pertanyaan Telegram akan diarahkan ke model murah/cepat terlebih dahulu.",
            f"Model utama mode murah: {selected or model}",
        ]
        if cheap_models:
            lines.append("Cadangan murah: " + ", ".join(str(item) for item in cheap_models[:8]))
        else:
            lines.append("Catatan: daftar model murah belum tersedia. Jalankan /speed 4321 jika ingin cek model aktif terlebih dahulu.")
        return "\n".join(lines)

    return "Format perintah: /ubah mahal atau /ubah murah"


def check_telegram_single_model_health(api_url: str, api_key: str, model: str, timeout: int = 12) -> Dict[str, Any]:
    """Check whether one model can answer a tiny OpenAI-compatible request."""
    started = time.time()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Jawab sangat singkat."},
            {"role": "user", "content": "ping"},
        ],
        "temperature": 0,
        "max_completion_tokens": 8,
    }
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=timeout)
        latency_ms = round((time.time() - started) * 1000, 1)
        if response.status_code != 200:
            return {
                "active": False,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
                "checked_at": _wib_now_text(),
                "error": response.text[:300],
            }
        data = response.json()
        choices = data.get("choices") or []
        content = ""
        if choices:
            content = str((choices[0].get("message") or {}).get("content") or "").strip()
        return {
            "active": bool(choices),
            "status_code": response.status_code,
            "latency_ms": latency_ms,
            "checked_at": _wib_now_text(),
            "sample": content[:60],
            "error": "" if choices else "Response 200 tetapi choices kosong",
        }
    except Exception as exc:
        return {
            "active": False,
            "status_code": None,
            "latency_ms": round((time.time() - started) * 1000, 1),
            "checked_at": _wib_now_text(),
            "error": str(exc)[:300],
        }


def refresh_telegram_runtime_models(api_url: str, api_key: str, current_model: str, config: Dict[str, Any], timeout: int = 12) -> Dict[str, Any]:
    """Refresh Telegram runtime routing so only active models are used."""
    if not api_url or not api_key:
        raise RuntimeError("SLASHAI_API_URL atau SLASHAI_API_KEY belum tersedia.")

    cheap_candidates = _as_string_list(config.get("all_cheap_models")) or _as_string_list(DEFAULT_CHEAP_FALLBACK_MODELS)
    cheap_candidates.extend(_as_string_list(config.get("fallback_models")))
    cheap_candidates.extend(_as_string_list(config.get("fast_cheap_models")))
    cheap_candidates.extend(_as_string_list(config.get("active_cheap_models")))

    expensive_candidates = _as_string_list(config.get("all_expensive_models")) or _as_string_list(DEFAULT_EXPENSIVE_FALLBACK_MODELS)
    expensive_candidates.extend(_as_string_list(config.get("expensive_fallback_models")))
    expensive_candidates.extend(_as_string_list(config.get("thinking_capable_models")))
    capable_override = str(config.get("thinking_capable_model") or "").strip()
    if capable_override:
        expensive_candidates.append(capable_override)

    all_model_candidates = _as_string_list(config.get("all_model_candidates"))
    candidates = _as_string_list([current_model] + cheap_candidates + expensive_candidates + all_model_candidates)

    health_cache: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        health_cache[candidate] = check_telegram_single_model_health(
            api_url=api_url,
            api_key=api_key,
            model=candidate,
            timeout=timeout,
        )

    active_cheap_priority = _prioritize_active_telegram_models(cheap_candidates, health_cache)
    active_cheap_fast = _prioritize_fastest_telegram_models(cheap_candidates, health_cache)
    active_expensive = _prioritize_active_telegram_models(expensive_candidates, health_cache)
    active_all = [model for model in candidates if health_cache.get(model, {}).get("active")]

    if active_cheap_fast:
        primary_model = active_cheap_fast[0]
    elif active_expensive:
        primary_model = active_expensive[0]
    elif active_all:
        primary_model = active_all[0]
    else:
        primary_model = current_model

    return {
        "primary_model": primary_model,
        "active_cheap_models": active_cheap_priority,
        "fast_cheap_models": active_cheap_fast,
        "fallback_models": [model for model in active_cheap_fast if model != primary_model],
        "active_expensive_models": active_expensive,
        "expensive_fallback_models": [model for model in active_expensive if model != primary_model],
        "thinking_capable_models": active_expensive,
        "health_cache": health_cache,
        "active_total": len(active_all),
        "checked_total": len(candidates),
    }


def build_speed_update_summary(result: Dict[str, Any]) -> str:
    """Human-readable Telegram summary after /speed command."""
    primary = result.get("primary_model") or "tidak ada"
    fast_cheap = result.get("fast_cheap_models") or []
    active_expensive = result.get("active_expensive_models") or []
    health_cache = result.get("health_cache") or {}
    lines = [
        "✅ Update model selesai.",
        "",
        f"Model utama sekarang: {primary}",
        f"Model murah aktif: {len(fast_cheap)}",
        f"Model menengah/mahal aktif: {len(active_expensive)}",
        f"Total dicek: {result.get('checked_total', 0)} | Hidup: {result.get('active_total', 0)}",
    ]
    if fast_cheap:
        lines.append("")
        lines.append("⚡ Urutan model murah tercepat:")
        for model_name in fast_cheap[:8]:
            latency = health_cache.get(model_name, {}).get("latency_ms")
            lines.append(f"- {model_name} ({latency} ms)")
    if active_expensive:
        lines.append("")
        lines.append("🧠 Model capable aktif:")
        for model_name in active_expensive[:5]:
            latency = health_cache.get(model_name, {}).get("latency_ms")
            lines.append(f"- {model_name} ({latency} ms)")
    if not fast_cheap and active_expensive:
        lines.append("")
        lines.append("Catatan: tidak ada model murah yang hidup, jadi bot sementara memakai model menengah/mahal aktif.")
    elif not fast_cheap and not active_expensive:
        lines.append("")
        lines.append("Peringatan: tidak ada model yang lolos health check. Bot tetap memakai model terakhir agar error tetap terlihat.")
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
            "model_health_checked_at": self._model_health_checked_at,
            "model_health_active_count": sum(1 for item in self._model_health_cache.values() if item.get("active")),
        }

    def start(self, config: Dict[str, Any]) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._running = True
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
        """Acquire an OS-level lock. This is stronger than only checking a file.

        Streamlit can rerun the app and, in some deployments, create more than one
        Python process. fcntl.flock prevents multiple pollers inside the same
        container/filesystem from reading the same Telegram updates.
        """
        try:
            lock_path = os.path.abspath(self._lock_file)
            self._lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(self._lock_fd, 0)
            os.write(
                self._lock_fd,
                json.dumps({
                    "worker_id": self._worker_id,
                    "pid": os.getpid(),
                    "started_at": time.time(),
                }).encode("utf-8"),
            )
            self._has_file_lock = True
            return True
        except BlockingIOError:
            self._last_error = (
                "Bot Telegram sudah aktif di proses lain. Instance baru tidak dijalankan "
                "agar jawaban tidak dobel."
            )
            return False
        except Exception as exc:
            self._last_error = f"Gagal membuat lock bot Telegram: {exc}"
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

    def _telegram_post(self, token: str, method: str, payload: Dict[str, Any], timeout: int = 35) -> Dict[str, Any]:
        url = TELEGRAM_API.format(token=token, method=method)
        resp = requests.post(url, json=payload, timeout=timeout)
        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"Telegram response bukan JSON: {resp.text[:1000]}")

        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error {method}: {data}")
        return data

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
        fast_cheap_models_runtime = _as_string_list(config.get("fast_cheap_models"))
        thinking_capable_models_runtime = _as_string_list(config.get("thinking_capable_models"))
        forced_model_mode = str(config.get("telegram_model_mode") or "auto").strip().lower()
        if forced_model_mode not in {"auto", "cheap", "expensive"}:
            forced_model_mode = "auto"
        self._runtime_primary_model = model
        self._forced_model_mode = forced_model_mode

        if not token:
            self._last_error = "TELEGRAM_BOT_TOKEN belum diisi."
            self._running = False
            self._release_file_lock()
            return

        memory = MemoryStore(memory_file)
        offset = None

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
                                "Halo, saya adioranye. Kirim pertanyaan apa saja, nanti saya bantu jawab.",
                                parse_mode=telegram_parse_mode,
                            )
                            continue

                        if text_lower in {"/help", "help"}:
                            self._send_message(
                                token,
                                chat_id,
                                "Perintah:\n"
                                "/start - mulai bot\n"
                                "/help - bantuan\n"
                                "/speed 4321 - update model aktif dan pilih yang tercepat\n"
                                "/ubah mahal - pakai model medium/mahal\n"
                                "/ubah murah - kembali pakai model murah/cepat\n\n"
                                "Langsung kirim pertanyaan untuk dijawab AI.",
                                parse_mode=telegram_parse_mode,
                            )
                            continue

                        if is_speed_update_command(text, expected_code=speed_update_code):
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
                                )
                                model = speed_result.get("primary_model") or model
                                fallback_models = speed_result.get("fallback_models") or []
                                expensive_fallback_models = speed_result.get("expensive_fallback_models") or []
                                fast_cheap_models_runtime = speed_result.get("fast_cheap_models") or []
                                thinking_capable_models_runtime = speed_result.get("thinking_capable_models") or []
                                allow_expensive_fallback = bool(expensive_fallback_models) or bool(config.get("allow_expensive_fallback", True))
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
                                self._runtime_primary_model = model
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

                        switch_mode = parse_model_switch_command(text)
                        if switch_mode:
                            forced_model_mode = switch_mode
                            config["telegram_model_mode"] = switch_mode
                            self._forced_model_mode = switch_mode

                            if switch_mode == "expensive":
                                allow_expensive_fallback = True
                                thinking_model_router = False
                                fast_normal_model_router = False
                                # Keep the current cheap primary model intact, but subsequent answers
                                # will request the capable model path first.
                            elif switch_mode == "cheap":
                                allow_expensive_fallback = False
                                thinking_model_router = False
                                fast_normal_model_router = True

                            cheap_pool = fast_cheap_models_runtime or _as_string_list(config.get("fast_cheap_models")) or _as_string_list(config.get("fallback_models"))
                            capable_pool = thinking_capable_models_runtime or _as_string_list(config.get("thinking_capable_models")) or _as_string_list(config.get("expensive_fallback_models"))
                            self._send_message(
                                token,
                                chat_id,
                                build_model_switch_summary(switch_mode, model, cheap_pool, capable_pool),
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

                        local_reply = handle_local_memory_command(text, memory) if allow_memory_commands else ""
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

                            answer, meta = generate_answer(
                                api_url=api_url,
                                api_key=api_key,
                                model=request_model,
                                system_prompt=persona,
                                user_text=text,
                                memory_text=memory_text,
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
                            )

                            if isinstance(meta, dict):
                                meta["telegram_thinking_mode"] = thinking_mode
                                meta["telegram_fast_normal_mode"] = fast_normal_mode
                                meta["telegram_forced_model_mode"] = manual_mode
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
                            self._last_error = str(exc)
                            self._send_message(
                                token,
                                chat_id,
                                "Maaf, bot belum bisa menjawab.\n\nDetail ringkas:\n" + str(exc)[:1200],
                                parse_mode=telegram_parse_mode,
                            )

                    if not updates:
                        time.sleep(0.5)

                except Exception as exc:
                    self._last_error = str(exc)
                    time.sleep(4)
        finally:
            self._running = False
            self._release_file_lock()


_service = TelegramBotService()


def get_telegram_service() -> TelegramBotService:
    return _service