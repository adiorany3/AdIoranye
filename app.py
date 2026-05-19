import hmac
import time
from typing import Any

import streamlit as st

from ai_core import DEFAULT_FALLBACK_MODELS, generate_answer
from memory_store import MemoryStore, handle_local_memory_command
from telegram_service import get_telegram_service


st.set_page_config(page_title="Adioranye AI + Telegram", page_icon="🤖", layout="wide")


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


def require_admin_login(admin_username: str, admin_password: str) -> None:
    """Stop rendering the dashboard until the admin enters the correct password."""
    if not admin_password:
        st.error("ADMIN_PASSWORD belum diisi di Streamlit Secrets.")
        st.info("Tambahkan ADMIN_PASSWORD di Settings → Secrets agar dashboard terlindungi.")
        st.stop()

    if st.session_state.get("admin_authenticated"):
        return

    st.title("🔐 Login Admin")
    st.write("Masukkan password admin untuk membuka dashboard, setting, memory, dan kontrol Bot Telegram.")

    with st.form("admin_login_form", clear_on_submit=False):
        username_input = st.text_input("Username", value="", placeholder="admin")
        password_input = st.text_input("Password Admin", type="password", placeholder="Masukkan password admin")
        submitted = st.form_submit_button("Masuk", use_container_width=True)

    if submitted:
        username_ok = safe_compare(username_input.strip(), admin_username)
        password_ok = safe_compare(password_input, admin_password)

        if username_ok and password_ok:
            st.session_state.admin_authenticated = True
            st.session_state.admin_username = admin_username
            st.rerun()
        else:
            st.error("Username atau password admin salah.")

    st.caption("Semua konfigurasi rahasia dibaca dari Streamlit Secrets/TOML.")
    st.stop()


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
]

api_key = get_secret("SLASHAI_API_KEY", "")
api_url = get_secret("SLASHAI_API_URL", "https://api.slashai.my.id/v1/chat/completions")
default_model = get_secret("SLASHAI_MODEL", "slashai/gpt-5-nano")
telegram_token = get_secret("TELEGRAM_BOT_TOKEN", "")
memory_file = get_secret("MEMORY_FILE", "assistant_memory.json")
persona_from_secret = get_secret("ASSISTANT_PERSONA", DEFAULT_PERSONA)
auto_start = parse_bool(get_secret("TELEGRAM_AUTO_START", False), default=False)
admin_username = str(get_secret("ADMIN_USERNAME", "admin"))
admin_password = str(get_secret("ADMIN_PASSWORD", "Admin"))

require_admin_login(admin_username, admin_password)

memory = MemoryStore(memory_file)

if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []

if "persona" not in st.session_state:
    st.session_state.persona = persona_from_secret


with st.sidebar:
    st.title("⚙️ Adioranye")
    st.success(f"Login sebagai: {st.session_state.get('admin_username', 'admin')}")
    if st.button("🚪 Logout Admin", use_container_width=True):
        st.session_state.admin_authenticated = False
        st.session_state.admin_username = ""
        st.rerun()

    st.subheader("Model AI")
    selected_model = st.selectbox(
        "Model default",
        MODEL_OPTIONS,
        index=MODEL_OPTIONS.index(default_model) if default_model in MODEL_OPTIONS else 0,
    )

    temperature = st.slider("Temperature", 0.0, 1.0, 0.3, 0.1)
    max_completion_tokens = st.slider("Max output tokens", 600, 4000, 1800, 100)

    st.subheader("Persona")
    st.session_state.persona = st.text_area("System persona", value=st.session_state.persona, height=140)

    st.subheader("Telegram Bot")
    service = get_telegram_service()
    status = service.status()

    if telegram_token:
        st.success("TELEGRAM_BOT_TOKEN terdeteksi.")
    else:
        st.error("TELEGRAM_BOT_TOKEN belum diisi di Streamlit Secrets.")

    if api_key:
        st.success("SLASHAI_API_KEY terdeteksi.")
    else:
        st.error("SLASHAI_API_KEY belum diisi di Streamlit Secrets.")

    st.write("Status:", "🟢 Berjalan" if status["running"] else "🔴 Mati")
    st.caption(f"Processed: {status['processed']}")
    if status.get("started_at"):
        st.caption(f"Started: {status['started_at']}")

    col_start, col_stop = st.columns(2)

    bot_config = {
        "telegram_token": telegram_token,
        "slashai_api_key": api_key,
        "slashai_api_url": api_url,
        "slashai_model": selected_model,
        "persona": st.session_state.persona,
        "memory_file": memory_file,
        "fallback_models": DEFAULT_FALLBACK_MODELS,
        "temperature": temperature,
        "max_completion_tokens": max_completion_tokens,
        "timeout": 60,
    }

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
        st.caption("Update terakhir:")
        st.code(status["last_update"])

    if status.get("last_error"):
        st.caption("Error terakhir:")
        st.code(status["last_error"][:1500])

    st.divider()

    if st.button("🧪 Tes AI", use_container_width=True):
        try:
            answer, meta = generate_answer(
                api_url=api_url,
                api_key=api_key,
                model=selected_model,
                system_prompt=st.session_state.persona,
                user_text="Jawab singkat: apakah kamu aktif?",
                memory_text=memory.as_prompt_text(limit=10),
                recent_messages=[],
                fallback_models=DEFAULT_FALLBACK_MODELS,
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
                timeout=60,
            )
            st.success(answer)
            st.caption(f"Model: {meta.get('model') or meta.get('model_requested')}")
        except Exception as exc:
            st.error(str(exc))


if auto_start:
    service = get_telegram_service()
    if not service.status()["running"] and telegram_token and api_key:
        service.start({
            "telegram_token": telegram_token,
            "slashai_api_key": api_key,
            "slashai_api_url": api_url,
            "slashai_model": selected_model,
            "persona": st.session_state.persona,
            "memory_file": memory_file,
            "fallback_models": DEFAULT_FALLBACK_MODELS,
            "temperature": temperature,
            "max_completion_tokens": max_completion_tokens,
            "timeout": 60,
        })


st.title("🤖 Adioranye AI")
st.write("Dashboard Streamlit Online + Bot Telegram dalam satu aplikasi.")

tab_chat, tab_memory, tab_setup = st.tabs(["Chat Test", "Memory", "Setup Streamlit Online"])


with tab_chat:
    st.subheader("Tes Chat di Streamlit")

    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_input = st.chat_input("Tulis pertanyaan untuk Adioranye...")

    if user_input:
        with st.chat_message("user"):
            st.markdown(user_input)

        st.session_state.chat_messages.append({"role": "user", "content": user_input})
        local_reply = handle_local_memory_command(user_input, memory)

        if local_reply:
            answer = local_reply
            meta = {}
        else:
            try:
                with st.spinner("Memproses jawaban..."):
                    answer, meta = generate_answer(
                        api_url=api_url,
                        api_key=api_key,
                        model=selected_model,
                        system_prompt=st.session_state.persona,
                        user_text=user_input,
                        memory_text=memory.as_prompt_text(limit=20),
                        recent_messages=st.session_state.chat_messages[-8:],
                        fallback_models=DEFAULT_FALLBACK_MODELS,
                        temperature=temperature,
                        max_completion_tokens=max_completion_tokens,
                        timeout=60,
                    )
            except Exception as exc:
                answer = "Maaf, AI belum bisa menjawab.\n\nDetail:\n" + str(exc)
                meta = {}

        with st.chat_message("assistant"):
            st.markdown(answer)

        st.session_state.chat_messages.append({"role": "assistant", "content": answer})

        if meta:
            with st.expander("Debug response"):
                st.json(meta)

    col_clear, col_remember = st.columns(2)

    with col_clear:
        if st.button("Hapus chat test"):
            st.session_state.chat_messages = []
            st.rerun()

    with col_remember:
        if st.button("Simpan info: pengguna memakai Streamlit Online"):
            memory.add("Pengguna menjalankan Adioranye di Streamlit Online.", source="button")
            st.success("Memori disimpan.")


with tab_memory:
    st.subheader("Memory Lokal")
    st.info(
        "Di Streamlit Online, file memory bisa hilang saat app restart/redeploy. "
        "Untuk memory permanen 24 jam, gunakan database eksternal seperti Supabase/Firebase."
    )

    current_memory = memory.list_text(limit=50)
    if current_memory:
        st.code(current_memory)
    else:
        st.write("Belum ada memori.")

    new_memory = st.text_input("Tambah memori")
    if st.button("Simpan memori"):
        if new_memory.strip():
            memory.add(new_memory.strip(), source="streamlit")
            st.success("Memori disimpan.")
            st.rerun()

    forget_keyword = st.text_input("Hapus memori yang mengandung kata")
    if st.button("Hapus berdasarkan kata"):
        count = memory.forget_contains(forget_keyword)
        st.warning(f"{count} memori dihapus.")
        st.rerun()

    if st.button("Reset semua memori"):
        memory.reset()
        st.warning("Semua memori dihapus.")
        st.rerun()


with tab_setup:
    st.subheader("Secrets untuk Streamlit Community Cloud")
    st.write("Masukkan ini di menu:")
    st.code("Streamlit Cloud → App → Settings → Secrets", language="text")

    secrets_text = '''
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "Admin"

TELEGRAM_BOT_TOKEN = "ISI_TOKEN_BOT_DARI_BOTFATHER"
SLASHAI_API_KEY = "ISI_API_KEY_SLASHAI_KAMU"
SLASHAI_API_URL = "https://api.slashai.my.id/v1/chat/completions"
SLASHAI_MODEL = "slashai/gpt-5-nano"

ASSISTANT_PERSONA = "Nama kamu adalah adioranye. Kamu adalah asisten pribadi yang pintar, cepat, ramah, dan dapat membantu menjawab berbagai pertanyaan pengguna. Jawab dalam bahasa Indonesia yang natural, jelas, praktis, dan tidak bertele-tele."
MEMORY_FILE = "assistant_memory.json"

# true = bot otomatis start saat aplikasi Streamlit Online dibuka
TELEGRAM_AUTO_START = true
    '''.strip()

    st.code(secrets_text, language="toml")

    st.subheader("Cara pakai")
    st.markdown(
        '''
1. Upload project ini ke GitHub.
2. Deploy ke Streamlit Community Cloud.
3. Isi semua Secrets di atas.
4. Buka aplikasi Streamlit kamu.
5. Klik **Start Bot** di sidebar, atau pakai `TELEGRAM_AUTO_START = true`.
6. Buka Telegram, chat bot kamu dengan `/start`.

**Catatan penting:** Streamlit Online bisa tidur ketika tidak ada pengunjung. Jika app tidur, bot Telegram ikut berhenti. Untuk 24 jam benar-benar nonstop, tetap lebih stabil memakai VPS.
        '''.strip()
    )

    st.subheader("Troubleshooting")
    st.markdown(
        '''
- Jika bot tidak membalas, klik **Tes AI** dulu.
- Jika Telegram error `Conflict`, berarti token bot yang sama sedang polling di tempat lain. Matikan proses bot lama.
- Jika model 403, model tersebut butuh deposit atau belum terbuka di akun API.
- Jika jawaban kosong, gunakan `slashai/gpt-5-nano` atau naikkan Max output tokens.
        '''.strip()
    )

time.sleep(0.05)
