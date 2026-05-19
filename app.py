
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
import streamlit as st


# =========================================================
# KONFIGURASI DASAR
# =========================================================
st.set_page_config(
    page_title="Asisten Pribadi AI",
    page_icon="🤖",
    layout="centered",
)

API_URL = st.secrets.get("SLASHAI_API_URL", "https://api.slashai.my.id/v1/chat/completions")
API_KEY = st.secrets.get("SLASHAI_API_KEY", "")
DEFAULT_MODEL = st.secrets.get("SLASHAI_MODEL", "slashai/gemini-3-flash")

# Harga per 1 juta token dari data user
MODEL_PRICES: Dict[str, Tuple[float, float]] = {
    "bai/claude-haiku-4.5": (50, 200),
    "bai/claude-opus-4.7": (5000, 25000),
    "bai/claude-sonnet-4.5": (500, 2000),
    "bai/deepseek-v4-flash": (50, 200),
    "bai/deepseek-v4-pro": (500, 2000),
    "bai/glm-5": (500, 2000),
    "cmc/MiniMaxAI/MiniMax-M2.5": (50, 200),
    "cx/gpt-5.2": (5000, 25000),
    "cx/gpt-5.4": (5000, 25000),
    "cx/gpt-5.5": (5000, 25000),
    "mimo/mimo-v2-omni": (500, 2000),
    "mimo/mimo-v2.5": (500, 2000),
    "mimo/mimo-v2.5-pro": (500, 2000),
    "slashai/GLM-5": (500, 2000),
    "slashai/GLM-5.1": (500, 2000),
    "slashai/Kimi-K2.5": (500, 2000),
    "slashai/Kimi-K2.6": (500, 2000),
    "slashai/MiniMax-M2.5": (50, 200),
    "slashai/MiniMax-M2.7": (50, 200),
    "slashai/Qwen3.6-Max-Preview": (5000, 25000),
    "slashai/Qwen3.6-Plus": (500, 2000),
    "slashai/Step-3.5-Flash": (50, 200),
    "slashai/claude-haiku-4.5": (50, 200),
    "slashai/claude-opus-4.5": (5000, 25000),
    "slashai/claude-opus-4.6": (5000, 25000),
    "slashai/claude-opus-4.7": (250000, 1250000),
    "slashai/claude-sonnet-4.5": (500, 2000),
    "slashai/claude-sonnet-4.6": (500, 2000),
    "slashai/claude-sonnet-4.7": (5000, 15000),
    "slashai/deepseek-3.2": (500, 2000),
    "slashai/deepseek-v3.2": (500, 2000),
    "slashai/deepseek-v4-flash": (1500, 6000),
    "slashai/deepseek-v4-pro": (4000, 18000),
    "slashai/gemini-3-flash": (50, 200),
    "slashai/gemini-3.1-pro": (50, 200),
    "slashai/glm-5": (500, 2000),
    "slashai/glm-5.1": (500, 2000),
    "slashai/gpt-5-codex": (5000, 25000),
    "slashai/gpt-5-codex-mini": (50, 200),
    "slashai/gpt-5-codex-mini-review": (50, 200),
    "slashai/gpt-5-codex-review": (5000, 25000),
    "slashai/gpt-5-mini": (50, 200),
    "slashai/gpt-5-nano": (50, 200),
    "slashai/gpt-5.1": (5000, 25000),
    "slashai/gpt-5.1-codex": (5000, 25000),
    "slashai/gpt-5.1-codex-max": (5000, 25000),
    "slashai/gpt-5.1-codex-max-review": (5000, 25000),
    "slashai/gpt-5.1-codex-mini": (50, 200),
    "slashai/gpt-5.1-codex-mini-high": (50, 200),
    "slashai/gpt-5.1-codex-mini-high-review": (50, 200),
    "slashai/gpt-5.1-codex-mini-review": (50, 200),
    "slashai/gpt-5.1-codex-review": (5000, 25000),
    "slashai/gpt-5.1-review": (5000, 25000),
    "slashai/gpt-5.2": (5000, 25000),
    "slashai/gpt-5.2-codex": (5000, 25000),
    "slashai/gpt-5.2-codex-review": (5000, 25000),
    "slashai/gpt-5.2-review": (5000, 25000),
    "slashai/gpt-5.3-codex": (5000, 25000),
    "slashai/gpt-5.3-codex-high": (5000, 25000),
    "slashai/gpt-5.3-codex-high-review": (5000, 25000),
    "slashai/gpt-5.3-codex-low": (50, 200),
    "slashai/gpt-5.3-codex-low-review": (50, 200),
    "slashai/gpt-5.3-codex-none": (5000, 25000),
    "slashai/gpt-5.3-codex-none-review": (5000, 25000),
    "slashai/gpt-5.3-codex-review": (5000, 25000),
    "slashai/gpt-5.3-codex-spark": (50, 200),
    "slashai/gpt-5.3-codex-spark-review": (50, 200),
    "slashai/gpt-5.3-codex-xhigh": (5000, 25000),
    "slashai/gpt-5.3-codex-xhigh-review": (5000, 25000),
    "slashai/gpt-5.4": (5000, 25000),
    "slashai/gpt-5.4-mini": (50, 200),
    "slashai/gpt-5.4-nano": (50, 200),
    "slashai/gpt-5.4-pro": (5000, 25000),
    "slashai/gpt-5.4-review": (5000, 25000),
    "slashai/gpt-5.5": (5000, 25000),
    "slashai/gpt-5.5-instant": (50, 200),
    "slashai/gpt-5.5-review": (5000, 25000),
    "slashai/kimi-k2.5": (500, 2000),
    "slashai/mimo-v2-flash": (50, 200),
    "slashai/mimo-v2-omni": (500, 2000),
    "slashai/mimo-v2-pro": (500, 2000),
    "slashai/mimo-v2.5": (500, 2000),
    "slashai/mimo-v2.5-pro": (500, 2000),
    "slashai/minimax-m2.5": (50, 200),
    "slashai/minimax-m2.7": (50, 200),
    "slashai/qwen3-coder-next": (500, 2000),
}

CHEAP_MODELS = [
    "slashai/gemini-3-flash",
    "slashai/gemini-3.1-pro",
    "slashai/gpt-5-nano",
    "slashai/gpt-5-mini",
    "slashai/gpt-5.4-nano",
    "slashai/gpt-5.4-mini",
    "slashai/gpt-5.5-instant",
    "slashai/mimo-v2-flash",
    "slashai/Step-3.5-Flash",
    "slashai/MiniMax-M2.5",
    "slashai/MiniMax-M2.7",
    "slashai/claude-haiku-4.5",
    "slashai/gpt-5-codex-mini",
    "slashai/gpt-5.1-codex-mini",
    "slashai/gpt-5.3-codex-spark",
    "slashai/gpt-5.3-codex-low",
]

FAST_FALLBACKS = [
    "slashai/gemini-3-flash",
    "slashai/gemini-3.1-pro",
    "slashai/mimo-v2-flash",
    "slashai/gpt-5-nano",
    "slashai/gpt-5-mini",
]

PERSONAL_ASSISTANT_PROMPT = """
Kamu adalah asisten pribadi AI berbahasa Indonesia.
Jawab dengan jelas, natural, singkat, dan langsung membantu.
Utamakan jawaban praktis. Jangan bertele-tele.
Jika pengguna meminta kode, berikan kode yang siap pakai.
Jika informasi kurang, tetap bantu dengan asumsi yang masuk akal dan sebutkan asumsi singkat.
"""


# =========================================================
# HELPER
# =========================================================
def rupiah(value: float) -> str:
    return f"Rp {value:,.4f}".replace(",", "X").replace(".", ",").replace("X", ".")


def estimate_tokens(text: str) -> int:
    # Estimasi kasar 1 token sekitar 4 karakter.
    return max(1, len(text) // 4)


def estimate_cost_from_usage(model: str, usage: Dict[str, Any]) -> float:
    input_price, output_price = MODEL_PRICES.get(model, (0, 0))
    prompt_tokens = usage.get("prompt_tokens", 0) or 0
    completion_tokens = usage.get("completion_tokens", 0) or 0
    return (prompt_tokens / 1_000_000 * input_price) + (completion_tokens / 1_000_000 * output_price)


def get_price_label(model: str) -> str:
    inp, out = MODEL_PRICES.get(model, (0, 0))
    if inp == 0 and out == 0:
        return f"{model} — harga tidak diketahui"
    return f"{model} — Rp{int(inp)}/Rp{int(out)} per 1M token"


def is_gpt5_family(model: str) -> bool:
    low = model.lower()
    return "gpt-5" in low or "gpt5" in low


def is_gpt51_or_newer_name(model: str) -> bool:
    low = model.lower()
    # Cukup aman untuk prefix slashai/gpt-5.1, 5.2, 5.3, 5.4, 5.5
    return any(tag in low for tag in ["gpt-5.1", "gpt-5.2", "gpt-5.3", "gpt-5.4", "gpt-5.5"])


def extract_text_from_content(content: Any) -> str:
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
                # Beberapa API mengembalikan content block.
                for key in ("text", "content", "output_text"):
                    val = item.get(key)
                    if isinstance(val, str):
                        parts.append(val)
                    elif isinstance(val, dict) and isinstance(val.get("value"), str):
                        parts.append(val["value"])
        return "\n".join([p for p in parts if p]).strip()
    if isinstance(content, dict):
        for key in ("text", "content", "output_text"):
            val = content.get(key)
            if isinstance(val, str):
                return val.strip()
    return str(content).strip()


def parse_openai_compatible_response(data: Dict[str, Any]) -> str:
    # Format Chat Completions
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] or {}

        message = choice.get("message") or {}
        if isinstance(message, dict):
            text = extract_text_from_content(message.get("content"))
            if text:
                return text

            # Kadang refusal ada walau content kosong
            refusal = extract_text_from_content(message.get("refusal"))
            if refusal:
                return refusal

        # Format lama/completions
        text = extract_text_from_content(choice.get("text"))
        if text:
            return text

        delta = choice.get("delta") or {}
        if isinstance(delta, dict):
            text = extract_text_from_content(delta.get("content"))
            if text:
                return text

    # Format Responses API / proxy tertentu
    for key in ("output_text", "text", "content"):
        text = extract_text_from_content(data.get(key))
        if text:
            return text

    output = data.get("output")
    if isinstance(output, list):
        parts = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") in ("message", "output_text"):
                text = extract_text_from_content(item.get("content") or item.get("text"))
                if text:
                    parts.append(text)
            else:
                text = extract_text_from_content(item.get("content") or item.get("text"))
                if text:
                    parts.append(text)
        if parts:
            return "\n".join(parts).strip()

    return ""


def build_messages(user_messages: List[Dict[str, str]], max_history_turns: int, answer_limit: str) -> List[Dict[str, str]]:
    limited_history = user_messages[-max_history_turns * 2 :] if max_history_turns > 0 else []
    system_prompt = PERSONAL_ASSISTANT_PROMPT.strip() + f"\n\nBatas gaya jawaban: {answer_limit}"
    return [{"role": "system", "content": system_prompt}] + limited_history


def build_payload(
    model: str,
    messages: List[Dict[str, str]],
    max_completion_tokens: int,
    temperature: float,
    reasoning_effort: str,
    omit_token_limit_for_no_reasoning: bool,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    # Masalah utama:
    # GPT-5 reasoning bisa menghabiskan semua completion token untuk reasoning_tokens,
    # lalu content menjadi kosong. Karena itu perlu reasoning_effort rendah/minimal/none.
    if is_gpt5_family(model):
        if reasoning_effort != "auto":
            payload["reasoning_effort"] = reasoning_effort

        # Untuk GPT-5.1+ mode "none", sebagian endpoint lebih stabil jika token limit tidak dikirim.
        # Jika user tetap ingin limit, matikan opsi ini di sidebar.
        if not (omit_token_limit_for_no_reasoning and reasoning_effort == "none" and is_gpt51_or_newer_name(model)):
            payload["max_completion_tokens"] = max_completion_tokens
    else:
        # Untuk non GPT-5, kebanyakan OpenAI-compatible masih menerima max_tokens.
        payload["max_tokens"] = max_completion_tokens

    return payload


def post_to_api(payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    if not API_KEY:
        raise RuntimeError("API key belum diisi. Masukkan SLASHAI_API_KEY di Streamlit Secrets.")

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    response = requests.post(API_URL, headers=headers, json=payload, timeout=timeout)
    try:
        data = response.json()
    except Exception:
        data = {"raw_text": response.text}

    if response.status_code >= 400:
        raise RuntimeError(f"API mengembalikan status {response.status_code}: {json.dumps(data, ensure_ascii=False)}")

    return data


def call_model_once(
    model: str,
    messages: List[Dict[str, str]],
    max_completion_tokens: int,
    temperature: float,
    reasoning_effort: str,
    timeout: int,
    omit_token_limit_for_no_reasoning: bool,
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    payload = build_payload(
        model=model,
        messages=messages,
        max_completion_tokens=max_completion_tokens,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        omit_token_limit_for_no_reasoning=omit_token_limit_for_no_reasoning,
    )
    data = post_to_api(payload, timeout=timeout)
    text = parse_openai_compatible_response(data)
    return text, data, payload


def is_empty_due_to_reasoning_limit(data: Dict[str, Any]) -> bool:
    try:
        choice = data.get("choices", [{}])[0]
        finish_reason = choice.get("finish_reason")
        content = (choice.get("message") or {}).get("content")
        usage = data.get("usage") or {}
        completion_tokens = usage.get("completion_tokens", 0) or 0
        details = usage.get("completion_tokens_details") or {}
        reasoning_tokens = details.get("reasoning_tokens", 0) or 0
        return (
            finish_reason == "length"
            and (content is None or content == "")
            and reasoning_tokens > 0
            and reasoning_tokens >= completion_tokens * 0.8
        )
    except Exception:
        return False


def chat_with_smart_retry(
    selected_model: str,
    messages: List[Dict[str, str]],
    mode: str,
    timeout: int,
    temperature: float,
    allow_fallback: bool,
    debug: bool,
) -> Tuple[str, str, Dict[str, Any], List[str], Optional[Dict[str, Any]]]:
    """
    Return:
    text, used_model, raw_response, logs, last_payload
    """
    logs: List[str] = []

    if mode == "Super Hemat":
        max_tokens = 768
        reasoning_effort = "minimal"
        omit_limit_for_none = False
    elif mode == "Stabil GPT-5":
        max_tokens = 4096
        reasoning_effort = "minimal"
        omit_limit_for_none = False
    else:  # Jawaban Panjang
        max_tokens = 6144
        reasoning_effort = "minimal"
        omit_limit_for_none = False

    models_to_try = [selected_model]
    if allow_fallback:
        for m in FAST_FALLBACKS:
            if m not in models_to_try:
                models_to_try.append(m)

    last_raw: Dict[str, Any] = {}
    last_payload: Optional[Dict[str, Any]] = None
    last_error = ""

    for model in models_to_try:
        try:
            logs.append(f"Mencoba model: {model}")

            # Percobaan 1
            text, raw, payload = call_model_once(
                model=model,
                messages=messages,
                max_completion_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                timeout=timeout,
                omit_token_limit_for_no_reasoning=omit_limit_for_none,
            )
            last_raw = raw
            last_payload = payload

            if text:
                return text, model, raw, logs, payload

            # Jika kosong karena reasoning token habis, ulang dengan token lebih besar.
            if is_gpt5_family(model) and is_empty_due_to_reasoning_limit(raw):
                logs.append("Jawaban kosong karena reasoning_tokens menghabiskan batas output. Mencoba ulang dengan token lebih besar.")
                text, raw, payload = call_model_once(
                    model=model,
                    messages=messages,
                    max_completion_tokens=8192,
                    temperature=temperature,
                    reasoning_effort="minimal",
                    timeout=timeout,
                    omit_token_limit_for_no_reasoning=False,
                )
                last_raw = raw
                last_payload = payload
                if text:
                    return text, model, raw, logs, payload

                # Untuk model GPT-5.1+ yang mendukung none, coba tanpa max_completion_tokens.
                if is_gpt51_or_newer_name(model):
                    logs.append("Masih kosong. Mencoba reasoning_effort none tanpa batas token.")
                    text, raw, payload = call_model_once(
                        model=model,
                        messages=messages,
                        max_completion_tokens=8192,
                        temperature=temperature,
                        reasoning_effort="none",
                        timeout=timeout,
                        omit_token_limit_for_no_reasoning=True,
                    )
                    last_raw = raw
                    last_payload = payload
                    if text:
                        return text, model, raw, logs, payload

            # Jika kosong bukan karena reasoning, lanjut fallback.
            logs.append("Model mengembalikan respons kosong. Lanjut model cadangan.")

        except Exception as e:
            last_error = str(e)
            logs.append(f"Gagal pada {model}: {last_error}")

            # Jika endpoint menolak reasoning_effort, ulang tanpa parameter reasoning.
            if is_gpt5_family(model) and ("reasoning" in last_error.lower() or "unsupported" in last_error.lower() or "invalid" in last_error.lower()):
                try:
                    logs.append("Mencoba ulang tanpa reasoning_effort.")
                    text, raw, payload = call_model_once(
                        model=model,
                        messages=messages,
                        max_completion_tokens=4096,
                        temperature=temperature,
                        reasoning_effort="auto",  # tidak dikirim ke payload
                        timeout=timeout,
                        omit_token_limit_for_no_reasoning=False,
                    )
                    last_raw = raw
                    last_payload = payload
                    if text:
                        return text, model, raw, logs, payload
                except Exception as e2:
                    last_error = str(e2)
                    logs.append(f"Retry tanpa reasoning_effort tetap gagal: {last_error}")

    msg = "Maaf, semua model gagal atau mengembalikan jawaban kosong."
    if last_error:
        msg += f"\n\nError terakhir: {last_error}"
    return msg, selected_model, last_raw, logs, last_payload


# =========================================================
# STATE
# =========================================================
if "messages" not in st.session_state:
    st.session_state.messages = []

if "last_raw" not in st.session_state:
    st.session_state.last_raw = {}

if "last_payload" not in st.session_state:
    st.session_state.last_payload = {}

if "last_logs" not in st.session_state:
    st.session_state.last_logs = []


# =========================================================
# UI SIDEBAR
# =========================================================
st.sidebar.title("⚙️ Pengaturan")

cheap_only = st.sidebar.checkbox("Tampilkan model hemat Rp50/Rp200 saja", value=True)
available_models = CHEAP_MODELS if cheap_only else list(MODEL_PRICES.keys())

if DEFAULT_MODEL not in available_models:
    available_models = [DEFAULT_MODEL] + available_models

selected_model = st.sidebar.selectbox(
    "Model",
    available_models,
    index=available_models.index(DEFAULT_MODEL) if DEFAULT_MODEL in available_models else 0,
    format_func=get_price_label,
)

mode = st.sidebar.radio(
    "Mode",
    ["Super Hemat", "Stabil GPT-5", "Jawaban Panjang"],
    index=1,
    help=(
        "Super Hemat membatasi output. Stabil GPT-5 memberi token lebih besar agar GPT-5 tidak kosong. "
        "Jawaban Panjang cocok untuk tugas yang butuh uraian."
    ),
)

max_history_turns = st.sidebar.slider("Riwayat yang dikirim ke API", 1, 8, 3)
temperature = st.sidebar.slider("Kreativitas", 0.0, 1.0, 0.3, 0.1)
timeout = st.sidebar.slider("Timeout API/detik", 15, 120, 60)
allow_fallback = st.sidebar.checkbox("Auto fallback ke model hemat", value=True)
debug_mode = st.sidebar.checkbox("Tampilkan debug raw response", value=False)

answer_limit = st.sidebar.selectbox(
    "Gaya jawaban",
    [
        "Jawab ringkas dan langsung ke inti.",
        "Jawab sedang, jelas, dan beri contoh bila perlu.",
        "Jawab lengkap, rapi, dan sistematis.",
    ],
    index=1,
)

if st.sidebar.button("🧪 Tes koneksi API"):
    test_messages = [
        {"role": "system", "content": "Jawab hanya dengan satu kata: OK"},
        {"role": "user", "content": "Tes koneksi"},
    ]
    with st.sidebar:
        with st.spinner("Menguji..."):
            text, used_model, raw, logs, payload = chat_with_smart_retry(
                selected_model=selected_model,
                messages=test_messages,
                mode="Stabil GPT-5",
                timeout=timeout,
                temperature=0.0,
                allow_fallback=allow_fallback,
                debug=True,
            )
            if text and not text.startswith("Maaf, semua model gagal"):
                st.success(f"Berhasil dengan {used_model}: {text}")
            else:
                st.error(text)
            with st.expander("Log tes"):
                st.write(logs)
            if debug_mode:
                with st.expander("Raw response"):
                    st.json(raw)
                with st.expander("Payload terakhir"):
                    st.json(payload or {})

if st.sidebar.button("🧹 Hapus riwayat chat"):
    st.session_state.messages = []
    st.session_state.last_raw = {}
    st.session_state.last_payload = {}
    st.session_state.last_logs = []
    st.rerun()


# =========================================================
# UI UTAMA
# =========================================================
st.title("🤖 Asisten Pribadi AI")
st.caption("Streamlit + API kompatibel OpenAI SlashAI. Versi fix untuk respons kosong GPT-5 reasoning.")

if not API_KEY:
    st.error("API key belum diisi. Tambahkan SLASHAI_API_KEY di Streamlit Secrets.")
    st.stop()

input_price, output_price = MODEL_PRICES.get(selected_model, (0, 0))
if input_price and output_price:
    st.info(f"Model aktif: `{selected_model}` • Harga: Rp{int(input_price)}/1M input token dan Rp{int(output_price)}/1M output token")

# Tampilkan riwayat
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("Tulis pertanyaan kamu...")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.markdown(prompt)

    api_messages = build_messages(
        user_messages=st.session_state.messages,
        max_history_turns=max_history_turns,
        answer_limit=answer_limit,
    )

    with st.chat_message("assistant"):
        placeholder = st.empty()
        with st.spinner("Sedang menjawab..."):
            text, used_model, raw, logs, payload = chat_with_smart_retry(
                selected_model=selected_model,
                messages=api_messages,
                mode=mode,
                timeout=timeout,
                temperature=temperature,
                allow_fallback=allow_fallback,
                debug=debug_mode,
            )

        placeholder.markdown(text)

        st.session_state.messages.append({"role": "assistant", "content": text})
        st.session_state.last_raw = raw
        st.session_state.last_payload = payload or {}
        st.session_state.last_logs = logs

        usage = raw.get("usage") if isinstance(raw, dict) else None
        if isinstance(usage, dict):
            cost = estimate_cost_from_usage(used_model, usage)
            completion_details = usage.get("completion_tokens_details") or {}
            reasoning_tokens = completion_details.get("reasoning_tokens", 0)

            st.caption(
                f"Model terpakai: `{used_model}` • "
                f"Input token: {usage.get('prompt_tokens', 0)} • "
                f"Output token: {usage.get('completion_tokens', 0)} • "
                f"Reasoning token: {reasoning_tokens} • "
                f"Estimasi biaya: {rupiah(cost)}"
            )
        else:
            estimated = estimate_tokens(json.dumps(api_messages, ensure_ascii=False))
            st.caption(f"Model terpakai: `{used_model}` • Estimasi input token: ±{estimated}")

if debug_mode:
    st.divider()
    st.subheader("Debug")
    with st.expander("Log percobaan model"):
        st.write(st.session_state.last_logs)
    with st.expander("Payload terakhir"):
        st.json(st.session_state.last_payload)
    with st.expander("Raw response terakhir"):
        st.json(st.session_state.last_raw)
