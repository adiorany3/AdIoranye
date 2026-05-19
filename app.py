import json
import time
from typing import Dict, List, Tuple, Optional

import requests
import streamlit as st


# =========================
# DATA MODEL + HARGA
# =========================

MODEL_PRICES: Dict[str, Dict[str, int]] = {
    # Super hemat / murah
    "bai/claude-haiku-4.5": {"input": 50, "output": 200},
    "bai/deepseek-v4-flash": {"input": 50, "output": 200},
    "cmc/MiniMaxAI/MiniMax-M2.5": {"input": 50, "output": 200},
    "slashai/MiniMax-M2.5": {"input": 50, "output": 200},
    "slashai/MiniMax-M2.7": {"input": 50, "output": 200},
    "slashai/Step-3.5-Flash": {"input": 50, "output": 200},
    "slashai/claude-haiku-4.5": {"input": 50, "output": 200},
    "slashai/gemini-3-flash": {"input": 50, "output": 200},
    "slashai/gemini-3.1-pro": {"input": 50, "output": 200},
    "slashai/gpt-5-codex-mini": {"input": 50, "output": 200},
    "slashai/gpt-5-codex-mini-review": {"input": 50, "output": 200},
    "slashai/gpt-5-mini": {"input": 50, "output": 200},
    "slashai/gpt-5-nano": {"input": 50, "output": 200},
    "slashai/gpt-5.1-codex-mini": {"input": 50, "output": 200},
    "slashai/gpt-5.1-codex-mini-high": {"input": 50, "output": 200},
    "slashai/gpt-5.1-codex-mini-high-review": {"input": 50, "output": 200},
    "slashai/gpt-5.1-codex-mini-review": {"input": 50, "output": 200},
    "slashai/gpt-5.3-codex-low": {"input": 50, "output": 200},
    "slashai/gpt-5.3-codex-low-review": {"input": 50, "output": 200},
    "slashai/gpt-5.3-codex-spark": {"input": 50, "output": 200},
    "slashai/gpt-5.3-codex-spark-review": {"input": 50, "output": 200},
    "slashai/gpt-5.4-mini": {"input": 50, "output": 200},
    "slashai/gpt-5.4-nano": {"input": 50, "output": 200},
    "slashai/gpt-5.5-instant": {"input": 50, "output": 200},
    "slashai/mimo-v2-flash": {"input": 50, "output": 200},
    "slashai/minimax-m2.5": {"input": 50, "output": 200},
    "slashai/minimax-m2.7": {"input": 50, "output": 200},

    # Menengah
    "bai/claude-sonnet-4.5": {"input": 500, "output": 2000},
    "bai/deepseek-v4-pro": {"input": 500, "output": 2000},
    "bai/glm-5": {"input": 500, "output": 2000},
    "mimo/mimo-v2-omni": {"input": 500, "output": 2000},
    "mimo/mimo-v2.5": {"input": 500, "output": 2000},
    "mimo/mimo-v2.5-pro": {"input": 500, "output": 2000},
    "slashai/GLM-5": {"input": 500, "output": 2000},
    "slashai/GLM-5.1": {"input": 500, "output": 2000},
    "slashai/Kimi-K2.5": {"input": 500, "output": 2000},
    "slashai/Kimi-K2.6": {"input": 500, "output": 2000},
    "slashai/Qwen3.6-Plus": {"input": 500, "output": 2000},
    "slashai/claude-sonnet-4.5": {"input": 500, "output": 2000},
    "slashai/claude-sonnet-4.6": {"input": 500, "output": 2000},
    "slashai/deepseek-3.2": {"input": 500, "output": 2000},
    "slashai/deepseek-v3.2": {"input": 500, "output": 2000},
    "slashai/glm-5": {"input": 500, "output": 2000},
    "slashai/glm-5.1": {"input": 500, "output": 2000},
    "slashai/kimi-k2.5": {"input": 500, "output": 2000},
    "slashai/mimo-v2-omni": {"input": 500, "output": 2000},
    "slashai/mimo-v2-pro": {"input": 500, "output": 2000},
    "slashai/mimo-v2.5": {"input": 500, "output": 2000},
    "slashai/mimo-v2.5-pro": {"input": 500, "output": 2000},
    "slashai/qwen3-coder-next": {"input": 500, "output": 2000},

    # Mahal
    "slashai/deepseek-v4-flash": {"input": 1500, "output": 6000},
    "slashai/deepseek-v4-pro": {"input": 4000, "output": 18000},
    "bai/claude-opus-4.7": {"input": 5000, "output": 25000},
    "cx/gpt-5.2": {"input": 5000, "output": 25000},
    "cx/gpt-5.4": {"input": 5000, "output": 25000},
    "cx/gpt-5.5": {"input": 5000, "output": 25000},
    "slashai/Qwen3.6-Max-Preview": {"input": 5000, "output": 25000},
    "slashai/claude-opus-4.5": {"input": 5000, "output": 25000},
    "slashai/claude-opus-4.6": {"input": 5000, "output": 25000},
    "slashai/claude-sonnet-4.7": {"input": 5000, "output": 15000},
    "slashai/gpt-5-codex": {"input": 5000, "output": 25000},
    "slashai/gpt-5-codex-review": {"input": 5000, "output": 25000},
    "slashai/gpt-5.1": {"input": 5000, "output": 25000},
    "slashai/gpt-5.1-codex": {"input": 5000, "output": 25000},
    "slashai/gpt-5.1-codex-max": {"input": 5000, "output": 25000},
    "slashai/gpt-5.1-codex-max-review": {"input": 5000, "output": 25000},
    "slashai/gpt-5.1-codex-review": {"input": 5000, "output": 25000},
    "slashai/gpt-5.1-review": {"input": 5000, "output": 25000},
    "slashai/gpt-5.2": {"input": 5000, "output": 25000},
    "slashai/gpt-5.2-codex": {"input": 5000, "output": 25000},
    "slashai/gpt-5.2-codex-review": {"input": 5000, "output": 25000},
    "slashai/gpt-5.2-review": {"input": 5000, "output": 25000},
    "slashai/gpt-5.3-codex": {"input": 5000, "output": 25000},
    "slashai/gpt-5.3-codex-high": {"input": 5000, "output": 25000},
    "slashai/gpt-5.3-codex-high-review": {"input": 5000, "output": 25000},
    "slashai/gpt-5.3-codex-none": {"input": 5000, "output": 25000},
    "slashai/gpt-5.3-codex-none-review": {"input": 5000, "output": 25000},
    "slashai/gpt-5.3-codex-review": {"input": 5000, "output": 25000},
    "slashai/gpt-5.3-codex-xhigh": {"input": 5000, "output": 25000},
    "slashai/gpt-5.3-codex-xhigh-review": {"input": 5000, "output": 25000},
    "slashai/gpt-5.4": {"input": 5000, "output": 25000},
    "slashai/gpt-5.4-pro": {"input": 5000, "output": 25000},
    "slashai/gpt-5.4-review": {"input": 5000, "output": 25000},
    "slashai/gpt-5.5": {"input": 5000, "output": 25000},
    "slashai/gpt-5.5-review": {"input": 5000, "output": 25000},

    # Sangat mahal / hindari untuk default
    "slashai/claude-opus-4.7": {"input": 250000, "output": 1250000},
}

CHEAP_FALLBACKS = [
    "slashai/gemini-3-flash",
    "slashai/gpt-5-nano",
    "slashai/gpt-5-mini",
    "slashai/mimo-v2-flash",
    "slashai/Step-3.5-Flash",
    "slashai/MiniMax-M2.5",
    "bai/deepseek-v4-flash",
    "bai/claude-haiku-4.5",
]

MODE_PRESETS = {
    "Super Hemat": {
        "default_model": "slashai/gemini-3-flash",
        "max_tokens": 500,
        "history_turns": 4,
        "temperature": 0.4,
        "timeout": 25,
        "stream": True,
        "fallback_attempts": 1,
    },
    "Cepat Seimbang": {
        "default_model": "slashai/gpt-5-nano",
        "max_tokens": 800,
        "history_turns": 6,
        "temperature": 0.5,
        "timeout": 35,
        "stream": True,
        "fallback_attempts": 2,
    },
    "Lebih Pintar": {
        "default_model": "slashai/gpt-5-mini",
        "max_tokens": 1200,
        "history_turns": 8,
        "temperature": 0.6,
        "timeout": 45,
        "stream": True,
        "fallback_attempts": 2,
    },
}


# =========================
# UTILITAS
# =========================

def rupiah(value: float) -> str:
    return "Rp " + f"{value:,.0f}".replace(",", ".")


def estimate_tokens(text: str) -> int:
    # Estimasi kasar: 1 token ~ 4 karakter.
    return max(1, int(len(text) / 4))


def estimate_messages_tokens(messages: List[Dict[str, str]]) -> int:
    total = 0
    for msg in messages:
        total += estimate_tokens(msg.get("content", "")) + 4
    return total


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Tuple[float, float, float]:
    price = MODEL_PRICES.get(model, {"input": 0, "output": 0})
    input_cost = (input_tokens / 1_000_000) * price["input"]
    output_cost = (output_tokens / 1_000_000) * price["output"]
    return input_cost, output_cost, input_cost + output_cost


def price_tier(model: str) -> str:
    p = MODEL_PRICES.get(model, {"input": 0, "output": 0})
    if p["input"] <= 50 and p["output"] <= 200:
        return "Super hemat"
    if p["input"] <= 500 and p["output"] <= 2000:
        return "Menengah"
    if p["input"] <= 5000 and p["output"] <= 25000:
        return "Mahal"
    return "Sangat mahal"


def model_label(model: str) -> str:
    p = MODEL_PRICES.get(model)
    if not p:
        return model
    return f"{model} — {price_tier(model)} — In {rupiah(p['input'])}/1M | Out {rupiah(p['output'])}/1M"


def sort_models_by_cost(models: List[str]) -> List[str]:
    return sorted(models, key=lambda m: (
        MODEL_PRICES.get(m, {"input": 999999999})["input"],
        MODEL_PRICES.get(m, {"output": 999999999})["output"],
        m.lower()
    ))


def get_secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return default


def build_messages(system_prompt: str, user_prompt: str, history_turns: int) -> List[Dict[str, str]]:
    # Ambil hanya beberapa percakapan terakhir agar lebih cepat dan murah.
    recent_history = st.session_state.messages[-history_turns * 2:] if history_turns > 0 else []
    return [{"role": "system", "content": system_prompt}] + recent_history + [
        {"role": "user", "content": user_prompt}
    ]


def parse_non_stream_response(response: requests.Response) -> str:
    data = response.json()
    try:
        return data["choices"][0]["message"]["content"] or ""
    except Exception:
        return json.dumps(data, ensure_ascii=False, indent=2)


def stream_chunks(response: requests.Response):
    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line:
            continue
        line = raw_line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            break
        try:
            data = json.loads(line)
            delta = data.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content")
            if content:
                yield content
        except Exception:
            continue


def call_api_once(
    api_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: int,
    use_streaming: bool,
):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": use_streaming,
    }

    response = requests.post(
        api_url,
        headers=headers,
        json=payload,
        timeout=timeout,
        stream=use_streaming,
    )

    if response.status_code != 200:
        # Potong pesan agar UI tidak terlalu panjang.
        try:
            err = response.json()
            err_text = json.dumps(err, ensure_ascii=False)
        except Exception:
            err_text = response.text
        raise RuntimeError(f"Status {response.status_code}: {err_text[:1000]}")

    return response


def generate_answer(
    api_url: str,
    api_key: str,
    selected_model: str,
    fallback_models: List[str],
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: int,
    use_streaming: bool,
    max_fallback_attempts: int,
):
    tried = []
    models_to_try = [selected_model] + [m for m in fallback_models if m != selected_model]
    models_to_try = models_to_try[: max(1, 1 + max_fallback_attempts)]

    last_error = None
    for model in models_to_try:
        tried.append(model)
        try:
            response = call_api_once(
                api_url=api_url,
                api_key=api_key,
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                use_streaming=use_streaming,
            )
            return model, response, None
        except Exception as exc:
            last_error = str(exc)
            continue

    return None, None, f"Semua model gagal dicoba: {', '.join(tried)}\n\nError terakhir:\n{last_error}"


# =========================
# UI STREAMLIT
# =========================

st.set_page_config(
    page_title="Asisten Pribadi AI",
    page_icon="🤖",
    layout="centered",
)

st.title("🤖 Asisten Pribadi AI")
st.caption("Versi cepat dan hemat biaya memakai API kompatibel OpenAI SlashAI.")

if "messages" not in st.session_state:
    st.session_state.messages = []

if "system_prompt" not in st.session_state:
    st.session_state.system_prompt = (
        "Kamu adalah asisten pribadi yang cepat, jelas, hemat token, dan membantu pengguna dalam bahasa Indonesia. "
        "Jawab langsung ke inti, tetapi tetap sopan. Jika diminta membuat kode, berikan kode yang siap pakai."
    )

api_url = get_secret("SLASHAI_API_URL", "https://api.slashai.my.id/v1/chat/completions")
api_key = get_secret("SLASHAI_API_KEY", "")
secret_model = get_secret("SLASHAI_MODEL", "slashai/gemini-3-flash")

with st.sidebar:
    st.header("⚙️ Pengaturan")

    mode = st.selectbox(
        "Mode",
        list(MODE_PRESETS.keys()),
        index=0,
        help="Super Hemat paling murah dan cepat. Lebih Pintar memakai output lebih panjang.",
    )
    preset = MODE_PRESETS[mode]

    st.divider()
    st.subheader("Model")

    only_cheap = st.checkbox("Tampilkan model Rp50/Rp200 saja", value=True)

    all_models = sort_models_by_cost(list(MODEL_PRICES.keys()))
    if only_cheap:
        shown_models = [m for m in all_models if MODEL_PRICES[m]["input"] <= 50 and MODEL_PRICES[m]["output"] <= 200]
    else:
        shown_models = all_models

    default_model = secret_model if secret_model in shown_models else preset["default_model"]
    if default_model not in shown_models:
        default_model = shown_models[0]

    selected_model = st.selectbox(
        "Pilih model utama",
        shown_models,
        index=shown_models.index(default_model),
        format_func=model_label,
    )

    custom_model_enabled = st.checkbox("Pakai model custom/manual", value=False)
    if custom_model_enabled:
        selected_model = st.text_input("Nama model custom", value=selected_model)

    p = MODEL_PRICES.get(selected_model)
    if p:
        st.info(f"Harga model: Input {rupiah(p['input'])}/1M token | Output {rupiah(p['output'])}/1M token")
    else:
        st.warning("Harga model custom belum ada di daftar, estimasi biaya tidak tersedia.")

    auto_fallback = st.checkbox("Auto fallback jika model error/403", value=True)
    fallback_models = []
    if auto_fallback:
        fallback_models = st.multiselect(
            "Fallback model murah",
            CHEAP_FALLBACKS,
            default=[m for m in CHEAP_FALLBACKS if m != selected_model][: preset["fallback_attempts"]],
            format_func=model_label,
        )
        max_fallback_attempts = st.slider(
            "Maksimal fallback",
            min_value=0,
            max_value=3,
            value=preset["fallback_attempts"],
            help="Makin kecil makin cepat. Gunakan 1–2 agar tidak lama.",
        )
    else:
        max_fallback_attempts = 0

    st.divider()
    st.subheader("Kecepatan & Token")

    use_streaming = st.toggle("Streaming jawaban", value=preset["stream"])
    max_tokens = st.slider("Maksimal output token", 150, 2500, preset["max_tokens"], step=50)
    history_turns = st.slider("Jumlah chat terakhir yang dikirim", 0, 12, preset["history_turns"])
    temperature = st.slider("Kreativitas", 0.0, 1.0, preset["temperature"], step=0.1)
    timeout = st.slider("Timeout API/detik", 10, 90, preset["timeout"], step=5)

    st.divider()
    st.subheader("Instruksi Asisten")
    st.session_state.system_prompt = st.text_area(
        "System prompt",
        value=st.session_state.system_prompt,
        height=140,
    )

    if st.button("🧹 Hapus riwayat chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    st.caption("Tips: untuk hemat biaya, pakai Super Hemat + model Rp50/Rp200 + riwayat 4–6 chat.")


if not api_key:
    st.error(
        "API key belum ditemukan. Isi Streamlit Secrets dengan SLASHAI_API_KEY, "
        "SLASHAI_API_URL, dan SLASHAI_MODEL."
    )
    st.stop()

# Tampilkan riwayat chat
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

user_prompt = st.chat_input("Tulis pertanyaan kamu di sini...")

if user_prompt:
    st.session_state.messages.append({"role": "user", "content": user_prompt})

    with st.chat_message("user"):
        st.markdown(user_prompt)

    messages_for_api = build_messages(
        system_prompt=st.session_state.system_prompt,
        user_prompt=user_prompt,
        history_turns=history_turns,
    )

    input_tokens_est = estimate_messages_tokens(messages_for_api)
    input_cost, output_cost, total_cost = estimate_cost(selected_model, input_tokens_est, max_tokens)

    with st.expander("Estimasi biaya request ini", expanded=False):
        st.write(f"Estimasi input: **{input_tokens_est:,} token**".replace(",", "."))
        st.write(f"Maksimal output: **{max_tokens:,} token**".replace(",", "."))
        if MODEL_PRICES.get(selected_model):
            st.write(f"Estimasi biaya maksimal: **{rupiah(total_cost)}**")
            st.caption(
                "Ini estimasi kasar. Biaya asli tergantung token aktual dari provider."
            )
        else:
            st.write("Estimasi biaya tidak tersedia untuk model custom.")

    start = time.time()

    with st.chat_message("assistant"):
        status_box = st.empty()
        answer_box = st.empty()

        status_box.caption("Menghubungi model...")

        used_model, response, error = generate_answer(
            api_url=api_url,
            api_key=api_key,
            selected_model=selected_model,
            fallback_models=fallback_models,
            messages=messages_for_api,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            use_streaming=use_streaming,
            max_fallback_attempts=max_fallback_attempts if auto_fallback else 0,
        )

        final_answer = ""

        if error:
            final_answer = (
                "Maaf, API belum berhasil menjawab.\n\n"
                f"```text\n{error}\n```\n\n"
                "Coba pilih model Rp50/Rp200 lain seperti `slashai/gemini-3-flash`, "
                "`slashai/gpt-5-nano`, atau `slashai/mimo-v2-flash`. "
                "Jika tetap 403, kemungkinan akun API belum memiliki akses atau perlu deposit."
            )
            status_box.empty()
            answer_box.markdown(final_answer)
        else:
            try:
                if use_streaming:
                    chunks = []
                    for chunk in stream_chunks(response):
                        chunks.append(chunk)
                        final_answer = "".join(chunks)
                        answer_box.markdown(final_answer + "▌")
                    answer_box.markdown(final_answer)
                else:
                    final_answer = parse_non_stream_response(response)
                    answer_box.markdown(final_answer)

                elapsed = time.time() - start
                status_box.caption(f"Model dipakai: `{used_model}` • selesai {elapsed:.1f} detik")
            except Exception as exc:
                final_answer = f"Jawaban diterima, tetapi parsing gagal:\n\n```text\n{exc}\n```"
                status_box.empty()
                answer_box.markdown(final_answer)

    st.session_state.messages.append({"role": "assistant", "content": final_answer})
