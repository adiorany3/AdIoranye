import json
from typing import Dict, List, Optional

import requests
import streamlit as st


# =========================
# KONFIGURASI HALAMAN
# =========================
st.set_page_config(
    page_title="Asisten Pribadi AI",
    page_icon="🤖",
    layout="centered",
)


# =========================
# DAFTAR MODEL SLASHAI
# =========================
MODEL_GROUPS: Dict[str, List[str]] = {
    "Claude": [
        "slashai/claude-haiku-4.5",
        "slashai/claude-opus-4.5",
        "slashai/claude-opus-4.6",
        "slashai/claude-opus-4.7",
        "slashai/claude-sonnet-4.5",
        "slashai/claude-sonnet-4.6",
        "slashai/claude-sonnet-4.7",
    ],
    "GPT / Codex": [
        "slashai/gpt-5-codex",
        "slashai/gpt-5-codex-mini",
        "slashai/gpt-5-codex-mini-review",
        "slashai/gpt-5-codex-review",
        "slashai/gpt-5-mini",
        "slashai/gpt-5-nano",
        "slashai/gpt-5.1",
        "slashai/gpt-5.1-codex",
        "slashai/gpt-5.1-codex-max",
        "slashai/gpt-5.1-codex-max-review",
        "slashai/gpt-5.1-codex-mini",
        "slashai/gpt-5.1-codex-mini-high",
        "slashai/gpt-5.1-codex-mini-high-review",
        "slashai/gpt-5.1-codex-mini-review",
        "slashai/gpt-5.1-codex-review",
        "slashai/gpt-5.1-review",
        "slashai/gpt-5.2",
        "slashai/gpt-5.2-codex",
        "slashai/gpt-5.2-codex-review",
        "slashai/gpt-5.2-review",
        "slashai/gpt-5.3-codex",
        "slashai/gpt-5.3-codex-high",
        "slashai/gpt-5.3-codex-high-review",
        "slashai/gpt-5.3-codex-low",
        "slashai/gpt-5.3-codex-low-review",
        "slashai/gpt-5.3-codex-none",
        "slashai/gpt-5.3-codex-none-review",
        "slashai/gpt-5.3-codex-review",
        "slashai/gpt-5.3-codex-spark",
        "slashai/gpt-5.3-codex-spark-review",
        "slashai/gpt-5.3-codex-xhigh",
        "slashai/gpt-5.3-codex-xhigh-review",
        "slashai/gpt-5.4",
        "slashai/gpt-5.4-mini",
        "slashai/gpt-5.4-nano",
        "slashai/gpt-5.4-pro",
        "slashai/gpt-5.4-review",
        "slashai/gpt-5.5",
        "slashai/gpt-5.5-instant",
        "slashai/gpt-5.5-review",
    ],
    "DeepSeek": [
        "slashai/deepseek-3.2",
        "slashai/deepseek-v3.2",
        "slashai/deepseek-v4-flash",
        "slashai/deepseek-v4-pro",
    ],
    "Gemini": [
        "slashai/gemini-3-flash",
        "slashai/gemini-3.1-pro",
    ],
    "Kimi": [
        "slashai/Kimi-K2.5",
        "slashai/Kimi-K2.6",
    ],
    "Qwen": [
        "slashai/qwen3-coder-next",
        "slashai/Qwen3.6-Max-Preview",
        "slashai/Qwen3.6-Plus",
    ],
    "GLM": [
        "slashai/GLM-5",
        "slashai/GLM-5.1",
    ],
    "MiniMax": [
        "slashai/MiniMax-M2.5",
        "slashai/MiniMax-M2.7",
    ],
    "MiMo": [
        "slashai/mimo-v2-flash",
        "slashai/mimo-v2-omni",
        "slashai/mimo-v2-pro",
        "slashai/mimo-v2.5",
        "slashai/mimo-v2.5-pro",
    ],
    "Step": [
        "slashai/Step-3.5-Flash",
    ],
}

ALL_MODELS = [model for models in MODEL_GROUPS.values() for model in models]


# =========================
# AMBIL SECRETS DARI STREAMLIT
# =========================
def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    """Ambil secret dengan aman dari st.secrets."""
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


API_KEY = get_secret("SLASHAI_API_KEY")
API_URL = get_secret("SLASHAI_API_URL", "https://api.slashai.my.id/v1/chat/completions")
DEFAULT_MODEL = get_secret("SLASHAI_MODEL", "slashai/gpt-5.5-instant")


# =========================
# PROMPT DASAR ASISTEN
# =========================
DEFAULT_SYSTEM_PROMPT = """
Kamu adalah asisten pribadi AI yang ramah, jelas, dan membantu.
Jawab dalam bahasa Indonesia kecuali pengguna meminta bahasa lain.
Berikan jawaban yang mudah dipahami, praktis, dan langsung ke inti masalah.
Jika pengguna meminta langkah teknis, berikan langkah bertahap dari awal.
Jika informasi belum cukup, buat asumsi terbaik dan jelaskan secara singkat.
""".strip()


# =========================
# FUNGSI REQUEST KE API OPENAI-COMPATIBLE
# =========================
def ask_ai(
    messages: List[Dict[str, str]],
    model: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Kirim percakapan ke endpoint OpenAI-compatible dan kembalikan jawaban AI."""
    if not API_KEY:
        raise RuntimeError(
            "API key belum diatur. Tambahkan SLASHAI_API_KEY di Streamlit Secrets."
        )

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    try:
        response = requests.post(
            API_URL,
            headers=headers,
            json=payload,
            timeout=120,
        )
    except requests.exceptions.Timeout as exc:
        raise RuntimeError(
            "Request timeout. Coba ulangi pertanyaan atau kecilkan Max Tokens."
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Gagal menghubungi API: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"API mengembalikan status {response.status_code}: {response.text[:1000]}"
        )

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Respons API bukan JSON valid: {response.text[:1000]}") from exc

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Format respons API tidak sesuai: {data}") from exc


def find_default_group(default_model: str) -> str:
    """Cari kategori model default."""
    for group_name, models in MODEL_GROUPS.items():
        if default_model in models:
            return group_name
    return "GPT / Codex"


# =========================
# SESSION STATE
# =========================
if "messages" not in st.session_state:
    st.session_state.messages = []

if "system_prompt" not in st.session_state:
    st.session_state.system_prompt = DEFAULT_SYSTEM_PROMPT


# =========================
# SIDEBAR
# =========================
with st.sidebar:
    st.title("⚙️ Pengaturan")

    default_group = find_default_group(DEFAULT_MODEL)
    group_names = list(MODEL_GROUPS.keys())
    selected_group = st.selectbox(
        "Kategori Model",
        options=group_names,
        index=group_names.index(default_group),
    )

    model_options = MODEL_GROUPS[selected_group]
    default_index = model_options.index(DEFAULT_MODEL) if DEFAULT_MODEL in model_options else 0

    selected_model = st.selectbox(
        "Model",
        options=model_options,
        index=default_index,
        help="Model dikirim pada field 'model' di request API, contoh: slashai/gpt-5.5-instant.",
    )

    use_custom_model = st.toggle("Gunakan model custom", value=False)
    if use_custom_model:
        model = st.text_input(
            "Model Custom",
            value=selected_model,
            placeholder="contoh: slashai/gpt-5.5-instant",
        ).strip()
    else:
        model = selected_model

    temperature = st.slider("Temperature", 0.0, 2.0, 0.7, 0.1)
    max_tokens = st.slider("Max Tokens", 128, 8192, 2048, 128)

    st.session_state.system_prompt = st.text_area(
        "Instruksi Asisten",
        value=st.session_state.system_prompt,
        height=180,
    )

    if st.button("🧹 Hapus Riwayat Chat"):
        st.session_state.messages = []
        st.rerun()

    st.divider()
    st.caption("Endpoint API")
    st.code(API_URL or "Belum diatur", language="text")

    if API_KEY:
        st.success("API key sudah terbaca dari Secrets.")
    else:
        st.error("API key belum ditemukan di Secrets.")


# =========================
# UI UTAMA
# =========================
st.title("🤖 Asisten Pribadi AI")
st.caption("Dibuat dengan Streamlit + API kompatibel OpenAI/Chat Completions")

with st.expander("ℹ️ Model aktif"):
    st.write(f"Model yang dipakai sekarang: `{model}`")

# Pesan awal
if not st.session_state.messages:
    with st.chat_message("assistant"):
        st.write("Halo! Saya siap membantu. Silakan tulis pertanyaan kamu.")

# Tampilkan histori percakapan
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Input pengguna
user_prompt = st.chat_input("Tulis pertanyaan di sini...")

if user_prompt:
    st.session_state.messages.append({"role": "user", "content": user_prompt})

    with st.chat_message("user"):
        st.markdown(user_prompt)

    api_messages = [
        {"role": "system", "content": st.session_state.system_prompt},
        *st.session_state.messages,
    ]

    with st.chat_message("assistant"):
        with st.spinner("Sedang menjawab..."):
            try:
                answer = ask_ai(
                    messages=api_messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                st.markdown(answer)
                st.session_state.messages.append(
                    {"role": "assistant", "content": answer}
                )
            except Exception as err:
                st.error(str(err))
