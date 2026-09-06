"""Run isolated production routing with deterministic health/scoring doubles."""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_core import rank_tier_models, TIER_ROUTING


source = (Path(__file__).resolve().parents[1] / "power_features.py").read_text()
tree = ast.parse(source)
function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "generate_power_answer")
start = next(i for i, n in enumerate(function.body) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "cheap_candidates" for t in n.targets))
end = next(i for i, n in enumerate(function.body) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "selected_model" for t in n.targets))
routing = compile(ast.Module(body=function.body[start:end + 1], type_ignores=[]), "routing", "exec")
ASTRA = "cx/gpt-6-astra"


class Store:
    def __init__(self, blocked):
        self.blocked = blocked

    def filter_blocked_models(self, models):
        return [m for m in models if m not in self.blocked]

    def rank_models_for_intent(self, models, intent):
        return list(reversed(models))  # Scoring deliberately prefers another model.


def check(*, current=False, risk="normal", critical=False, mode="normal", blocked=(), adaptive=True,
          text="question", smart=True, expensive=True):
    context = dict(
        model="default", fallback_models=["fallback", TIER_ROUTING["standard_model"], "tamandata"], expensive_fallback_models=[ASTRA],
        user_text=text, effective_answer_mode=mode, intent="general",
        smart_model_router=smart, allow_expensive_fallback=expensive,
        semantic_cache_enabled=True,
        rank_tier_models=rank_tier_models, TIER_ROUTING=TIER_ROUTING,
        enable_circuit_breaker=True, enable_adaptive_scoring=adaptive,
        store=Store(blocked),
        classify_question_context=lambda _: {"risk_level": risk, "needs_current_data": current},
        detect_critical_question=lambda _: {"is_critical": critical},
    )
    exec(routing, context)
    return context["selected_model"], context["ranked_all"]


assert check()[0] == TIER_ROUTING["standard_model"]
assert check(current=True)[0] == TIER_ROUTING["standard_model"]
assert check(text="Bandingkan dua metode")[0] == "tamandata"
assert check(text="Buktikan teorema ini")[0] == ASTRA
assert check(text="Buktikan teorema ini", expensive=False)[0] == "tamandata"
assert check(smart=False, critical=True)[0] == "default"
for options in ({"risk": "high"}, {"critical": True}, {"mode": "kritis"}, {"critical": True, "adaptive": False}):
    selected, candidates = check(**options)
    assert selected == ASTRA, options
    assert "default" in candidates and "fallback" in candidates
assert check(current=True, blocked=(ASTRA,))[0] == TIER_ROUTING["standard_model"]
assert ASTRA not in check(current=True, blocked=(ASTRA,))[1]
print("Critical/current routing, scoring override, ordinary routing, and blocked-model fallback passed.")