import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ai_core
from power_features import PowerStore, build_power_context, generate_power_answer


def retry_policy(session: requests.Session) -> object:
    return session.get_adapter("https://").max_retries


normal_retry = retry_policy(ai_core._HTTP_SESSION)
call_retry = retry_policy(ai_core._CALL_API_SESSION)
assert normal_retry.connect == 3 and normal_retry.read == 3 and normal_retry.status == 3
assert call_retry.total == 0 and call_retry.connect == 0 and call_retry.read == 0 and call_retry.status == 0

calls = 0


def raise_timeout(*args: object, **kwargs: object) -> None:
    global calls
    calls += 1
    raise requests.exceptions.Timeout("simulated")


with patch.object(ai_core._CALL_API_SESSION, "post", side_effect=raise_timeout), patch.object(ai_core.time, "sleep"):
    try:
        ai_core.call_api_once("https://example.invalid", "test", "test-model", [{"role": "user", "content": "test"}])
    except RuntimeError as exc:
        assert "setelah 3 percobaan" in str(exc)
    else:
        raise AssertionError("Timeout harus menghasilkan RuntimeError.")
assert calls == 3

with tempfile.TemporaryDirectory() as tmp:
    store = PowerStore(str(Path(tmp) / "power.db"))
    store.set_semantic_cached_response("apa manfaat cahaya matahari", "jawaban-a", user_id="user-a", channel="telegram")
    store.set_semantic_cached_response("apa manfaat cahaya matahari", "jawaban-b", user_id="user-b", channel="telegram")
    cached = store.get_semantic_cached_response("apa manfaat cahaya matahari", user_id="user-a", channel="telegram")
    assert cached and cached[0] == "jawaban-a"
    assert store.get_semantic_cached_response("apa manfaat cahaya matahari", user_id="user-a", channel="web") is None

    rag_calls = [0]

    def count_search(*args: object, **kwargs: object) -> list[dict[str, object]]:
        rag_calls[0] += 1
        return []

    store.search_documents = count_search  # type: ignore[method-assign]
    build_power_context(store, "pertanyaan panjang untuk rag", enable_persistent_memory=False, preselected_docs=[])
    assert rag_calls[0] == 0

    answer, meta = generate_power_answer(
        api_url="https://example.invalid",
        api_key="test",
        model="test-model",
        system_prompt="test",
        user_text="apa manfaat cahaya matahari",
        store=store,
        user_id="user-a",
        channel="telegram",
        enable_rag=False,
        enable_persistent_memory=False,
        anti_hallucination_enabled=False,
        answer_mode="ringkas",
    )
    assert answer == "jawaban-a" and meta.get("semantic_cache_hit") is True
    assert meta.get("usage") == {} and meta.get("latency_seconds") == 0
    with store._connect() as conn:
        logged = conn.execute("SELECT input_tokens, output_tokens, cost_idr, meta_json FROM interactions ORDER BY id DESC LIMIT 1").fetchone()
    assert logged and logged[0] == 0 and logged[1] == 0 and logged[2] == 0
    assert "semantic_cache_hit" in logged[3]

print("Retry, cache scope, logging cache hit, dan reuse hasil RAG terverifikasi.")
