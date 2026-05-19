import hmac
import time
from typing import Any, Dict

import streamlit as st

from ai_core import DEFAULT_FALLBACK_MODELS, generate_answer
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
        st.session_state.active_max_tokens = int(get_secret("MAX_COMPLETION_TOKENS", 2200) or 2200)
    if "show_debug" not in st.session_state:
        st.session_state.show_debug = False


# =========================
# Defaults
# =========================

DEFAULT_PERSONA = (
    "Nama kamu adalah adioranye. "
    "Kamu adalah asisten pribadi yang pintar, cepat, ramah, dan dapat membantu menjawab berbagai pertanyaan pengguna. "
    "Jawab dalam bahasa Indonesia yang natural, jelas, praktis, dan tidak bertele-tele."
)

MODEL_OPTIONS = [
    "slashai/gpt-5-nano",
    "slashai/gpt-5-mini",
    "slashai/gemini-3-flash",
    "slashai/mimo-v2-flash",
    "slashai/Step-3.5-Flash",
    "slashai/MiniMax-M2.5",
    "slashai/gpt-5.4-nano",
    "slashai/gpt-5.5-instant",
    "slashai/claude-haiku-4.5",
    "slashai/deepseek-v4-flash",
]

CHEAP_MODEL_OPTIONS = [
    "slashai/gpt-5-nano",
    "slashai/gpt-5-mini",
    "slashai/gemini-3-flash",
    "slashai/mimo-v2-flash",
    "slashai/Step-3.5-Flash",
    "slashai/MiniMax-M2.5",
    "slashai/gpt-5.4-nano",
    "slashai/gpt-5.5-instant",
]

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
telegram_lock_file = str(get_secret("TELEGRAM_LOCK_FILE", ".telegram_bot_worker.lock"))
admin_username = str(get_secret("ADMIN_USERNAME", "admin"))
admin_password = str(get_secret("ADMIN_PASSWORD", "Admin"))

init_state()
memory = MemoryStore(memory_file)
service = get_telegram_service()


# =========================
# Eye-friendly adaptive styling
# =========================
st.markdown(
    """
    <style>
    :root {
        --app-bg: #f7f8fb;
        --app-surface: #ffffff;
        --app-surface-soft: #f1f4f8;
        --app-text: #111827;
        --app-muted: #4b5563;
        --app-border: rgba(17, 24, 39, 0.14);
        --app-primary: #2563eb;
        --app-primary-soft: rgba(37, 99, 235, 0.10);
        --app-shadow: 0 12px 30px rgba(15, 23, 42, 0.08);
        --user-bubble: #e8f0ff;
        --assistant-bubble: #ffffff;
        --input-bg: #ffffff;
    }

    @media (prefers-color-scheme: dark) {
        :root {
            --app-bg: #0f172a;
            --app-surface: #111827;
            --app-surface-soft: #1f2937;
            --app-text: #f8fafc;
            --app-muted: #cbd5e1;
            --app-border: rgba(226, 232, 240, 0.16);
            --app-primary: #93c5fd;
            --app-primary-soft: rgba(147, 197, 253, 0.14);
            --app-shadow: 0 12px 30px rgba(0, 0, 0, 0.35);
            --user-bubble: #1e3a5f;
            --assistant-bubble: #111827;
            --input-bg: #111827;
        }
    }

    html, body, .stApp {
        background: var(--app-bg) !important;
        color: var(--app-text) !important;
    }

    .main .block-container {
        max-width: 940px;
        padding-top: 1.2rem;
        padding-bottom: 4.5rem;
    }

    div[data-testid="stSidebar"] {
        min-width: 330px;
        background: var(--app-surface) !important;
        border-right: 1px solid var(--app-border);
    }

    div[data-testid="stSidebar"] * {
        color: var(--app-text);
    }

    .chat-hero {
        border: 1px solid var(--app-border);
        border-radius: 24px;
        padding: 22px 24px;
        margin-bottom: 18px;
        background:
            radial-gradient(circle at top left, var(--app-primary-soft), transparent 34%),
            var(--app-surface);
        color: var(--app-text);
        box-shadow: var(--app-shadow);
    }

    .chat-hero h1 {
        font-size: 2.05rem;
        margin-bottom: 0.25rem;
        color: var(--app-text);
        letter-spacing: -0.02em;
    }

    .chat-hero p {
        margin-bottom: 0;
        color: var(--app-muted);
        line-height: 1.55;
    }

    .status-pill {
        display: inline-block;
        padding: 6px 11px;
        border-radius: 999px;
        background: var(--app-primary-soft);
        border: 1px solid var(--app-border);
        color: var(--app-text) !important;
        font-size: 0.85rem;
        margin-right: 6px;
        margin-bottom: 6px;
    }

    .quick-card {
        border: 1px solid var(--app-border);
        border-radius: 18px;
        padding: 14px 15px;
        min-height: 90px;
        background: var(--app-surface);
        color: var(--app-text);
        box-shadow: 0 8px 20px rgba(15, 23, 42, 0.05);
        line-height: 1.45;
    }

    .small-note,
    .stCaptionContainer,
    div[data-testid="stCaptionContainer"] {
        color: var(--app-muted) !important;
        font-size: 0.88rem;
    }

    /* Chat bubbles: keep enough contrast in both Streamlit light and dark theme. */
    div[data-testid="stChatMessage"] {
        border: 1px solid var(--app-border);
        border-radius: 18px;
        padding: 0.35rem 0.5rem;
        margin-bottom: 0.75rem;
        background: var(--assistant-bubble);
        color: var(--app-text) !important;
        box-shadow: 0 6px 18px rgba(15, 23, 42, 0.04);
    }

    div[data-testid="stChatMessage"] * {
        color: var(--app-text) !important;
    }

    div[data-testid="stChatMessage"] p,
    div[data-testid="stMarkdownContainer"] p,
    div[data-testid="stMarkdownContainer"] li {
        line-height: 1.62;
    }

    textarea,
    input,
    div[data-baseweb="input"] input,
    div[data-baseweb="textarea"] textarea {
        background: var(--input-bg) !important;
        color: var(--app-text) !important;
        border-color: var(--app-border) !important;
    }

    div[data-testid="stChatInput"] {
        background: var(--app-bg) !important;
        border-top: 1px solid var(--app-border);
    }

    button[kind="primary"],
    div[data-testid="stFormSubmitButton"] button,
    div[data-testid="stButton"] button {
        border-radius: 12px !important;
        border: 1px solid var(--app-border) !important;
        color: var(--app-text) !important;
    }

    .stAlert {
        border-radius: 16px;
    }

    hr {
        border-color: var(--app-border) !important;
    }

    code, pre {
        border-radius: 12px !important;
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
                "fallback_models": DEFAULT_FALLBACK_MODELS,
                "temperature": cfg["temperature"],
                "max_completion_tokens": cfg["max_completion_tokens"],
                "timeout": 60,
                "drop_pending_updates": drop_pending_updates,
                "send_processing_message": send_processing_message,
                "lock_file": telegram_lock_file,
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
        model_list = CHEAP_MODEL_OPTIONS if st.toggle("Tampilkan model hemat saja", value=True) else MODEL_OPTIONS
        current_model = st.session_state.active_model if st.session_state.active_model in model_list else default_model
        if current_model not in model_list:
            current_model = model_list[0]

        st.session_state.active_model = st.selectbox(
            "Model aktif",
            model_list,
            index=model_list.index(current_model),
        )
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
                        memory_text=memory.as_prompt_text(limit=10),
                        recent_messages=[],
                        fallback_models=DEFAULT_FALLBACK_MODELS,
                        temperature=float(st.session_state.active_temperature),
                        max_completion_tokens=int(st.session_state.active_max_tokens),
                        timeout=60,
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
                st.session_state.active_max_tokens = 2200
                st.session_state.show_debug = False
                st.rerun()

    with tab_bot:
        st.markdown("#### Kontrol Bot Telegram")
        format_token_status("TELEGRAM_BOT_TOKEN", telegram_token)
        format_token_status("SLASHAI_API_KEY", api_key)
        st.info("Mode single-worker aktif: aplikasi mencegah bot berjalan dobel. Pesan 'Sedang diproses' juga dimatikan agar Telegram hanya mengirim jawaban akhir.")

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
            "fallback_models": DEFAULT_FALLBACK_MODELS,
            "temperature": float(st.session_state.active_temperature),
            "max_completion_tokens": int(st.session_state.active_max_tokens),
            "timeout": 60,
            "drop_pending_updates": drop_pending_updates,
            "send_processing_message": send_processing_message,
            "lock_file": telegram_lock_file,
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
                st.warning("Bot Telegram dihentikan.")

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

ASSISTANT_PERSONA = "Nama kamu adalah adioranye. Kamu adalah asisten pribadi yang pintar, cepat, ramah, dan dapat membantu menjawab berbagai pertanyaan pengguna. Jawab dalam bahasa Indonesia yang natural, jelas, praktis, dan tidak bertele-tele."
MEMORY_FILE = "assistant_memory.json"

# true = bot Telegram otomatis start saat app Streamlit dibuka/aktif
TELEGRAM_AUTO_START = true
TELEGRAM_DROP_PENDING_UPDATES = true
TELEGRAM_SEND_PROCESSING_MESSAGE = false
TELEGRAM_LOCK_FILE = ".telegram_bot_worker.lock"

# Opsional
TEMPERATURE = 0.3
MAX_COMPLETION_TOKENS = 2200''',
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
    st.markdown(f'<span class="status-pill">Model: {cfg["model"]}</span>', unsafe_allow_html=True)
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
    """
    <div class="chat-hero">
        <h1>🤖 Adioranye AI</h1>
        <p>Asisten pribadi pintar untuk menjawab pertanyaan, membantu menulis, merangkum, membuat ide, dan memberi solusi praktis.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

if not api_key:
    st.warning("SLASHAI_API_KEY belum diisi. Chat belum bisa digunakan sampai admin mengisi Secrets di Streamlit Cloud.")

# Quick prompt cards only shown when chat is empty
if not st.session_state.chat_messages:
    st.markdown("**Contoh yang bisa ditanyakan:**")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown('<div class="quick-card">Buatkan caption promosi produk yang singkat dan menarik.</div>', unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="quick-card">Ringkas materi ini menjadi bahasa mahasiswa yang natural.</div>', unsafe_allow_html=True)
    with c3:
        st.markdown('<div class="quick-card">Bantu susun jawaban presentasi agar terdengar percaya diri.</div>', unsafe_allow_html=True)
    st.caption("Ketik pertanyaan di kolom chat paling bawah.")

# Chat toolbar
col_toolbar_1, col_toolbar_2, col_toolbar_3 = st.columns([1, 1, 3])
with col_toolbar_1:
    if st.button("🧹 Chat baru", use_container_width=True):
        st.session_state.chat_messages = []
        st.rerun()
with col_toolbar_2:
    st.caption(f"{len(st.session_state.chat_messages)} pesan")
with col_toolbar_3:
    st.caption("Memory aktif sebagai konteks ringkas. Pengaturan hanya bisa diubah admin.")

st.divider()

for msg in st.session_state.chat_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

user_input = st.chat_input("Tulis pertanyaan kamu untuk Adioranye...")

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
    else:
        try:
            with st.chat_message("assistant"):
                placeholder = st.empty()
                placeholder.markdown("Sedang memproses jawaban...")
                answer, meta = generate_answer(
                    api_url=api_url,
                    api_key=api_key,
                    model=cfg["model"],
                    system_prompt=cfg["persona"],
                    user_text=user_input,
                    memory_text=memory.as_prompt_text(limit=20),
                    recent_messages=st.session_state.chat_messages[-8:],
                    fallback_models=DEFAULT_FALLBACK_MODELS,
                    temperature=float(cfg["temperature"]),
                    max_completion_tokens=int(cfg["max_completion_tokens"]),
                    timeout=60,
                )
                placeholder.markdown(answer)
        except Exception as exc:
            answer = (
                "Maaf, Adioranye belum bisa menjawab saat ini. "
                "Silakan coba lagi beberapa saat lagi atau hubungi admin.\n\n"
                f"Detail ringkas: {str(exc)[:1000]}"
            )
            meta = {}
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
