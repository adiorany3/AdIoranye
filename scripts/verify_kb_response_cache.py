import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from power_features import PowerStore, make_response_cache_key


with tempfile.TemporaryDirectory() as tmp:
    store = PowerStore(str(Path(tmp) / "power.db"))
    version_before = store.get_kb_cache_version()
    key_before = make_response_cache_key(
        model="model-a",
        system_prompt="system",
        user_text="pertanyaan KB",
        memory_text="konteks KB",
        intent="general",
        route_signature=f"route|kb_version={version_before}",
    )
    store.set_cached_response(key_before, "jawaban lama", ttl_seconds=31536000)
    store.set_semantic_cached_response(
        "pertanyaan tentang isi KB",
        "jawaban semantik lama",
        kb_version=version_before,
        ttl_seconds=31536000,
    )
    assert store.get_cached_response(key_before)
    assert store.get_semantic_cached_response(
        "pertanyaan tentang isi KB", kb_version=version_before
    )

    with store._connect() as conn:
        conn.execute(
            "INSERT INTO documents(title, source, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("Dokumen baru", "uji", 1.0, 1.0),
        )

    version_after = store.get_kb_cache_version()
    assert version_after != version_before
    key_after = make_response_cache_key(
        model="model-a",
        system_prompt="system",
        user_text="pertanyaan KB",
        memory_text="konteks KB",
        intent="general",
        route_signature=f"route|kb_version={version_after}",
    )
    assert key_after != key_before
    assert store.get_cached_response(key_after) is None
    assert store.get_semantic_cached_response(
        "pertanyaan tentang isi KB", kb_version=version_after
    ) is None

    store.set_cached_response(key_after, "jawaban baru")
    store.set_semantic_cached_response(
        "pertanyaan tentang isi KB",
        "jawaban semantik baru",
        kb_version=version_after,
    )
    assert store.clear_response_cache() == 4
    assert store.get_cached_response(key_after) is None
    assert store.get_semantic_cached_response(
        "pertanyaan tentang isi KB", kb_version=version_after
    ) is None

print("KB response cache verification passed.")
