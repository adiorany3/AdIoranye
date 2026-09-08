"""Offline production-function regression; no app import, network, secrets or DB."""
import ast
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_core as core

tree = ast.parse((Path(__file__).resolve().parents[1] / "app.py").read_text())
names = {
    "unique_models", "get_prioritized_fallback_models", "build_model_routing_plan",
    "ensure_minimum_primary_model_pool", "get_rotating_cheap_primary",
    "_runtime_block_store", "is_model_runtime_blocked", "filter_runtime_blocked_models",
    "_render_public_chat", "render_public_sidebar",
}
functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]

class State(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__

STANDARD = core.TIER_ROUTING["standard_model"]
ASTRA = core.TIER_ROUTING["advanced_model"]
warnings = []
state = State()
env = dict(vars(core), Any=Any, Dict=Dict, List=List, Tuple=Tuple,
    st=SimpleNamespace(session_state=state, warning=warnings.append, sidebar=nullcontext()),
    time=time, runtime_model_block_enabled=True, required_primary_models=[],
    min_primary_active_models=1, default_model="tamandata", ai_operation_mode_default="Seimbang",
    auto_replace_inactive_primary_model=False,
    _normalize_operation_mode=lambda mode: mode,
    _tier_rank=lambda model: {"cheap": 0, "medium": 1, "expensive": 2}.get(core.model_cost_tier(model), 3),
    prioritize_free_nano_for_simple_questions=lambda models, health, require_active=False: list(dict.fromkeys(
        m for m in models if not require_active or health.get(m, {}).get("active"))),
    sort_health_models_for_simple_chat=lambda models, health: [m for m in models if health.get(m, {}).get("active")],
    estimate_prompt_complexity=lambda text: {}, is_thinking_question=lambda text: text != "halo",
    get_capable_primary_model=lambda models, health: next(iter(models), ""),
    get_runtime_config=lambda: {}, message_effects_enabled=False, answer_sound_enabled=False,
    render_sound_unlock_script=lambda: None, hydrate_model_readiness_from_file=lambda: None,
    parse_bool=lambda value, default: value, get_secret=lambda name, default: False,
    api_key="test", api_url="https://example.invalid/v1")
exec(compile(ast.Module(body=functions, type_ignores=[]), "app.py", "exec"), env)

def setup(models, health=None, blocked=(), **settings):
    state.clear()
    state.update(model_health_cache=health or {}, pending_prompt="queued", **settings)
    state["model_runtime_blocks"] = {m: {"until": time.time() + 600} for m in blocked}
    for name in ("CHEAP_MODEL_OPTIONS", "DEFAULT_CHEAP_FALLBACK_MODELS"):
        env[name] = [m for m in models if core.model_cost_tier(m) == "cheap"]
    for name in ("MEDIUM_MODEL_OPTIONS", "HIGH_COST_MODEL_OPTIONS", "EXPENSIVE_MODEL_OPTIONS", "DEFAULT_EXPENSIVE_FALLBACK_MODELS"):
        env[name] = [m for m in models if core.model_cost_tier(m) != "cheap"]
    env["TOP_USAGE_MODEL_CANDIDATES"] = models

def route(text="halo"):
    return env["build_model_routing_plan"](user_text=text)

def unavailable():
    try:
        route()
    except RuntimeError:
        pass
    else:
        raise AssertionError("Invalid catalog allowed routing")
    warnings.clear()
    env["render_public_sidebar"]()
    env["_render_public_chat"]()  # No downstream UI/send/voucher doubles: must return first.
    assert len(warnings) == 2 and "SLASHAI_API_KEY" in warnings[-1]
    assert state.pending_prompt == ""

models = [STANDARD, "tamandata", ASTRA]
setup(models)
assert route()["primary_model"] == STANDARD
assert route("bandingkan metode")["primary_model"] == STANDARD
assert route("buktikan teorema")["primary_model"] == ASTRA
setup(models, {"tamandata": {"active": False}})
assert route()["primary_model"] == STANDARD  # Partial failed quick probe must not erase untested IDs.
assert state.model_health_cache == {"tamandata": {"active": False}}
assert "tamandata" not in route()["primary_model_pool"]
setup(models, {m: {"active": False} for m in models})
unavailable()
setup(models, blocked=models)
unavailable()
setup(models, blocked=[STANDARD])
assert STANDARD not in route()["primary_model_pool"]
for settings in ({"active_operation_mode": "Hemat"}, {"allow_expensive_fallback": False}):
    setup(models, **settings)
    plan = route("buktikan teorema")
    assert plan["primary_model"] == "tamandata" and not plan["allow_expensive_fallback"]
    assert ASTRA not in plan["primary_model_pool"]
setup(models, active_smart_router=False, active_model=STANDARD)
assert route()["primary_model_pool"] == [STANDARD]
setup(models, {STANDARD: {"active": False}}, active_smart_router=False, active_model=STANDARD)
unavailable()
# Truly empty producer catalog, including no implicit Tamandata fallback.
original = env["get_prioritized_fallback_models"]
env["get_prioritized_fallback_models"] = lambda: ([], [])
setup([])
unavailable()
env["get_prioritized_fallback_models"] = original
setup(models)
env["api_key"] = ""
warnings.clear()
env["_render_public_chat"]()
assert warnings and state.pending_prompt == ""
print("PASS: empty/failed/blocked startup, partial probes, tiers, manual mode, public/sidebar fail-closed, missing provider config")