from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


DEFAULT_API_URL = "https://api.slashai.my.id/v1/chat/completions"
DEFAULT_MODEL = "slashai/gpt-5-nano"
DEFAULT_PERSONA = (
    "Nama kamu adalah adioranye. Kamu adalah asisten pribadi yang pintar, cepat, ramah, "
    "dan dapat membantu menjawab berbagai pertanyaan yang diberikan pengguna. Jawab dalam "
    "bahasa Indonesia yang natural, jelas, praktis, dan tidak bertele-tele."
)

CHEAP_FALLBACK_MODELS = [
    "slashai/gpt-5-nano",
    "slashai/gpt-5-mini",
    "slashai/gpt-5.4-nano",
    "slashai/gemini-3-flash",
    "slashai/mimo-v2-flash",
    "slashai/Step-3.5-Flash",
    "slashai/MiniMax-M2.5",
]

MODEL_PRICES_IDR_PER_1M = {
    "slashai/gpt-5-nano": (50, 200),
    "slashai/gpt-5-mini": (50, 200),
    "slashai/gpt-5.4-nano": (50, 200),
    "slashai/gpt-5.4-mini": (50, 200),
    "slashai/gpt-5.5-instant": (50, 200),
    "slashai/gpt-5-codex-mini": (50, 200),
    "slashai/gpt-5.1-codex-mini": (50, 200),
    "slashai/gpt-5.3-codex-low": (50, 200),
    "slashai/gpt-5.3-codex-spark": (50, 200),
    "slashai/gemini-3-flash": (50, 200),
    "slashai/gemini-3.1-pro": (50, 200),
    "slashai/mimo-v2-flash": (50, 200),
    "slashai/Step-3.5-Flash": (50, 200),
    "slashai/MiniMax-M2.5": (50, 200),
    "slashai/MiniMax-M2.7": (50, 200),
    "slashai/minimax-m2.5": (50, 200),
    "slashai/minimax-m2.7": (50, 200),
}


@dataclass
class AIConfig:
    api_key: str
    api_url: str = DEFAULT_API_URL
    model: str = DEFAULT_MODEL
    persona: str = DEFAULT_PERSONA
    memory_file: str = "assistant_memory.json"
    telegram_bot_token: str = ""
    request_timeout: int = 45
    max_context_messages: int = 6
    max_memory_items: int = 8
    temperature: float = 0.4
    max_output_tokens: int = 1200
    enable_fallback: bool = True
    fallback_models: Optional[List[str]] = None
    debug: bool = False

    def normalized_fallback_models(self) -> List[str]:
        if self.fallback_models:
            return [m.strip() for m in self.fallback_models if m.strip()]
        return CHEAP_FALLBACK_MODELS.copy()


def load_toml_file(path: str | Path = ".streamlit/secrets.toml") -> Dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def _get_value(data: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Read either root-level KEY or nested sections like [slashai].api_key."""
    if key in data:
        return data.get(key, default)

    lower_key = key.lower()
    for section_name in ("slashai", "telegram", "assistant", "app"):
        section = data.get(section_name, {})
        if isinstance(section, dict):
            if lower_key in section:
                return section.get(lower_key, default)
            if key in section:
                return section.get(key, default)
    return default


def config_from_toml(path: str | Path = ".streamlit/secrets.toml") -> AIConfig:
    data = load_toml_file(path)

    fallback_models = _get_value(data, "FALLBACK_MODELS", None)
    if isinstance(fallback_models, str):
        fallback_models = [m.strip() for m in fallback_models.split(",") if m.strip()]

    return AIConfig(
        api_key=os.getenv("SLASHAI_API_KEY") or _get_value(data, "SLASHAI_API_KEY", ""),
        api_url=os.getenv("SLASHAI_API_URL") or _get_value(data, "SLASHAI_API_URL", DEFAULT_API_URL),
        model=os.getenv("SLASHAI_MODEL") or _get_value(data, "SLASHAI_MODEL", DEFAULT_MODEL),
        persona=os.getenv("ASSISTANT_PERSONA") or _get_value(data, "ASSISTANT_PERSONA", DEFAULT_PERSONA),
        memory_file=os.getenv("MEMORY_FILE") or _get_value(data, "MEMORY_FILE", "assistant_memory.json"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or _get_value(data, "TELEGRAM_BOT_TOKEN", ""),
        request_timeout=int(_get_value(data, "REQUEST_TIMEOUT", 45)),
        max_context_messages=int(_get_value(data, "MAX_CONTEXT_MESSAGES", 6)),
        max_memory_items=int(_get_value(data, "MAX_MEMORY_ITEMS", 8)),
        temperature=float(_get_value(data, "TEMPERATURE", 0.4)),
        max_output_tokens=int(_get_value(data, "MAX_OUTPUT_TOKENS", 1200)),
        enable_fallback=bool(_get_value(data, "ENABLE_FALLBACK", True)),
        fallback_models=fallback_models,
        debug=bool(_get_value(data, "DEBUG", False)),
    )


def config_from_streamlit_secrets(st_secrets: Any) -> AIConfig:
    def g(key: str, default: Any = None) -> Any:
        try:
            if key in st_secrets:
                return st_secrets.get(key, default)
        except Exception:
            pass
        lower_key = key.lower()
        for section_name in ("slashai", "telegram", "assistant", "app"):
            try:
                section = st_secrets.get(section_name, {})
                if isinstance(section, dict):
                    return section.get(lower_key, section.get(key, default))
            except Exception:
                continue
        return default

    fallback_models = g("FALLBACK_MODELS", None)
    if isinstance(fallback_models, str):
        fallback_models = [m.strip() for m in fallback_models.split(",") if m.strip()]

    return AIConfig(
        api_key=g("SLASHAI_API_KEY", ""),
        api_url=g("SLASHAI_API_URL", DEFAULT_API_URL),
        model=g("SLASHAI_MODEL", DEFAULT_MODEL),
        persona=g("ASSISTANT_PERSONA", DEFAULT_PERSONA),
        memory_file=g("MEMORY_FILE", "assistant_memory.json"),
        telegram_bot_token=g("TELEGRAM_BOT_TOKEN", ""),
        request_timeout=int(g("REQUEST_TIMEOUT", 45)),
        max_context_messages=int(g("MAX_CONTEXT_MESSAGES", 6)),
        max_memory_items=int(g("MAX_MEMORY_ITEMS", 8)),
        temperature=float(g("TEMPERATURE", 0.4)),
        max_output_tokens=int(g("MAX_OUTPUT_TOKENS", 1200)),
        enable_fallback=bool(g("ENABLE_FALLBACK", True)),
        fallback_models=fallback_models,
        debug=bool(g("DEBUG", False)),
    )


def is_gpt5_model(model: str) -> bool:
    return "gpt-5" in model.lower()


def rough_token_count(text: str) -> int:
    # Perkiraan kasar: 1 token sekitar 4 karakter pada teks campuran.
    return max(1, len(text) // 4)


def estimate_cost_idr(model: str, prompt_tokens: int, output_tokens: int) -> float:
    input_price, output_price = MODEL_PRICES_IDR_PER_1M.get(model, (50, 200))
    return (prompt_tokens / 1_000_000 * input_price) + (output_tokens / 1_000_000 * output_price)


def safe_json_loads(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, str(exc)


def _decode_json_string(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except Exception:
        return raw.replace('\\n', '\n').replace('\\"', '"').replace('\\/', '/')


def extract_content_from_raw_text(text: str) -> str:
    """Fallback parser when provider returns truncated/invalid JSON but content exists."""
    if not text:
        return ""

    # Most useful pattern: "message":{"...","content":"..."}
    patterns = [
        r'"message"\s*:\s*\{.*?"content"\s*:\s*"((?:\\.|[^"\\])*)"',
        r'"content"\s*:\s*"((?:\\.|[^"\\])*)"',
        r"'content'\s*:\s*'((?:\\.|[^'\\])*)'",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text, flags=re.DOTALL)
        if matches:
            # Pick the longest non-empty content.
            decoded = [_decode_json_string(m) for m in matches]
            decoded = [d.strip() for d in decoded if d and d.strip()]
            if decoded:
                return max(decoded, key=len)
    return ""


def extract_message_content(response_text: str) -> Tuple[str, Dict[str, Any], Optional[str]]:
    data, json_error = safe_json_loads(response_text)
    if data is not None:
        content = ""
        try:
            choice = data.get("choices", [{}])[0]
            message = choice.get("message") or {}
            content = message.get("content") or ""
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        parts.append(str(item.get("text") or item.get("content") or ""))
                    else:
                        parts.append(str(item))
                content = "\n".join([p for p in parts if p.strip()])
        except Exception:
            content = ""
        return content.strip(), data, json_error

    content = extract_content_from_raw_text(response_text)
    meta = {
        "raw_text_preview": response_text[:2000],
        "json_parse_error": json_error,
        "recovered_from_invalid_json": bool(content),
    }
    return content.strip(), meta, json_error


def build_system_prompt(persona: str, memory_items: List[str]) -> str:
    memory_text = "\n".join([f"- {m}" for m in memory_items]) if memory_items else "Belum ada memori penting."
    return f"""{persona}

MEMORI PENTING PENGGUNA:
{memory_text}

ATURAN JAWABAN:
- Jawab langsung dan jangan kosong.
- Gunakan bahasa Indonesia yang natural kecuali pengguna meminta bahasa lain.
- Jika pertanyaan teknis, berikan langkah yang praktis.
- Jangan mengulang seluruh riwayat percakapan.
- Gunakan memori hanya jika relevan dengan pertanyaan.
""".strip()


def build_messages(
    user_text: str,
    persona: str,
    memory_items: Optional[List[str]] = None,
    recent_messages: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": build_system_prompt(persona, memory_items or [])}
    ]
    if recent_messages:
        for msg in recent_messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role in {"user", "assistant"} and content.strip():
                messages.append({"role": role, "content": content.strip()})
    messages.append({"role": "user", "content": user_text})
    return messages


def build_payload(model: str, messages: List[Dict[str, str]], config: AIConfig, max_tokens: Optional[int] = None) -> Dict[str, Any]:
    output_tokens = int(max_tokens or config.max_output_tokens)
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
    }

    if is_gpt5_model(model):
        # Fix untuk kasus content kosong karena token habis di reasoning_tokens.
        payload["max_completion_tokens"] = max(output_tokens, 1200)
        payload["reasoning_effort"] = "minimal"
    else:
        payload["max_tokens"] = output_tokens
        payload["temperature"] = config.temperature
    return payload


def _post_chat_completion(payload: Dict[str, Any], config: AIConfig) -> Tuple[bool, str, Optional[requests.Response]]:
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            config.api_url,
            headers=headers,
            json=payload,
            timeout=config.request_timeout,
        )
    except requests.RequestException as exc:
        return False, f"Request gagal: {exc}", None

    if response.status_code >= 400:
        return False, f"API mengembalikan status {response.status_code}: {response.text[:2000]}", response

    return True, response.text, response


def ask_ai(
    user_text: str,
    config: AIConfig,
    memory_items: Optional[List[str]] = None,
    recent_messages: Optional[List[Dict[str, str]]] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    if not config.api_key:
        return {
            "ok": False,
            "answer": "",
            "error": "SLASHAI_API_KEY belum diisi di .streamlit/secrets.toml.",
            "model": model or config.model,
        }

    base_model = model or config.model
    models_to_try = [base_model]
    if config.enable_fallback:
        for m in config.normalized_fallback_models():
            if m not in models_to_try:
                models_to_try.append(m)

    messages = build_messages(
        user_text=user_text,
        persona=config.persona,
        memory_items=memory_items or [],
        recent_messages=recent_messages or [],
    )

    attempts = []
    raw_last = ""
    for model_name in models_to_try[:4]:  # Biar cepat dan hemat, maksimal 4 model.
        token_budget = config.max_output_tokens
        for retry in range(2):
            payload = build_payload(model_name, messages, config, max_tokens=token_budget)
            ok, body, resp = _post_chat_completion(payload, config)
            raw_last = body
            if not ok:
                attempts.append({"model": model_name, "error": body, "retry": retry})
                break

            content, meta, json_error = extract_message_content(body)
            if content:
                usage = meta.get("usage", {}) if isinstance(meta, dict) else {}
                return {
                    "ok": True,
                    "answer": content,
                    "model": model_name,
                    "raw": meta if config.debug else None,
                    "usage": usage,
                    "attempts": attempts,
                    "json_error": json_error,
                }

            # Jika GPT-5 kosong karena reasoning_tokens habis, retry dengan token lebih besar.
            usage = meta.get("usage", {}) if isinstance(meta, dict) else {}
            completion_details = usage.get("completion_tokens_details", {}) if isinstance(usage, dict) else {}
            reasoning_tokens = completion_details.get("reasoning_tokens", 0) if isinstance(completion_details, dict) else 0
            attempts.append({
                "model": model_name,
                "error": "Respons kosong",
                "retry": retry,
                "reasoning_tokens": reasoning_tokens,
            })
            if is_gpt5_model(model_name):
                token_budget = max(token_budget * 2, 2200)
                time.sleep(0.2)
                continue
            break

    return {
        "ok": False,
        "answer": "",
        "error": "Semua model gagal atau respons kosong.",
        "model": base_model,
        "attempts": attempts,
        "raw_last": raw_last[:3000],
    }
