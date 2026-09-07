"""Offline consultation regression: no DB, credentials or network."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_core as core

STANDARD = core.TIER_ROUTING["standard_model"]
ASTRA = core.TIER_ROUTING["advanced_model"]
INITIAL = "Jawaban awal dengan referensi https://example.invalid/source."
FINAL = "Jawaban lengkap terkoreksi dengan referensi https://example.invalid/source."
base = dict(api_url="https://example.invalid/v1", api_key="dummy-secret",
            model="tamandata", system_prompt="Aturan tepercaya.",
            user_text="Analisis mendalam dua metode", fallback_models=[STANDARD],
            expensive_fallback_models=[], max_completion_tokens=1200)


def run(replies=None, score=0.9, **options):
    calls = []
    replies = list(replies if replies is not None else [
        INITIAL, '{"needs_revision": true, "critique": "Perjelas asumsi."}', FINAL])

    def respond(**kwargs):
        calls.append(kwargs)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, {"usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}

    with patch.object(core, "call_api_once", side_effect=respond), \
         patch.object(core, "should_use_cache", return_value=False), \
         patch.object(core, "answer_quality_score", return_value=(score, [])):
        answer, meta = core.generate_answer(**{**base, **options})
    return answer, meta, calls


ORDINARY = ("Halo", "Kenapa langit biru?", "Mengapa es mencair?", "Hitung 2 + 2",
            "Bandingkan apel dan jeruk", "Calculate 7 * 8", "Compare tea and coffee")
for question in ORDINARY:
    assert not core.is_complex_question(question), question
    assert core.rank_tier_models(question, ["tamandata", ASTRA, STANDARD])[0] == STANDARD
    answer, meta, calls = run(user_text=question, model=STANDARD, score=0.1)
    assert len(calls) == 1 and calls[0]["model"] == STANDARD and answer == INITIAL
for question in ("Buktikan teorema ini", "Hitung biaya awal kemudian hitung laba dan evaluasi risiko",
                 "Debug kode Python dengan traceback ini", "kata " * 121):
    assert core.is_complex_question(question), question
assert not core.is_complex_question("kata " * 120)
assert core.is_complex_question("Halo", advanced=True)

for options in ({"user_text": "Halo"}, {"smart_model_router": False},
                {"consultation_enabled": False}):
    answer, meta, calls = run(**options)
    assert len(calls) == 1 and answer == INITIAL

answer, meta, calls = run()
assert [c["model"] for c in calls] == ["tamandata", STANDARD, "tamandata"]
assert answer == FINAL and meta["active_model_final"] == "tamandata"
assert meta["consulted_models"] == [STANDARD] and meta["usage"]["total_tokens"] == 90
assert sum(c["max_completion_tokens"] for c in calls[1:]) <= 1200
assert calls[1]["deadline"] == calls[2]["deadline"]
assert all(c["api_url"] == base["api_url"] for c in calls)
assert all("dummy-secret" not in str(c["messages"]) for c in calls)
assert "tidak tepercaya" in calls[1]["messages"][0]["content"]
assert "chain of thought" in calls[2]["messages"][0]["content"]

answer, meta, calls = run(model=ASTRA, user_text="Buktikan teorema ini", fallback_models=["tamandata", STANDARD])
assert [c["model"] for c in calls] == [ASTRA, "tamandata", ASTRA]
for options in ({"fallback_models": [], "expensive_fallback_models": []},
                {"max_smart_models": 0},
                {"fallback_models": [], "allow_expensive_fallback": False}):
    answer, meta, calls = run(**options)
    assert len(calls) == 1 and answer == INITIAL

for replies in ([INITIAL, RuntimeError("dummy-secret")],
                [INITIAL, "not JSON"],
                [INITIAL, '{"needs_revision": true, "critique": "Gap"}', TimeoutError()],
                [INITIAL, '{"needs_revision": false, "critique": "Cukup"}']):
    answer, meta, calls = run(replies)
    assert answer == INITIAL and len(calls) <= 3
    assert "dummy-secret" not in str(meta)

with patch.object(core.time, "monotonic", side_effect=[0, 46]):
    answer, meta, calls = run()
assert len(calls) == 1 and meta["consultation_status"] == "budget_skipped"

# Deadline-aware transport must not multiply peer calls on transient errors.
with patch.object(core._CALL_API_SESSION, "post", side_effect=core.requests.exceptions.Timeout) as post:
    try:
        core.call_api_once(api_url=base["api_url"], api_key="dummy", model=STANDARD,
                           messages=[], deadline=core.time.monotonic() + 5)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected transport failure")
    assert post.call_count == 1

print("PASS: standard/manual skips, medium/advanced peers, bounded calls/tokens/deadline, empty pools, failure preservation, synthesis, usage, prompt isolation, no transport retry")

# Exercise shared production web/Telegram path using disposable SQLite only.
import power_features as power
from power_features import PowerStore, generate_power_answer
from types import SimpleNamespace

with tempfile.TemporaryDirectory() as tmp:
    store = PowerStore(str(Path(tmp) / "test.db"))
    persona = "Persona tepercaya: jawab hangat. " * 150
    for channel in ("web", "telegram"):
        for question in ORDINARY:
            with patch.object(core, "call_api_once", return_value=(INITIAL, {})) as transport, \
                 patch.object(core, "should_use_cache", return_value=False), \
                 patch.object(core, "answer_quality_score", return_value=(0.1, [])), \
                 patch.object(power, "score_answer_quality", return_value=SimpleNamespace(score=0.1, needs_verification=True)), \
                 patch.object(power, "should_self_verify", return_value=True), \
                 patch.object(power, "verify_answer_with_model") as self_verify, \
                 patch.object(power, "verify_and_repair_answer") as quality_verify:
                answer, meta = generate_power_answer(
                    **{**base, "user_text": question, "system_prompt": persona}, channel=channel, store=store,
                    enable_rag=False, enable_persistent_memory=False, enable_prompt_templates=True,
                    enable_response_cache=False, enable_adaptive_scoring=False,
                    enable_self_verification=True, quality_control_enabled=True, quality_verifier_enabled=True,
                    anti_hallucination_enabled=True, allow_policy_force_rag=False,
                    performance_optimizer_enabled=False, live_web_fallback_enabled=False, live_music_chart_enabled=False,
                )
            assert transport.call_count == 1, (channel, question, transport.call_args_list)
            assert transport.call_args.kwargs["model"] == STANDARD
            system = transport.call_args.kwargs["messages"][0]["content"]
            assert persona.strip() in system and core.STABLE_IDENTITY_INSTRUCTION in system
            assert not self_verify.called and not quality_verify.called
            assert not meta.get("quality_control_error"), meta
    for channel in ("web", "telegram"):
        for blocked in (False, True):
            calls = []

            def respond_power(**kwargs):
                calls.append(kwargs)
                return [INITIAL, '{"needs_revision": true, "critique": "Gap"}', FINAL][len(calls) - 1], {}

            with patch.object(core, "call_api_once", side_effect=respond_power), \
                 patch.object(core, "should_use_cache", return_value=False), \
                 patch.object(core, "answer_quality_score", return_value=(0.9, [])), \
                 patch.object(store, "filter_blocked_models", side_effect=lambda models: [m for m in models if not blocked or m != STANDARD]):
                answer, meta = generate_power_answer(
                    **{**base, "expensive_fallback_models": [], "allow_expensive_fallback": False}, channel=channel, store=store,
                    enable_rag=False, enable_persistent_memory=False,
                    enable_prompt_templates=True, enable_response_cache=False,
                    enable_adaptive_scoring=False, enable_self_verification=True,
                    quality_control_enabled=True, quality_verifier_enabled=True,
                    anti_hallucination_enabled=False, allow_policy_force_rag=False,
                    performance_optimizer_enabled=False, live_web_fallback_enabled=False,
                    live_music_chart_enabled=False,
                )
            assert len(calls) == (1 if blocked else 3), (channel, blocked, calls)
            assert answer == (INITIAL if blocked else FINAL)
            assert meta["active_model_final"] == "tamandata"
            assert "power_kb_sources" in meta
            assert not meta.get("self_verified_by") and not meta.get("quality_verified_by")
print("PASS: web/Telegram production integration, blocked peer, no stacked verifiers, source metadata")