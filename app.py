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
# Mobile-first, eye-friendly adaptive styling
# =========================
st.markdown(
    """
    <style>
    :root {
        --app-bg: #f6f7fb;
        --app-surface: #ffffff;
        --app-surface-soft: #eef2f8;
        --app-text: #111827;
        --app-muted: #4b5563;
        --app-border: rgba(17, 24, 39, 0.13);
        --app-primary: #2563eb;
        --app-primary-strong: #1d4ed8;
        --app-primary-soft: rgba(37, 99, 235, 0.10);
        --app-shadow: 0 12px 28px rgba(15, 23, 42, 0.08);
        --assistant-bubble: #ffffff;
        --user-bubble: #e8f0ff;
        --input-bg: #ffffff;
        --success-soft: rgba(34, 197, 94, 0.12);
    }

    @media (prefers-color-scheme: dark) {
        :root {
            --app-bg: #0b1120;
            --app-surface: #111827;
            --app-surface-soft: #1f2937;
            --app-text: #f8fafc;
            --app-muted: #cbd5e1;
            --app-border: rgba(226, 232, 240, 0.15);
            --app-primary: #93c5fd;
            --app-primary-strong: #bfdbfe;
            --app-primary-soft: rgba(147, 197, 253, 0.14);
            --app-shadow: 0 12px 30px rgba(0, 0, 0, 0.35);
            --assistant-bubble: #111827;
            --user-bubble: #1e3a5f;
            --input-bg: #111827;
            --success-soft: rgba(34, 197, 94, 0.16);
        }
    }

    html, body, .stApp {
        background: var(--app-bg) !important;
        color: var(--app-text) !important;
    }

    /* Main layout: comfortable on phone, still clean on desktop. */
    .main .block-container {
        width: min(100%, 860px);
        max-width: 860px;
        padding: 0.85rem 1rem 6.5rem 1rem;
    }

    @media (max-width: 640px) {
        .main .block-container {
            padding: 0.65rem 0.72rem 7.2rem 0.72rem;
        }
    }

    /* Sidebar/admin should remain readable when opened on mobile. */
    div[data-testid="stSidebar"] {
        min-width: min(88vw, 360px) !important;
        max-width: min(88vw, 390px) !important;
        background: var(--app-surface) !important;
        border-right: 1px solid var(--app-border);
    }

    div[data-testid="stSidebar"] * {
        color: var(--app-text) !important;
    }

    .mobile-hero {
        border: 1px solid var(--app-border);
        border-radius: 26px;
        padding: 18px 18px;
        margin: 0 0 14px 0;
        background:
            radial-gradient(circle at 4% 8%, var(--app-primary-soft), transparent 38%),
            var(--app-surface);
        color: var(--app-text);
        box-shadow: var(--app-shadow);
    }

    .mobile-hero-title {
        display: flex;
        align-items: center;
        gap: 10px;
        font-size: clamp(1.35rem, 5.3vw, 2rem);
        line-height: 1.15;
        margin: 0 0 8px 0;
        font-weight: 800;
        letter-spacing: -0.035em;
        color: var(--app-text);
    }

    .mobile-hero p {
        margin: 0;
        color: var(--app-muted);
        line-height: 1.52;
        font-size: clamp(0.94rem, 3.6vw, 1rem);
    }

    .status-row {
        display: flex;
        flex-wrap: wrap;
        gap: 7px;
        margin-top: 12px;
    }

    .status-pill {
        display: inline-flex;
        align-items: center;
        padding: 6px 10px;
        min-height: 32px;
        border-radius: 999px;
        background: var(--app-primary-soft);
        border: 1px solid var(--app-border);
        color: var(--app-text) !important;
        font-size: 0.84rem;
        font-weight: 600;
        white-space: nowrap;
    }

    .suggestion-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
        margin: 10px 0 6px;
    }

    @media (max-width: 520px) {
        .suggestion-grid {
            grid-template-columns: 1fr;
        }
    }

    .quick-card {
        border: 1px solid var(--app-border);
        border-radius: 18px;
        padding: 14px 15px;
        background: var(--app-surface);
        color: var(--app-text);
        box-shadow: 0 8px 20px rgba(15, 23, 42, 0.05);
        line-height: 1.48;
        font-size: 0.96rem;
        min-height: 74px;
    }

    .toolbar-card {
        border: 1px solid var(--app-border);
        border-radius: 18px;
        padding: 10px 12px;
        margin: 10px 0 12px;
        background: var(--app-surface);
        color: var(--app-muted);
    }

    .small-note,
    .stCaptionContainer,
    div[data-testid="stCaptionContainer"] {
        color: var(--app-muted) !important;
        font-size: 0.88rem;
    }

    /* Chat bubbles */
    div[data-testid="stChatMessage"] {
        border: 1px solid var(--app-border);
        border-radius: 20px;
        padding: 0.48rem 0.62rem;
        margin-bottom: 0.78rem;
        background: var(--assistant-bubble);
        color: var(--app-text) !important;
        box-shadow: 0 7px 18px rgba(15, 23, 42, 0.04);
    }

    div[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        background: var(--user-bubble);
    }

    div[data-testid="stChatMessage"] * {
        color: var(--app-text) !important;
    }

    div[data-testid="stMarkdownContainer"] p,
    div[data-testid="stMarkdownContainer"] li {
        line-height: 1.68;
        font-size: clamp(0.98rem, 3.7vw, 1.04rem);
    }

    div[data-testid="stMarkdownContainer"] code,
    code, pre {
        border-radius: 12px !important;
        white-space: pre-wrap !important;
        word-break: break-word !important;
    }

    /* Inputs: 16px prevents zoom on iOS. */
    textarea,
    input,
    div[data-baseweb="input"] input,
    div[data-baseweb="textarea"] textarea {
        background: var(--input-bg) !important;
        color: var(--app-text) !important;
        border-color: var(--app-border) !important;
        font-size: 16px !important;
    }

    div[data-testid="stChatInput"] {
        background: color-mix(in srgb, var(--app-bg) 92%, transparent) !important;
        border-top: 1px solid var(--app-border);
        padding: 0.55rem 0.72rem max(0.55rem, env(safe-area-inset-bottom)) 0.72rem;
        backdrop-filter: blur(10px);
    }

    div[data-testid="stChatInput"] textarea {
        min-height: 46px !important;
        border-radius: 18px !important;
    }

    button[kind="primary"],
    div[data-testid="stFormSubmitButton"] button,
    div[data-testid="stButton"] button,
    div[data-testid="stDownloadButton"] button {
        min-height: 44px;
        border-radius: 14px !important;
        border: 1px solid var(--app-border) !important;
        color: var(--app-text) !important;
        font-weight: 650 !important;
    }

    /* Make buttons easier to tap on small screens. */
    @media (max-width: 640px) {
        div[data-testid="column"] {
            width: 100% !important;
            flex: 1 1 100% !important;
            min-width: 100% !important;
        }
        button {
            width: 100% !important;
        }
        .stTabs [data-baseweb="tab-list"] {
            gap: 4px;
            overflow-x: auto;
            white-space: nowrap;
        }
    }

    .stAlert {
        border-radius: 16px;
    }

    hr {
        border-color: var(--app-border) !important;
        margin: 0.9rem 0 !important;
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
    st.subheader("🔐 Admin Settings")
    st.caption("Chat AI bisa dipakai tanpa login. Login hanya untuk membuka setting, memory, debug, dan kontrol Bot Telegram.")

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


def render_admin_settings() -> None:
    st.subheader("⚙️ Admin Settings")
    st.success(f"Login sebagai: {admin_username}")

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
    st.caption("Chat publik aktif. Setting hanya untuk admin.")

    cfg = get_runtime_config()
    price = model_price(cfg["model"])
    st.markdown(f'<span class="status-pill">Model utama: {cfg["model"]}</span>', unsafe_allow_html=True)
    st.markdown(f'<span class="status-pill">Tier: {model_cost_tier(cfg["model"])} | Rp{price.get("input",0):,}/Rp{price.get("output",0):,}</span>'.replace(',', '.'), unsafe_allow_html=True)
    last_meta = st.session_state.get("last_answer_meta", {}) or {}
    if last_meta:
        last_model = last_meta.get("active_model_final") or last_meta.get("model_requested") or last_meta.get("model") or cfg["model"]
        exp_used = "ya" if last_meta.get("expensive_fallback_used") else "tidak"
        st.markdown(f'<span class="status-pill">Jawaban terakhir: {last_model}</span>', unsafe_allow_html=True)
        st.markdown(f'<span class="status-pill">Model mahal dipakai: {exp_used}</span>', unsafe_allow_html=True)
    st.markdown(
        f'<span class="status-pill">Telegram: {"ON" if service.status()["running"] else "OFF"}</span>',
        unsafe_allow_html=True,
    )

    st.divider()
    if st.session_state.admin_authenticated:
        render_admin_settings()
    else:
        render_admin_login()


# =========================
# Public Chat UI
# =========================
cfg = get_runtime_config()

st.markdown(
    f"""
    <div class="mobile-hero">
        <div class="mobile-hero-title">🤖 Adioranye AI</div>
        <p>Asisten pribadi yang siap membantu menjawab pertanyaan, menulis, merangkum, membuat ide, dan memberi solusi praktis.</p>
        <div class="status-row">
            <span class="status-pill">💬 Chat publik</span>
            <span class="status-pill">⚙️ Setting terkunci admin</span>
            <span class="status-pill">📱 Mobile friendly</span>
            <span class="status-pill">⚡ Fast accurate router</span>
            <span class="status-pill">🧠 Model: {cfg["model"]}</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

if not api_key:
    st.warning("SLASHAI_API_KEY belum diisi. Chat belum bisa digunakan sampai admin mengisi Secrets di Streamlit Cloud.")

# Quick prompt buttons only shown when chat is empty
if not st.session_state.chat_messages:
    st.markdown("**Pilih contoh cepat atau langsung ketik di bawah:**")
    prompt_examples = [
        "Buatkan caption promosi produk yang singkat dan menarik.",
        "Ringkas materi ini menjadi bahasa mahasiswa yang natural.",
        "Bantu susun jawaban presentasi agar terdengar percaya diri.",
        "Buatkan ide konten TikTok edukasi yang berpotensi ramai.",
    ]
    cols = st.columns(2)
    for idx, example in enumerate(prompt_examples):
        with cols[idx % 2]:
            if st.button(example, key=f"quick_prompt_{idx}", use_container_width=True):
                st.session_state.pending_prompt = example

# Chat toolbar
st.markdown('<div class="toolbar-card">', unsafe_allow_html=True)
col_toolbar_1, col_toolbar_2 = st.columns([1, 2])
with col_toolbar_1:
    if st.button("🧹 Chat baru", use_container_width=True):
        st.session_state.chat_messages = []
        st.session_state.pending_prompt = ""
        st.rerun()
with col_toolbar_2:
    st.caption(f"{len(st.session_state.chat_messages)} pesan • Model utama: {cfg['model']} • Router {'ON' if cfg['smart_model_router'] else 'OFF'} • Mahal jika perlu: {'ON' if cfg['allow_expensive_fallback'] else 'OFF'}")
st.markdown('</div>', unsafe_allow_html=True)

st.divider()

for msg in st.session_state.chat_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

typed_input = st.chat_input("Tulis pertanyaan kamu untuk Adioranye...")
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
                placeholder.markdown("⏳ Adioranye sedang menyusun jawaban...")
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
