import json
from typing import Dict, List, Optional, Tuple

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
# Catatan:
# - Daftar ini berasal dari daftar model yang kamu berikan.
# - Tidak semua model pasti bisa dipakai di akun kamu.
# - Jika API mengembalikan 403 access_denied/deposit required, berarti model itu terkunci oleh provider.
MODEL_GROUPS: Dict[str, List[str]] = {
    "Rekomendasi Hemat / Coba Dulu": [
        "slashai/deepseek-v4-flash",
        "slashai/gpt-5-nano",
        "slashai/gpt-5.4-nano",
        "slashai/gpt-5-mini",
        "slashai/gemini-3-flash",
        "slashai/mimo-v2-flash",
        "slashai/Step-3.5-Flash",
    ],
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

ALL_MODELS = []
for models in MODEL_GROUPS.values():
    for model_name in models:
        if model_name not in ALL_MODELS:
            ALL_MODELS.append(model_name)

# Urutan cadangan ketika model utama kena 403/access_denied.
# Silakan ubah dari sidebar jika ada model lain yang terbukti aktif di akun kamu.
DEFAULT_FALLBACK_MODELS = [
    "slashai/deepseek-v4-flash",
    "slashai/gpt-5-nano",
    "slashai/gpt-5.4-nano",
    "slashai/gpt-5-mini",
    "slashai/gemini-3-flash",
    "slashai/mimo-v2-flash",
    "slashai/Step-3.5-Flash",
]


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
DEFAULT_MODEL = get_secret("SLASHAI_MODEL", "slashai/deepseek-v4-flash")


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
def is_access_denied_error(status_code: int, text: str) -> bool:
    """Deteksi error model terkunci/deposit required dari provider."""
    lower_text = text.lower()
    return (
        status_code == 403
        or "access_denied" in lower_text
        or "deposit required" in lower_text
        or "premium models" in lower_text
    )


def request_ai_once(
    messages: List[Dict[str, str]],
    model: str,
    temperature: float,
    max_tokens: int,
) -> Tuple[str, str]:
    """Kirim request sekali ke model tertentu. Return: (jawaban, model_yang_dipakai)."""
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
        short_text = response.text[:1500]
        if is_access_denied_error(response.status_code, short_text):
            raise PermissionError(
                f"Model terkunci oleh provider: {model}\n\n"
                f"Detail API: status {response.status_code} - {short_text}"
            )
        raise RuntimeError(
            f"API mengembalikan status {response.status_code}: {short_text}"
        )

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Respons API bukan JSON valid: {response.text[:1000]}") from exc

    try:
        return data["choices"][0]["message"]["content"], model
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Format respons API tidak sesuai: {data}") from exc


def ask_ai(
    messages: List[Dict[str, str]],
    primary_model: str,
    fallback_models: List[str],
    use_fallback: bool,
    temperature: float,
    max_tokens: int,
) -> Tuple[str, str, List[str]]:
    """
    Coba model utama. Jika kena 403/access_denied, coba model cadangan.
    Return: (jawaban, model_yang_berhasil, daftar_model_gagal)
    """
    models_to_try = [primary_model]

    if use_fallback:
        for fallback_model in fallback_models:
            fallback_model = fallback_model.strip()
            if fallback_model and fallback_model not in models_to_try:
                models_to_try.append(fallback_model)

    failed_locked_models: List[str] = []
    last_error: Optional[Exception] = None

    for model_name in models_to_try:
        try:
            answer, used_model = request_ai_once(
                messages=messages,
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return answer, used_model, failed_locked_models
        except PermissionError as exc:
            failed_locked_models.append(model_name)
            last_error = exc
            continue
        except Exception as exc:
            # Error selain akses ditolak biasanya bukan masalah model premium,
            # jadi langsung tampilkan agar mudah diperbaiki.
            raise exc

    failed_text = "\n".join([f"- {m}" for m in failed_locked_models])
    raise RuntimeError(
        "Semua model yang dicoba masih ditolak oleh provider.\n\n"
        "Model yang terkunci/ditolak:\n"
        f"{failed_text}\n\n"
        "Solusi: pilih model lain yang aktif di akun kamu, atau lakukan deposit/top up di provider SlashAI "
        "jika ingin memakai model premium.\n\n"
        f"Error terakhir: {last_error}"
    )


def find_default_group(default_model: str) -> str:
    """Cari kategori model default."""
    for group_name, models in MODEL_GROUPS.items():
        if default_model in models:
            return group_name
    return "Rekomendasi Hemat / Coba Dulu"


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

    st.info(
        "Jika muncul 403 deposit required, berarti model itu premium/terkunci di akun kamu. "
        "Gunakan kategori Rekomendasi Hemat atau aktifkan Auto Fallback."
    )

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
        "Model Utama",
        options=model_options,
        index=default_index,
        help="Model dikirim pada field 'model' di request API.",
    )

    use_custom_model = st.toggle("Gunakan model custom", value=False)
    if use_custom_model:
        model = st.text_input(
            "Model Custom",
            value=selected_model,
            placeholder="contoh: slashai/deepseek-v4-flash",
        ).strip()
    else:
        model = selected_model

    use_fallback = st.toggle(
        "Auto coba model cadangan jika akses ditolak",
        value=True,
        help="Jika model utama kena 403/access_denied, aplikasi otomatis mencoba model pada daftar cadangan.",
    )

    fallback_text = st.text_area(
        "Daftar Model Cadangan",
        value="\n".join(DEFAULT_FALLBACK_MODELS),
        height=150,
        help="Satu model per baris. Urutan paling atas akan dicoba lebih dulu.",
    )
    fallback_models = [line.strip() for line in fallback_text.splitlines() if line.strip()]

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
    st.write(f"Model utama: `{model}`")
    st.write(f"Auto fallback: `{'Aktif' if use_fallback else 'Nonaktif'}`")
    if use_fallback:
        st.write("Model cadangan:")
        st.code("\n".join(fallback_models), language="text")

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
                answer, used_model, failed_locked_models = ask_ai(
                    messages=api_messages,
                    primary_model=model,
                    fallback_models=fallback_models,
                    use_fallback=use_fallback,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if failed_locked_models:
                    st.warning(
                        "Model utama/cadangan sempat ditolak, lalu berhasil memakai: "
                        f"`{used_model}`"
                    )
                    with st.expander("Lihat model yang ditolak"):
                        st.code("\n".join(failed_locked_models), language="text")

                st.markdown(answer)
                st.caption(f"Model yang menjawab: `{used_model}`")
                st.session_state.messages.append(
                    {"role": "assistant", "content": answer}
                )
            except Exception as err:
                st.error(str(err))
