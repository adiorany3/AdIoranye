"""Power features for Adioranye AI.

Adds persistent memory, lightweight RAG, intent routing metadata, usage/cost
logging, prompt templates, self-verification, and small admin commands without
requiring extra heavy dependencies. Designed to be safe on Streamlit Cloud.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from ai_core import call_api_once, model_cost_tier, model_price

WIB_TZ = ZoneInfo("Asia/Jakarta")
DEFAULT_POWER_DB = ".adioranye_power.db"


def now_wib_text() -> str:
    return datetime.now(WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")


def _utc_ts() -> float:
    return time.time()


def _safe_json(data: Any) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


def _tokenize(text: str) -> List[str]:
    words = re.findall(r"[a-zA-Z0-9_\-]{3,}", str(text or "").lower())
    stop = {
        "yang", "dan", "atau", "untuk", "dengan", "dari", "pada", "agar", "jadi", "ini", "itu",
        "the", "and", "for", "with", "from", "that", "this", "are", "was", "were",
    }
    return [w for w in words if w not in stop]


def _score_text(query: str, text: str) -> float:
    q_terms = _tokenize(query)
    if not q_terms:
        return 0.0
    hay = str(text or "").lower()
    tokens = set(_tokenize(text))
    score = 0.0
    for term in q_terms:
        if term in tokens:
            score += 2.0
        if term in hay:
            score += 0.5
    # Light phrase boost.
    q_clean = " ".join(q_terms[:6])
    if q_clean and q_clean in hay:
        score += 2.0
    return score / max(1.0, math.sqrt(len(tokens) + 1))


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 160) -> List[str]:
    clean = re.sub(r"\r\n?", "\n", str(text or "")).strip()
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    if not clean:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", clean) if p.strip()]
    chunks: List[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= chunk_size:
            current = (current + "\n\n" + paragraph).strip()
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= chunk_size:
            current = paragraph
        else:
            start = 0
            while start < len(paragraph):
                piece = paragraph[start : start + chunk_size].strip()
                if piece:
                    chunks.append(piece)
                start += max(200, chunk_size - overlap)
            current = ""
    if current:
        chunks.append(current)
    return chunks[:2000]


@dataclass
class PowerStore:
    db_path: str = DEFAULT_POWER_DB

    def __post_init__(self) -> None:
        self.db_path = str(self.db_path or DEFAULT_POWER_DB)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True) if Path(self.db_path).parent != Path(".") else None
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL DEFAULT 'global',
                    text TEXT NOT NULL,
                    tags TEXT DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id, created_at);

                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    source TEXT DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    terms TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id, chunk_index);

                CREATE TABLE IF NOT EXISTS interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    user_id TEXT DEFAULT 'public',
                    channel TEXT DEFAULT 'web',
                    intent TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cost_idr REAL DEFAULT 0,
                    latency_seconds REAL DEFAULT 0,
                    success INTEGER DEFAULT 1,
                    question_preview TEXT DEFAULT '',
                    answer_preview TEXT DEFAULT '',
                    meta_json TEXT DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_interactions_ts ON interactions(ts);
                CREATE INDEX IF NOT EXISTS idx_interactions_model ON interactions(model);

                CREATE TABLE IF NOT EXISTS benchmarks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    model TEXT NOT NULL,
                    task TEXT NOT NULL,
                    score REAL DEFAULT 0,
                    latency_seconds REAL DEFAULT 0,
                    success INTEGER DEFAULT 0,
                    error TEXT DEFAULT '',
                    meta_json TEXT DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_benchmarks_model ON benchmarks(model, ts);
                """
            )

    # Persistent memory
    def add_memory(self, text: str, user_id: str = "global", tags: str = "") -> int:
        clean = str(text or "").strip()
        if not clean:
            return 0
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO memories(user_id, text, tags, created_at) VALUES (?, ?, ?, ?)",
                (str(user_id or "global"), clean[:4000], str(tags or "")[:300], _utc_ts()),
            )
            return int(cur.lastrowid)

    def delete_memories_containing(self, keyword: str, user_id: Optional[str] = None) -> int:
        key = str(keyword or "").strip().lower()
        if not key:
            return 0
        with self._connect() as conn:
            if user_id:
                cur = conn.execute(
                    "DELETE FROM memories WHERE user_id = ? AND lower(text) LIKE ?",
                    (str(user_id), f"%{key}%"),
                )
            else:
                cur = conn.execute("DELETE FROM memories WHERE lower(text) LIKE ?", (f"%{key}%",))
            return int(cur.rowcount or 0)

    def search_memories(self, query: str, user_id: str = "global", limit: int = 8) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE user_id IN (?, 'global') ORDER BY created_at DESC LIMIT 300",
                (str(user_id or "global"),),
            ).fetchall()
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for row in rows:
            item = dict(row)
            score = _score_text(query, item.get("text", ""))
            # Keep recent short preference memories even with low lexical match.
            if score > 0 or len(str(item.get("text", ""))) <= 220:
                scored.append((score, item))
        scored.sort(key=lambda x: (x[0], x[1].get("created_at", 0)), reverse=True)
        return [item for _, item in scored[: max(1, int(limit or 8))]]

    # Knowledge base / RAG
    def add_document(self, title: str, text: str, source: str = "manual") -> Tuple[int, int]:
        title = str(title or "Dokumen tanpa judul").strip()[:240]
        chunks = chunk_text(text)
        if not chunks:
            return 0, 0
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO documents(title, source, created_at) VALUES (?, ?, ?)",
                (title, str(source or "manual")[:500], _utc_ts()),
            )
            doc_id = int(cur.lastrowid)
            for idx, chunk in enumerate(chunks):
                terms = " ".join(sorted(set(_tokenize(chunk)))[:300])
                conn.execute(
                    "INSERT INTO chunks(doc_id, chunk_index, content, terms, created_at) VALUES (?, ?, ?, ?, ?)",
                    (doc_id, idx, chunk[:6000], terms, _utc_ts()),
                )
            return doc_id, len(chunks)

    def search_documents(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.doc_id, c.chunk_index, c.content, d.title, d.source, c.created_at
                FROM chunks c JOIN documents d ON d.id = c.doc_id
                ORDER BY c.created_at DESC LIMIT 1200
                """
            ).fetchall()
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for row in rows:
            item = dict(row)
            score = _score_text(query, item.get("content", "") + " " + item.get("title", ""))
            if score > 0:
                item["score"] = round(score, 4)
                scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[: max(1, int(limit or 5))]]

    def list_documents(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT d.id, d.title, d.source, d.created_at, COUNT(c.id) AS chunks
                FROM documents d LEFT JOIN chunks c ON c.doc_id = d.id
                GROUP BY d.id ORDER BY d.created_at DESC LIMIT ?
                """,
                (max(1, int(limit or 20)),),
            ).fetchall()
        return [dict(row) for row in rows]

    # Usage / observability
    def log_interaction(
        self,
        user_id: str,
        channel: str,
        intent: str,
        model: str,
        question: str,
        answer: str,
        meta: Optional[Dict[str, Any]] = None,
        success: bool = True,
    ) -> None:
        meta = meta or {}
        usage = meta.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        cost_idr = estimate_cost_idr(meta, model)
        latency = float(meta.get("latency_seconds") or 0)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO interactions(ts,user_id,channel,intent,model,input_tokens,output_tokens,cost_idr,
                                         latency_seconds,success,question_preview,answer_preview,meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _utc_ts(), str(user_id or "public")[:120], str(channel or "web")[:40], str(intent or "")[:80],
                    str(model or "")[:160], input_tokens, output_tokens, cost_idr, latency, 1 if success else 0,
                    str(question or "")[:500], str(answer or "")[:500], _safe_json(meta)[:8000],
                ),
            )

    def usage_summary(self, days: int = 1) -> Dict[str, Any]:
        since = _utc_ts() - max(1, int(days or 1)) * 86400
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT model, COUNT(*) AS requests, SUM(input_tokens) AS input_tokens,
                       SUM(output_tokens) AS output_tokens, SUM(cost_idr) AS cost_idr,
                       AVG(latency_seconds) AS avg_latency
                FROM interactions WHERE ts >= ? GROUP BY model ORDER BY cost_idr DESC
                """,
                (since,),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) AS requests, SUM(cost_idr) AS cost_idr FROM interactions WHERE ts >= ?",
                (since,),
            ).fetchone()
        return {
            "days": days,
            "requests": int((total or {}).get("requests") or 0),
            "cost_idr": float((total or {}).get("cost_idr") or 0),
            "by_model": [dict(row) for row in rows],
        }

    def count_expensive_calls_today(self) -> int:
        start = datetime.now(WIB_TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self._connect() as conn:
            rows = conn.execute("SELECT model FROM interactions WHERE ts >= ?", (start,)).fetchall()
        return sum(1 for row in rows if model_cost_tier(str(row["model"] or "")) in {"medium", "expensive", "ultra"})

    def add_benchmark(self, model: str, task: str, score: float, latency_seconds: float, success: bool, error: str = "", meta: Optional[Dict[str, Any]] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO benchmarks(ts,model,task,score,latency_seconds,success,error,meta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_utc_ts(), model, task, float(score or 0), float(latency_seconds or 0), 1 if success else 0, str(error or "")[:1000], _safe_json(meta or {})[:4000]),
            )

    def latest_benchmarks(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM benchmarks ORDER BY ts DESC LIMIT ?", (max(1, int(limit or 50)),)).fetchall()
        return [dict(row) for row in rows]


_STORE_CACHE: Dict[str, PowerStore] = {}


def get_power_store(db_path: str = DEFAULT_POWER_DB) -> PowerStore:
    path = str(db_path or DEFAULT_POWER_DB)
    if path not in _STORE_CACHE:
        _STORE_CACHE[path] = PowerStore(path)
    return _STORE_CACHE[path]


def classify_intent_text(text: str) -> str:
    t = str(text or "").lower().strip()
    wc = len(t.split())
    if not t:
        return "empty"
    if t.startswith(("/", "!")):
        return "admin_command"
    if any(x in t for x in ["```", "def ", "class ", "traceback", "error", "bug", "streamlit", "api", "vercel", "github", "kode", "coding"]):
        return "coding"
    if any(x in t for x in ["skripsi", "jurnal", "bab ", "metode", "kutipan", "referensi", "smartpls", "penelitian", "akademik"]):
        return "academic"
    if any(x in t for x in ["hitung", "rumus", "berapa", "persentase", "kalkulasi", "calculate"]):
        return "calculation"
    if any(x in t for x in ["dokumen", "file", "pdf", "rag", "knowledge", "sumber", "berdasarkan file"]):
        return "document_question"
    if any(x in t for x in ["riset", "cari data", "terbaru", "berita", "validasi", "cek sumber"]):
        return "research"
    if any(x in t for x in ["caption", "konten", "desain", "copywriting", "promosi", "judul produk"]):
        return "creative"
    if any(x in t for x in ["analisis", "analisa", "evaluasi", "strategi", "arsitektur", "bandingkan", "solusi terbaik"]):
        return "deep_reasoning"
    if wc <= 12:
        return "quick_chat"
    return "general"


PROMPT_TEMPLATES: Dict[str, str] = {
    "coding": "Jawab sebagai code reviewer senior. Berikan diagnosis, letak masalah, patch/kode yang bisa ditempel, dan langkah test singkat.",
    "academic": "Jawab dengan struktur akademik yang rapi, bahasa natural, tidak mengarang sumber, dan berikan poin yang langsung bisa dipakai.",
    "calculation": "Hitung secara teliti, tampilkan rumus/angka utama, lalu berikan jawaban akhir yang jelas.",
    "document_question": "Jawab berdasarkan konteks dokumen/knowledge base yang tersedia. Jika tidak ada sumber yang cukup, katakan keterbatasannya.",
    "research": "Pisahkan fakta, asumsi, dan langkah verifikasi. Jangan mengarang data terbaru.",
    "creative": "Berikan output kreatif yang siap pakai, ringkas, dan sesuai konteks komersial/branding.",
    "deep_reasoning": "Analisis masalah secara bertahap, prioritaskan solusi praktis, dan berikan rekomendasi akhir yang jelas.",
}


def enhance_prompt_for_intent(user_text: str, intent: str, enable_templates: bool = True) -> str:
    if not enable_templates:
        return user_text
    template = PROMPT_TEMPLATES.get(intent)
    if not template:
        return user_text
    return f"{user_text}\n\nInstruksi mode {intent}: {template}"


def build_power_context(
    store: PowerStore,
    user_text: str,
    base_memory: str = "",
    user_id: str = "global",
    enable_rag: bool = True,
    enable_persistent_memory: bool = True,
    rag_top_k: int = 5,
    memory_top_k: int = 8,
) -> str:
    sections: List[str] = []
    if str(base_memory or "").strip():
        sections.append(str(base_memory).strip())
    if enable_persistent_memory:
        memories = store.search_memories(user_text, user_id=user_id, limit=memory_top_k)
        if memories:
            lines = [f"- {m['text']}" for m in memories if str(m.get("text", "")).strip()]
            if lines:
                sections.append("MEMORY SQLITE RELEVAN:\n" + "\n".join(lines)[:3000])
    if enable_rag:
        docs = store.search_documents(user_text, limit=rag_top_k)
        if docs:
            lines = []
            for idx, doc in enumerate(docs, start=1):
                content = re.sub(r"\s+", " ", str(doc.get("content", ""))).strip()[:1200]
                lines.append(f"[KB{idx}] {doc.get('title')} (chunk {doc.get('chunk_index')}): {content}")
            sections.append("KONTEKS KNOWLEDGE BASE/RAG NON-INSTRUKSI:\n" + "\n\n".join(lines))
    return "\n\n".join([s for s in sections if s.strip()])[:9000]


def estimate_cost_idr(meta: Dict[str, Any], model: str = "") -> float:
    meta = meta or {}
    model_name = str(model or meta.get("active_model_final") or meta.get("model_requested") or meta.get("model") or "")
    usage = meta.get("usage") or {}
    in_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    out_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    price = model_price(model_name)
    return round((in_tokens / 1_000_000) * int(price.get("input") or 0) + (out_tokens / 1_000_000) * int(price.get("output") or 0), 4)


def should_self_verify(intent: str, user_text: str, enabled: bool = False) -> bool:
    if not enabled:
        return False
    if intent in {"coding", "academic", "calculation", "deep_reasoning", "research", "document_question"}:
        return True
    t = str(user_text or "").lower()
    return any(x in t for x in ["pastikan", "cek lagi", "valid", "akurat", "jangan salah"])


def verify_answer_with_model(
    api_url: str,
    api_key: str,
    verifier_model: str,
    system_prompt: str,
    user_text: str,
    answer: str,
    temperature: float = 0.1,
    max_completion_tokens: int = 2200,
    timeout: int = 60,
) -> Tuple[str, Dict[str, Any]]:
    if not verifier_model:
        return answer, {"self_verification_skipped": "no_verifier_model"}
    messages = [
        {"role": "system", "content": (system_prompt or "Kamu adalah pemeriksa jawaban yang teliti.")[:2200]},
        {
            "role": "user",
            "content": (
                "Periksa jawaban berikut. Jika sudah benar, rapikan sedikit tanpa memperpanjang. "
                "Jika ada kekeliruan, perbaiki langsung. Jangan sebut proses verifikasi.\n\n"
                f"Pertanyaan pengguna:\n{str(user_text)[:4500]}\n\nJawaban awal:\n{str(answer)[:5500]}"
            ),
        },
    ]
    started = time.time()
    verified, meta = call_api_once(
        api_url=api_url,
        api_key=api_key,
        model=verifier_model,
        messages=messages,
        temperature=temperature,
        max_completion_tokens=max_completion_tokens,
        timeout=timeout,
    )
    meta["self_verified_by"] = verifier_model
    meta["self_verification_latency_seconds"] = round(time.time() - started, 3)
    return verified or answer, meta


def generate_power_answer(
    *,
    api_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_text: str,
    base_memory_text: str = "",
    recent_messages: Optional[List[Dict[str, str]]] = None,
    fallback_models: Optional[List[str]] = None,
    expensive_fallback_models: Optional[List[str]] = None,
    allow_expensive_fallback: bool = True,
    max_expensive_models: int = 1,
    temperature: float = 0.3,
    max_completion_tokens: int = 1800,
    timeout: int = 60,
    smart_model_router: bool = True,
    return_to_primary: bool = True,
    max_smart_models: int = 2,
    store: Optional[PowerStore] = None,
    user_id: str = "public",
    channel: str = "web",
    enable_rag: bool = True,
    enable_persistent_memory: bool = True,
    enable_prompt_templates: bool = True,
    enable_self_verification: bool = False,
    daily_cost_limit_idr: float = 0,
    max_expensive_calls_per_day: int = 0,
) -> Tuple[str, Dict[str, Any]]:
    store = store or get_power_store()
    intent = classify_intent_text(user_text)

    # Budget guard: hard stop only when limit is configured and already reached.
    if daily_cost_limit_idr and daily_cost_limit_idr > 0:
        usage = store.usage_summary(days=1)
        if float(usage.get("cost_idr") or 0) >= float(daily_cost_limit_idr):
            return (
                "Batas biaya harian AI sudah tercapai. Admin dapat menaikkan DAILY_COST_LIMIT_IDR atau menunggu reset hari berikutnya.",
                {"budget_guard_blocked": True, "intent": intent, "daily_cost_limit_idr": daily_cost_limit_idr},
            )
    if max_expensive_calls_per_day and max_expensive_calls_per_day > 0:
        if store.count_expensive_calls_today() >= int(max_expensive_calls_per_day):
            allow_expensive_fallback = False
            expensive_fallback_models = []

    memory_text = build_power_context(
        store=store,
        user_text=user_text,
        base_memory=base_memory_text,
        user_id=user_id,
        enable_rag=enable_rag,
        enable_persistent_memory=enable_persistent_memory,
    )
    routed_user_text = enhance_prompt_for_intent(user_text, intent, enable_templates=enable_prompt_templates)

    started = time.time()
    answer = ""
    meta: Dict[str, Any] = {}
    success = False
    try:
        answer, meta = __import__("ai_core").generate_answer(
            api_url=api_url,
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            user_text=routed_user_text,
            memory_text=memory_text,
            recent_messages=recent_messages or [],
            fallback_models=fallback_models or [],
            expensive_fallback_models=expensive_fallback_models or [],
            allow_expensive_fallback=allow_expensive_fallback,
            max_expensive_models=max_expensive_models,
            temperature=temperature,
            max_completion_tokens=max_completion_tokens,
            timeout=timeout,
            smart_model_router=smart_model_router,
            return_to_primary=return_to_primary,
            max_smart_models=max_smart_models,
        )
        meta = meta or {}
        meta["power_intent"] = intent
        meta["power_rag_enabled"] = bool(enable_rag)
        meta["power_persistent_memory_enabled"] = bool(enable_persistent_memory)
        meta["power_prompt_template_enabled"] = bool(enable_prompt_templates)
        meta["power_latency_seconds"] = round(time.time() - started, 3)
        final_model = str(meta.get("active_model_final") or meta.get("model_requested") or model)

        if should_self_verify(intent, user_text, enabled=enable_self_verification):
            verifier = ""
            if expensive_fallback_models:
                verifier = expensive_fallback_models[0]
            elif fallback_models:
                verifier = fallback_models[0]
            else:
                verifier = final_model
            try:
                verified_answer, verify_meta = verify_answer_with_model(
                    api_url=api_url,
                    api_key=api_key,
                    verifier_model=verifier,
                    system_prompt=system_prompt,
                    user_text=user_text,
                    answer=answer,
                    temperature=min(float(temperature), 0.2),
                    max_completion_tokens=max(max_completion_tokens, 2200),
                    timeout=timeout,
                )
                if verified_answer:
                    answer = verified_answer
                    meta["self_verification"] = verify_meta
                    meta["self_verified_by"] = verifier
            except Exception as exc:
                meta["self_verification_error"] = str(exc)[:500]
        success = True
        return answer, meta
    finally:
        try:
            final_model = str((meta or {}).get("active_model_final") or (meta or {}).get("model_requested") or model)
            store.log_interaction(
                user_id=user_id,
                channel=channel,
                intent=intent,
                model=final_model,
                question=user_text,
                answer=answer,
                meta=meta,
                success=success,
            )
        except Exception:
            pass


def handle_power_command(text: str, store: PowerStore, user_id: str = "global", is_admin: bool = False) -> str:
    raw = str(text or "").strip()
    lower = raw.lower()
    if not raw.startswith("/"):
        return ""

    admin_only_prefixes = ("/ingat", "/lupa", "/rag", "/kb", "/biaya", "/usage", "/dokumen", "/benchmark")
    if lower.startswith(admin_only_prefixes) and not is_admin:
        return "Perintah ini hanya untuk admin."

    if lower.startswith("/ingat "):
        body = raw.split(" ", 1)[1].strip()
        mem_id = store.add_memory(body, user_id=user_id)
        return f"✅ Memory permanen disimpan. ID: {mem_id}"

    if lower.startswith("/lupa "):
        key = raw.split(" ", 1)[1].strip()
        count = store.delete_memories_containing(key, user_id=None)
        return f"✅ Memory yang mengandung '{key}' dihapus: {count}."

    if lower.startswith(("/rag cari ", "/kb cari ")):
        query = raw.split(" ", 2)[2].strip()
        docs = store.search_documents(query, limit=6)
        if not docs:
            return "Belum ada potongan knowledge base yang cocok."
        lines = ["🔎 Hasil Knowledge Base:"]
        for idx, doc in enumerate(docs, start=1):
            snippet = re.sub(r"\s+", " ", doc.get("content", "")).strip()[:420]
            lines.append(f"{idx}. {doc.get('title')} | score {doc.get('score')}\n{snippet}")
        return "\n\n".join(lines)

    if lower.startswith(("/rag tambah", "/kb tambah")):
        body = raw.split(" ", 2)[2].strip() if len(raw.split(" ", 2)) >= 3 else ""
        if "\n" in body:
            title, content = body.split("\n", 1)
        else:
            title, content = "Catatan manual", body
        doc_id, chunks = store.add_document(title=title.strip() or "Catatan manual", text=content, source=f"telegram:{user_id}")
        if not chunks:
            return "Gagal menambahkan RAG: isi dokumen kosong."
        return f"✅ Knowledge base ditambahkan. Doc ID: {doc_id}, chunks: {chunks}."

    if lower in {"/biaya", "/usage", "/biaya hari ini", "/usage hari ini"}:
        data = store.usage_summary(days=1)
        lines = [f"📊 Usage 24 jam terakhir: {data['requests']} request | estimasi Rp{data['cost_idr']:.2f}"]
        for row in data.get("by_model", [])[:10]:
            lines.append(
                f"- {row.get('model') or '-'}: {row.get('requests')} req | in {int(row.get('input_tokens') or 0)} | out {int(row.get('output_tokens') or 0)} | Rp{float(row.get('cost_idr') or 0):.2f}"
            )
        return "\n".join(lines)

    if lower in {"/template", "/template list", "/mode list"}:
        return "Mode tersedia: " + ", ".join(sorted(PROMPT_TEMPLATES.keys()))

    return ""


def run_model_benchmark(
    *,
    store: PowerStore,
    api_url: str,
    api_key: str,
    models: List[str],
    system_prompt: str = "Jawab ringkas dan akurat dalam bahasa Indonesia.",
    timeout: int = 45,
    max_models: int = 8,
) -> List[Dict[str, Any]]:
    tests = [
        ("quick", "Jawab satu kalimat: apa fungsi utama router model AI?"),
        ("reasoning", "Berikan 3 langkah prioritas memperbaiki aplikasi chatbot yang lambat dan sering fallback."),
        ("coding", "Sebutkan bug umum pada Python f-string jika tanda kutip nested salah, lalu berikan contoh perbaikannya."),
    ]
    results: List[Dict[str, Any]] = []
    for model in list(dict.fromkeys([m for m in models if m]))[: max(1, int(max_models or 8))]:
        for task, prompt in tests:
            started = time.time()
            try:
                answer, meta = call_api_once(
                    api_url=api_url,
                    api_key=api_key,
                    model=model,
                    messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
                    temperature=0,
                    max_completion_tokens=600,
                    timeout=timeout,
                )
                latency = round(time.time() - started, 3)
                score = 0.0
                if answer and len(answer.split()) >= 8:
                    score += 0.45
                if any(x in answer.lower() for x in ["router", "model", "fallback", "f-string", "kutip", "langkah"]):
                    score += 0.35
                if len(answer) < 1600:
                    score += 0.20
                score = round(min(1.0, score), 3)
                row = {"model": model, "task": task, "score": score, "latency_seconds": latency, "success": True, "error": ""}
                store.add_benchmark(model, task, score, latency, True, meta=meta)
            except Exception as exc:
                latency = round(time.time() - started, 3)
                row = {"model": model, "task": task, "score": 0.0, "latency_seconds": latency, "success": False, "error": str(exc)[:500]}
                store.add_benchmark(model, task, 0.0, latency, False, error=str(exc))
            results.append(row)
    return results
