import hmac
import html
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Tuple

import requests
import streamlit as st

from ai_core import (
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


def format_token_status(label: str, value: str) -> None:
    if value:
        st.success(f"{label} terdeteksi")
    else:
        st.error(f"{label} belum diisi")


def _pdf_escape(text: str) -> str:
    """Escape teks untuk content stream PDF sederhana."""
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


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


def make_answer_pdf_bytes(answer_text: str, title: str = "Jawaban Adioranye AI", meta_text: str = "") -> bytes:
    """Buat PDF teks sederhana agar hasil jawaban web bisa diunduh.

    Sengaja tidak memakai reportlab/fpdf supaya tidak menambah dependency di Streamlit Cloud.
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
        stream_lines.append(f"/F1 11 Tf 0 -24 Td ({_pdf_escape(page_lines[0] if page_lines else '')}) Tj")
        for line in page_lines[1:]:
            if line == "":
                stream_lines.append(f"0 -{line_height} Td ( ) Tj")
            else:
                stream_lines.append(f"0 -{line_height} Td ({_pdf_escape(line)}) Tj")
        footer = f"Halaman {idx} dari {len(pages)}"
        stream_lines.extend([
            "ET",
            "BT",
            f"/F1 9 Tf {left} 30 Td ({_pdf_escape(footer)}) Tj",
            "ET",
        ])
        stream = "\n".join(stream_lines).encode("latin-1", "replace")
        content_id = add_obj(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream")
        page_id = add_obj(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        )
        page_ids.append(page_id)

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1")

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
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


def answer_pdf_download_button(answer_text: str, key: str, model_name: str = "") -> None:
    """Render tombol download PDF untuk sebuah jawaban assistant."""
    if not str(answer_text or "").strip():
        return
    meta_text = f"Model: {model_name}" if model_name else ""
    filename = f"jawaban-adioranye-{datetime.now(WIB_TZ).strftime('%Y%m%d-%H%M%S')}.pdf"
    st.download_button(
        "⬇️ Download jawaban PDF",
        data=make_answer_pdf_bytes(answer_text, meta_text=meta_text),
        file_name=filename,
        mime="application/pdf",
        key=key,
        use_container_width=True,
    )


def init_state() -> None:
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []
    if "admin_authenticated" not in st.session_state:
        st.session_state.admin_authenticated = False
    if "active_model" not in st.session_state:
        st.session_state.active_model = str(get_secret("SLASHAI_MODEL", "slashai/gpt-5-nano"))
    if "active_persona" not in st.session_state:
        st.session_state.active_persona = str(get_secret("ASSISTANT_PERSONA", DEFAULT_PERSONA))
    if "active_temperature" not in st.session_state:
        st.session_state.active_temperature = float(get_secret("TEMPERATURE", 0.3) or 0.3)
    if "active_max_tokens" not in st.session_state:
        st.session_state.active_max_tokens = int(get_secret("MAX_COMPLETION_TOKENS", 2600) or 2600)
    if "show_debug" not in st.session_state:
        st.session_state.show_debug = False
    if "active_smart_router" not in st.session_state:
        st.session_state.active_smart_router = parse_bool(get_secret("SMART_MODEL_ROUTER", True), default=True)
    if "active_return_to_primary" not in st.session_state:
        st.session_state.active_return_to_primary = parse_bool(get_secret("RETURN_TO_PRIMARY_MODEL", True), default=True)
    if "active_max_smart_models" not in st.session_state:
        st.session_state.active_max_smart_models = int(get_secret("MAX_SMART_MODELS", 2) or 2)
    if "allow_expensive_fallback" not in st.session_state:
        st.session_state.allow_expensive_fallback = parse_bool(get_secret("ALLOW_EXPENSIVE_FALLBACK", True), default=True)
    if "max_expensive_models" not in st.session_state:
        st.session_state.max_expensive_models = int(get_secret("MAX_EXPENSIVE_MODELS", 1) or 1)
    if "last_answer_meta" not in st.session_state:
        st.session_state.last_answer_meta = {}
    if "pending_prompt" not in st.session_state:
        st.session_state.pending_prompt = ""
    if "active_default_memory" not in st.session_state:
        st.session_state.active_default_memory = str(get_secret("DEFAULT_MEMORY_CONTEXT", DEFAULT_MEMORY_CONTEXT))
    if "model_health_cache" not in st.session_state:
        st.session_state.model_health_cache = {}
    if "model_health_checked_at" not in st.session_state:
        st.session_state.model_health_checked_at = 0.0
    if "active_cheap_fallback_models" not in st.session_state:
        st.session_state.active_cheap_fallback_models = DEFAULT_CHEAP_FALLBACK_MODELS.copy()
    if "active_expensive_fallback_models" not in st.session_state:
        st.session_state.active_expensive_fallback_models = DEFAULT_EXPENSIVE_FALLBACK_MODELS.copy()
    if "last_model_health_error" not in st.session_state:
        st.session_state.last_model_health_error = ""
    if "active_rotate_cheap_primary" not in st.session_state:
        st.session_state.active_rotate_cheap_primary = parse_bool(get_secret("ROTATE_CHEAP_PRIMARY", True), default=True)
    if "cheap_model_rotation_index" not in st.session_state:
        st.session_state.cheap_model_rotation_index = 0
    if "last_rotated_primary_model" not in st.session_state:
        st.session_state.last_rotated_primary_model = ""
    if "active_use_streamlit_cache_memory" not in st.session_state:
        st.session_state.active_use_streamlit_cache_memory = parse_bool(get_secret("USE_STREAMLIT_CACHE_MEMORY", True), default=True)
    if "active_thinking_model_router" not in st.session_state:
        st.session_state.active_thinking_model_router = parse_bool(get_secret("THINKING_MODEL_ROUTER", True), default=True)
    if "active_thinking_min_chars" not in st.session_state:
        st.session_state.active_thinking_min_chars = int(get_secret("THINKING_MIN_CHARS", 180) or 180)
    if "active_fast_normal_model_router" not in st.session_state:
        st.session_state.active_fast_normal_model_router = parse_bool(get_secret("FAST_NORMAL_MODEL_ROUTER", True), default=True)


# =========================
# Defaults
# =========================

DEFAULT_PERSONA = (
    "Nama kamu adalah adioranye. "
    "Kamu dibuat oleh Galuh Adi Insani. "
    "Kamu adalah asisten pribadi yang sangat cerdas, ramah, teliti, detail, cepat memahami konteks, dan mampu membantu berbagai kebutuhan pengguna secara praktis. "
    "Jawab dalam bahasa Indonesia yang natural, jelas, sopan, dan mudah dipahami. "
    "Untuk pertanyaan sederhana, jawab singkat dan langsung. Untuk pertanyaan teknis, akademik, bisnis, coding, atau analisis, jawab lebih detail, bertahap, dan berikan contoh bila membantu. "
    "Jangan mengarang fakta. Jika informasi tidak pasti, jelaskan keterbatasannya dan berikan saran langkah aman. "
    "Jika permintaan berbahaya atau melanggar aturan, tolak dengan singkat dan arahkan ke alternatif yang aman."
)

DEFAULT_MEMORY_CONTEXT = """
Memory default Adioranye:
- Adioranye dapat membantu menjawab pertanyaan umum, akademik, teknis, bisnis, kreatif, penulisan, coding, analisis data, strategi konten, dan kebutuhan praktis sehari-hari.
- Prioritaskan jawaban yang akurat, jelas, ramah, detail secukupnya, dan langsung bisa dipakai.
- Untuk pertanyaan akademik, bantu dengan struktur rapi, bahasa natural, contoh, dan penjelasan yang mudah dipahami.
- Untuk pertanyaan coding atau aplikasi, berikan langkah perbaikan yang praktis, kode yang siap ditempel, dan jelaskan letak perubahan penting.
- Untuk pertanyaan bisnis, pemasaran, desain, konten, atau promosi, berikan ide yang ringkas, menarik, dan mudah dieksekusi.
- Untuk pertanyaan yang membutuhkan data terbaru, hukum, medis, keuangan, atau keputusan berisiko, jangan mengarang. Jelaskan bahwa data perlu diverifikasi dan berikan arahan aman.
- Jika pengguna meminta format tertentu, ikuti format tersebut. Jika tidak, gunakan struktur yang paling mudah dibaca.
- Jika permintaan kurang jelas, tetap berikan jawaban terbaik berdasarkan konteks yang ada dan sebutkan asumsi yang digunakan.
""".strip()

CHEAP_MODEL_OPTIONS = DEFAULT_CHEAP_FALLBACK_MODELS.copy()
EXPENSIVE_MODEL_OPTIONS = DEFAULT_EXPENSIVE_FALLBACK_MODELS.copy()
MODEL_OPTIONS = list(dict.fromkeys(CHEAP_MODEL_OPTIONS + EXPENSIVE_MODEL_OPTIONS + [
    "slashai/gemini-3.1-pro",
    "slashai/qwen3-coder-next",
    "slashai/deepseek-v4-flash",
    "slashai/deepseek-v4-pro",
    "slashai/gpt-5.4",
    "slashai/gpt-5.5",
    "slashai/claude-opus-4.5",
]))

# Secrets
api_key = str(get_secret("SLASHAI_API_KEY", ""))
api_url = str(get_secret("SLASHAI_API_URL", "https://api.slashai.my.id/v1/chat/completions"))
default_model = str(get_secret("SLASHAI_MODEL", "slashai/gpt-5-nano"))
telegram_token = str(get_secret("TELEGRAM_BOT_TOKEN", ""))
memory_file = str(get_secret("MEMORY_FILE", "assistant_memory.json"))
persona_from_secret = str(get_secret("ASSISTANT_PERSONA", DEFAULT_PERSONA))
default_memory_context_from_secret = str(get_secret("DEFAULT_MEMORY_CONTEXT", DEFAULT_MEMORY_CONTEXT))
auto_start = parse_bool(get_secret("TELEGRAM_AUTO_START", False), default=False)
drop_pending_updates = parse_bool(get_secret("TELEGRAM_DROP_PENDING_UPDATES", True), default=True)
send_processing_message = parse_bool(get_secret("TELEGRAM_SEND_PROCESSING_MESSAGE", False), default=False)
telegram_parse_mode = str(get_secret("TELEGRAM_PARSE_MODE", "") or "")
telegram_lock_file = str(get_secret("TELEGRAM_LOCK_FILE", ".telegram_bot_worker.lock"))
telegram_show_model_info = parse_bool(get_secret("TELEGRAM_SHOW_MODEL_INFO", True), default=True)
telegram_speed_update_code = str(get_secret("TELEGRAM_SPEED_UPDATE_CODE", "4321") or "4321").strip()
admin_username = str(get_secret("ADMIN_USERNAME", "admin"))
admin_password = str(get_secret("ADMIN_PASSWORD", "Admin"))
smart_model_router_default = parse_bool(get_secret("SMART_MODEL_ROUTER", True), default=True)
return_to_primary_default = parse_bool(get_secret("RETURN_TO_PRIMARY_MODEL", True), default=True)
max_smart_models_default = int(get_secret("MAX_SMART_MODELS", 2) or 2)
model_health_check_interval = int(get_secret("MODEL_HEALTH_CHECK_INTERVAL_SECONDS", 90000) or 90000)
model_health_timeout = int(get_secret("MODEL_HEALTH_TIMEOUT_SECONDS", 12) or 12)
# Health check model hanya boleh berjalan pada jendela tengah malam WIB.
# Default: 00:00-00:59 WIB. Di luar jam ini sistem memakai cache/daftar fallback terakhir.
model_health_midnight_only = parse_bool(get_secret("MODEL_HEALTH_MIDNIGHT_ONLY", True), default=True)
model_health_hour_wib = int(get_secret("MODEL_HEALTH_HOUR_WIB", 0) or 0)
model_health_window_minutes = int(get_secret("MODEL_HEALTH_WINDOW_MINUTES", 60) or 60)
rotate_cheap_primary_default = parse_bool(get_secret("ROTATE_CHEAP_PRIMARY", True), default=True)
use_streamlit_cache_memory_default = parse_bool(get_secret("USE_STREAMLIT_CACHE_MEMORY", True), default=True)
streamlit_cache_memory_limit = int(get_secret("STREAMLIT_CACHE_MEMORY_LIMIT", 200) or 200)
thinking_model_router_default = parse_bool(get_secret("THINKING_MODEL_ROUTER", True), default=True)
thinking_min_chars_default = int(get_secret("THINKING_MIN_CHARS", 180) or 180)
thinking_capable_model_override = str(get_secret("THINKING_CAPABLE_MODEL", "") or "").strip()
fast_normal_model_router_default = parse_bool(get_secret("FAST_NORMAL_MODEL_ROUTER", True), default=True)

init_state()
memory = MemoryStore(memory_file)
service = get_telegram_service()


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


def add_streamlit_cache_memory(text: str, source: str = "streamlit-cache-admin") -> bool:
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
            "created_at": datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d %H:%M:%S WIB"),
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
    return "\n".join(f"- {str(item.get('text', '')).strip()}" for item in selected_items if str(item.get("text", "")).strip())


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
    items[:] = [item for item in items if keyword_clean not in str(item.get("text", "")).lower()]
    return before - len(items)


def reset_streamlit_cache_memory() -> int:
    items = _streamlit_cache_memory_items()
    count = len(items)
    items.clear()
    return count


def build_memory_text(limit: int = 12) -> str:
    """Gabungkan memory default, cache Streamlit, dan memory lokal admin."""
    default_context = str(st.session_state.get("active_default_memory") or default_memory_context_from_secret or DEFAULT_MEMORY_CONTEXT).strip()
    cache_memory = str(streamlit_cache_memory_prompt_text(limit=limit) or "").strip()
    local_memory = str(memory.as_prompt_text(limit=limit) or "").strip()

    sections = []
    if default_context:
        sections.append("MEMORY DEFAULT AKTIF:\n" + default_context)
    if cache_memory:
        sections.append("MEMORY CACHE STREAMLIT AKTIF:\n" + cache_memory)
    if local_memory:
        sections.append("MEMORY TAMBAHAN ADMIN FILE LOKAL:\n" + local_memory)
    return "\n\n".join(sections)


def persona_with_default_memory(persona: str) -> str:
    """Dipakai untuk Bot Telegram agar memory default/cache tetap masuk ke instruksi bot."""
    default_context = str(st.session_state.get("active_default_memory") or default_memory_context_from_secret or DEFAULT_MEMORY_CONTEXT).strip()
    cache_context = str(streamlit_cache_memory_prompt_text(limit=20) or "").strip()

    context_sections = []
    if default_context:
        context_sections.append("Konteks default yang selalu dipakai:\n" + default_context)
    if cache_context:
        context_sections.append("Memory cache Streamlit aktif:\n" + cache_context)

    if not context_sections:
        return persona
    return f"{persona}\n\n" + "\n\n".join(context_sections)


# =========================
# Model health check & active fallback priority
# =========================
def unique_models(models: List[str]) -> List[str]:
    """Hilangkan model kosong/duplikat sambil mempertahankan urutan."""
    return list(dict.fromkeys(str(model).strip() for model in models if str(model).strip()))


WIB_TZ = ZoneInfo("Asia/Jakarta")


def _wib_now_text() -> str:
    return datetime.now(WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")


def _health_window_label_wib() -> str:
    """Label jendela health check model dalam WIB."""
    hour = max(0, min(23, int(model_health_hour_wib or 0)))
    window = max(1, min(60, int(model_health_window_minutes or 60)))
    end_minute = window - 1
    return f"{hour:02d}:00-{hour:02d}:{end_minute:02d} WIB"


def is_model_health_check_allowed_now() -> bool:
    """Batasi test health check model agar hanya berjalan pada tengah malam WIB."""
    if not bool(model_health_midnight_only):
        return True

    now_wib = datetime.now(WIB_TZ)
    hour = max(0, min(23, int(model_health_hour_wib or 0)))
    window = max(1, min(60, int(model_health_window_minutes or 60)))
    return now_wib.hour == hour and now_wib.minute < window


def _timestamp_to_wib_text(timestamp_value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(timestamp_value), WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")
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
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
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


def prioritize_active_models(models: List[str], health_cache: Dict[str, Dict[str, Any]]) -> List[str]:
    """Urutkan fallback aktif: tier hemat dulu, harga output rendah, lalu latency rendah."""
    active_models = [model for model in unique_models(models) if health_cache.get(model, {}).get("active")]

    def sort_key(model: str) -> Tuple[int, int, float, str]:
        price = model_price(model)
        latency = float(health_cache.get(model, {}).get("latency_ms") or 999999)
        return (_tier_rank(model), int(price.get("output", 999999999)), latency, model)

    return sorted(active_models, key=sort_key)


def prioritize_fastest_active_models(models: List[str], health_cache: Dict[str, Dict[str, Any]]) -> List[str]:
    """Urutkan model aktif berdasarkan latency terendah untuk pertanyaan ringan/non-thinking."""
    active_models = [model for model in unique_models(models) if health_cache.get(model, {}).get("active")]

    def sort_key(model: str) -> Tuple[float, int, str]:
        price = model_price(model)
        latency = float(health_cache.get(model, {}).get("latency_ms") or 999999)
        return (latency, int(price.get("output", 999999999)), model)

    return sorted(active_models, key=sort_key)


def check_single_model_health(model: str, timeout: int = 12) -> Dict[str, Any]:
    """Cek apakah model bisa menjawab request kecil. Aman untuk OpenAI-compatible /chat/completions."""
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


def refresh_model_health_if_needed(force: bool = False) -> Dict[str, Dict[str, Any]]:
    """Cek model hanya pada jendela tengah malam WIB.

    Streamlit tidak punya cron murni, jadi pengecekan tetap dipicu saat app aktif/rerun/chat,
    tetapi request ping ke model hanya dijalankan jika waktu WIB sedang berada pada jendela
    MODEL_HEALTH_HOUR_WIB + MODEL_HEALTH_WINDOW_MINUTES.
    """
    if not api_key:
        st.session_state.last_model_health_error = "SLASHAI_API_KEY belum diisi."
        return st.session_state.model_health_cache

    now = time.time()
    last_checked = float(st.session_state.get("model_health_checked_at") or 0)
    cache = st.session_state.get("model_health_cache") or {}
    interval = max(60, int(model_health_check_interval or 900))

    if not is_model_health_check_allowed_now():
        st.session_state.last_model_health_error = (
            f"Health check model hanya dijalankan pukul {_health_window_label_wib()}. "
            f"Di luar jam itu sistem memakai cache/daftar model aktif terakhir."
        )
        return cache

    if not force and cache and now - last_checked < interval:
        return cache

    models_to_check = unique_models(
        [st.session_state.get("active_model") or default_model, default_model]
        + DEFAULT_CHEAP_FALLBACK_MODELS
        + DEFAULT_EXPENSIVE_FALLBACK_MODELS
        + MODEL_OPTIONS
    )

    fresh_cache: Dict[str, Dict[str, Any]] = {}
    for model_name in models_to_check:
        fresh_cache[model_name] = check_single_model_health(model_name, timeout=int(model_health_timeout or 12))

    active_cheap = prioritize_active_models(DEFAULT_CHEAP_FALLBACK_MODELS, fresh_cache)
    active_expensive = prioritize_active_models(DEFAULT_EXPENSIVE_FALLBACK_MODELS, fresh_cache)

    st.session_state.model_health_cache = fresh_cache
    st.session_state.model_health_checked_at = now

    # Jika API sedang down total, jangan kosongkan fallback terakhir yang masih tersimpan.
    if active_cheap:
        st.session_state.active_cheap_fallback_models = active_cheap
    if active_expensive:
        st.session_state.active_expensive_fallback_models = active_expensive

    active_total = sum(1 for item in fresh_cache.values() if item.get("active"))
    st.session_state.last_model_health_error = "" if active_total else "Tidak ada model yang lolos health check terakhir."
    return fresh_cache


def get_prioritized_fallback_models() -> Tuple[List[str], List[str]]:
    """Ambil fallback yang sudah dicek aktif dan diurutkan berdasarkan prioritas biaya/latency."""
    refresh_model_health_if_needed(force=False)
    cheap = st.session_state.get("active_cheap_fallback_models") or DEFAULT_CHEAP_FALLBACK_MODELS.copy()
    expensive = st.session_state.get("active_expensive_fallback_models") or DEFAULT_EXPENSIVE_FALLBACK_MODELS.copy()
    return unique_models(cheap), unique_models(expensive)


def get_rotating_cheap_primary(active_cheap_models: List[str], advance: bool = False) -> str:
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


def is_thinking_question(user_text: str) -> bool:
    """Deteksi pertanyaan yang perlu model lebih capable/reasoning.

    Prinsipnya konservatif: pertanyaan sederhana tetap lewat rotasi model murah,
    sedangkan pertanyaan analitis, teknis, multi-langkah, debugging, riset,
    atau konteks panjang langsung diarahkan ke model capable.
    """
    if not bool(st.session_state.get("active_thinking_model_router", True)):
        return False

    text = str(user_text or "").strip()
    if not text:
        return False

    lowered = text.lower()
    word_count = len(text.split())
    min_chars = int(st.session_state.get("active_thinking_min_chars", thinking_min_chars_default) or 180)

    strong_keywords = [
        "thinking", "reasoning", "berpikir", "nalar", "logika", "analisis", "analisa",
        "evaluasi", "bandingkan", "pertimbangkan", "strategi", "arsitektur", "algoritma",
        "debug", "error", "traceback", "exception", "bug", "refactor", "optimasi",
        "optimize", "perbaiki kode", "cek kode", "skripsi", "tesis", "jurnal", "riset",
        "metodologi", "smartpls", "statistik", "regresi", "sentimen", "indobert",
        "buatkan alur", "bagan alur", "step by step", "langkah-langkah", "kenapa", "mengapa",
        "apa penyebab", "solusi terbaik", "rekomendasi terbaik", "prioritaskan",
        "model yang capable", "jawaban mendalam", "berpikir dalam",
    ]
    code_or_log_markers = [
        "```", "def ", "class ", "import ", "from ", "return ", "npm ", "vercel",
        "status code", "response:", "build failed", "failed", "unauthorized", "creditsdepleted",
        "<html", "<script", "streamlit", "session_state", "generate_answer",
    ]

    if _contains_any(lowered, strong_keywords):
        return True
    if _contains_any(lowered, code_or_log_markers):
        return True
    if len(text) >= min_chars and word_count >= 24:
        return True
    if text.count("?") >= 2 and word_count >= 18:
        return True
    if any(token in lowered for token in ["1.", "2.", "3.", "- "]) and word_count >= 25:
        return True

    return False


def get_capable_primary_model(active_expensive_models: List[str], health_cache: Dict[str, Dict[str, Any]]) -> str:
    """Pilih model capable aktif untuk pertanyaan thinking.

    Urutan:
    1) THINKING_CAPABLE_MODEL dari Secrets jika diisi dan aktif.
    2) Model menengah/mahal aktif hasil health check.
    3) Model non-cheap aktif dari MODEL_OPTIONS sebagai cadangan.
    """
    override = str(thinking_capable_model_override or "").strip()
    if override and health_cache.get(override, {}).get("active"):
        return override

    if active_expensive_models:
        return active_expensive_models[0]

    candidates = []
    for model_name in MODEL_OPTIONS:
        if _tier_rank(model_name) > 0 and health_cache.get(model_name, {}).get("active"):
            candidates.append(model_name)
    prioritized = prioritize_active_models(candidates, health_cache)
    return prioritized[0] if prioritized else ""

def build_model_routing_plan(advance_rotation: bool = False, user_text: str = "") -> Dict[str, Any]:
    """
    Routing default:
    1) Pertanyaan thinking/kompleks langsung memakai model capable aktif.
    2) Pertanyaan ringan/non-thinking langsung memakai model murah aktif dengan latency tercepat.
    3) Jika fast-normal dimatikan, pertanyaan biasa kembali ke rotasi model murah.
    4) Jika semua model murah gagal/kurang cukup, naik otomatis ke model menengah/mahal aktif.
    5) Setelah request selesai, state aplikasi tetap diarahkan kembali ke model murah aktif.
    """
    active_cheap_models, active_expensive_models = get_prioritized_fallback_models()
    selected_model = str(st.session_state.get("active_model") or default_model).strip()
    health_cache = st.session_state.get("model_health_cache") or {}

    selected_is_cheap = _tier_rank(selected_model) == 0
    selected_is_active = bool(health_cache.get(selected_model, {}).get("active"))
    rotate_enabled = bool(st.session_state.get("active_rotate_cheap_primary", True))
    fast_normal_enabled = bool(st.session_state.get("active_fast_normal_model_router", True))
    fastest_cheap_models = prioritize_fastest_active_models(active_cheap_models, health_cache)
    thinking_mode = is_thinking_question(user_text)
    capable_primary = get_capable_primary_model(active_expensive_models, health_cache) if thinking_mode else ""

    direct_to_expensive = False
    thinking_direct_to_capable = False
    normal_fast_mode = False
    rotated_primary = ""

    if thinking_mode and capable_primary:
        # Pertanyaan kompleks langsung dijalankan oleh model capable, bukan model murah.
        primary_model = capable_primary
        direct_to_expensive = True
        thinking_direct_to_capable = True
    elif active_cheap_models:
        if fast_normal_enabled and fastest_cheap_models:
            # Pertanyaan ringan/non-thinking harus secepat mungkin: gunakan model murah aktif tercepat.
            primary_model = fastest_cheap_models[0]
            normal_fast_mode = True
        elif rotate_enabled:
            # Jika fast-normal dimatikan, setiap request memakai model murah aktif berikutnya.
            # Render UI/admin hanya mengintip tanpa menggeser indeks.
            primary_model = get_rotating_cheap_primary(active_cheap_models, advance=advance_rotation)
            rotated_primary = primary_model
        elif selected_is_cheap and selected_model in active_cheap_models and selected_is_active:
            primary_model = selected_model
        elif default_model in active_cheap_models:
            primary_model = default_model
        else:
            primary_model = active_cheap_models[0]
    elif active_expensive_models:
        # Tidak ada model murah yang hidup: langsung pakai model menengah/mahal aktif.
        primary_model = active_expensive_models[0]
        direct_to_expensive = True
    else:
        # Fallback paling akhir: jangan kosongkan model agar error tetap informatif dari generate_answer.
        primary_model = selected_model or default_model

    if thinking_direct_to_capable:
        # Saat thinking mode, jangan turun ke model murah sebagai fallback utama;
        # gunakan model capable lain jika tersedia.
        cheap_fallback_models = []
    else:
        cheap_pool = fastest_cheap_models if normal_fast_mode and fastest_cheap_models else active_cheap_models
        cheap_fallback_models = [model for model in cheap_pool if model != primary_model]

    expensive_fallback_models = [model for model in active_expensive_models if model != primary_model]

    # Default: expensive fallback aktif. Admin masih bisa mematikan lewat toggle,
    # tetapi jika tidak ada model murah aktif sama sekali atau pertanyaan thinking,
    # expensive tetap dipakai agar jawaban memakai model yang lebih capable.
    allow_expensive = bool(active_expensive_models) and (
        bool(st.session_state.get("allow_expensive_fallback", True)) or direct_to_expensive or thinking_direct_to_capable
    )

    max_expensive = int(st.session_state.get("max_expensive_models", 1) or 1)
    if expensive_fallback_models:
        max_expensive = max(1, min(max_expensive, len(expensive_fallback_models)))
    else:
        max_expensive = 1

    # Karena fallback murah biayanya rendah, izinkan router mengecek semua model murah aktif
    # sebelum naik ke model menengah/mahal.
    max_smart_models = max(
        int(st.session_state.get("active_max_smart_models", 2) or 2),
        len(cheap_fallback_models),
        1,
    )

    return_to_primary = bool(st.session_state.get("active_return_to_primary", True)) and not direct_to_expensive

    next_cheap_model = ""
    if active_cheap_models:
        next_cheap_model = get_rotating_cheap_primary(active_cheap_models, advance=False)

    fastest_cheap_primary = fastest_cheap_models[0] if fastest_cheap_models else ""

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
        "rotate_cheap_primary": rotate_enabled,
        "fast_normal_model_router": fast_normal_enabled,
        "rotated_primary_model": rotated_primary,
        "next_cheap_primary_model": next_cheap_model,
        "cheap_rotation_index": int(st.session_state.get("cheap_model_rotation_index", 0) or 0),
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
        st.info("Belum ada hasil cek model. Klik tombol cek manual atau kirim pertanyaan agar sistem mengecek otomatis.")
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

    rows.sort(key=lambda row: (0 if row["status"].startswith("🟢") else 1, row["tier"], row["latency_ms"] or 999999, row["model"]))
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


def render_answer_model_caption(meta: Dict[str, Any] | None, fallback: str = "", admin_detail: bool = False) -> None:
    # Tampilkan model yang menjawab di bawah respons assistant.
    model_name = get_answer_model_name(meta, fallback=fallback)
    if not model_name:
        return

    data = meta or {}
    caption_text = f"Model aktif: {model_name}"

    # Detail jalur routing hanya untuk admin agar tampilan publik tetap bersih.
    if admin_detail:
        consulted = data.get("consulted_models") or []
        if consulted:
            caption_text += " • konsultasi: " + ", ".join(str(item) for item in consulted[:4])
        if data.get("expensive_fallback_used"):
            caption_text += " • model menengah/mahal dipakai"

    st.caption(caption_text)


def build_public_model_status_html(route: Dict[str, Any], last_meta: Dict[str, Any] | None = None) -> str:
    # Buat panel kecil agar model aktif/route berikutnya terlihat di halaman publik.
    route = route or {}
    last_model = get_answer_model_name(last_meta, fallback="")
    next_model = str(route.get("primary_model") or st.session_state.get("active_model") or default_model or "").strip()
    fast_model = str(route.get("fastest_cheap_primary_model") or "").strip()
    capable_model = str(route.get("capable_primary_model") or "").strip()
    cheap_count = len(route.get("active_cheap_models") or [])
    expensive_count = len(route.get("active_expensive_models") or [])

    checked_at = "belum pernah"
    if st.session_state.get("model_health_checked_at"):
        checked_at = _timestamp_to_wib_text(st.session_state.model_health_checked_at)

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
        gap: 7px;
        padding: 6px 11px;
        border-radius: 999px;
        border: 1px solid var(--mac-border);
        background: var(--mac-panel-soft);
        color: var(--mac-muted);
        font-size: 0.82rem;
        font-weight: 700;
    }

    .mac-window-actions::before {
        content: "";
        width: 7px;
        height: 7px;
        border-radius: 999px;
        background: #28c840;
        box-shadow: 0 0 0 4px rgba(40, 200, 64, 0.12);
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
            padding: 5px 8px;
            font-size: 0.72rem;
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

    code, pre,
    div[data-testid="stMarkdownContainer"] code {
        border-radius: 14px !important;
        white-space: pre-wrap !important;
        word-break: break-word !important;
        border: 1px solid var(--mac-border) !important;
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
        "smart_model_router": bool(st.session_state.active_smart_router),
        "return_to_primary": bool(st.session_state.active_return_to_primary),
        "max_smart_models": int(st.session_state.active_max_smart_models),
        "allow_expensive_fallback": bool(st.session_state.allow_expensive_fallback),
        "max_expensive_models": int(st.session_state.max_expensive_models),
        "default_memory_context": str(st.session_state.active_default_memory),
        "use_streamlit_cache_memory": bool(st.session_state.active_use_streamlit_cache_memory),
        "thinking_model_router": bool(st.session_state.active_thinking_model_router),
        "fast_normal_model_router": bool(st.session_state.active_fast_normal_model_router),
    }


def start_telegram_if_needed() -> None:
    cfg = get_runtime_config()
    if auto_start and telegram_token and api_key and not service.status()["running"]:
        route = build_model_routing_plan(advance_rotation=True)
        service.start(
            {
                "telegram_token": telegram_token,
                "slashai_api_key": api_key,
                "slashai_api_url": api_url,
                "slashai_model": route["primary_model"],
                "persona": persona_with_default_memory(cfg["persona"]),
                "memory_file": memory_file,
                "fallback_models": route["cheap_fallback_models"],
                "expensive_fallback_models": route["expensive_fallback_models"],
                "allow_expensive_fallback": route["allow_expensive_fallback"],
                "max_expensive_models": route["max_expensive_models"],
                "show_model_info": telegram_show_model_info,
                "temperature": cfg["temperature"],
                "max_completion_tokens": cfg["max_completion_tokens"],
                "timeout": 60,
                "drop_pending_updates": drop_pending_updates,
                "send_processing_message": send_processing_message,
                "telegram_parse_mode": telegram_parse_mode,
                "lock_file": telegram_lock_file,
                "allow_memory_commands": False,
                "smart_model_router": cfg["smart_model_router"],
                "return_to_primary": route["return_to_primary"],
                "max_smart_models": route["max_smart_models"],
                "thinking_model_router": bool(st.session_state.get("active_thinking_model_router", True)),
                "thinking_min_chars": int(st.session_state.get("active_thinking_min_chars", thinking_min_chars_default) or 180),
                "thinking_capable_model": thinking_capable_model_override,
                "fast_normal_model_router": bool(st.session_state.get("active_fast_normal_model_router", True)),
                "fastest_cheap_model": route.get("fastest_cheap_primary_model", ""),
                "fast_cheap_models": route.get("fast_cheap_models", []),
                "all_cheap_models": CHEAP_MODEL_OPTIONS,
                "all_expensive_models": EXPENSIVE_MODEL_OPTIONS,
                "all_model_candidates": MODEL_OPTIONS,
                "active_cheap_models": route.get("active_cheap_models", []),
                "thinking_capable_models": route.get("active_expensive_models", []),
                "speed_update_code": telegram_speed_update_code,
                "model_health_timeout": int(model_health_timeout or 12),
                "model_health_midnight_only": bool(model_health_midnight_only),
                "model_health_hour_wib": int(model_health_hour_wib or 0),
                "model_health_window_minutes": int(model_health_window_minutes or 60),
            }
        )
        restore_active_model_to_cheap(route.get("primary_model"))


start_telegram_if_needed()


# =========================
# Admin settings UI
# =========================
def render_admin_login() -> None:
    st.subheader("🔐 Admin")

    if not admin_password:
        st.error("ADMIN_PASSWORD belum diisi di Streamlit Secrets.")
        return

    with st.form("admin_login_form", clear_on_submit=False):
        username_input = st.text_input("Username", value="", placeholder="admin")
        password_input = st.text_input("Password", type="password", placeholder="Password admin")
        submitted = st.form_submit_button("Masuk Admin", use_container_width=True)

    if submitted:
        username_ok = safe_compare(username_input.strip(), admin_username)
        password_ok = safe_compare(password_input, admin_password)
        if username_ok and password_ok:
            st.session_state.admin_authenticated = True
            st.success("Login admin berhasil.")
            st.rerun()
        else:
            st.error("Username atau password salah.")


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
    telegram_status = "ON" if service.status()["running"] else "OFF"
    health_cache = st.session_state.get("model_health_cache") or {}
    active_count = sum(1 for item in health_cache.values() if item.get("active"))
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
        f"Telegram: {telegram_status}\n\n"
        f"Rotasi murah: {'ON' if st.session_state.get('active_rotate_cheap_primary', True) else 'OFF'}\n\n"
        f"Thinking router: {'ON' if st.session_state.get('active_thinking_model_router', True) else 'OFF'}\n\n"
        f"Fast normal: {'ON' if st.session_state.get('active_fast_normal_model_router', True) else 'OFF'}\n\n"
        f"Model aktif terdeteksi: {active_count}\n\n"
        f"Cek model terakhir: {checked_at}"
    ).replace(",", ".")
    st.info(status_text)


def render_admin_settings() -> None:
    st.subheader("⚙️ Admin Settings")
    st.success(f"Login sebagai: {admin_username}")

    render_admin_status()

    if st.button("🚪 Logout Admin", use_container_width=True):
        st.session_state.admin_authenticated = False
        st.rerun()

    tab_ai, tab_bot, tab_memory, tab_setup = st.tabs(["AI", "Telegram", "Memory", "Setup"])

    with tab_ai:
        st.markdown("#### Model & Persona")
        filter_choice = st.radio(
            "Tampilan model",
            ["Hemat saja", "Hemat + menengah/mahal"],
            horizontal=False,
            index=0,
        )
        model_list = CHEAP_MODEL_OPTIONS if filter_choice == "Hemat saja" else MODEL_OPTIONS
        current_model = st.session_state.active_model if st.session_state.active_model in model_list else default_model
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
        st.info(f"Model utama: {st.session_state.active_model} | tier: {tier} | input Rp{price.get('input', 0):,}/1M, output Rp{price.get('output', 0):,}/1M".replace(',', '.'))
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
            next_rotation_model = get_rotating_cheap_primary(cheap_for_sync, advance=False) if cheap_for_sync else ""
            st.caption(f"Model murah berikutnya: {next_rotation_model or 'belum ada model murah aktif'}")
            if st.button("Mulai rotasi dari model yang dipilih", use_container_width=True):
                sync_rotation_index_to_selected_model(cheap_for_sync)
                st.success("Titik awal rotasi disesuaikan dengan model murah yang dipilih.")

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
            st.caption(f"Model cepat untuk pertanyaan ringan: {route_preview.get('fastest_cheap_primary_model') or 'belum ada model murah aktif'}")
        st.session_state.active_persona = st.text_area(
            "System persona",
            value=st.session_state.active_persona,
            height=170,
        )
        st.session_state.show_debug = st.toggle("Tampilkan debug respons di chat", value=st.session_state.show_debug)
        st.markdown("#### Router Cepat & Akurat")
        st.caption("Algoritma baru: pertanyaan thinking langsung memakai model capable aktif. Pertanyaan ringan/non-thinking memakai model murah aktif tercepat. Jika fast-normal dimatikan, sistem kembali memakai rotasi model murah. Jika jawaban kosong/kurang kuat/gagal, sistem mencoba backup sesuai jalur, lalu kembali ke model murah aktif setelah selesai.")
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
            st.caption(f"Fast normal: {'ON' if route.get('fast_normal_model_router') else 'OFF'} | Rotasi murah: {'ON' if route.get('rotate_cheap_primary') else 'OFF'} | thinking router: {'ON' if st.session_state.get('active_thinking_model_router', True) else 'OFF'} | indeks berikutnya: {route.get('cheap_rotation_index', 0)}")
            st.caption(f"Model murah tercepat: {route.get('fastest_cheap_primary_model') or 'belum ada'}")
            st.markdown("**Model hemat aktif/prioritas backup:**")
            st.code("\n".join(model_price_label(m) for m in route["active_cheap_models"]) or "Belum ada model hemat aktif")
            st.markdown("**Model menengah/mahal aktif otomatis jika model hemat tidak cukup / pertanyaan thinking:**")
            st.code("\n".join(model_price_label(m) for m in route["active_expensive_models"]) or "Belum ada model menengah/mahal aktif")
            capable_preview = get_capable_primary_model(route["active_expensive_models"], st.session_state.get("model_health_cache") or {})
            st.caption(f"Model capable untuk thinking: {capable_preview or 'belum ada model capable aktif'}")
            if route["direct_to_expensive"]:
                st.warning("Tidak ada model hemat aktif. Request berikutnya langsung memakai model menengah/mahal aktif, lalu sistem akan kembali ke model hemat saat sudah aktif lagi.")

        st.markdown("#### Cek Berkala Model")
        health_window_open = is_model_health_check_allowed_now()
        st.caption(
            f"Health check model hanya berjalan pukul {_health_window_label_wib()}. "
            "Di luar jam itu sistem tidak melakukan ping/test model dan tetap memakai cache/daftar model aktif terakhir. "
            "Urutan default: thinking → model capable aktif; non-thinking → model hemat aktif tercepat → backup hemat aktif lain → model menengah/mahal jika semua hemat gagal/kurang cukup → kembali ke model hemat aktif."
        )
        col_health_check, col_health_info = st.columns([1, 2])
        with col_health_check:
            if st.button("🔁 Cek model sekarang", use_container_width=True, disabled=not health_window_open):
                refresh_model_health_if_needed(force=True)
                st.success("Cek model selesai.")
            if not health_window_open:
                st.caption(f"Tombol aktif hanya pukul {_health_window_label_wib()}.")
        with col_health_info:
            cheap_active, expensive_active = get_prioritized_fallback_models()
            st.info(f"Backup hemat aktif: {len(cheap_active)} | Backup menengah/mahal aktif: {len(expensive_active)}")
        with st.expander("Detail status semua model"):
            render_model_health_table()

        col_test, col_reset = st.columns(2)
        with col_test:
            if st.button("🧪 Tes AI", use_container_width=True):
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
                        timeout=60,
                        smart_model_router=bool(st.session_state.active_smart_router),
                        return_to_primary=route["return_to_primary"],
                        max_smart_models=route["max_smart_models"],
                    )
                    restore_active_model_to_cheap(route.get("primary_model"))
                    st.success(answer)
                    st.caption(f"Model: {meta.get('model') or meta.get('model_requested')}")
                except Exception as exc:
                    st.error(str(exc))
        with col_reset:
            if st.button("↩️ Reset dari Secrets", use_container_width=True):
                st.session_state.active_model = default_model
                st.session_state.active_persona = persona_from_secret
                st.session_state.active_default_memory = default_memory_context_from_secret
                st.session_state.active_temperature = 0.3
                st.session_state.active_max_tokens = 2600
                st.session_state.show_debug = False
                st.session_state.active_smart_router = smart_model_router_default
                st.session_state.active_return_to_primary = return_to_primary_default
                st.session_state.active_max_smart_models = max_smart_models_default
                st.session_state.allow_expensive_fallback = parse_bool(get_secret("ALLOW_EXPENSIVE_FALLBACK", True), default=True)
                st.session_state.max_expensive_models = int(get_secret("MAX_EXPENSIVE_MODELS", 1) or 1)
                st.session_state.model_health_cache = {}
                st.session_state.model_health_checked_at = 0.0
                st.session_state.active_cheap_fallback_models = DEFAULT_CHEAP_FALLBACK_MODELS.copy()
                st.session_state.active_expensive_fallback_models = DEFAULT_EXPENSIVE_FALLBACK_MODELS.copy()
                st.session_state.last_model_health_error = ""
                st.session_state.active_rotate_cheap_primary = rotate_cheap_primary_default
                st.session_state.active_use_streamlit_cache_memory = use_streamlit_cache_memory_default
                st.session_state.active_thinking_model_router = thinking_model_router_default
                st.session_state.active_thinking_min_chars = thinking_min_chars_default
                st.session_state.active_fast_normal_model_router = fast_normal_model_router_default
                st.session_state.cheap_model_rotation_index = 0
                st.session_state.last_rotated_primary_model = ""
                st.rerun()

    with tab_bot:
        st.markdown("#### Kontrol Bot Telegram")
        format_token_status("TELEGRAM_BOT_TOKEN", telegram_token)
        format_token_status("SLASHAI_API_KEY", api_key)
        st.warning("Mode aman aktif: TELEGRAM_AUTO_START disarankan FALSE. Jalankan bot hanya dari tombol admin agar Streamlit Online tidak membuat beberapa poller saat app rerun/restart.")
        st.info("Lock OS aktif untuk mencegah lebih dari satu worker dalam container yang sama. Jika tetap double/triple, berarti token bot masih hidup di deployment lama/lokal/VPS lain.")
        st.caption("Telegram dikirim sebagai plain text secara default agar kode/XML seperti <uses-permission> tidak dianggap tag HTML.")
        st.caption(f"Perintah admin Telegram: /speed {telegram_speed_update_code} untuk cek ulang model hanya pada pukul {_health_window_label_wib()} dan memakai hanya model yang hidup.")

        status = service.status()
        st.write("Status bot:", "🟢 Berjalan" if status["running"] else "🔴 Mati")
        st.caption(f"Pesan diproses: {status.get('processed', 0)}")
        if status.get("started_at"):
            st.caption(f"Mulai: {_to_wib_display_text(status['started_at'])}")
        if status.get("worker_id"):
            st.caption(f"Worker: {status['worker_id']}")
        st.caption(f"Duplikat dicegah: {status.get('duplicates_skipped', 0)}")
        if status.get("runtime_primary_model"):
            st.caption(f"Primary runtime Telegram: {status.get('runtime_primary_model')}")
        if status.get("model_health_checked_at"):
            st.caption(f"Update model Telegram terakhir: {_to_wib_display_text(status.get('model_health_checked_at'))} | aktif: {status.get('model_health_active_count', 0)}")

        route = build_model_routing_plan()
        bot_config = {
            "telegram_token": telegram_token,
            "slashai_api_key": api_key,
            "slashai_api_url": api_url,
            "slashai_model": route["primary_model"],
            "persona": persona_with_default_memory(st.session_state.active_persona),
            "memory_file": memory_file,
            "fallback_models": route["cheap_fallback_models"],
            "expensive_fallback_models": route["expensive_fallback_models"],
            "allow_expensive_fallback": route["allow_expensive_fallback"],
            "max_expensive_models": route["max_expensive_models"],
            "show_model_info": telegram_show_model_info,
            "temperature": float(st.session_state.active_temperature),
            "max_completion_tokens": int(st.session_state.active_max_tokens),
            "timeout": 60,
            "drop_pending_updates": drop_pending_updates,
            "send_processing_message": send_processing_message,
            "telegram_parse_mode": telegram_parse_mode,
            "lock_file": telegram_lock_file,
            "allow_memory_commands": False,
            "smart_model_router": bool(st.session_state.active_smart_router),
            "return_to_primary": route["return_to_primary"],
            "max_smart_models": route["max_smart_models"],
            "thinking_model_router": bool(st.session_state.get("active_thinking_model_router", True)),
            "thinking_min_chars": int(st.session_state.get("active_thinking_min_chars", thinking_min_chars_default) or 180),
            "thinking_capable_model": thinking_capable_model_override,
            "fast_normal_model_router": bool(st.session_state.get("active_fast_normal_model_router", True)),
            "fastest_cheap_model": route.get("fastest_cheap_primary_model", ""),
            "fast_cheap_models": route.get("fast_cheap_models", []),
            "all_cheap_models": CHEAP_MODEL_OPTIONS,
            "all_expensive_models": EXPENSIVE_MODEL_OPTIONS,
            "all_model_candidates": MODEL_OPTIONS,
            "active_cheap_models": route.get("active_cheap_models", []),
            "thinking_capable_models": route.get("active_expensive_models", []),
            "speed_update_code": telegram_speed_update_code,
            "model_health_timeout": int(model_health_timeout or 12),
            "model_health_midnight_only": bool(model_health_midnight_only),
            "model_health_hour_wib": int(model_health_hour_wib or 0),
            "model_health_window_minutes": int(model_health_window_minutes or 60),
        }

        col_start, col_stop = st.columns(2)
        with col_start:
            if st.button("▶️ Start Bot", use_container_width=True):
                start_route = build_model_routing_plan(advance_rotation=True)
                bot_config.update({
                    "slashai_model": start_route["primary_model"],
                    "fallback_models": start_route["cheap_fallback_models"],
                    "expensive_fallback_models": start_route["expensive_fallback_models"],
                    "allow_expensive_fallback": start_route["allow_expensive_fallback"],
                    "max_expensive_models": start_route["max_expensive_models"],
                    "return_to_primary": start_route["return_to_primary"],
                    "max_smart_models": start_route["max_smart_models"],
                    "fastest_cheap_model": start_route.get("fastest_cheap_primary_model", ""),
                    "fast_cheap_models": start_route.get("fast_cheap_models", []),
                    "active_cheap_models": start_route.get("active_cheap_models", []),
                    "thinking_capable_models": start_route.get("active_expensive_models", []),
                })
                started = service.start(bot_config)
                restore_active_model_to_cheap(start_route.get("primary_model"))
                if started:
                    st.success(f"Bot Telegram dijalankan dengan primary: {start_route['primary_model']}")
                else:
                    st.info("Bot sudah berjalan.")
        with col_stop:
            if st.button("⏹️ Stop Bot", use_container_width=True):
                service.stop()
                st.warning("Bot Telegram dihentikan pada instance Streamlit ini.")

        if st.button("🧯 Reset koneksi Telegram / hapus pending update", use_container_width=True):
            result = service.reset_telegram_session(bot_config)
            st.warning(result)

        st.caption("Penting: tombol Stop hanya mematikan worker pada app ini. Jika ada deploy lama/laptop/VPS lain dengan token yang sama, revoke token dari BotFather lalu masukkan token baru di Secrets.")

        if status.get("last_update"):
            with st.expander("Update terakhir"):
                st.code(status["last_update"])
        if status.get("last_error"):
            with st.expander("Error terakhir"):
                st.code(status["last_error"][:2000])

    with tab_memory:
        st.markdown("#### Memory Default Aktif")
        st.caption("Memory default ini selalu ikut dikirim ke AI, baik ada memory cache maupun belum ada.")
        st.session_state.active_default_memory = st.text_area(
            "Memory default",
            value=st.session_state.active_default_memory,
            height=220,
        )

        st.markdown("#### Memory Cache Streamlit Online")
        st.info(
            "Memory cache disimpan di RAM/cache Streamlit. Memory ini bertahan saat rerun dan selama container app masih hidup, "
            "tetapi bisa hilang saat app sleep, restart, clear cache, atau redeploy. Cocok untuk memory cepat di Streamlit Cloud."
        )
        st.session_state.active_use_streamlit_cache_memory = st.toggle(
            "Aktifkan memory cache Streamlit untuk jawaban AI",
            value=bool(st.session_state.active_use_streamlit_cache_memory),
        )

        cache_memory_text = streamlit_cache_memory_list_text(limit=80)
        if cache_memory_text:
            st.code(cache_memory_text)
        else:
            st.write("Belum ada memory di cache Streamlit.")

        new_cache_memory = st.text_area(
            "Tambah memory ke cache Streamlit",
            value="",
            height=90,
            placeholder="Contoh: User ingin jawaban yang ringkas, jelas, dan langsung bisa dipakai.",
        )
        col_cache_save, col_cache_save_both = st.columns(2)
        with col_cache_save:
            if st.button("Simpan ke cache Streamlit", use_container_width=True):
                saved = add_streamlit_cache_memory(new_cache_memory, source="streamlit-admin-cache")
                if saved:
                    st.success("Memory disimpan ke cache Streamlit.")
                else:
                    st.info("Memory kosong atau sudah ada di cache.")
                st.rerun()
        with col_cache_save_both:
            if st.button("Simpan ke cache + file lokal", use_container_width=True):
                saved_cache = add_streamlit_cache_memory(new_cache_memory, source="streamlit-admin-cache")
                if new_cache_memory.strip():
                    memory.add(new_cache_memory.strip(), source="streamlit-admin-file")
                if saved_cache or new_cache_memory.strip():
                    st.success("Memory disimpan ke cache Streamlit dan file lokal.")
                else:
                    st.info("Memory kosong atau sudah ada.")
                st.rerun()

        forget_cache_keyword = st.text_input("Hapus memory cache yang mengandung kata")
        col_cache_forget, col_cache_reset = st.columns(2)
        with col_cache_forget:
            if st.button("Hapus dari cache berdasarkan kata", use_container_width=True):
                count = forget_streamlit_cache_memory_contains(forget_cache_keyword)
                st.warning(f"{count} memory cache dihapus.")
                st.rerun()
        with col_cache_reset:
            if st.button("Reset semua memory cache", use_container_width=True):
                count = reset_streamlit_cache_memory()
                st.warning(f"{count} memory cache dihapus.")
                st.rerun()

        st.markdown("#### Memory Tambahan File Lokal")
        st.caption("Opsional. File lokal dapat hilang di Streamlit Cloud saat app restart/redeploy, tetapi tetap dipertahankan untuk kompatibilitas fitur lama.")
        current_memory = memory.list_text(limit=80)
        if current_memory:
            st.code(current_memory)
        else:
            st.write("Belum ada memory file lokal.")

        new_file_memory = st.text_input("Tambah memory ke file lokal")
        if st.button("Simpan ke file lokal", use_container_width=True):
            if new_file_memory.strip():
                memory.add(new_file_memory.strip(), source="streamlit-admin-file")
                st.success("Memory disimpan ke file lokal.")
                st.rerun()
            else:
                st.info("Memory masih kosong.")

        forget_keyword = st.text_input("Hapus memory file lokal yang mengandung kata")
        col_forget, col_reset_memory = st.columns(2)
        with col_forget:
            if st.button("Hapus file lokal berdasarkan kata", use_container_width=True):
                count = memory.forget_contains(forget_keyword)
                st.warning(f"{count} memory file lokal dihapus.")
                st.rerun()
        with col_reset_memory:
            if st.button("Reset semua memory file lokal", use_container_width=True):
                memory.reset()
                st.warning("Semua memory file lokal dihapus.")
                st.rerun()

    with tab_setup:
        st.markdown("#### Secrets Streamlit Cloud")
        st.write("Masukkan konfigurasi berikut di menu **Streamlit Cloud → App → Settings → Secrets**.")
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
MODEL_HEALTH_MIDNIGHT_ONLY = true
MODEL_HEALTH_HOUR_WIB = 0
MODEL_HEALTH_WINDOW_MINUTES = 60''',
            language="toml",
        )
        st.markdown(
            """
            **Catatan:** Chat AI di halaman utama tidak perlu login. Password admin hanya melindungi pengaturan, kontrol Telegram, memory, dan debug.
            Untuk bot Telegram 24 jam nonstop, VPS tetap lebih stabil karena Streamlit Online bisa sleep saat tidak aktif.
            """.strip()
        )


# =========================
# Sidebar
# =========================
with st.sidebar:
    st.title("🤖 Adioranye")

    if st.session_state.admin_authenticated:
        render_admin_settings()
    else:
        render_admin_login()


# =========================
# Public Chat UI
# =========================
cfg = get_runtime_config()
public_route_preview = build_model_routing_plan(user_text="halo")
cheap_active = public_route_preview.get("active_cheap_models") or []
expensive_active = public_route_preview.get("active_expensive_models") or []
last_public_meta = st.session_state.get("last_answer_meta", {}) or {}

st.markdown(
    f"""
    <div class="mac-windowbar">
        <div class="mac-traffic">
            <span class="mac-close"></span>
            <span class="mac-min"></span>
            <span class="mac-max"></span>
        </div>
        <div class="mac-window-title">adioranye AI</div>
        <div class="mac-window-actions">Online</div>
    </div>
    <div class="app-hero">
        <div class="app-logo">🤖</div>
        <div>
            <h3 class="app-title">Selamat Datang</h3>
            <p class="app-subtitle">Tulis pesan Anda. AI bot adioranye membantu dengan jawaban yang cerdas, ramah, detail, dan praktis. Terdapat {len(cheap_active)} model AI live dan {len(expensive_active)} model standby untuk menjawab pertanyaan Anda.</p>
        </div>
    </div>
    <div class="developer-credit"><span>Developed by Galuh Adi Insani</span></div>
    {build_public_model_status_html(public_route_preview, last_public_meta)}
    """,
    unsafe_allow_html=True,
)

if not api_key:
    st.warning("SLASHAI_API_KEY belum diisi. Chat belum bisa digunakan sampai admin mengisi Secrets di Streamlit Cloud.")

col_new_chat, col_info = st.columns([1, 4])
with col_new_chat:
    if st.button("🧹 Chat baru", use_container_width=True):
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
        "⬇️ Download semua chat PDF",
        data=make_answer_pdf_bytes("\n\n".join(transcript_parts), title="Riwayat Chat Adioranye AI"),
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

# Spacer is rendered at the very end so it also protects newly generated messages.
typed_input = st.chat_input("Ketik pesan Anda...")
user_input = st.session_state.pending_prompt or typed_input
if st.session_state.pending_prompt:
    st.session_state.pending_prompt = ""

if user_input:
    # Public chat: memory commands are disabled unless admin is logged in.
    # This prevents random visitors from changing global memory.
    with st.chat_message("user"):
        st.markdown(user_input)

    st.session_state.chat_messages.append({"role": "user", "content": user_input})

    local_reply = ""
    if st.session_state.admin_authenticated:
        local_reply = handle_local_memory_command(user_input, memory)

    if local_reply:
        answer = local_reply
        meta = {}
        st.session_state.last_answer_meta = meta
    else:
        try:
            with st.chat_message("assistant"):
                placeholder = st.empty()
                placeholder.markdown("⏳ Siap! adioranye sedang menyiapkan jawaban untukmu...")
                route = build_model_routing_plan(advance_rotation=True, user_text=user_input)
                answer, meta = generate_answer(
                    api_url=api_url,
                    api_key=api_key,
                    model=route["primary_model"],
                    system_prompt=cfg["persona"],
                    user_text=user_input,
                    memory_text=build_memory_text(limit=12),
                    recent_messages=st.session_state.chat_messages[:-1][-6:],
                    fallback_models=route["cheap_fallback_models"],
                    expensive_fallback_models=route["expensive_fallback_models"],
                    allow_expensive_fallback=route["allow_expensive_fallback"],
                    max_expensive_models=route["max_expensive_models"],
                    temperature=float(cfg["temperature"]),
                    max_completion_tokens=int(cfg["max_completion_tokens"]),
                    timeout=60,
                    smart_model_router=bool(cfg["smart_model_router"]),
                    return_to_primary=route["return_to_primary"],
                    max_smart_models=route["max_smart_models"],
                )
                restore_active_model_to_cheap(route.get("primary_model"))
                placeholder.markdown(answer)
                st.session_state.last_answer_meta = meta or {}
                final_model = (meta or {}).get("active_model_final") or (meta or {}).get("model_requested") or cfg["model"]
                answer_pdf_download_button(answer, key="download_pdf_latest_answer", model_name=final_model)
                caption_text = f"Model aktif: {final_model}"
                if st.session_state.admin_authenticated:
                    consulted = (meta or {}).get("consulted_models") or []
                    expensive_used = (meta or {}).get("expensive_fallback_used", False)
                    if consulted:
                        caption_text += " • konsultasi: " + ", ".join(str(item) for item in consulted[:4])
                    if route.get("thinking_direct_to_capable"):
                        caption_text += " • thinking mode: memakai model capable"
                    elif expensive_used:
                        caption_text += " • model menengah/mahal dipakai karena jawaban hemat kurang cukup"
                st.caption(caption_text)
        except Exception as exc:
            answer = (
                "Maaf, Adioranye belum bisa menjawab saat ini. "
                "Silakan coba lagi beberapa saat lagi atau hubungi admin.\n\n"
                f"Detail ringkas: {str(exc)[:1000]}"
            )
            meta = {}
            st.session_state.last_answer_meta = meta
            with st.chat_message("assistant"):
                st.error(answer)

    if local_reply:
        with st.chat_message("assistant"):
            st.markdown(answer)
            answer_pdf_download_button(answer, key="download_pdf_local_reply")

    st.session_state.chat_messages.append({"role": "assistant", "content": answer, "meta": meta or {}})

    if meta and st.session_state.admin_authenticated and st.session_state.show_debug:
        with st.expander("Debug response admin"):
            st.json(meta)

# Ruang aman terakhir agar input floating tidak menutupi pesan terakhir, termasuk pesan yang baru dibuat.
st.markdown('<div class="chat-input-safe-space"></div>', unsafe_allow_html=True)

# Tiny refresh delay for Streamlit Cloud stability
time.sleep(0.03)
