from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_core import (
    ALL_SLASHAI_MODELS,
    DEFAULT_FALLBACK_MODELS,
    MODEL_COST_MULTIPLIERS,
    MODEL_PRICE_IDR,
    MODEL_PROFILES,
    SINGLE_MODEL_CATALOG,
    model_cost_tier,
    model_price_label,
)

EXPECTED = {
    "gemini/gemini-3.5-flash-lite": {"input": 1.0, "output": 0.7},
    "gemini/gemini-3.6-flash": {"input": 1.0, "output": 0.7},
    "gemini/gemini-3-flash-preview": {"input": 1.0, "output": 0.7},
    "gemini/gemini-2.5-flash": {"input": 1.0, "output": 0.7},
    "z/deepseek-v4-flash": {"input": 1.0, "output": 0.7},
    "z/hy3": {"input": 1.0, "output": 0.7},
    "z/qwen3.8-flash": {"input": 1.0, "output": 0.7},
    "z/deepseek-v4-flash-vision-exp": {"input": 1.0, "output": 0.7},
    "z/glm-5.3-flash": {"input": 1.5, "output": 1.0},
    "z/mimo-v2.5": {"input": 1.0, "output": 0.7},
    "cbai/glm-5.2": {"input": 1.5, "output": 1.0},
}

catalog_models = [item["model"] for item in SINGLE_MODEL_CATALOG]
assert len(catalog_models) == len(set(catalog_models))
assert MODEL_COST_MULTIPLIERS == EXPECTED
assert set(EXPECTED) <= set(catalog_models)
assert set(EXPECTED) <= set(ALL_SLASHAI_MODELS)
assert set(EXPECTED) <= set(DEFAULT_FALLBACK_MODELS)
assert set(EXPECTED).isdisjoint(MODEL_PRICE_IDR)
assert set(EXPECTED) <= set(MODEL_PROFILES)
assert model_cost_tier("z/glm-5.3-flash") == "medium"
assert model_cost_tier("z/deepseek-v4-flash") == "cheap"
assert model_price_label("z/deepseek-v4-flash").endswith("harga tidak diketahui")
assert MODEL_PROFILES["z/glm-5.3-flash"]["cost"] == 1.5
print("Model catalog verification passed.")
