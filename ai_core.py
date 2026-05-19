import json
import re
from typing import Any, Dict, List, Tuple, Optional

import requests


DEFAULT_FALLBACK_MODELS = [
    "slashai/gpt-5-nano",
    "slashai/gpt-5-mini",
    "slashai/gemini-3-flash",
    "slashai/mimo-v2-flash",
    "slashai/Step-3.5-Flash",
]


def is_gpt5_model(model: str) -> bool:
    return "gpt-5" in (model or "").lower()


def extract_text_from_response_text(raw_text: str) -> str:
    if not raw_text:
        return ""

    patterns = [
        r'"message"\s*:\s*\{.*?"content"\s*:\s*"((?:\\.|[^"\\])*)"',
        r'"content"\s*:\s*"((?:\\.|[^"\\])*)"',
    ]

    for pattern in patterns:
        match = re.search(pattern, raw_text, flags=re.DOTALL)
        if match:
            try:
                return json.loads('"' + match.group(1) + '"').strip()
            except Exception:
                return match.group(1).replace("\\n", "\n").replace('\\"', '"').strip()

    return ""


def parse_chat_completion(data: Any, raw_text: str = "") -> Tuple[str, Dict[str, Any]]:
    meta: Dict[str, Any] = {}

    if isinstance(data, dict):
        meta["id"] = data.get("id")
        meta["model"] = data.get("model")
        meta["usage"] = data.get("usage")
        choices = data.get("choices") or []

        if choices and isinstance(choices[0], dict):
            choice = choices[0]
            meta["finish_reason"] = choice.get("finish_reason")
            message = choice.get("message") or {}

            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip(), meta

            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        parts.append(str(item.get("text") or item.get("content") or ""))
                    else:
                        parts.append(str(item))
                joined = "\n".join([p for p in parts if p.strip()]).strip()
                if joined:
                    return joined, meta

            delta = choice.get("delta") or {}
            delta_content = delta.get("content")
            if isinstance(delta_content, str) and delta_content.strip():
                return delta_content.strip(), meta

    fallback_text = extract_text_from_response_text(raw_text)
    if fallback_text:
        return fallback_text, meta

    return "", meta


def build_payload(model: str, messages: List[Dict[str, str]], temperature: float = 0.3, max_completion_tokens: int = 1600) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_completion_tokens": max_completion_tokens,
        "stream": False,
    }
    if is_gpt5_model(model):
        payload["reasoning_effort"] = "minimal"
    return payload


def call_api_once(
    api_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.3,
    max_completion_tokens: int = 1600,
    timeout: int = 60,
) -> Tuple[str, Dict[str, Any]]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = build_payload(model=model, messages=messages, temperature=temperature, max_completion_tokens=max_completion_tokens)
    response = requests.post(api_url, headers=headers, json=payload, timeout=timeout)
    raw_text = response.text or ""

    if response.status_code != 200:
        raise RuntimeError(f"API status {response.status_code}: {raw_text[:1200]}")

    try:
        data = response.json()
    except Exception:
        content = extract_text_from_response_text(raw_text)
        if content:
            return content, {"model": model, "warning": "Respons bukan JSON valid, tetapi content berhasil diekstrak.", "raw_preview": raw_text[:1200]}
        raise RuntimeError(f"Respons API bukan JSON valid: {raw_text[:1200]}")

    content, meta = parse_chat_completion(data, raw_text=raw_text)
    meta["raw_preview"] = raw_text[:1200]
    meta["model_requested"] = model

    usage = meta.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    reasoning_tokens = details.get("reasoning_tokens", 0)

    if not content and reasoning_tokens:
        raise RuntimeError(
            "Respons kosong karena output habis untuk reasoning_tokens. "
            f"reasoning_tokens={reasoning_tokens}. Coba max_completion_tokens lebih besar."
        )

    if not content:
        raise RuntimeError(f"Respons API berhasil, tetapi isi jawaban kosong. Raw: {raw_text[:1200]}")

    return content, meta


def generate_answer(
    api_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_text: str,
    memory_text: str = "",
    recent_messages: Optional[List[Dict[str, str]]] = None,
    fallback_models: Optional[List[str]] = None,
    temperature: float = 0.3,
    max_completion_tokens: int = 1600,
    timeout: int = 60,
) -> Tuple[str, Dict[str, Any]]:
    if not api_key:
        raise RuntimeError("SLASHAI_API_KEY belum diisi.")
    if not api_url:
        raise RuntimeError("SLASHAI_API_URL belum diisi.")
    if not model:
        raise RuntimeError("SLASHAI_MODEL belum diisi.")

    full_system_prompt = system_prompt.strip()
    if memory_text.strip():
        full_system_prompt += "\n\nMemori penting pengguna:\n" + memory_text.strip()

    messages: List[Dict[str, str]] = [{"role": "system", "content": full_system_prompt}]

    if recent_messages:
        for msg in recent_messages[-8:]:
            role = msg.get("role")
            content = msg.get("content", "")
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": str(content)[:3000]})

    messages.append({"role": "user", "content": user_text})

    tried: List[str] = []
    errors: Dict[str, str] = {}
    ordered_models = [model]
    for fb in (fallback_models or DEFAULT_FALLBACK_MODELS):
        if fb not in ordered_models:
            ordered_models.append(fb)
    ordered_models = ordered_models[:4]

    for candidate_model in ordered_models:
        tried.append(candidate_model)
        token_budget = max_completion_tokens
        if is_gpt5_model(candidate_model):
            token_budget = max(max_completion_tokens, 2200)

        try:
            content, meta = call_api_once(
                api_url=api_url,
                api_key=api_key,
                model=candidate_model,
                messages=messages,
                temperature=temperature,
                max_completion_tokens=token_budget,
                timeout=timeout,
            )
            meta["tried_models"] = tried
            meta["errors"] = errors
            return content, meta
        except Exception as exc:
            error_text = str(exc)
            errors[candidate_model] = error_text
            if "reasoning_tokens" in error_text and is_gpt5_model(candidate_model):
                try:
                    content, meta = call_api_once(
                        api_url=api_url,
                        api_key=api_key,
                        model=candidate_model,
                        messages=messages,
                        temperature=temperature,
                        max_completion_tokens=4000,
                        timeout=timeout,
                    )
                    meta["tried_models"] = tried
                    meta["errors"] = errors
                    meta["retry_reasoning_fix"] = True
                    return content, meta
                except Exception as retry_exc:
                    errors[candidate_model] = f"{error_text} | Retry gagal: {retry_exc}"
            continue

    detail = "\n\n".join([f"{m}: {e}" for m, e in errors.items()])
    raise RuntimeError(f"Semua model gagal.\n\n{detail}")
