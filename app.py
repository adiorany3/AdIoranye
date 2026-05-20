import hmac
import time
from typing import Any, Dict

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
    page_title="Adioranye AI",
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


# =========================
# Defaults
# =========================

DEFAULT_PERSONA = (
    "Nama kamu adalah adioranye. "
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
admin_username = str(get_secret("ADMIN_USERNAME", "admin"))
admin_password = str(get_secret("ADMIN_PASSWORD", "Admin"))
smart_model_router_default = parse_bool(get_secret("SMART_MODEL_ROUTER", True), default=True)
return_to_primary_default = parse_bool(get_secret("RETURN_TO_PRIMARY_MODEL", True), default=True)
max_smart_models_default = int(get_secret("MAX_SMART_MODELS", 2) or 2)

init_state()
memory = MemoryStore(memory_file)
service = get_telegram_service()


def build_memory_text(limit: int = 12) -> str:
    """Gabungkan memory default dengan memory lokal admin."""
    default_context = str(st.session_state.get("active_default_memory") or default_memory_context_from_secret or DEFAULT_MEMORY_CONTEXT).strip()
    local_memory = str(memory.as_prompt_text(limit=limit) or "").strip()

    sections = []
    if default_context:
        sections.append("MEMORY DEFAULT AKTIF:\n" + default_context)
    if local_memory:
        sections.append("MEMORY TAMBAHAN ADMIN:\n" + local_memory)
    return "\n\n".join(sections)


def persona_with_default_memory(persona: str) -> str:
    """Dipakai untuk Bot Telegram agar memory default tetap masuk ke instruksi bot."""
    default_context = str(st.session_state.get("active_default_memory") or default_memory_context_from_secret or DEFAULT_MEMORY_CONTEXT).strip()
    if not default_context:
        return persona
    return f"{persona}\n\nKonteks default yang selalu dipakai:\n{default_context}"


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
    }


def start_telegram_if_needed() -> None:
    cfg = get_runtime_config()
    if auto_start and telegram_token and api_key and not service.status()["running"]:
        service.start(
            {
                "telegram_token": telegram_token,
                "slashai_api_key": api_key,
                "slashai_api_url": api_url,
                "slashai_model": cfg["model"],
                "persona": persona_with_default_memory(cfg["persona"]),
                "memory_file": memory_file,
                "fallback_models": DEFAULT_CHEAP_FALLBACK_MODELS,
                "expensive_fallback_models": DEFAULT_EXPENSIVE_FALLBACK_MODELS,
                "allow_expensive_fallback": cfg["allow_expensive_fallback"],
                "max_expensive_models": cfg["max_expensive_models"],
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
                "return_to_primary": cfg["return_to_primary"],
                "max_smart_models": cfg["max_smart_models"],
            }
        )


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

    st.markdown("#### Status Sistem")
    st.caption("Chat publik aktif. Setting hanya untuk admin.")
    status_text = (
        f"Model utama: {cfg['model']}\n\n"
        f"Tier: {model_cost_tier(cfg['model'])} | Rp{price.get('input', 0):,}/Rp{price.get('output', 0):,}\n\n"
        f"Jawaban terakhir: {last_model}\n\n"
        f"Model mahal dipakai: {exp_used}\n\n"
        f"Telegram: {telegram_status}"
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
        st.session_state.active_persona = st.text_area(
            "System persona",
            value=st.session_state.active_persona,
            height=170,
        )
        st.session_state.show_debug = st.toggle("Tampilkan debug respons di chat", value=st.session_state.show_debug)
        st.markdown("#### Router Cepat & Akurat")
        st.caption("Algoritma baru: model utama menjawab dulu. Jika skor jawaban rendah/kosong/tidak yakin, barulah 1-2 model cadangan dikonsultasikan secara paralel terbatas, lalu hasil akhir dikembalikan ke model utama.")
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
            st.markdown("**Model hemat utama/cadangan:**")
            st.code("\n".join(model_price_label(m) for m in CHEAP_MODEL_OPTIONS))
            st.markdown("**Model menengah/mahal hanya saat perlu:**")
            st.code("\n".join(model_price_label(m) for m in EXPENSIVE_MODEL_OPTIONS))

        col_test, col_reset = st.columns(2)
        with col_test:
            if st.button("🧪 Tes AI", use_container_width=True):
                try:
                    answer, meta = generate_answer(
                        api_url=api_url,
                        api_key=api_key,
                        model=st.session_state.active_model,
                        system_prompt=st.session_state.active_persona,
                        user_text="Jawab singkat: apakah kamu aktif?",
                        memory_text=build_memory_text(limit=8),
                        recent_messages=[],
                        fallback_models=DEFAULT_CHEAP_FALLBACK_MODELS,
                        expensive_fallback_models=DEFAULT_EXPENSIVE_FALLBACK_MODELS,
                        allow_expensive_fallback=bool(st.session_state.allow_expensive_fallback),
                        max_expensive_models=int(st.session_state.max_expensive_models),
                        temperature=float(st.session_state.active_temperature),
                        max_completion_tokens=int(st.session_state.active_max_tokens),
                        timeout=60,
                        smart_model_router=bool(st.session_state.active_smart_router),
                        return_to_primary=bool(st.session_state.active_return_to_primary),
                        max_smart_models=int(st.session_state.active_max_smart_models),
                    )
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
                st.rerun()

    with tab_bot:
        st.markdown("#### Kontrol Bot Telegram")
        format_token_status("TELEGRAM_BOT_TOKEN", telegram_token)
        format_token_status("SLASHAI_API_KEY", api_key)
        st.warning("Mode aman aktif: TELEGRAM_AUTO_START disarankan FALSE. Jalankan bot hanya dari tombol admin agar Streamlit Online tidak membuat beberapa poller saat app rerun/restart.")
        st.info("Lock OS aktif untuk mencegah lebih dari satu worker dalam container yang sama. Jika tetap double/triple, berarti token bot masih hidup di deployment lama/lokal/VPS lain.")
        st.caption("Telegram dikirim sebagai plain text secara default agar kode/XML seperti <uses-permission> tidak dianggap tag HTML.")

        status = service.status()
        st.write("Status bot:", "🟢 Berjalan" if status["running"] else "🔴 Mati")
        st.caption(f"Pesan diproses: {status.get('processed', 0)}")
        if status.get("started_at"):
            st.caption(f"Mulai: {status['started_at']}")
        if status.get("worker_id"):
            st.caption(f"Worker: {status['worker_id']}")
        st.caption(f"Duplikat dicegah: {status.get('duplicates_skipped', 0)}")

        bot_config = {
            "telegram_token": telegram_token,
            "slashai_api_key": api_key,
            "slashai_api_url": api_url,
            "slashai_model": st.session_state.active_model,
            "persona": persona_with_default_memory(st.session_state.active_persona),
            "memory_file": memory_file,
            "fallback_models": DEFAULT_CHEAP_FALLBACK_MODELS,
            "expensive_fallback_models": DEFAULT_EXPENSIVE_FALLBACK_MODELS,
            "allow_expensive_fallback": bool(st.session_state.allow_expensive_fallback),
            "max_expensive_models": int(st.session_state.max_expensive_models),
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
            "return_to_primary": bool(st.session_state.active_return_to_primary),
            "max_smart_models": int(st.session_state.active_max_smart_models),
        }

        col_start, col_stop = st.columns(2)
        with col_start:
            if st.button("▶️ Start Bot", use_container_width=True):
                started = service.start(bot_config)
                if started:
                    st.success("Bot Telegram dijalankan.")
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
        st.markdown("#### Memory Lokal")
        st.info(
            "Memory ini dipakai sebagai konteks tambahan. Di Streamlit Online, file lokal bisa hilang saat app restart/redeploy."
        )
        st.markdown("#### Memory Default Aktif")
        st.caption("Memory default ini selalu ikut dikirim ke AI, baik ada memori lokal maupun belum ada.")
        st.session_state.active_default_memory = st.text_area(
            "Memory default",
            value=st.session_state.active_default_memory,
            height=220,
        )

        st.markdown("#### Memory Tambahan Admin")
        current_memory = memory.list_text(limit=80)
        if current_memory:
            st.code(current_memory)
        else:
            st.write("Belum ada memori tambahan.")

        new_memory = st.text_input("Tambah memori")
        if st.button("Simpan memori", use_container_width=True):
            if new_memory.strip():
                memory.add(new_memory.strip(), source="streamlit-admin")
                st.success("Memori disimpan.")
                st.rerun()

        forget_keyword = st.text_input("Hapus memori yang mengandung kata")
        col_forget, col_reset_memory = st.columns(2)
        with col_forget:
            if st.button("Hapus berdasarkan kata", use_container_width=True):
                count = memory.forget_contains(forget_keyword)
                st.warning(f"{count} memori dihapus.")
                st.rerun()
        with col_reset_memory:
            if st.button("Reset semua memori", use_container_width=True):
                memory.reset()
                st.warning("Semua memori dihapus.")
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

# Opsional
TEMPERATURE = 0.3
MAX_COMPLETION_TOKENS = 2600
SMART_MODEL_ROUTER = true
RETURN_TO_PRIMARY_MODEL = true
MAX_SMART_MODELS = 2
ALLOW_EXPENSIVE_FALLBACK = true
MAX_EXPENSIVE_MODELS = 1''',
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

st.markdown(
    """
    <div class="mac-windowbar">
        <div class="mac-traffic">
            <span class="mac-close"></span>
            <span class="mac-min"></span>
            <span class="mac-max"></span>
        </div>
        <div class="mac-window-title">Adioranye AI</div>
        <div class="mac-window-actions">Online</div>
    </div>
    <div class="app-hero">
        <div class="app-logo">🤖</div>
        <div>
            <h1 class="app-title">Adioranye AI</h1>
            <p class="app-subtitle">Tulis pesan Anda. Adioranye membantu dengan jawaban yang cerdas, ramah, detail, dan praktis.</p>
        </div>
    </div>
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

st.divider()

for msg in st.session_state.chat_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

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
                placeholder.markdown("⏳ adioranye sedang berpikir dalam...")
                answer, meta = generate_answer(
                    api_url=api_url,
                    api_key=api_key,
                    model=cfg["model"],
                    system_prompt=cfg["persona"],
                    user_text=user_input,
                    memory_text=build_memory_text(limit=12),
                    recent_messages=st.session_state.chat_messages[:-1][-6:],
                    fallback_models=DEFAULT_CHEAP_FALLBACK_MODELS,
                    expensive_fallback_models=DEFAULT_EXPENSIVE_FALLBACK_MODELS,
                    allow_expensive_fallback=bool(cfg["allow_expensive_fallback"]),
                    max_expensive_models=int(cfg["max_expensive_models"]),
                    temperature=float(cfg["temperature"]),
                    max_completion_tokens=int(cfg["max_completion_tokens"]),
                    timeout=60,
                    smart_model_router=bool(cfg["smart_model_router"]),
                    return_to_primary=bool(cfg["return_to_primary"]),
                    max_smart_models=int(cfg["max_smart_models"]),
                )
                placeholder.markdown(answer)
                st.session_state.last_answer_meta = meta or {}
                final_model = (meta or {}).get("active_model_final") or (meta or {}).get("model_requested") or cfg["model"]
                consulted = (meta or {}).get("consulted_models") or []
                expensive_used = (meta or {}).get("expensive_fallback_used", False)
                if st.session_state.admin_authenticated:
                    caption_text = f"Model aktif: {final_model}"
                    if consulted:
                        caption_text += " • konsultasi: " + ", ".join(consulted[:4])
                    if expensive_used:
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

    st.session_state.chat_messages.append({"role": "assistant", "content": answer})

    if meta and st.session_state.admin_authenticated and st.session_state.show_debug:
        with st.expander("Debug response admin"):
            st.json(meta)

# Ruang aman terakhir agar input floating tidak menutupi pesan terakhir, termasuk pesan yang baru dibuat.
st.markdown('<div class="chat-input-safe-space"></div>', unsafe_allow_html=True)

# Tiny refresh delay for Streamlit Cloud stability
time.sleep(0.03)