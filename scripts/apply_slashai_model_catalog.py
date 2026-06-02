#!/usr/bin/env python3
"""
Apply current SlashAI model catalog and safer routing defaults to ai_core.py.

Run from repository root:
    python scripts/apply_slashai_model_catalog.py

What it updates:
- SLASHAI_MODEL_CATALOG
- MODEL_PRICE_IDR alias compatibility block
- TOP_USAGE_MODEL_CANDIDATES
- priority price update block
- DEFAULT_CHEAP_FALLBACK_MODELS
- DEFAULT_EXPENSIVE_FALLBACK_MODELS
- model_is_free(), so models with "free" in the name but non-zero price are not treated as free
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Any

ROOT = pathlib.Path.cwd()
AI_CORE_PATH = ROOT / "ai_core.py"
CATALOG_PATH = ROOT / "config" / "slashai_model_catalog_current.json"

TOP_USAGE_MODEL_CANDIDATES = [
    "slashai/deepseek-v4-flash-free",
    "slashai/claude-sonnet-4.5-free",
    "slashai/mimo-v2.5-free",
    "slashai/nemotron-3-super-free",
    "slashai/minimax-m2.5:fast",
    "slashai/gpt-oss-120b-medium",
    "slashai/qwen3-coder-next:fast",
    "slashai/deepseek-3.2:fast",
    "slashai/deepseek-v4-pro",
    "slashai/qwen3.6-plus",
    "slashai/glm-5:fast",
    "slashai/gemini-3-flash",
]

DEFAULT_CHEAP_FALLBACK_MODELS = [
    "slashai/deepseek-v4-flash-free",
    "slashai/claude-sonnet-4.5-free",
    "slashai/mimo-v2.5-free",
    "slashai/nemotron-3-super-free",
    "slashai/minimax-m2.5:fast",
    "slashai/gpt-oss-120b-medium",
    "slashai/qwen3-coder-next:fast",
    "slashai/deepseek-3.2:fast",
    "slashai/deepseek-v4-pro",
    "slashai/qwen3.6-plus",
    "slashai/glm-5:fast",
    "slashai/gemini-3-flash",
    "slashai/mimo-v2.5",
    "slashai/gpt-5.1-codex-mini",
    "slashai/gpt-5-codex-mini",
    "slashai/gpt-5.1-codex-mini-review",
    "slashai/minimax-m3-free",
]

DEFAULT_EXPENSIVE_FALLBACK_MODELS = [
    "slashai/deepseek-v4-pro",
    "slashai/qwen3.6-plus",
    "slashai/glm-5:fast",
    "slashai/gemini-3-flash",
    "slashai/mimo-v2.5",
    "slashai/mimo-v2-omni",
    "slashai/mimo-v2-pro",
    "slashai/gpt-5.1-codex-mini-high",
    "slashai/gpt-5-codex-mini",
    "slashai/gpt-5.1",
    "slashai/gpt-5.1-codex",
    "slashai/gpt-5-codex",
    "slashai/gemini-3.5-flash",
    "slashai/qwen3.7-max",
    "slashai/gemini-3.1-pro-low",
    "slashai/gemini-3.1-pro-high",
    "slashai/gpt-5.4:cx",
    "slashai/gpt-5.5:cx",
    "slashai/claude-opus-4.7:fast",
    "slashai/claude-opus-4.8",
]

ALIASES = {
    "slashai/claude-haiku-4.5": "slashai/claude-haiku-4.5:fast",
    "slashai/claude-opus-4.7": "slashai/claude-opus-4.7:fast",
    "slashai/claude-sonnet-4.5": "slashai/claude-sonnet-4.5-free",
    "slashai/claude-sonnet-4.5:free": "slashai/claude-sonnet-4.5-free",
    "slashai/deepseek-3.2": "slashai/deepseek-3.2:fast",
    "slashai/deepseek-v4-flash": "slashai/deepseek-v4-flash-free",
    "slashai/glm-5": "slashai/glm-5:fast",
    "slashai/gpt-5.2": "slashai/gpt-5.2-review",
    "slashai/gpt-5.3": "slashai/gpt-5.3-codex",
    "slashai/gpt-5.4": "slashai/gpt-5.4:cx",
    "slashai/gpt-5.5": "slashai/gpt-5.5:cx",
    "slashai/mimo-v2.5:free": "slashai/mimo-v2.5-free",
    "slashai/minimax-m2.5": "slashai/minimax-m2.5:fast",
    "slashai/qwen3-coder-next": "slashai/qwen3-coder-next:fast",
}

MODEL_IS_FREE_FUNCTION = '''def model_is_free(model: str) -> bool:
    """True hanya untuk model yang benar-benar Rp0/Rp0.

    Jangan hanya mengandalkan kata "free" pada nama model, karena beberapa
    provider memakai suffix free tetapi harga dashboard tetap non-zero.
    """
    model_name = str(model or "").strip()
    lower_name = model_name.lower()

    explicit_price = MODEL_PRICE_IDR.get(model_name)
    if explicit_price is None:
        explicit_price = MODEL_PRICE_IDR.get(lower_name)

    if explicit_price is None:
        for key, value in MODEL_PRICE_IDR.items():
            if str(key).lower() == lower_name:
                explicit_price = value
                break

    if isinstance(explicit_price, dict):
        return (
            int(explicit_price.get("input", 0) or 0) == 0
            and int(explicit_price.get("output", 0) or 0) == 0
        )

    # Fallback terakhir untuk model baru yang belum masuk katalog.
    return lower_name.endswith("-free") or lower_name.endswith(":free")

'''


def unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def load_catalog() -> list[dict[str, Any]]:
    if not CATALOG_PATH.exists():
        raise FileNotFoundError(f"Catalog file not found: {CATALOG_PATH}")
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Catalog JSON must be a list")
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            raise ValueError(f"Invalid catalog item: {item!r}")
        model = str(item.get("model") or "").strip()
        if not model:
            raise ValueError(f"Catalog item without model: {item!r}")
        if model in seen:
            continue
        seen.add(model)
        cleaned.append(
            {
                "model": model,
                "aliases": list(item.get("aliases") or []),
                "input": int(item.get("input") or 0),
                "output": int(item.get("output") or 0),
            }
        )
    return cleaned


def price_map(catalog: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return {
        str(item["model"]): {
            "input": int(item["input"]),
            "output": int(item["output"]),
        }
        for item in catalog
    }


def format_catalog_block(catalog: list[dict[str, Any]]) -> str:
    lines = [
        "SLASHAI_MODEL_CATALOG: List[Dict[str, Any]] = [",
    ]
    for item in catalog:
        lines.append(
            "    "
            + repr(
                {
                    "model": item["model"],
                    "aliases": item.get("aliases") or [],
                    "input": int(item["input"]),
                    "output": int(item["output"]),
                }
            )
            + ","
        )
    lines.append("]")
    return "\n".join(lines)


def format_price_update_block(label: str, mapping: dict[str, dict[str, int]]) -> str:
    lines = [f"{label}.update({{"]
    for key, value in mapping.items():
        lines.append(
            f"    {key!r}: "
            f"{{\"input\": {int(value['input'])}, \"output\": {int(value['output'])}}},"
        )
    lines.append("})")
    return "\n".join(lines)


def format_model_list_assignment(name: str, models: list[str], suffix: str = "") -> str:
    lines = [f"{name} = _unique_ordered(["]
    for model in unique(models):
        lines.append(f"    {model!r},")
    lines.append("]" + suffix)
    return "\n".join(lines)


def replace_block(text: str, pattern: str, replacement: str, label: str) -> str:
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"Failed to replace block: {label}")
    return new_text


def build_alias_mapping(catalog: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    prices = price_map(catalog)
    result: dict[str, dict[str, int]] = {}
    for alias, target in ALIASES.items():
        if target in prices:
            result[alias] = prices[target]
            result[alias.lower()] = prices[target]
    return result


def main() -> int:
    if not AI_CORE_PATH.exists():
        print(f"ERROR: {AI_CORE_PATH} not found. Run from repository root.", file=sys.stderr)
        return 2

    catalog = load_catalog()
    prices = price_map(catalog)
    alias_mapping = build_alias_mapping(catalog)
    priority_price_mapping = {
        model: prices[model]
        for model in TOP_USAGE_MODEL_CANDIDATES
        if model in prices
    }

    original = AI_CORE_PATH.read_text(encoding="utf-8")
    patched = original

    patched = replace_block(
        patched,
        r"SLASHAI_MODEL_CATALOG:\s*List\[Dict\[str,\s*Any\]\]\s*=\s*\[.*?\]\s*def\s+_unique_ordered",
        format_catalog_block(catalog) + "\n\n\ndef _unique_ordered",
        "SLASHAI_MODEL_CATALOG",
    )

    patched = replace_block(
        patched,
        r"MODEL_PRICE_IDR\.update\(\{.*?\}\)\s*# Model prioritas untuk health check dan routing hemat\.",
        format_price_update_block("MODEL_PRICE_IDR", alias_mapping)
        + "\n\n# Model prioritas untuk health check dan routing hemat.",
        "MODEL_PRICE_IDR aliases",
    )

    patched = replace_block(
        patched,
        r"TOP_USAGE_MODEL_CANDIDATES\s*=\s*_unique_ordered\(\[.*?\]\)\s*# Pastikan kandidat prioritas juga memiliki harga\.",
        format_model_list_assignment("TOP_USAGE_MODEL_CANDIDATES", TOP_USAGE_MODEL_CANDIDATES)
        + "\n\n# Pastikan kandidat prioritas juga memiliki harga.",
        "TOP_USAGE_MODEL_CANDIDATES",
    )

    patched = replace_block(
        patched,
        r"MODEL_PRICE_IDR\.update\(\{.*?\}\)\s*ALL_SLASHAI_MODELS\s*=",
        format_price_update_block("MODEL_PRICE_IDR", priority_price_mapping)
        + "\n\nALL_SLASHAI_MODELS =",
        "priority model prices",
    )

    patched = replace_block(
        patched,
        r"DEFAULT_CHEAP_FALLBACK_MODELS\s*=\s*_unique_ordered\(\[.*?\]\s*\+\s*ALL_CHEAP_MODELS\)\s*# Jalur menengah/mahal",
        format_model_list_assignment(
            "DEFAULT_CHEAP_FALLBACK_MODELS",
            DEFAULT_CHEAP_FALLBACK_MODELS,
            suffix=" + ALL_CHEAP_MODELS)",
        )
        + "\n\n# Jalur menengah/mahal",
        "DEFAULT_CHEAP_FALLBACK_MODELS",
    )

    patched = replace_block(
        patched,
        r"DEFAULT_EXPENSIVE_FALLBACK_MODELS\s*=\s*_unique_ordered\(\[.*?\]\s*\+\s*ALL_CAPABLE_MODELS\)\s*# Kompatibilitas dengan versi lama\.",
        format_model_list_assignment(
            "DEFAULT_EXPENSIVE_FALLBACK_MODELS",
            DEFAULT_EXPENSIVE_FALLBACK_MODELS,
            suffix=" + ALL_CAPABLE_MODELS)",
        )
        + "\n\n# Kompatibilitas dengan versi lama.",
        "DEFAULT_EXPENSIVE_FALLBACK_MODELS",
    )

    patched = replace_block(
        patched,
        r"def\s+model_is_free\(model:\s*str\)\s*->\s*bool:.*?def\s+model_is_nano",
        MODEL_IS_FREE_FUNCTION + "def model_is_nano",
        "model_is_free",
    )

    try:
        ast.parse(patched)
    except SyntaxError as exc:
        print(f"ERROR: patched ai_core.py has SyntaxError: {exc}", file=sys.stderr)
        return 3

    backup_path = AI_CORE_PATH.with_name(
        f"ai_core.py.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    shutil.copy2(AI_CORE_PATH, backup_path)
    AI_CORE_PATH.write_text(patched, encoding="utf-8")

    print(f"Patched: {AI_CORE_PATH}")
    print(f"Backup : {backup_path}")

    try:
        subprocess.run(
            [sys.executable, "-m", "py_compile", str(AI_CORE_PATH)],
            check=True,
        )
        print("Syntax check: OK")
    except subprocess.CalledProcessError as exc:
        print(f"WARNING: py_compile failed: {exc}", file=sys.stderr)
        return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
