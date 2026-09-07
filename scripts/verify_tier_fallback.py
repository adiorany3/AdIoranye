"""Offline fallback limits and safety regression. No DB, credentials, or network."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_core as core

calls = []
def respond(**kwargs):
    calls.append(kwargs["model"])
    return "Jawaban", {}

base = dict(api_url="https://example.invalid/v1", api_key="test", model="tamandata",
            system_prompt="", user_text="Bandingkan metode", return_to_primary=False,
            fallback_models=[], expensive_fallback_models=[], consultation_enabled=False)
with patch.object(core, "should_use_cache", return_value=False), \
     patch.object(core, "call_api_once", side_effect=respond), \
     patch.object(core, "answer_quality_score", return_value=(0.1, [])):
    core.generate_answer(**base)
    assert calls == ["tamandata"], calls
    calls.clear()
    core.generate_answer(**{**base, "expensive_fallback_models": ["cbai/glm-5.2", "cx/gpt-6-astra"]})
    assert calls == ["tamandata"], calls
    calls.clear()
    core.generate_answer(**{**base, "smart_model_router": False})
    assert calls == ["tamandata"]

with patch.object(core, "should_use_cache", return_value=False), \
     patch.object(core, "call_api_once", side_effect=core.ContentFilterError("blocked")) as api:
    answer, meta = core.generate_answer(**base)
    assert meta["local_content_filter_message"]
    assert api.call_count == 2
    assert all(call.kwargs["model"] == "tamandata" for call in api.call_args_list)
with patch.object(core, "should_use_cache", return_value=False), \
     patch.object(core, "call_api_once", side_effect=[RuntimeError("offline"), ("Jawaban", {})]) as api:
    answer, meta = core.generate_answer(**{**base, "fallback_models": [core.TIER_ROUTING["standard_model"]]})
    assert api.call_count == 2 and answer == "Jawaban" and not meta["returned_to_primary"]
print("PASS: empty pools, no low-score escalation, error-only fallback, manual selection, content-filter isolation")