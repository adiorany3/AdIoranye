
import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import streamlit as st


# =========================
# Konfigurasi dasar
# =========================

st.set_page_config(
    page_title="Asisten Pribadi AI",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)


def secret(name: str, default: str = "") -> str:
    """Ambil secret Streamlit dengan aman."""
    try:
        value = st.secrets.get(name, default)
        return str(value) if value is not None else default
    except Exception:
        return default


API_URL = secret("SLASHAI_API_URL", "https://api.slashai.my.id/v1/chat/completions")
API_KEY = secret("SLASHAI_API_KEY", "")
DEFAULT_MODEL = secret("SLASHAI_MODEL", "slashai/gemini-3-flash")
MEMORY_FILE = secret("MEMORY_FILE", "assistant_memory.json")

DEFAULT_PERSONA = secret(
    "ASSISTANT_PERSONA",
    (
        "Kamu adalah asisten pribadi yang cepat, hemat token, ramah, dan to the point. "
        "Jawab dalam bahasa Indonesia yang natural. "
        "Bantu pengguna mengerjakan tugas teknis, akademik, bisnis, otomasi, coding, dan penulisan. "
        "Jika konteks kurang, tetap beri jawaban terbaik berdasarkan informasi yang tersedia. "
        "Jangan terlalu panjang kecuali diminta."
    ),
)

CHEAP_MODELS = [
    "slashai/gemini-3-flash",
    "slashai/gemini-3.1-pro",
    "slashai/gpt-5-nano",
    "slashai/gpt-5-mini",
    "slashai/gpt-5.4-nano",
    "slashai/gpt-5.4-mini",
    "slashai/gpt-5.5-instant",
    "slashai/gpt-5-codex-mini",
    "slashai/gpt-5.1-codex-mini",
    "slashai/gpt-5.3-codex-low",
    "slashai/gpt-5.3-codex-spark",
    "slashai/mimo-v2-flash",
    "slashai/minimax-m2.5",
    "slashai/minimax-m2.7",
    "slashai/MiniMax-M2.5",
    "slashai/MiniMax-M2.7",
    "slashai/Step-3.5-Flash",
    "slashai/claude-haiku-4.5",
    "bai/claude-haiku-4.5",
    "bai/deepseek-v4-flash",
    "cmc/MiniMaxAI/MiniMax-M2.5",
]

MID_MODELS = [
    "slashai/claude-sonnet-4.5",
    "slashai/claude-sonnet-4.6",
    "slashai/Qwen3.6-Plus",
    "slashai/qwen3-coder-next",
    "slashai/Kimi-K2.5",
    "slashai/Kimi-K2.6",
    "slashai/GLM-5",
    "slashai/GLM-5.1",
    "slashai/glm-5",
    "slashai/glm-5.1",
    "slashai/deepseek-3.2",
    "slashai/deepseek-v3.2",
    "slashai/mimo-v2-omni",
    "slashai/mimo-v2-pro",
    "slashai/mimo-v2.5",
    "slashai/mimo-v2.5-pro",
    "bai/claude-sonnet-4.5",
    "bai/deepseek-v4-pro",
    "bai/glm-5",
]

PREMIUM_MODELS = [
    "slashai/gpt-5.2",
    "slashai/gpt-5.4",
    "slashai/gpt-5.5",
    "slashai/gpt-5.1",
    "slashai/gpt-5-codex",
    "slashai/claude-opus-4.5",
    "slashai/claude-opus-4.6",
    "slashai/claude-opus-4.7",
    "slashai/claude-sonnet-4.7",
    "slashai/Qwen3.6-Max-Preview",
    "cx/gpt-5.2",
    "cx/gpt-5.4",
    "cx/gpt-5.5",
]

ALL_MODELS = []
for m in CHEAP_MODELS + MID_MODELS + PREMIUM_MODELS:
    if m not in ALL_MODELS:
        ALL_MODELS.append(m)

# Harga kasar dari daftar user: Rp per 1M token.
PRICE_TABLE = {
    # murah
    "slashai/gemini-3-flash": (50, 200),
    "slashai/gemini-3.1-pro": (50, 200),
    "slashai/gpt-5-nano": (50, 200),
    "slashai/gpt-5-mini": (50, 200),
    "slashai/gpt-5.4-nano": (50, 200),
    "slashai/gpt-5.4-mini": (50, 200),
    "slashai/gpt-5.5-instant": (50, 200),
    "slashai/mimo-v2-flash": (50, 200),
    "slashai/minimax-m2.5": (50, 200),
    "slashai/minimax-m2.7": (50, 200),
    "slashai/MiniMax-M2.5": (50, 200),
    "slashai/MiniMax-M2.7": (50, 200),
    "slashai/Step-3.5-Flash": (50, 200),
    "slashai/claude-haiku-4.5": (50, 200),
    "bai/claude-haiku-4.5": (50, 200),
    "bai/deepseek-v4-flash": (50, 200),
    "cmc/MiniMaxAI/MiniMax-M2.5": (50, 200),
    # menengah
    "slashai/claude-sonnet-4.5": (500, 2000),
    "slashai/claude-sonnet-4.6": (500, 2000),
    "slashai/Qwen3.6-Plus": (500, 2000),
    "slashai/qwen3-coder-next": (500, 2000),
    "slashai/Kimi-K2.5": (500, 2000),
    "slashai/Kimi-K2.6": (500, 2000),
    "slashai/GLM-5": (500, 2000),
    "slashai/GLM-5.1": (500, 2000),
    "slashai/glm-5": (500, 2000),
    "slashai/glm-5.1": (500, 2000),
    # mahal
    "slashai/deepseek-v4-flash": (1500, 6000),
    "slashai/deepseek-v4-pro": (4000, 18000),
    "slashai/gpt-5.2": (5000, 25000),
    "slashai/gpt-5.4": (5000, 25000),
    "slashai/gpt-5.5": (5000, 25000),
}


# =========================
# Utility memory/persona
# =========================

def memory_path() -> Path:
    path = Path(MEMORY_FILE)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def default_store() -> Dict[str, Any]:
    return {
        "persona": DEFAULT_PERSONA,
        "memories": [],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def load_store() -> Dict[str, Any]:
    path = memory_path()
    if not path.exists():
        return default_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default_store()
        data.setdefault("persona", DEFAULT_PERSONA)
        data.setdefault("memories", [])
        return data
    except Exception:
        return default_store()


def save_store(store: Dict[str, Any]) -> None:
    store["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path = memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def add_memory(text: str, source: str = "manual") -> None:
    text = clean_text(text)
    if not text:
        return
    store = st.session_state.store
    # Hindari duplikat persis.
    existing = [m.get("text", "").strip().lower() for m in store.get("memories", [])]
    if text.lower() in existing:
        return
    store["memories"].append(
        {
            "id": str(uuid.uuid4())[:8],
            "text": text,
            "source": source,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    # Batasi agar hemat token.
    store["memories"] = store["memories"][-100:]
    save_store(store)


def delete_memory(memory_id: str) -> bool:
    store = st.session_state.store
    before = len(store.get("memories", []))
    store["memories"] = [m for m in store.get("memories", []) if m.get("id") != memory_id]
    after = len(store.get("memories", []))
    save_store(store)
    return after < before


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def tokenish_len(text: str) -> int:
    """Estimasi kasar token, bukan hitungan resmi."""
    if not text:
        return 0
    return max(1, len(text) // 4)


STOPWORDS = {
    "yang", "dan", "di", "ke", "dari", "untuk", "dengan", "atau", "ini", "itu", "saya", "aku", "kamu",
    "buat", "tolong", "minta", "adalah", "sebagai", "dalam", "pada", "agar", "biar", "tidak", "gak",
    "nggak", "nya", "the", "a", "an", "is", "are", "of", "to", "in",
}


def keywords(text: str) -> set:
    words = re.findall(r"[A-Za-zÀ-ÿ0-9_./-]{3,}", (text or "").lower())
    return {w for w in words if w not in STOPWORDS}


def relevant_memories(query: str, limit: int = 8, max_chars: int = 1600) -> List[str]:
    """Ambil memori yang relevan saja agar tidak boros token."""
    memories = st.session_state.store.get("memories", [])
    if not memories:
        return []

    qwords = keywords(query)
    scored = []
    for idx, mem in enumerate(memories):
        text = mem.get("text", "")
        mwords = keywords(text)
        overlap = len(qwords & mwords)
        # Recency tetap dihargai sedikit.
        recency_bonus = idx / max(1, len(memories)) * 0.25
        score = overlap + recency_bonus
        scored.append((score, idx, text))

    # Jika tidak ada overlap, tetap ambil beberapa memori terbaru, tapi sedikit saja.
    if not any(score >= 1 for score, _, _ in scored):
        selected = [m.get("text", "") for m in memories[-3:]]
    else:
        selected = [text for score, _, text in sorted(scored, reverse=True) if score >= 1][:limit]

    result = []
    total = 0
    for text in selected:
        text = clean_text(text)
        if not text:
            continue
        if total + len(text) > max_chars:
            break
        result.append(text)
        total += len(text)
    return result


def get_recent_messages(max_turns: int) -> List[Dict[str, str]]:
    """Ambil N turn terakhir saja agar request cepat dan murah."""
    messages = st.session_state.get("messages", [])
    if max_turns <= 0:
        return []
    # 1 turn kira-kira user+assistant, jadi ambil 2*turns pesan terakhir.
    recent = messages[-(max_turns * 2):]
    cleaned = []
    for msg in recent:
        role = msg.get("role")
        content = clean_text(msg.get("content", ""))
        if role in {"user", "assistant"} and content:
            cleaned.append({"role": role, "content": content})
    return cleaned


def build_system_prompt(user_prompt: str) -> str:
    persona = st.session_state.store.get("persona") or DEFAULT_PERSONA
    mems = relevant_memories(user_prompt)
    memory_block = ""
    if mems:
        memory_block = "\n\nMEMORI PENGGUNA YANG RELEVAN:\n" + "\n".join(f"- {m}" for m in mems)

    return (
        f"{persona}"
        f"{memory_block}\n\n"
        "ATURAN JAWABAN:\n"
        "- Gunakan memori hanya jika relevan.\n"
        "- Jangan menyebutkan daftar memori kecuali pengguna meminta.\n"
        "- Jawab langsung, praktis, dan tidak bertele-tele.\n"
        "- Untuk pertanyaan teknis, berikan langkah yang bisa langsung dijalankan.\n"
        "- Jika pengguna meminta menyimpan hal baru, arahkan gunakan format: /ingat isi memori."
    )


def build_messages(user_prompt: str, max_turns: int) -> List[Dict[str, str]]:
    system = {"role": "system", "content": build_system_prompt(user_prompt)}
    recent = get_recent_messages(max_turns=max_turns)
    return [system] + recent


# =========================
# Local commands: tidak perlu panggil API
# =========================

def handle_local_command(prompt: str) -> Optional[str]:
    raw = prompt.strip()
    low = raw.lower().strip()

    remember_patterns = [
        r"^/ingat\s+(.+)$",
        r"^ingat(?:kan)?\s+bahwa\s+(.+)$",
        r"^simpan\s+memori\s*:?\s*(.+)$",
        r"^memory\s*:?\s*(.+)$",
    ]
    for pat in remember_patterns:
        m = re.match(pat, raw, flags=re.IGNORECASE | re.DOTALL)
        if m:
            memory_text = clean_text(m.group(1))
            add_memory(memory_text, source="chat_command")
            return f"Siap, saya simpan ke memori: “{memory_text}”"

    if low in {"/memori", "/memory", "lihat memori", "tampilkan memori"}:
        memories = st.session_state.store.get("memories", [])
        if not memories:
            return "Belum ada memori yang tersimpan."
        lines = ["Memori yang tersimpan:"]
        for i, mem in enumerate(memories[-20:], start=1):
            lines.append(f"{i}. {mem.get('text', '')}")
        return "\n".join(lines)

    m = re.match(r"^/lupa\s+(.+)$", raw, flags=re.IGNORECASE | re.DOTALL)
    if m:
        key = clean_text(m.group(1)).lower()
        store = st.session_state.store
        before = len(store.get("memories", []))
        store["memories"] = [
            mem for mem in store.get("memories", [])
            if key not in mem.get("text", "").lower() and key != mem.get("id", "").lower()
        ]
        removed = before - len(store.get("memories", []))
        save_store(store)
        if removed:
            return f"Sudah saya hapus {removed} memori yang cocok dengan: “{key}”."
        return f"Tidak ada memori yang cocok dengan: “{key}”."

    if low in {"/reset memori", "/hapus semua memori", "reset memori"}:
        st.session_state.store["memories"] = []
        save_store(st.session_state.store)
        return "Semua memori sudah dihapus."

    m = re.match(r"^/persona\s+(.+)$", raw, flags=re.IGNORECASE | re.DOTALL)
    if m:
        new_persona = clean_text(m.group(1))
        st.session_state.store["persona"] = new_persona
        save_store(st.session_state.store)
        return "Persona asisten sudah diperbarui."

    if low in {"/help", "bantuan memori"}:
        return (
            "Perintah lokal tanpa memanggil API:\n"
            "- `/ingat nama saya Budi` untuk menyimpan memori.\n"
            "- `/memori` untuk melihat memori.\n"
            "- `/lupa kata_kunci` untuk menghapus memori tertentu.\n"
            "- `/reset memori` untuk menghapus semua memori.\n"
            "- `/persona ...` untuk mengganti persona asisten."
        )

    return None


# =========================
# API OpenAI-compatible
# =========================

def is_gpt_reasoning_model(model: str) -> bool:
    lower = model.lower()
    return "gpt-5" in lower or "codex" in lower


def payload_token_field(model: str) -> str:
    # Model GPT-5/reasoning lebih aman memakai max_completion_tokens.
    if is_gpt_reasoning_model(model):
        return "max_completion_tokens"
    return "max_tokens"


def make_payload(
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_output_tokens: int,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    field = payload_token_field(model)
    payload[field] = max_output_tokens

    # Reasoning dibuat minimal agar token tidak habis di reasoning_tokens dan content tidak kosong.
    if is_gpt_reasoning_model(model):
        payload["reasoning_effort"] = "minimal"
        # Beberapa gateway mengikuti Responses API style, sebagian mengabaikan field ini.
        payload["verbosity"] = "low"

    return payload


def parse_content(data: Dict[str, Any]) -> str:
    # Format umum chat.completion
    choices = data.get("choices") or []
    if choices:
        choice = choices[0] or {}
        msg = choice.get("message") or {}
        content = msg.get("content")

        if isinstance(content, str):
            return content.strip()

        # Content kadang berupa list part.
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if isinstance(item.get("text"), str):
                        parts.append(item["text"])
                    elif isinstance(item.get("content"), str):
                        parts.append(item["content"])
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts).strip()

        if isinstance(choice.get("text"), str):
            return choice["text"].strip()

    # Format lain yang kadang dipakai gateway
    if isinstance(data.get("output_text"), str):
        return data["output_text"].strip()

    output = data.get("output")
    if isinstance(output, list):
        parts = []
        for item in output:
            if isinstance(item, dict):
                if isinstance(item.get("content"), list):
                    for c in item["content"]:
                        if isinstance(c, dict) and isinstance(c.get("text"), str):
                            parts.append(c["text"])
                elif isinstance(item.get("text"), str):
                    parts.append(item["text"])
        return "\n".join(parts).strip()

    return ""


def usage_cost_idr(model: str, data: Dict[str, Any]) -> Tuple[Optional[float], str]:
    if isinstance(data.get("_resell"), dict) and data["_resell"].get("cost_idr") is not None:
        try:
            return float(data["_resell"]["cost_idr"]), "provider"
        except Exception:
            pass

    usage = data.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    price = PRICE_TABLE.get(model)
    if not price:
        return None, "unknown"
    input_price, output_price = price
    cost = (prompt_tokens / 1_000_000 * input_price) + (completion_tokens / 1_000_000 * output_price)
    return cost, "estimated"


def call_once(
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_output_tokens: int,
    timeout: int,
) -> Tuple[str, Dict[str, Any]]:
    if not API_KEY:
        raise RuntimeError("SLASHAI_API_KEY belum diisi di Streamlit Secrets.")

    payload = make_payload(model, messages, temperature, max_output_tokens)
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(API_URL, headers=headers, json=payload, timeout=timeout)
    except requests.Timeout as exc:
        raise RuntimeError(f"Timeout: API tidak merespons dalam {timeout} detik.") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Gagal menghubungi API: {exc}") from exc

    # Kalau field token tidak cocok, coba satu kali dengan field alternatif.
    if r.status_code >= 400 and ("max_tokens" in r.text or "max_completion_tokens" in r.text):
        alt_payload = dict(payload)
        if "max_tokens" in alt_payload:
            alt_payload["max_completion_tokens"] = alt_payload.pop("max_tokens")
        elif "max_completion_tokens" in alt_payload:
            alt_payload["max_tokens"] = alt_payload.pop("max_completion_tokens")
        r = requests.post(API_URL, headers=headers, json=alt_payload, timeout=timeout)

    if r.status_code >= 400:
        raise RuntimeError(f"API mengembalikan status {r.status_code}: {r.text[:1200]}")

    try:
        data = r.json()
    except Exception as exc:
        raise RuntimeError(f"Respons API bukan JSON valid: {r.text[:1200]}") from exc

    content = parse_content(data)
    if not content:
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        reasoning_tokens = details.get("reasoning_tokens", 0)
        finish_reason = ""
        try:
            finish_reason = data.get("choices", [{}])[0].get("finish_reason", "")
        except Exception:
            pass

        # Kasus GPT-5: semua token habis untuk reasoning.
        if reasoning_tokens or finish_reason == "length":
            raise RuntimeError(
                "Respons kosong. Kemungkinan token output habis untuk reasoning_tokens. "
                "Naikkan Max output tokens atau pakai model non-reasoning seperti gemini/mimo/minimax."
            )
        raise RuntimeError("Respons API berhasil, tetapi isi jawaban kosong.")

    return content, data


def call_with_fallback(
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_output_tokens: int,
    timeout: int,
    auto_fallback: bool,
    fallback_models: List[str],
) -> Tuple[str, Dict[str, Any], str, List[str]]:
    tried = []
    errors = []

    models_to_try = [model]
    if auto_fallback:
        for m in fallback_models:
            if m not in models_to_try:
                models_to_try.append(m)

    for m in models_to_try:
        tried.append(m)
        # GPT-5 butuh ruang output lebih besar agar tidak kosong.
        current_max = max(max_output_tokens, 1600) if is_gpt_reasoning_model(m) else max_output_tokens
        try:
            content, data = call_once(m, messages, temperature, current_max, timeout)
            return content, data, m, tried
        except Exception as exc:
            errors.append(f"{m}: {exc}")

    raise RuntimeError("Semua model gagal.\n\n" + "\n\n".join(errors[-3:]))


# =========================
# Init session
# =========================

if "store" not in st.session_state:
    st.session_state.store = load_store()

if "messages" not in st.session_state:
    st.session_state.messages = []

if "last_raw" not in st.session_state:
    st.session_state.last_raw = None

if "last_payload_messages" not in st.session_state:
    st.session_state.last_payload_messages = None


# =========================
# Sidebar
# =========================

with st.sidebar:
    st.title("⚙️ Pengaturan")

    if API_KEY:
        st.success("API key terbaca dari Secrets.")
    else:
        st.error("API key belum ada. Isi SLASHAI_API_KEY di Secrets.")

    st.subheader("Model")
    mode = st.selectbox(
        "Mode kerja",
        ["Super Hemat", "Cepat Seimbang", "Stabil GPT-5", "Lebih Pintar"],
        index=1,
        help="Super Hemat membatasi konteks dan output. Stabil GPT-5 memberi token lebih besar untuk model reasoning.",
    )

    model_group = st.radio(
        "Daftar model",
        ["Model murah saja", "Semua model", "Custom"],
        index=0,
        horizontal=False,
    )

    if model_group == "Model murah saja":
        model = st.selectbox("Model utama", CHEAP_MODELS, index=CHEAP_MODELS.index(DEFAULT_MODEL) if DEFAULT_MODEL in CHEAP_MODELS else 0)
    elif model_group == "Semua model":
        model = st.selectbox("Model utama", ALL_MODELS, index=ALL_MODELS.index(DEFAULT_MODEL) if DEFAULT_MODEL in ALL_MODELS else 0)
    else:
        model = st.text_input("Model custom", value=DEFAULT_MODEL)

    if mode == "Super Hemat":
        temperature = 0.3
        max_turns = 2
        max_output_tokens = 500
        fallback_limit = 2
    elif mode == "Cepat Seimbang":
        temperature = 0.5
        max_turns = 4
        max_output_tokens = 900
        fallback_limit = 3
    elif mode == "Stabil GPT-5":
        temperature = 0.4
        max_turns = 3
        max_output_tokens = 2200
        fallback_limit = 3
    else:
        temperature = 0.7
        max_turns = 6
        max_output_tokens = 1800
        fallback_limit = 4

    with st.expander("Tuning lanjutan"):
        temperature = st.slider("Temperature", 0.0, 1.2, float(temperature), 0.1)
        max_turns = st.slider("Jumlah turn terakhir dikirim", 0, 12, int(max_turns), 1)
        max_output_tokens = st.slider("Max output tokens", 200, 4000, int(max_output_tokens), 100)
        timeout = st.slider("Timeout API/detik", 10, 90, 35, 5)
        auto_fallback = st.toggle("Auto fallback model murah", value=True)
        show_debug = st.toggle("Tampilkan debug raw response", value=False)

    fallback_models = [m for m in CHEAP_MODELS if m != model][:fallback_limit]

    st.divider()
    st.subheader("Persona")
    persona_text = st.text_area(
        "Persona asisten",
        value=st.session_state.store.get("persona", DEFAULT_PERSONA),
        height=180,
        help="Persona ini disimpan lokal, lalu dikirim sebagai system prompt ringkas.",
    )
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Simpan persona", use_container_width=True):
            st.session_state.store["persona"] = clean_text(persona_text)
            save_store(st.session_state.store)
            st.success("Persona disimpan.")
    with col_b:
        if st.button("Reset persona", use_container_width=True):
            st.session_state.store["persona"] = DEFAULT_PERSONA
            save_store(st.session_state.store)
            st.rerun()

    st.divider()
    st.subheader("Memory")
    new_memory = st.text_area(
        "Tambah memori manual",
        placeholder="Contoh: Nama saya Raka. Saya suka jawaban singkat dan teknis.",
        height=90,
    )
    if st.button("Tambah memori", use_container_width=True):
        if clean_text(new_memory):
            add_memory(new_memory, source="sidebar")
            st.success("Memori ditambahkan.")
            st.rerun()

    memories = st.session_state.store.get("memories", [])
    st.caption(f"Total memori: {len(memories)}")
    with st.expander("Lihat / hapus memori"):
        if not memories:
            st.info("Belum ada memori.")
        else:
            for mem in memories[-25:][::-1]:
                c1, c2 = st.columns([0.82, 0.18])
                with c1:
                    st.write(f"• {mem.get('text', '')}")
                with c2:
                    if st.button("Hapus", key=f"del_{mem.get('id')}", use_container_width=True):
                        delete_memory(mem.get("id", ""))
                        st.rerun()
        if st.button("Hapus semua memori", use_container_width=True):
            st.session_state.store["memories"] = []
            save_store(st.session_state.store)
            st.rerun()

    st.caption(
        "Perintah cepat di chat: `/ingat ...`, `/memori`, `/lupa ...`, `/reset memori`, `/persona ...`."
    )

    st.divider()
    if st.button("Tes koneksi API", use_container_width=True):
        test_messages = [
            {"role": "system", "content": "Jawab singkat."},
            {"role": "user", "content": "Ketik OK jika koneksi berhasil."},
        ]
        with st.spinner("Mengetes API..."):
            try:
                answer, raw, used_model, tried = call_with_fallback(
                    model=model,
                    messages=test_messages,
                    temperature=0.1,
                    max_output_tokens=200,
                    timeout=timeout,
                    auto_fallback=auto_fallback,
                    fallback_models=fallback_models,
                )
                st.success(f"Berhasil via {used_model}: {answer}")
            except Exception as exc:
                st.error(str(exc))

    if st.button("Hapus chat saat ini", use_container_width=True):
        st.session_state.messages = []
        st.rerun()


# =========================
# Main UI
# =========================

st.title("🤖 Asisten Pribadi AI")
st.caption("Persona + memory lokal agar konteks tetap ingat tanpa mengirim seluruh riwayat chat setiap request.")

info_cols = st.columns(4)
with info_cols[0]:
    st.metric("Model", model[:24] + ("..." if len(model) > 24 else ""))
with info_cols[1]:
    st.metric("Memori", len(st.session_state.store.get("memories", [])))
with info_cols[2]:
    st.metric("Turn dikirim", max_turns)
with info_cols[3]:
    price = PRICE_TABLE.get(model)
    st.metric("Harga", f"Rp{price[0]}/Rp{price[1]}" if price else "Tidak diketahui")

with st.expander("Cara pakai memory", expanded=False):
    st.markdown(
        """
        Gunakan perintah lokal ini agar tidak perlu memanggil API:

        ```text
        /ingat nama saya Raka dan saya sedang membuat aplikasi Streamlit AI
        /memori
        /lupa Raka
        /persona Kamu adalah asisten bisnis yang singkat dan teknis
        ```

        Untuk setiap pertanyaan biasa, aplikasi hanya mengirim:
        1. Persona ringkas.
        2. Memori yang relevan saja.
        3. Beberapa chat terakhir sesuai pengaturan.
        """
    )

# Tampilkan chat
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("Tulis pertanyaan, atau pakai /ingat untuk menyimpan memori...")

if prompt:
    prompt = clean_text(prompt)

    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    local_response = handle_local_command(prompt)

    if local_response is not None:
        with st.chat_message("assistant"):
            st.markdown(local_response)
        st.session_state.messages.append({"role": "assistant", "content": local_response})
        st.stop()

    messages_for_api = build_messages(prompt, max_turns=max_turns)
    st.session_state.last_payload_messages = messages_for_api

    with st.chat_message("assistant"):
        placeholder = st.empty()
        with st.spinner("Menjawab..."):
            try:
                started = time.time()
                answer, raw, used_model, tried = call_with_fallback(
                    model=model,
                    messages=messages_for_api,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    timeout=timeout,
                    auto_fallback=auto_fallback,
                    fallback_models=fallback_models,
                )
                elapsed = time.time() - started
                st.session_state.last_raw = raw

                placeholder.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})

                cost, source = usage_cost_idr(used_model, raw)
                usage = raw.get("usage") or {}
                footer = f"Model: `{used_model}` · Waktu: `{elapsed:.1f}s`"
                if usage:
                    footer += f" · Token: `{usage.get('total_tokens', '-')}`"
                if cost is not None:
                    footer += f" · Biaya: `Rp {cost:.4f}` ({source})"
                if len(tried) > 1:
                    footer += f" · Fallback dicoba: `{', '.join(tried)}`"
                st.caption(footer)

            except Exception as exc:
                err = str(exc)
                placeholder.error(
                    "Maaf, model belum mengembalikan jawaban yang bisa dibaca.\n\n"
                    f"Detail:\n\n{err}"
                )

if show_debug:
    st.divider()
    st.subheader("Debug")
    with st.expander("Messages yang dikirim ke API"):
        st.json(st.session_state.get("last_payload_messages"))
    with st.expander("Raw response terakhir"):
        st.json(st.session_state.get("last_raw"))

