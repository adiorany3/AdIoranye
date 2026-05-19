import json
from typing import Dict, Generator, List, Optional, Tuple

import requests
import streamlit as st


# =====================================================
# KONFIGURASI HALAMAN
# =====================================================
st.set_page_config(
    page_title="Asisten Pribadi AI Cepat",
    page_icon="⚡",
    layout="centered",
)


# =====================================================
# MODEL PRIORITAS CEPAT
# =====================================================
FAST_MODELS: List[str] = [
    "slashai/deepseek-v4-flash",
    "slashai/gemini-3-flash",
    "slashai/mimo-v2-flash",
    "slashai/Step-3.5-Flash",
    "slashai/gpt-5-nano",
    "slashai/gpt-5.4-nano",
    "slashai/gpt-5-mini",
]

MODEL_GROUPS: Dict[str, List[str]] = {
    "Cepat & Hemat": FAST_MODELS,
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
    "Claude": [
        "slashai/claude-haiku-4.5",
        "slashai/claude-opus-4.5",
        "slashai/claude-opus-4.6",
        "slashai/claude-opus-4.7",
        "slashai/claude-sonnet-4.5",
        "slashai/claude-sonnet-4.6",
        "slashai/claude-sonnet-4.7",
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
    "Lainnya": [
        "slashai/Kimi-K2.5",
        "slashai/Kimi-K2.6",
        "slashai/qwen3-coder-next",
        "slashai/Qwen3.6-Max-Preview",
        "slashai/Qwen3.6-Plus",
        "slashai/GLM-5",
        "slashai/GLM-5.1",
        "slashai/MiniMax-M2.5",
        "slashai/MiniMax-M2.7",
        "slashai/mimo-v2-flash",
        "slashai/mimo-v2-omni",
        "slashai/mimo-v2-pro",
        "slashai/mimo-v2.5",
        "slashai/mimo-v2.5-pro",
        "slashai/Step-3.5-Flash",
    ],
}

DEFAULT_SYSTEM_PROMPT = """
Kamu adalah asisten pribadi AI yang cepat, jelas, dan praktis.
Jawab dalam bahasa Indonesia kecuali pengguna meminta bahasa lain.
Prioritaskan jawaban langsung, ringkas, dan mudah dipakai.
Jika pertanyaan teknis, berikan langkah yang runtut tanpa terlalu banyak teori.
""".strip()


# =====================================================
# SECRETS STREAMLIT
# =====================================================
def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


API_KEY = get_secret("SLASHAI_API_KEY")
API_URL = get_secret("SLASHAI_API_URL", "https://api.slashai.my.id/v1/chat/completions")
DEFAULT_MODEL = get_secret("SLASHAI_MODEL", "slashai/deepseek-v4-flash")


# =====================================================
# HELPER
# =====================================================
def init_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "system_prompt" not in st.session_state:
        st.session_state.system_prompt = DEFAULT_SYSTEM_PROMPT
    if "last_used_model" not in st.session_state:
        st.session_state.last_used_model = DEFAULT_MODEL


def is_access_denied_error(status_code: int, text: str) -> bool:
    text_lower = text.lower()
    return (
        status_code == 403
        or "access_denied" in text_lower
        or "deposit required" in text_lower
        or "premium models" in text_lower
    )


def find_default_group(model_name: str) -> str:
    for group, models in MODEL_GROUPS.items():
        if model_name in models:
            return group
    return "Cepat & Hemat"


def trim_history(messages: List[Dict[str, str]], max_chat_messages: int) -> List[Dict[str, str]]:
    """
    Supaya cepat dan hemat token, hanya kirim beberapa chat terakhir ke API.
    Riwayat tetap tampil di UI, tetapi request API dibuat lebih ringan.
    """
    if max_chat_messages <= 0:
        return []
    return messages[-max_chat_messages:]


def build_api_messages(system_prompt: str, history: List[Dict[str, str]], max_chat_messages: int) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        *trim_history(history, max_chat_messages=max_chat_messages),
    ]


def parse_non_stream_response(response: requests.Response, model: str) -> str:
    try:
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except Exception as exc:
        raise RuntimeError(f"Format respons API tidak sesuai dari model {model}: {response.text[:1000]}") from exc


def stream_chunks(response: requests.Response, model: str) -> Generator[str, None, None]:
    """Parse format Server-Sent Events ala OpenAI: data: {...}."""
    found_chunk = False

    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line:
            continue

        line = raw_line.strip()
        if line.startswith("data:"):
            line = line[len("data:"):].strip()

        if line == "[DONE]":
            break

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        choice = data.get("choices", [{}])[0]
        delta = choice.get("delta", {}) or {}
        message = choice.get("message", {}) or {}
        content = delta.get("content") or message.get("content") or ""

        if content:
            found_chunk = True
            yield content

    if not found_chunk:
        raise RuntimeError(
            f"Model {model} tidak mengirim chunk streaming. Matikan mode streaming di sidebar, lalu coba lagi."
        )


def post_chat(
    messages: List[Dict[str, str]],
    model: str,
    temperature: float,
    max_tokens: int,
    stream: bool,
    timeout_seconds: int,
) -> requests.Response:
    if not API_KEY:
        raise RuntimeError("API key belum diatur. Isi SLASHAI_API_KEY di Streamlit Secrets.")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            API_URL,
            headers=headers,
            json=payload,
            stream=stream,
            timeout=(8, timeout_seconds),
        )
    except requests.exceptions.Timeout as exc:
        raise RuntimeError("Request timeout. Turunkan Max Tokens atau gunakan model yang lebih ringan.") from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Gagal menghubungi API: {exc}") from exc

    if response.status_code != 200:
        detail = response.text[:1200]
        if is_access_denied_error(response.status_code, detail):
            raise PermissionError(f"Model terkunci/ditolak: {model}. Detail: {detail}")
        raise RuntimeError(f"API mengembalikan status {response.status_code}: {detail}")

    return response


def generate_answer_fast(
    placeholder,
    messages: List[Dict[str, str]],
    primary_model: str,
    fallback_models: List[str],
    max_fallback: int,
    temperature: float,
    max_tokens: int,
    use_streaming: bool,
    timeout_seconds: int,
) -> Tuple[str, str, List[str]]:
    """
    Alur cepat:
    1. Coba model utama.
    2. Jika 403/deposit required, coba fallback maksimal beberapa model saja.
    3. Tidak mencoba semua model agar tidak lambat.
    """
    models_to_try = [primary_model]
    for item in fallback_models[:max_fallback]:
        if item and item not in models_to_try:
            models_to_try.append(item)

    locked_models: List[str] = []
    last_error: Optional[Exception] = None

    for model in models_to_try:
        try:
            response = post_chat(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=use_streaming,
                timeout_seconds=timeout_seconds,
            )

            if use_streaming:
                collected = ""
                for chunk in stream_chunks(response=response, model=model):
                    collected += chunk
                    placeholder.markdown(collected + "▌")
                placeholder.markdown(collected)
                return collected, model, locked_models

            answer = parse_non_stream_response(response=response, model=model)
            placeholder.markdown(answer)
            return answer, model, locked_models

        except PermissionError as exc:
            locked_models.append(model)
            last_error = exc
            continue

    failed = "\n".join([f"- {m}" for m in locked_models])
    raise RuntimeError(
        "Semua model yang dicoba masih ditolak oleh provider.\n\n"
        f"Model yang ditolak:\n{failed}\n\n"
        "Solusi cepat: isi deposit/top up, atau pilih model lain yang sudah aktif di akun API kamu.\n\n"
        f"Error terakhir: {last_error}"
    )


# =====================================================
# UI
# =====================================================
init_state()

with st.sidebar:
    st.title("⚙️ Mode Cepat")

    default_group = find_default_group(DEFAULT_MODEL)
    group_names = list(MODEL_GROUPS.keys())
    selected_group = st.selectbox(
        "Kategori Model",
        options=group_names,
        index=group_names.index(default_group),
    )

    model_options = MODEL_GROUPS[selected_group]
    default_index = model_options.index(DEFAULT_MODEL) if DEFAULT_MODEL in model_options else 0
    selected_model = st.selectbox("Model", model_options, index=default_index)

    use_custom_model = st.toggle("Custom model", value=False)
    model = selected_model
    if use_custom_model:
        model = st.text_input("Nama model", value=selected_model).strip()

    speed_profile = st.radio(
        "Profil Kecepatan",
        options=["Cepat", "Seimbang", "Lengkap"],
        index=0,
        horizontal=True,
        help="Cepat = respons lebih ringan. Lengkap = konteks lebih panjang, tapi bisa lebih lambat.",
    )

    if speed_profile == "Cepat":
        default_max_messages = 8
        default_max_tokens = 900
        default_temperature = 0.4
        default_max_fallback = 2
        default_timeout = 45
    elif speed_profile == "Seimbang":
        default_max_messages = 14
        default_max_tokens = 1500
        default_temperature = 0.6
        default_max_fallback = 3
        default_timeout = 75
    else:
        default_max_messages = 24
        default_max_tokens = 2500
        default_temperature = 0.7
        default_max_fallback = 5
        default_timeout = 120

    use_streaming = st.toggle(
        "Streaming jawaban",
        value=True,
        help="Jawaban muncul bertahap. Jika provider tidak mendukung streaming, matikan opsi ini.",
    )

    with st.expander("Pengaturan lanjutan"):
        temperature = st.slider("Temperature", 0.0, 1.5, float(default_temperature), 0.1)
        max_tokens = st.slider("Max tokens", 256, 4096, int(default_max_tokens), 128)
        max_chat_messages = st.slider("Jumlah chat terakhir yang dikirim", 4, 30, int(default_max_messages), 2)
        max_fallback = st.slider("Maksimal fallback", 0, 6, int(default_max_fallback), 1)
        timeout_seconds = st.slider("Timeout API/detik", 20, 180, int(default_timeout), 5)
        fallback_text = st.text_area(
            "Model fallback",
            value="\n".join([m for m in FAST_MODELS if m != selected_model]),
            height=130,
            help="Satu model per baris. Aplikasi hanya mencoba sesuai batas Maksimal fallback.",
        )
        fallback_models = [line.strip() for line in fallback_text.splitlines() if line.strip()]

    st.session_state.system_prompt = st.text_area(
        "Instruksi Asisten",
        value=st.session_state.system_prompt,
        height=130,
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🧹 Hapus", use_container_width=True):
            st.session_state.messages = []
            st.rerun()
    with col2:
        if st.button("🔄 Reset", use_container_width=True):
            st.session_state.system_prompt = DEFAULT_SYSTEM_PROMPT
            st.session_state.messages = []
            st.rerun()

    st.divider()
    if API_KEY:
        st.success("API key terbaca")
    else:
        st.error("API key belum ada")
    st.caption(f"Endpoint: {API_URL}")


st.title("⚡ Asisten Pribadi AI")
st.caption("Alur cepat: konteks dipangkas, model ringan, streaming, dan fallback dibatasi.")

if st.session_state.last_used_model:
    st.caption(f"Model terakhir: `{st.session_state.last_used_model}`")

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if not st.session_state.messages:
    with st.chat_message("assistant"):
        st.markdown("Halo! Tulis pertanyaan kamu. Saya akan jawab dengan alur cepat.")

user_prompt = st.chat_input("Tulis pertanyaan...")

if user_prompt:
    st.session_state.messages.append({"role": "user", "content": user_prompt})

    with st.chat_message("user"):
        st.markdown(user_prompt)

    api_messages = build_api_messages(
        system_prompt=st.session_state.system_prompt,
        history=st.session_state.messages,
        max_chat_messages=max_chat_messages,
    )

    with st.chat_message("assistant"):
        placeholder = st.empty()
        with st.spinner("Menjawab..."):
            try:
                answer, used_model, locked_models = generate_answer_fast(
                    placeholder=placeholder,
                    messages=api_messages,
                    primary_model=model,
                    fallback_models=fallback_models,
                    max_fallback=max_fallback,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    use_streaming=use_streaming,
                    timeout_seconds=timeout_seconds,
                )

                st.session_state.last_used_model = used_model
                st.session_state.messages.append({"role": "assistant", "content": answer})

                if locked_models:
                    st.caption(
                        "Beberapa model ditolak, lalu berhasil memakai: "
                        f"`{used_model}`"
                    )
                else:
                    st.caption(f"Model: `{used_model}`")

            except Exception as err:
                st.error(str(err))
