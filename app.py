import json
import time
from typing import Any, Dict, List, Tuple

import requests
import streamlit as st

# ==============================
# KONFIGURASI MODEL HEMAT
# ==============================

MODEL_PRICES = {
    "slashai/gemini-3-flash": {"input": 50, "output": 200},
    "slashai/gemini-3.1-pro": {"input": 50, "output": 200},
    "slashai/gpt-5-nano": {"input": 50, "output": 200},
    "slashai/gpt-5-mini": {"input": 50, "output": 200},
    "slashai/gpt-5.4-nano": {"input": 50, "output": 200},
    "slashai/gpt-5.4-mini": {"input": 50, "output": 200},
    "slashai/gpt-5.5-instant": {"input": 50, "output": 200},
    "slashai/gpt-5-codex-mini": {"input": 50, "output": 200},
    "slashai/mimo-v2-flash": {"input": 50, "output": 200},
    "slashai/MiniMax-M2.5": {"input": 50, "output": 200},
    "slashai/MiniMax-M2.7": {"input": 50, "output": 200},
    "slashai/minimax-m2.5": {"input": 50, "output": 200},
    "slashai/minimax-m2.7": {"input": 50, "output": 200},
    "slashai/Step-3.5-Flash": {"input": 50, "output": 200},
    "slashai/claude-haiku-4.5": {"input": 50, "output": 200},
    "bai/deepseek-v4-flash": {"input": 50, "output": 200},
    "bai/claude-haiku-4.5": {"input": 50, "output": 200},
    "cmc/MiniMaxAI/MiniMax-M2.5": {"input": 50, "output": 200},

    # Model menengah untuk opsi manual cepat.
    "slashai/Qwen3.6-Plus": {"input": 500, "output": 2000},
    "slashai/qwen3-coder-next": {"input": 500, "output": 2000},
    "slashai/claude-sonnet-4.5": {"input": 500, "output": 2000},
    "slashai/claude-sonnet-4.6": {"input": 500, "output": 2000},
    "slashai/Kimi-K2.5": {"input": 500, "output": 2000},
    "slashai/Kimi-K2.6": {"input": 500, "output": 2000},
    "slashai/GLM-5": {"input": 500, "output": 2000},
    "slashai/GLM-5.1": {"input": 500, "output": 2000},
    "slashai/mimo-v2-omni": {"input": 500, "output": 2000},
    "slashai/mimo-v2-pro": {"input": 500, "output": 2000},
    "slashai/mimo-v2.5": {"input": 500, "output": 2000},
    "slashai/mimo-v2.5-pro": {"input": 500, "output": 2000},
}

CHEAP_MODELS = [m for m, p in MODEL_PRICES.items() if p["input"] <= 50 and p["output"] <= 200]
DEFAULT_FALLBACKS = [
    "slashai/gemini-3-flash",
    "slashai/gpt-5-nano",
    "slashai/gpt-5-mini",
    "slashai/mimo-v2-flash",
    "slashai/Step-3.5-Flash",
    "slashai/MiniMax-M2.5",
    "slashai/claude-haiku-4.5",
]


# ==============================
# UTILITAS
# ==============================

def get_secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return default


def rupiah(value: float) -> str:
    return "Rp " + f"{value:,.0f}".replace(",", ".")


def model_label(model: str) -> str:
    price = MODEL_PRICES.get(model)
    if not price:
        return model
    return f"{model} — In {rupiah(price['input'])}/1M | Out {rupiah(price['output'])}/1M"


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def estimate_total_tokens(messages: List[Dict[str, str]]) -> int:
    return sum(estimate_tokens(m.get("content", "")) + 4 for m in messages)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price = MODEL_PRICES.get(model)
    if not price:
        return 0.0
    return (input_tokens / 1_000_000 * price["input"]) + (output_tokens / 1_000_000 * price["output"])


def trim_history(messages: List[Dict[str, str]], max_turns: int) -> List[Dict[str, str]]:
    if max_turns <= 0:
        return []
    return messages[-max_turns * 2:]


def build_messages(system_prompt: str, history: List[Dict[str, str]], user_prompt: str, max_turns: int) -> List[Dict[str, str]]:
    # History diambil sebelum user terbaru ditambahkan, supaya prompt user tidak dobel.
    return [{"role": "system", "content": system_prompt}] + trim_history(history, max_turns) + [
        {"role": "user", "content": user_prompt}
    ]


def extract_text_from_content(content: Any) -> str:
    """Membaca content dari beberapa bentuk respons API OpenAI-compatible."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                # Bentuk umum: {"type":"text", "text":"..."}
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return "".join(parts).strip()
    if isinstance(content, dict):
        for key in ("text", "content", "message", "output_text"):
            if isinstance(content.get(key), str):
                return content[key].strip()
    return ""


def extract_answer(data: Dict[str, Any]) -> str:
    """Parsing dibuat longgar karena provider kompatibel OpenAI kadang formatnya berbeda."""
    # Format Chat Completions umum.
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, dict):
            msg = choice.get("message")
            if isinstance(msg, dict):
                text = extract_text_from_content(msg.get("content"))
                if text:
                    return text
                # Beberapa provider menyimpan jawaban di key lain.
                for key in ("text", "output_text", "reasoning_content"):
                    text = extract_text_from_content(msg.get(key))
                    if text:
                        return text
            text = extract_text_from_content(choice.get("text"))
            if text:
                return text
            delta = choice.get("delta")
            if isinstance(delta, dict):
                text = extract_text_from_content(delta.get("content"))
                if text:
                    return text

    # Format Responses API / variasi lain.
    for key in ("output_text", "text", "message", "content", "response", "answer"):
        text = extract_text_from_content(data.get(key))
        if text:
            return text

    output = data.get("output")
    if isinstance(output, list):
        parts = []
        for item in output:
            if isinstance(item, dict):
                parts.append(extract_text_from_content(item.get("content")))
                parts.append(extract_text_from_content(item.get("text")))
        text = "".join(parts).strip()
        if text:
            return text

    return ""


def parse_stream_line(line: str) -> str:
    line = line.strip()
    if not line:
        return ""
    if line.startswith("data:"):
        line = line[5:].strip()
    if line == "[DONE]":
        return ""
    try:
        data = json.loads(line)
    except Exception:
        return ""

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, dict):
            delta = choice.get("delta")
            if isinstance(delta, dict):
                text = extract_text_from_content(delta.get("content"))
                if text:
                    return text
            # Ada provider yang tetap memakai message saat streaming.
            msg = choice.get("message")
            if isinstance(msg, dict):
                return extract_text_from_content(msg.get("content"))
    return extract_answer(data)


def call_chat_completion(
    api_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: int,
    stream: bool,
) -> Tuple[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }

    response = requests.post(
        api_url,
        headers=headers,
        json=payload,
        timeout=timeout,
        stream=stream,
    )

    raw_text = ""
    if response.status_code != 200:
        try:
            raw_text = json.dumps(response.json(), ensure_ascii=False, indent=2)
        except Exception:
            raw_text = response.text
        raise RuntimeError(f"HTTP {response.status_code}: {raw_text[:1500]}")

    if stream:
        chunks = []
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            piece = parse_stream_line(raw_line)
            if piece:
                chunks.append(piece)
        answer = "".join(chunks).strip()
        return answer, "[streaming response]"

    try:
        data = response.json()
        raw_text = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        raw_text = response.text
        data = {}

    answer = extract_answer(data).strip()
    return answer, raw_text


def ask_with_fallback(
    api_url: str,
    api_key: str,
    primary_model: str,
    fallback_models: List[str],
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: int,
    stream: bool,
    empty_retry_non_stream: bool,
) -> Tuple[str, str, str, List[str]]:
    """Return: answer, used_model, raw_debug, tried_models."""
    tried = []
    ordered_models = [primary_model] + [m for m in fallback_models if m != primary_model]
    last_error = ""
    last_raw = ""

    for model in ordered_models:
        tried.append(model)
        try:
            answer, raw = call_chat_completion(
                api_url=api_url,
                api_key=api_key,
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                stream=stream,
            )
            last_raw = raw
            if answer:
                return answer, model, raw, tried

            # Jika streaming kosong, coba ulang model yang sama tanpa streaming.
            if stream and empty_retry_non_stream:
                answer, raw = call_chat_completion(
                    api_url=api_url,
                    api_key=api_key,
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    stream=False,
                )
                last_raw = raw
                if answer:
                    return answer, model, raw, tried

            last_error = "Respons API berhasil, tetapi isi jawaban kosong."
        except Exception as exc:
            last_error = str(exc)

    raise RuntimeError(
        "Semua model gagal atau mengembalikan jawaban kosong.\n"
        f"Model dicoba: {', '.join(tried)}\n\n"
        f"Error terakhir: {last_error}\n\n"
        f"Raw terakhir:\n{last_raw[:2000]}"
    )


# ==============================
# UI STREAMLIT
# ==============================

st.set_page_config(page_title="Asisten Pribadi AI", page_icon="🤖", layout="centered")
st.title("🤖 Asisten Pribadi AI")
st.caption("Versi no-empty fix: non-streaming default, parsing respons diperkuat, dan prompt tidak dobel.")

if "messages" not in st.session_state:
    st.session_state.messages = []

if "system_prompt" not in st.session_state:
    st.session_state.system_prompt = (
        "Kamu adalah asisten pribadi yang cepat, jelas, dan membantu. "
        "Jawab dalam bahasa Indonesia. Jawaban harus langsung ke inti, tetapi tetap lengkap jika dibutuhkan."
    )

api_url = get_secret("SLASHAI_API_URL", "https://api.slashai.my.id/v1/chat/completions")
api_key = get_secret("SLASHAI_API_KEY", "")
secret_model = get_secret("SLASHAI_MODEL", "slashai/gemini-3-flash")

with st.sidebar:
    st.header("⚙️ Pengaturan")

    st.subheader("Model")
    sorted_models = sorted(MODEL_PRICES.keys(), key=lambda m: (MODEL_PRICES[m]["input"], MODEL_PRICES[m]["output"], m.lower()))
    default_model = secret_model if secret_model in sorted_models else "slashai/gemini-3-flash"

    selected_model = st.selectbox(
        "Model utama",
        sorted_models,
        index=sorted_models.index(default_model),
        format_func=model_label,
    )

    use_custom_model = st.checkbox("Pakai model manual", value=False)
    if use_custom_model:
        selected_model = st.text_input("Nama model", value=selected_model)

    auto_fallback = st.checkbox("Auto fallback ke model hemat", value=True)
    if auto_fallback:
        fallback_models = st.multiselect(
            "Model cadangan",
            DEFAULT_FALLBACKS,
            default=[m for m in DEFAULT_FALLBACKS if m != selected_model][:2],
            format_func=model_label,
        )
    else:
        fallback_models = []

    st.divider()
    st.subheader("Respons")
    # Non-streaming default karena beberapa API kompatibel OpenAI mengirim stream dengan format berbeda sehingga terlihat kosong.
    stream = st.toggle("Streaming jawaban", value=False, help="Matikan jika jawaban kosong. Default sengaja OFF agar lebih stabil.")
    empty_retry_non_stream = st.checkbox("Jika streaming kosong, coba ulang non-streaming", value=True)
    max_tokens = st.slider("Maksimal output token", 150, 2000, 700, step=50)
    history_turns = st.slider("Riwayat chat yang dikirim", 0, 10, 4)
    temperature = st.slider("Kreativitas", 0.0, 1.0, 0.4, step=0.1)
    timeout = st.slider("Timeout API/detik", 10, 90, 45, step=5)

    st.divider()
    st.subheader("Debug")
    show_debug = st.checkbox("Tampilkan raw response jika kosong/error", value=False)

    if st.button("Tes koneksi API", use_container_width=True):
        test_messages = [
            {"role": "system", "content": "Jawab singkat."},
            {"role": "user", "content": "Jawab satu kata saja: OK"},
        ]
        try:
            ans, raw = call_chat_completion(
                api_url=api_url,
                api_key=api_key,
                model=selected_model,
                messages=test_messages,
                temperature=0,
                max_tokens=20,
                timeout=timeout,
                stream=False,
            )
            if ans:
                st.success(f"API aktif. Jawaban: {ans}")
            else:
                st.warning("API aktif, tetapi isi jawaban kosong. Coba model lain atau aktifkan debug.")
                if show_debug:
                    st.code(raw[:3000], language="json")
        except Exception as exc:
            st.error(str(exc))

    st.divider()
    st.subheader("Instruksi Asisten")
    st.session_state.system_prompt = st.text_area("System prompt", st.session_state.system_prompt, height=130)

    if st.button("🧹 Hapus riwayat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

if not api_key:
    st.error("API key belum ada. Isi SLASHAI_API_KEY di Streamlit Secrets.")
    st.code(
        'SLASHAI_API_KEY = "ISI_API_KEY_KAMU"\n'
        'SLASHAI_API_URL = "https://api.slashai.my.id/v1/chat/completions"\n'
        'SLASHAI_MODEL = "slashai/gemini-3-flash"',
        language="toml",
    )
    st.stop()

# Tampilkan riwayat lama.
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

user_prompt = st.chat_input("Tulis pertanyaan kamu...")

if user_prompt:
    # Build pesan sebelum user terbaru dimasukkan ke session, agar tidak dobel.
    messages_for_api = build_messages(
        system_prompt=st.session_state.system_prompt,
        history=st.session_state.messages,
        user_prompt=user_prompt,
        max_turns=history_turns,
    )

    st.session_state.messages.append({"role": "user", "content": user_prompt})
    with st.chat_message("user"):
        st.markdown(user_prompt)

    estimated_input = estimate_total_tokens(messages_for_api)
    estimated_cost = estimate_cost(selected_model, estimated_input, max_tokens)

    with st.expander("Estimasi biaya", expanded=False):
        st.write(f"Input kira-kira: **{estimated_input} token**")
        st.write(f"Output maksimal: **{max_tokens} token**")
        if MODEL_PRICES.get(selected_model):
            st.write(f"Estimasi biaya maksimal: **{rupiah(estimated_cost)}**")
        else:
            st.write("Harga model manual tidak ada di daftar.")

    with st.chat_message("assistant"):
        status = st.empty()
        output = st.empty()
        raw_debug_text = ""
        started = time.time()

        try:
            status.caption("Mengirim pertanyaan ke API...")
            answer, used_model, raw_debug_text, tried = ask_with_fallback(
                api_url=api_url,
                api_key=api_key,
                primary_model=selected_model,
                fallback_models=fallback_models,
                messages=messages_for_api,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                stream=stream,
                empty_retry_non_stream=empty_retry_non_stream,
            )
            output.markdown(answer)
            elapsed = time.time() - started
            status.caption(f"Model: `{used_model}` • {elapsed:.1f} detik")
            st.session_state.messages.append({"role": "assistant", "content": answer})
        except Exception as exc:
            status.empty()
            error_text = (
                "Maaf, model belum mengembalikan jawaban yang bisa dibaca.\n\n"
                "Coba langkah ini:\n"
                "1. Pastikan **Streaming jawaban** dalam posisi OFF.\n"
                "2. Tekan **Tes koneksi API** di sidebar.\n"
                "3. Ganti model ke `slashai/gpt-5-nano`, `slashai/gpt-5-mini`, atau `slashai/mimo-v2-flash`.\n"
                "4. Jika muncul 403, berarti akun API/model belum punya akses atau butuh deposit.\n\n"
                f"Detail error:\n```text\n{str(exc)[:2500]}\n```"
            )
            output.markdown(error_text)
            if show_debug and raw_debug_text:
                st.code(raw_debug_text[:4000], language="json")
            st.session_state.messages.append({"role": "assistant", "content": error_text})
