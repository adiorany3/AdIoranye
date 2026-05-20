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


# =========================
# Defaults
# =========================

DEFAULT_PERSONA = (
    "Nama kamu adalah adioranye. "
    "Kamu adalah asisten pribadi yang pintar, cepat, ramah, dan dapat membantu menjawab berbagai pertanyaan yang aman dan bermanfaat. "
    "Jawab dalam bahasa Indonesia yang natural, jelas, praktis, dan tidak bertele-tele. "
    "Jika permintaan berbahaya atau melanggar aturan, tolak dengan singkat dan arahkan ke alternatif yang aman."
)

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


# =========================
# Clean glass mobile-first styling
# =========================
st.markdown(
    """
    <style>
    :root {
        --bg-main: #eef5ff;
        --bg-soft: #f8fbff;
        --orb-blue: rgba(37, 99, 235, 0.24);
        --orb-pink: rgba(236, 72, 153, 0.14);
        --orb-teal: rgba(20, 184, 166, 0.14);
        --glass: rgba(255, 255, 255, 0.58);
        --glass-strong: rgba(255, 255, 255, 0.76);
        --glass-muted: rgba(255, 255, 255, 0.34);
        --text: #0f172a;
        --muted: #64748b;
        --border: rgba(255, 255, 255, 0.62);
        --border-soft: rgba(15, 23, 42, 0.08);
        --primary: #2563eb;
        --primary-soft: rgba(37, 99, 235, 0.12);
        --user-bubble: rgba(219, 234, 254, 0.78);
        --assistant-bubble: rgba(255, 255, 255, 0.68);
        --shadow: 0 22px 48px rgba(15, 23, 42, 0.12);
        --shadow-soft: 0 12px 28px rgba(15, 23, 42, 0.08);
        --blur: blur(20px) saturate(150%);
    }

    @media (prefers-color-scheme: dark) {
        :root {
            --bg-main: #07111f;
            --bg-soft: #0f172a;
            --orb-blue: rgba(37, 99, 235, 0.30);
            --orb-pink: rgba(168, 85, 247, 0.18);
            --orb-teal: rgba(20, 184, 166, 0.14);
            --glass: rgba(15, 23, 42, 0.56);
            --glass-strong: rgba(15, 23, 42, 0.74);
            --glass-muted: rgba(30, 41, 59, 0.44);
            --text: #f8fafc;
            --muted: #cbd5e1;
            --border: rgba(255, 255, 255, 0.14);
            --border-soft: rgba(255, 255, 255, 0.10);
            --primary: #93c5fd;
            --primary-soft: rgba(147, 197, 253, 0.14);
            --user-bubble: rgba(30, 64, 112, 0.70);
            --assistant-bubble: rgba(15, 23, 42, 0.62);
            --shadow: 0 22px 48px rgba(0, 0, 0, 0.34);
            --shadow-soft: 0 12px 28px rgba(0, 0, 0, 0.24);
        }
    }

    html, body, .stApp {
        background:
            radial-gradient(circle at 12% 4%, var(--orb-blue), transparent 30%),
            radial-gradient(circle at 92% 14%, var(--orb-pink), transparent 28%),
            radial-gradient(circle at 46% 96%, var(--orb-teal), transparent 32%),
            linear-gradient(135deg, var(--bg-main), var(--bg-soft)) !important;
        color: var(--text) !important;
        min-height: 100%;
    }

    .stApp::before {
        content: "";
        position: fixed;
        inset: 0;
        z-index: 0;
        pointer-events: none;
        background-image:
            linear-gradient(rgba(255,255,255,0.10) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,0.10) 1px, transparent 1px);
        background-size: 48px 48px;
        mask-image: linear-gradient(to bottom, rgba(0,0,0,0.30), transparent 70%);
    }

    #MainMenu, footer {
        visibility: hidden;
    }

    header[data-testid="stHeader"] {
        background: transparent !important;
    }

    .main .block-container {
        position: relative;
        z-index: 1;
        max-width: 740px;
        padding: 1rem 1rem 6.9rem;
    }

    @media (max-width: 640px) {
        .main .block-container {
            padding: 0.72rem 0.78rem 7.7rem;
        }
    }

    div[data-testid="stSidebar"] {
        min-width: min(88vw, 360px) !important;
        max-width: min(88vw, 390px) !important;
        background: var(--glass-strong) !important;
        border-right: 1px solid var(--border);
        box-shadow: var(--shadow);
        backdrop-filter: var(--blur);
        -webkit-backdrop-filter: var(--blur);
    }

    div[data-testid="stSidebar"] * {
        color: var(--text) !important;
    }

    div[data-testid="stSidebar"] section,
    div[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {
        background: transparent !important;
    }

    .app-hero {
        position: relative;
        overflow: hidden;
        display: flex;
        align-items: center;
        gap: 14px;
        padding: 18px;
        margin: 0 0 16px;
        border: 1px solid var(--border);
        border-radius: 28px;
        background: linear-gradient(135deg, var(--glass-strong), var(--glass-muted));
        box-shadow: var(--shadow);
        backdrop-filter: var(--blur);
        -webkit-backdrop-filter: var(--blur);
    }

    .app-hero::after {
        content: "";
        position: absolute;
        width: 130px;
        height: 130px;
        right: -56px;
        top: -62px;
        border-radius: 999px;
        background: var(--primary-soft);
        filter: blur(2px);
    }

    .app-logo {
        position: relative;
        z-index: 1;
        flex: 0 0 50px;
        width: 50px;
        height: 50px;
        display: grid;
        place-items: center;
        border-radius: 19px;
        background: rgba(255, 255, 255, 0.42);
        border: 1px solid var(--border);
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.38), var(--shadow-soft);
        font-size: 1.48rem;
        backdrop-filter: var(--blur);
        -webkit-backdrop-filter: var(--blur);
    }

    .app-title {
        position: relative;
        z-index: 1;
        margin: 0;
        color: var(--text);
        font-size: clamp(1.38rem, 5vw, 1.95rem);
        line-height: 1.08;
        font-weight: 850;
        letter-spacing: -0.035em;
    }

    .app-subtitle {
        position: relative;
        z-index: 1;
        max-width: 520px;
        margin: 6px 0 0;
        color: var(--muted);
        font-size: clamp(0.93rem, 3.6vw, 1rem);
        line-height: 1.48;
    }

    .section-title {
        margin: 14px 0 9px;
        color: var(--text);
        font-weight: 780;
        font-size: 0.95rem;
        letter-spacing: -0.01em;
    }

    .simple-note,
    .stCaptionContainer,
    div[data-testid="stCaptionContainer"] {
        color: var(--muted) !important;
        font-size: 0.86rem;
    }

    div[data-testid="stChatMessage"] {
        border: 1px solid var(--border);
        border-radius: 23px;
        padding: 0.58rem 0.68rem;
        margin-bottom: 0.78rem;
        background: var(--assistant-bubble);
        color: var(--text) !important;
        box-shadow: var(--shadow-soft);
        backdrop-filter: var(--blur);
        -webkit-backdrop-filter: var(--blur);
    }

    div[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        background: var(--user-bubble);
    }

    div[data-testid="stChatMessage"] * {
        color: var(--text) !important;
    }

    div[data-testid="stChatMessageAvatarUser"],
    div[data-testid="stChatMessageAvatarAssistant"] {
        background: rgba(255, 255, 255, 0.36) !important;
        border: 1px solid var(--border) !important;
        backdrop-filter: var(--blur);
        -webkit-backdrop-filter: var(--blur);
    }

    div[data-testid="stMarkdownContainer"] p,
    div[data-testid="stMarkdownContainer"] li {
        line-height: 1.65;
        font-size: clamp(0.97rem, 3.7vw, 1.03rem);
    }

    code, pre,
    div[data-testid="stMarkdownContainer"] code {
        border-radius: 14px !important;
        white-space: pre-wrap !important;
        word-break: break-word !important;
        border: 1px solid var(--border-soft) !important;
    }

    textarea,
    input,
    div[data-baseweb="input"] input,
    div[data-baseweb="textarea"] textarea {
        background: rgba(255, 255, 255, 0.50) !important;
        color: var(--text) !important;
        border-color: var(--border) !important;
        font-size: 16px !important;
        backdrop-filter: var(--blur);
        -webkit-backdrop-filter: var(--blur);
    }

    @media (prefers-color-scheme: dark) {
        textarea,
        input,
        div[data-baseweb="input"] input,
        div[data-baseweb="textarea"] textarea {
            background: rgba(15, 23, 42, 0.58) !important;
        }
    }

    div[data-testid="stChatInput"] {
        background: rgba(238, 245, 255, 0.66) !important;
        border-top: 1px solid var(--border);
        padding: 0.58rem 0.76rem max(0.62rem, env(safe-area-inset-bottom));
        backdrop-filter: blur(20px) saturate(160%);
        -webkit-backdrop-filter: blur(20px) saturate(160%);
    }

    @media (prefers-color-scheme: dark) {
        div[data-testid="stChatInput"] {
            background: rgba(7, 17, 31, 0.70) !important;
        }
    }

    div[data-testid="stChatInput"] textarea {
        min-height: 48px !important;
        border-radius: 21px !important;
        border: 1px solid var(--border) !important;
        box-shadow: var(--shadow-soft);
    }

    button[kind="primary"],
    div[data-testid="stFormSubmitButton"] button,
    div[data-testid="stButton"] button,
    div[data-testid="stDownloadButton"] button {
        min-height: 44px;
        border-radius: 16px !important;
        border: 1px solid var(--border) !important;
        background: linear-gradient(135deg, var(--glass-strong), var(--glass-muted)) !important;
        color: var(--text) !important;
        font-weight: 680 !important;
        text-align: center !important;
        box-shadow: var(--shadow-soft) !important;
        backdrop-filter: var(--blur);
        -webkit-backdrop-filter: var(--blur);
        transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
    }

    button[kind="primary"]:hover,
    div[data-testid="stFormSubmitButton"] button:hover,
    div[data-testid="stButton"] button:hover,
    div[data-testid="stDownloadButton"] button:hover {
        transform: translateY(-1px);
        border-color: rgba(37, 99, 235, 0.28) !important;
        box-shadow: 0 16px 30px rgba(15, 23, 42, 0.13) !important;
    }

    div[data-testid="stAlert"],
    .stAlert {
        border-radius: 18px !important;
        border: 1px solid var(--border) !important;
        box-shadow: var(--shadow-soft);
        backdrop-filter: var(--blur);
        -webkit-backdrop-filter: var(--blur);
    }

    div[data-baseweb="select"] > div,
    div[data-baseweb="textarea"] > div,
    div[data-baseweb="input"] > div {
        border-radius: 16px !important;
        border-color: var(--border) !important;
        background: var(--glass-muted) !important;
        backdrop-filter: var(--blur);
        -webkit-backdrop-filter: var(--blur);
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 6px;
    }

    .stTabs [data-baseweb="tab"] {
        min-height: 38px;
        border-radius: 999px;
        background: var(--glass-muted);
        border: 1px solid var(--border);
        color: var(--text) !important;
        backdrop-filter: var(--blur);
        -webkit-backdrop-filter: var(--blur);
    }

    hr {
        border-color: var(--border-soft) !important;
        margin: 0.86rem 0 !important;
    }

    @media (max-width: 640px) {
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
                "persona": cfg["persona"],
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
                        memory_text=memory.as_prompt_text(limit=8),
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
            "persona": st.session_state.active_persona,
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
        current_memory = memory.list_text(limit=80)
        if current_memory:
            st.code(current_memory)
        else:
            st.write("Belum ada memori.")

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

ASSISTANT_PERSONA = "Nama kamu adalah adioranye. Kamu adalah asisten pribadi yang pintar, cepat, ramah, dan dapat membantu menjawab berbagai pertanyaan yang aman dan bermanfaat. Jawab dalam bahasa Indonesia yang natural, jelas, praktis, dan tidak bertele-tele. Jika permintaan berbahaya atau melanggar aturan, tolak dengan singkat dan arahkan ke alternatif yang aman."
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
    <div class="app-hero">
        <div class="app-logo">🤖</div>
        <div>
            <h1 class="app-title">Adioranye AI</h1>
            <p class="app-subtitle">Tulis pesan Anda, lalu Adioranye akan membantu dengan jawaban yang jelas dan praktis.</p>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

if not api_key:
    st.warning("SLASHAI_API_KEY belum diisi. Chat belum bisa digunakan sampai admin mengisi Secrets di Streamlit Cloud.")

# Quick prompt buttons are only shown when chat is empty.
if not st.session_state.chat_messages:
    st.markdown('<div class="section-title">Contoh cepat</div>', unsafe_allow_html=True)
    prompt_examples = [
        ("Caption promosi", "Buatkan caption promosi produk yang singkat dan menarik."),
        ("Ringkas teks", "Ringkas materi ini menjadi bahasa yang natural dan mudah dipahami."),
        ("Jawaban presentasi", "Bantu susun jawaban presentasi agar terdengar percaya diri."),
        ("Ide konten TikTok", "Buatkan ide konten TikTok edukasi yang berpotensi ramai."),
    ]
    cols = st.columns(2)
    for idx, (label, prompt_text) in enumerate(prompt_examples):
        with cols[idx % 2]:
            if st.button(label, key=f"quick_prompt_{idx}", use_container_width=True):
                st.session_state.pending_prompt = prompt_text

col_new_chat, col_info = st.columns([1, 2])
with col_new_chat:
    if st.button("🧹 Chat baru", use_container_width=True):
        st.session_state.chat_messages = []
        st.session_state.pending_prompt = ""
        st.rerun()
with col_info:
    st.caption(f"{len(st.session_state.chat_messages)} pesan")

st.divider()

for msg in st.session_state.chat_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

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
                placeholder.markdown("⏳ sedang berpikir dalam...")
                answer, meta = generate_answer(
                    api_url=api_url,
                    api_key=api_key,
                    model=cfg["model"],
                    system_prompt=cfg["persona"],
                    user_text=user_input,
                    memory_text=memory.as_prompt_text(limit=12),
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

# Tiny refresh delay for Streamlit Cloud stability
time.sleep(0.03)