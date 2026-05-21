"""Power features for Adioranye AI.

Production-oriented extension layer for model optimization, persistent memory,
lightweight RAG, response cache, usage/cost logging, per-intent prompt templates,
model benchmarking, adaptive model scoring, and circuit breaker protection.

Designed for Streamlit Cloud: SQLite only, no heavy dependencies required.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from ai_core import call_api_once, model_cost_tier, model_price

WIB_TZ = ZoneInfo("Asia/Jakarta")
DEFAULT_POWER_DB = ".adioranye_power.db"


# =========================
# General helpers
# =========================

def now_wib_text() -> str:
    return datetime.now(WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")


def _utc_ts() -> float:
    return time.time()


def _safe_json(data: Any) -> str:
    try:
        return json.dumps(data or {}, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


def _safe_json_loads(text: Any) -> Dict[str, Any]:
    try:
        value = json.loads(str(text or "{}"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _row_value(row: Any, key: str, index: int = 0, default: Any = None) -> Any:
    """Read a value safely from sqlite3.Row, dict, tuple/list, or None."""
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        return default


def _row_to_dict(row: Any, columns: Optional[List[str]] = None) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        pass
    if columns and isinstance(row, (tuple, list)):
        return {columns[i]: row[i] if i < len(row) else None for i in range(len(columns))}
    return {}


def _tokenize(text: str) -> List[str]:
    words = re.findall(r"[a-zA-Z0-9_\-]{3,}", str(text or "").lower())
    stop = {
        "yang", "dan", "atau", "untuk", "dengan", "dari", "pada", "agar", "jadi", "ini", "itu",
        "dalam", "akan", "bisa", "saya", "anda", "kami", "kamu", "apa", "bagaimana", "karena",
        "the", "and", "for", "with", "from", "that", "this", "are", "was", "were", "you", "your",
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
    return chunks[:3000]


# =========================
# Knowledge base file extraction helpers
# =========================

def _decode_bytes(data: bytes) -> str:
    raw = data or b""
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc, "ignore")
        except Exception:
            continue
    return raw.decode("utf-8", "ignore")


def _csv_bytes_to_text(data: bytes) -> str:
    text = _decode_bytes(data)
    reader = csv.reader(io.StringIO(text))
    lines: List[str] = []
    for idx, row in enumerate(reader):
        if idx >= 5000:
            lines.append("[CSV dipotong: lebih dari 5000 baris]")
            break
        clean = [str(cell or "").strip() for cell in row]
        if any(clean):
            lines.append(" | ".join(clean))
    return "\n".join(lines)


def _json_bytes_to_text(data: bytes) -> str:
    text = _decode_bytes(data)
    try:
        obj = json.loads(text)
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return text


def extract_text_from_file_bytes(filename: str, data: bytes) -> Tuple[str, str]:
    """Extract text from common knowledge-base uploads with optional dependencies.

    Supported without extra dependencies: txt, md, csv, json, log, py, js, html, css, xml.
    Supported if libraries exist in the app environment: pdf via pypdf/PyPDF2, docx via python-docx,
    xlsx via openpyxl. On failure, it returns a clear text note instead of crashing Streamlit.
    """
    name = str(filename or "uploaded_file").strip() or "uploaded_file"
    suffix = Path(name).suffix.lower()
    raw = data or b""
    if not raw:
        return "", "empty"

    text_suffixes = {".txt", ".md", ".markdown", ".log", ".py", ".js", ".ts", ".html", ".css", ".xml", ".jsonl"}
    if suffix in text_suffixes:
        return _decode_bytes(raw), "text"
    if suffix == ".csv":
        return _csv_bytes_to_text(raw), "csv"
    if suffix == ".json":
        return _json_bytes_to_text(raw), "json"

    if suffix == ".pdf":
        try:
            try:
                from pypdf import PdfReader  # type: ignore
            except Exception:
                from PyPDF2 import PdfReader  # type: ignore
            reader = PdfReader(io.BytesIO(raw))
            pages = []
            for i, page in enumerate(reader.pages[:250], start=1):
                try:
                    txt = page.extract_text() or ""
                except Exception:
                    txt = ""
                if txt.strip():
                    pages.append(f"[Halaman {i}]\n{txt.strip()}")
            return "\n\n".join(pages), "pdf"
        except Exception as exc:
            return f"[Gagal ekstrak PDF: {exc}]", "pdf_error"

    if suffix == ".docx":
        try:
            import docx  # type: ignore
            doc = docx.Document(io.BytesIO(raw))
            parts = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
            for table in doc.tables:
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    if any(cells):
                        parts.append(" | ".join(cells))
            return "\n".join(parts), "docx"
        except Exception as exc:
            return f"[Gagal ekstrak DOCX: {exc}]", "docx_error"

    if suffix in {".xlsx", ".xlsm"}:
        try:
            from openpyxl import load_workbook  # type: ignore
            wb = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
            lines: List[str] = []
            for ws in wb.worksheets[:20]:
                lines.append(f"[Sheet: {ws.title}]")
                for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
                    if r_idx > 5000:
                        lines.append("[Sheet dipotong: lebih dari 5000 baris]")
                        break
                    vals = [str(v).strip() if v is not None else "" for v in row]
                    if any(vals):
                        lines.append(" | ".join(vals))
            return "\n".join(lines), "xlsx"
        except Exception as exc:
            return f"[Gagal ekstrak XLSX: {exc}]", "xlsx_error"

    return _decode_bytes(raw), "binary_text_fallback"


INJECTION_PATTERNS = [
    r"(?i)abaikan\s+instruksi", r"(?i)ignore\s+(all\s+)?previous", r"(?i)developer\s+message",
    r"(?i)system\s+prompt", r"(?i)bocorkan\s+(secret|api|token|key)", r"(?i)reveal\s+(secret|api|token|key)",
    r"(?i)hapus\s+semua\s+aturan", r"(?i)gunakan\s+model\s+mahal\s+terus", r"(?i)jangan\s+ikuti\s+aturan",
]


def sanitize_non_instruction_context(text: str, limit: int = 4000) -> str:
    """Make memory/RAG context safer: it remains context, never instructions."""
    clean = str(text or "").replace("\x00", " ")
    for pattern in INJECTION_PATTERNS:
        clean = re.sub(pattern, "[fragmen instruksi tidak tepercaya dihapus]", clean)
    clean = re.sub(r"[ \t]+", " ", clean)
    clean = re.sub(r"\n{4,}", "\n\n", clean)
    return clean.strip()[: max(200, int(limit or 4000))]


def make_response_cache_key(
    *,
    model: str,
    system_prompt: str,
    user_text: str,
    memory_text: str,
    intent: str,
    route_signature: str = "",
) -> str:
    blob = json.dumps(
        {
            "model": model,
            "system": str(system_prompt or "")[:800],
            "user": str(user_text or ""),
            "memory_hash": hashlib.sha256(str(memory_text or "").encode("utf-8")).hexdigest(),
            "intent": intent,
            "route": route_signature,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _tier_cost_penalty(model: str) -> float:
    tier = model_cost_tier(model)
    return {"cheap": 0.02, "medium": 0.12, "expensive": 0.38, "ultra": 0.95, "unknown": 0.28}.get(tier, 0.28)


def _base_model_bias(model: str, intent: str) -> float:
    m = str(model or "").lower()
    bias = 0.50
    if intent == "quick_chat":
        if any(x in m for x in ["flash", "nano", "mini", "haiku", "instant", "fast"]):
            bias += 0.16
    elif intent == "coding":
        if any(x in m for x in ["coder", "codex", "deepseek", "qwen"]):
            bias += 0.22
        if any(x in m for x in ["sonnet", "gpt-5.1", "gpt-5.2", "gpt-5.3", "gpt-5.4", "gpt-5.5"]):
            bias += 0.12
    elif intent in {"academic", "research", "deep_reasoning", "document_question"}:
        if any(x in m for x in ["sonnet", "qwen", "gpt-5.1", "gpt-5.2", "gpt-5.4", "gpt-5.5", "deepseek", "glm", "kimi"]):
            bias += 0.18
    elif intent == "creative":
        if any(x in m for x in ["claude", "gemini", "haiku", "sonnet", "gpt"]):
            bias += 0.14
    else:
        if any(x in m for x in ["mini", "flash", "nano", "haiku"]):
            bias += 0.08
    if ":free" in m or ":fast" in m:
        bias += 0.04
    if ":slow" in m:
        bias -= 0.03
    return max(0.05, min(0.95, bias))


def adaptive_token_budget_for_intent(intent: str, user_text: str, base: int = 1800) -> int:
    base = int(base or 1800)
    words = len(str(user_text or "").split())
    budgets = {
        "quick_chat": 800,
        "creative": 1400,
        "calculation": 1800,
        "coding": 3600,
        "academic": 4200,
        "document_question": 4800,
        "research": 4200,
        "deep_reasoning": 4200,
        "general": 2400,
    }
    target = budgets.get(intent, base)
    if words > 300:
        target = max(target, 4800)
    elif words > 140:
        target = max(target, 3600)
    return max(500, min(max(base, target), 6500))


def extract_quality_score(meta: Dict[str, Any], answer: str = "") -> float:
    try:
        if meta and meta.get("quality_score") is not None:
            return float(meta.get("quality_score") or 0)
    except Exception:
        pass
    words = len(str(answer or "").split())
    if words <= 0:
        return 0.0
    score = 0.42
    if words >= 20:
        score += 0.16
    if words >= 80:
        score += 0.10
    if "maaf" in str(answer or "").lower() and words < 50:
        score -= 0.08
    return max(0.0, min(1.0, score))


# =========================
# Store / schema
# =========================

@dataclass
class PowerStore:
    db_path: str = DEFAULT_POWER_DB

    def __post_init__(self) -> None:
        self.db_path = str(self.db_path or DEFAULT_POWER_DB)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        path = Path(self.db_path)
        if path.parent and str(path.parent) not in {"", "."}:
            path.parent.mkdir(parents=True, exist_ok=True)
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
                CREATE INDEX IF NOT EXISTS idx_interactions_intent ON interactions(intent, ts);

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

                CREATE TABLE IF NOT EXISTS response_cache (
                    cache_key TEXT PRIMARY KEY,
                    ts REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    model TEXT DEFAULT '',
                    intent TEXT DEFAULT '',
                    answer TEXT NOT NULL,
                    meta_json TEXT DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_response_cache_expires ON response_cache(expires_at);

                CREATE TABLE IF NOT EXISTS model_scores (
                    model TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    total_requests INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    total_latency REAL DEFAULT 0,
                    total_quality REAL DEFAULT 0,
                    total_cost REAL DEFAULT 0,
                    last_success_at REAL DEFAULT 0,
                    last_error_at REAL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(model, intent)
                );
                CREATE INDEX IF NOT EXISTS idx_model_scores_intent ON model_scores(intent, updated_at);

                CREATE TABLE IF NOT EXISTS circuit_breakers (
                    model TEXT PRIMARY KEY,
                    failure_count INTEGER DEFAULT 0,
                    open_until REAL DEFAULT 0,
                    last_error TEXT DEFAULT '',
                    updated_at REAL NOT NULL
                );
                """
            )
            # Optional SQLite FTS5. Streamlit Cloud usually supports it, but keep fallback.
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(content, title, source, content='')"
                )
            except Exception:
                pass

    # Persistent memory
    def add_memory(self, text: str, user_id: str = "global", tags: str = "") -> int:
        clean = sanitize_non_instruction_context(str(text or "").strip(), limit=4000)
        if not clean:
            return 0
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO memories(user_id, text, tags, created_at) VALUES (?, ?, ?, ?)",
                (str(user_id or "global"), clean, str(tags or "")[:300], _utc_ts()),
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
                "SELECT * FROM memories WHERE user_id IN (?, 'global') ORDER BY created_at DESC LIMIT 500",
                (str(user_id or "global"),),
            ).fetchall()
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for row in rows:
            item = dict(row)
            score = _score_text(query, item.get("text", ""))
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
                safe_chunk = sanitize_non_instruction_context(chunk, limit=6000)
                terms = " ".join(sorted(set(_tokenize(safe_chunk)))[:500])
                cur_chunk = conn.execute(
                    "INSERT INTO chunks(doc_id, chunk_index, content, terms, created_at) VALUES (?, ?, ?, ?, ?)",
                    (doc_id, idx, safe_chunk, terms, _utc_ts()),
                )
                try:
                    conn.execute(
                        "INSERT INTO chunks_fts(rowid, content, title, source) VALUES (?, ?, ?, ?)",
                        (int(cur_chunk.lastrowid), safe_chunk, title, str(source or "manual")[:500]),
                    )
                except Exception:
                    pass
            return doc_id, len(chunks)

    def search_documents(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        q = str(query or "").strip()
        if not q:
            return []
        results: Dict[int, Dict[str, Any]] = {}
        # Try FTS5 first.
        try:
            terms = " OR ".join(_tokenize(q)[:8]) or q
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT c.id, c.doc_id, c.chunk_index, c.content, d.title, d.source, c.created_at,
                           bm25(chunks_fts) AS fts_score
                    FROM chunks_fts
                    JOIN chunks c ON c.id = chunks_fts.rowid
                    JOIN documents d ON d.id = c.doc_id
                    WHERE chunks_fts MATCH ?
                    ORDER BY fts_score ASC LIMIT ?
                    """,
                    (terms, max(1, int(limit or 5)) * 2),
                ).fetchall()
            for row in rows:
                item = dict(row)
                item["score"] = round(1.0 / (1.0 + abs(float(item.get("fts_score") or 0))), 4)
                results[int(item["id"])] = item
        except Exception:
            pass

        # Lexical fallback and supplement.
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.doc_id, c.chunk_index, c.content, d.title, d.source, c.created_at
                FROM chunks c JOIN documents d ON d.id = c.doc_id
                ORDER BY c.created_at DESC LIMIT 1600
                """
            ).fetchall()
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for row in rows:
            item = dict(row)
            score = _score_text(q, item.get("content", "") + " " + item.get("title", ""))
            if score > 0:
                item["score"] = round(score, 4)
                scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        for score, item in scored[: max(1, int(limit or 5)) * 3]:
            results.setdefault(int(item["id"]), item)
        final = list(results.values())
        final.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
        return final[: max(1, int(limit or 5))]

    def list_documents(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT d.id, d.title, d.source, d.created_at, COUNT(c.id) AS chunks,
                       COALESCE(SUM(LENGTH(c.content)), 0) AS characters
                FROM documents d LEFT JOIN chunks c ON c.doc_id = d.id
                GROUP BY d.id ORDER BY d.created_at DESC LIMIT ?
                """,
                (max(1, int(limit or 20)),),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["created_at_wib"] = datetime.fromtimestamp(float(item.get("created_at") or 0), WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")
            except Exception:
                item["created_at_wib"] = ""
            out.append(item)
        return out

    def get_document(self, doc_id: int, max_chars: int = 8000) -> Dict[str, Any]:
        try:
            doc_id = int(doc_id)
        except Exception:
            return {}
        with self._connect() as conn:
            doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if not doc:
                return {}
            chunks = conn.execute(
                "SELECT chunk_index, content FROM chunks WHERE doc_id = ? ORDER BY chunk_index ASC",
                (doc_id,),
            ).fetchall()
        item = dict(doc)
        item["chunks"] = len(chunks)
        body_parts = []
        total = 0
        for ch in chunks:
            text = str(ch["content"] or "")
            if total + len(text) > max_chars:
                body_parts.append("[Dokumen dipotong untuk preview]")
                break
            body_parts.append(f"[Chunk {ch['chunk_index']}]\n{text}")
            total += len(text)
        item["preview"] = "\n\n".join(body_parts)
        return item

    def delete_document(self, doc_id: int) -> bool:
        try:
            doc_id = int(doc_id)
        except Exception:
            return False
        with self._connect() as conn:
            chunk_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM chunks WHERE doc_id = ?", (doc_id,)).fetchall()]
            if chunk_ids:
                try:
                    conn.executemany("DELETE FROM chunks_fts WHERE rowid = ?", [(cid,) for cid in chunk_ids])
                except Exception:
                    pass
            cur = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            return bool(cur.rowcount)

    def rebuild_knowledge_index(self) -> Tuple[int, int]:
        """Rebuild FTS index from chunks. Returns (documents, chunks)."""
        with self._connect() as conn:
            try:
                conn.execute("DELETE FROM chunks_fts")
            except Exception:
                try:
                    conn.execute("DROP TABLE IF EXISTS chunks_fts")
                    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(content, title, source, content='')")
                except Exception:
                    pass
            rows = conn.execute(
                """
                SELECT c.id, c.content, d.title, d.source
                FROM chunks c JOIN documents d ON d.id = c.doc_id
                ORDER BY c.id ASC
                """
            ).fetchall()
            inserted = 0
            for row in rows:
                try:
                    conn.execute(
                        "INSERT INTO chunks_fts(rowid, content, title, source) VALUES (?, ?, ?, ?)",
                        (int(row["id"]), str(row["content"] or ""), str(row["title"] or ""), str(row["source"] or "")),
                    )
                    inserted += 1
                except Exception:
                    pass
            doc_count = int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] or 0)
            return doc_count, inserted

    def knowledge_stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            docs = int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] or 0)
            chunks = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] or 0)
            chars = int(conn.execute("SELECT COALESCE(SUM(LENGTH(content)), 0) FROM chunks").fetchone()[0] or 0)
            latest = conn.execute("SELECT title, source, created_at FROM documents ORDER BY created_at DESC LIMIT 1").fetchone()
        data = {"documents": docs, "chunks": chunks, "characters": chars}
        if latest:
            data["latest_title"] = latest["title"]
            data["latest_source"] = latest["source"]
            try:
                data["latest_at_wib"] = datetime.fromtimestamp(float(latest["created_at"] or 0), WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")
            except Exception:
                data["latest_at_wib"] = ""
        return data

    # Cache
    def get_cached_response(self, cache_key: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        if not cache_key:
            return None
        now = _utc_ts()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM response_cache WHERE cache_key = ?", (cache_key,)).fetchone()
            if not row:
                return None
            if float(_row_value(row, "expires_at", 2, 0) or 0) < now:
                conn.execute("DELETE FROM response_cache WHERE cache_key = ?", (cache_key,))
                return None
            answer = str(_row_value(row, "answer", 5, "") or "")
            meta = _safe_json_loads(_row_value(row, "meta_json", 6, "{}"))
            meta["power_response_cache_hit"] = True
            return answer, meta

    def set_cached_response(self, cache_key: str, answer: str, meta: Dict[str, Any], ttl_seconds: int = 1800) -> None:
        if not cache_key or not str(answer or "").strip():
            return
        ttl = max(60, int(ttl_seconds or 1800))
        now = _utc_ts()
        with self._connect() as conn:
            conn.execute("DELETE FROM response_cache WHERE expires_at < ?", (now,))
            conn.execute(
                """
                INSERT OR REPLACE INTO response_cache(cache_key, ts, expires_at, model, intent, answer, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_key,
                    now,
                    now + ttl,
                    str((meta or {}).get("active_model_final") or (meta or {}).get("model_requested") or "")[:160],
                    str((meta or {}).get("power_intent") or "")[:80],
                    str(answer)[:30000],
                    _safe_json(meta)[:12000],
                ),
            )

    # Circuit breaker
    def is_model_blocked(self, model: str) -> bool:
        if not model:
            return False
        with self._connect() as conn:
            row = conn.execute("SELECT open_until FROM circuit_breakers WHERE model = ?", (model,)).fetchone()
        return bool(row and float(_row_value(row, "open_until", 0, 0) or 0) > _utc_ts())

    def filter_blocked_models(self, models: List[str]) -> List[str]:
        unique = list(dict.fromkeys([m for m in models if m]))
        available = [m for m in unique if not self.is_model_blocked(m)]
        return available or unique

    def register_model_failure(self, model: str, error: str = "", max_failures: int = 3, cooldown_seconds: int = 1800) -> None:
        if not model:
            return
        now = _utc_ts()
        with self._connect() as conn:
            row = conn.execute("SELECT failure_count FROM circuit_breakers WHERE model = ?", (model,)).fetchone()
            failures = int(_row_value(row, "failure_count", 0, 0) or 0) + 1
            open_until = now + max(60, int(cooldown_seconds or 1800)) if failures >= max(1, int(max_failures or 3)) else 0
            conn.execute(
                """
                INSERT OR REPLACE INTO circuit_breakers(model, failure_count, open_until, last_error, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (model, failures, open_until, str(error or "")[:800], now),
            )

    def register_model_success(self, model: str) -> None:
        if not model:
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO circuit_breakers(model, failure_count, open_until, last_error, updated_at)
                VALUES (?, 0, 0, '', ?)
                """,
                (model, _utc_ts()),
            )

    def circuit_breaker_status(self, limit: int = 80) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM circuit_breakers ORDER BY open_until DESC, failure_count DESC, updated_at DESC LIMIT ?",
                (max(1, int(limit or 80)),),
            ).fetchall()
        out = []
        now = _utc_ts()
        for row in rows:
            item = dict(row)
            item["blocked"] = float(item.get("open_until") or 0) > now
            item["open_until_wib"] = datetime.fromtimestamp(float(item.get("open_until") or 0), WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB") if item.get("open_until") else ""
            out.append(item)
        return out

    # Adaptive model score
    def update_model_score(self, model: str, intent: str, success: bool, latency_seconds: float, quality_score: float, cost_idr: float) -> None:
        model = str(model or "").strip()
        intent = str(intent or "general").strip() or "general"
        if not model:
            return
        now = _utc_ts()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM model_scores WHERE model = ? AND intent = ?", (model, intent)).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE model_scores
                    SET total_requests = total_requests + 1,
                        success_count = success_count + ?,
                        error_count = error_count + ?,
                        total_latency = total_latency + ?,
                        total_quality = total_quality + ?,
                        total_cost = total_cost + ?,
                        last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END,
                        last_error_at = CASE WHEN ? THEN ? ELSE last_error_at END,
                        updated_at = ?
                    WHERE model = ? AND intent = ?
                    """,
                    (
                        1 if success else 0,
                        0 if success else 1,
                        float(latency_seconds or 0),
                        float(quality_score or 0),
                        float(cost_idr or 0),
                        1 if success else 0,
                        now,
                        0 if success else 1,
                        now,
                        now,
                        model,
                        intent,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO model_scores(model,intent,total_requests,success_count,error_count,total_latency,total_quality,total_cost,last_success_at,last_error_at,updated_at)
                    VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        model,
                        intent,
                        1 if success else 0,
                        0 if success else 1,
                        float(latency_seconds or 0),
                        float(quality_score or 0),
                        float(cost_idr or 0),
                        now if success else 0,
                        0 if success else now,
                        now,
                    ),
                )

    def model_score_rows(self, intent: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if intent:
                rows = conn.execute(
                    "SELECT * FROM model_scores WHERE intent = ? ORDER BY updated_at DESC LIMIT ?",
                    (intent, max(1, int(limit or 100))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM model_scores ORDER BY updated_at DESC LIMIT ?",
                    (max(1, int(limit or 100)),),
                ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            req = max(1, int(item.get("total_requests") or 1))
            item["success_rate"] = round(float(item.get("success_count") or 0) / req, 4)
            item["avg_latency"] = round(float(item.get("total_latency") or 0) / req, 3)
            item["avg_quality"] = round(float(item.get("total_quality") or 0) / req, 4)
            item["avg_cost"] = round(float(item.get("total_cost") or 0) / req, 4)
            item["computed_score"] = round(self.compute_model_score(item.get("model", ""), item.get("intent", "general")), 4)
            out.append(item)
        out.sort(key=lambda x: float(x.get("computed_score") or 0), reverse=True)
        return out

    def compute_model_score(self, model: str, intent: str) -> float:
        model = str(model or "").strip()
        intent = str(intent or "general").strip() or "general"
        if not model:
            return 0.0
        if self.is_model_blocked(model):
            return -10.0
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM model_scores WHERE model = ? AND intent = ?", (model, intent)).fetchone()
            if not row and intent != "general":
                row = conn.execute("SELECT * FROM model_scores WHERE model = ? AND intent = 'general'", (model,)).fetchone()
        base = _base_model_bias(model, intent)
        cost_penalty = _tier_cost_penalty(model)
        if not row:
            return max(0.01, base - cost_penalty)
        req = max(1, int(_row_value(row, "total_requests", 2, 1) or 1))
        success_rate = float(_row_value(row, "success_count", 3, 0) or 0) / req
        error_rate = float(_row_value(row, "error_count", 4, 0) or 0) / req
        avg_latency = float(_row_value(row, "total_latency", 5, 0) or 0) / req
        avg_quality = float(_row_value(row, "total_quality", 6, 0) or 0) / req
        avg_cost = float(_row_value(row, "total_cost", 7, 0) or 0) / req
        latency_score = 1.0 / (1.0 + max(0.0, avg_latency) / 8.0)
        cost_score = 1.0 / (1.0 + max(0.0, avg_cost) / 20.0)
        confidence = min(1.0, req / 12.0)
        learned = (success_rate * 0.34) + (avg_quality * 0.30) + (latency_score * 0.18) + (cost_score * 0.10) - (error_rate * 0.20)
        blended = base * (1.0 - confidence) + learned * confidence
        return max(-1.0, min(1.5, blended - cost_penalty))

    def rank_models_for_intent(self, models: List[str], intent: str) -> List[str]:
        unique = self.filter_blocked_models(list(dict.fromkeys([m for m in models if m])))
        return sorted(unique, key=lambda m: self.compute_model_score(m, intent), reverse=True)

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
        latency = float(meta.get("latency_seconds") or meta.get("power_latency_seconds") or 0)
        quality = extract_quality_score(meta, answer)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO interactions(ts,user_id,channel,intent,model,input_tokens,output_tokens,cost_idr,
                                         latency_seconds,success,question_preview,answer_preview,meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _utc_ts(),
                    str(user_id or "public")[:120],
                    str(channel or "web")[:40],
                    str(intent or "")[:80],
                    str(model or "")[:160],
                    input_tokens,
                    output_tokens,
                    cost_idr,
                    latency,
                    1 if success else 0,
                    str(question or "")[:500],
                    str(answer or "")[:500],
                    _safe_json(meta)[:12000],
                ),
            )
        self.update_model_score(model=model, intent=intent, success=success, latency_seconds=latency, quality_score=quality, cost_idr=cost_idr)
        if success:
            self.register_model_success(model)
        else:
            self.register_model_failure(model, error=str((meta or {}).get("error") or "failed_interaction"))

    def usage_summary(self, days: int = 1) -> Dict[str, Any]:
        since = _utc_ts() - max(1, int(days or 1)) * 86400
        by_model_columns = ["model", "requests", "input_tokens", "output_tokens", "cost_idr", "avg_latency"]
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT model, COUNT(*) AS requests, COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens, COALESCE(SUM(cost_idr), 0) AS cost_idr,
                       COALESCE(AVG(latency_seconds), 0) AS avg_latency
                FROM interactions WHERE ts >= ? GROUP BY model ORDER BY cost_idr DESC
                """,
                (since,),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) AS requests, COALESCE(SUM(cost_idr), 0) AS cost_idr FROM interactions WHERE ts >= ?",
                (since,),
            ).fetchone()
        return {
            "days": int(days or 1),
            "requests": int(_row_value(total, "requests", 0, 0) or 0),
            "cost_idr": float(_row_value(total, "cost_idr", 1, 0) or 0),
            "by_model": [_row_to_dict(row, by_model_columns) for row in rows],
        }

    def count_expensive_calls_today(self) -> int:
        start = datetime.now(WIB_TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self._connect() as conn:
            rows = conn.execute("SELECT model FROM interactions WHERE ts >= ?", (start,)).fetchall()
        return sum(
            1
            for row in rows
            if model_cost_tier(str(_row_value(row, "model", 0, "") or "")) in {"medium", "expensive", "ultra"}
        )

    def database_overview(self) -> Dict[str, Any]:
        """Return compact DB statistics for admin health center."""
        with self._connect() as conn:
            tables = {
                "memories": "memories",
                "documents": "documents",
                "chunks": "chunks",
                "interactions": "interactions",
                "benchmarks": "benchmarks",
                "response_cache": "response_cache",
                "model_scores": "model_scores",
                "circuit_breakers": "circuit_breakers",
            }
            out: Dict[str, Any] = {}
            for key, table in tables.items():
                try:
                    out[key] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
                except Exception:
                    out[key] = 0
        try:
            size = Path(self.db_path).stat().st_size
            units = ["B", "KB", "MB", "GB"]
            value = float(size)
            for unit in units:
                if value < 1024 or unit == units[-1]:
                    out["db_size"] = f"{value:.1f} {unit}"
                    break
                value /= 1024
        except Exception:
            out["db_size"] = "0 B"
        return out

    def cleanup_old_data(self, log_days: int = 30, cache_days: int = 7, benchmark_days: int = 14) -> Dict[str, int]:
        """Delete old operational rows while preserving knowledge base and memories."""
        now = _utc_ts()
        deleted: Dict[str, int] = {}
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM interactions WHERE ts < ?", (now - max(1, int(log_days or 30)) * 86400,))
            deleted["interactions"] = int(cur.rowcount or 0)
            cur = conn.execute("DELETE FROM response_cache WHERE expires_at < ? OR ts < ?", (now, now - max(1, int(cache_days or 7)) * 86400))
            deleted["response_cache"] = int(cur.rowcount or 0)
            cur = conn.execute("DELETE FROM benchmarks WHERE ts < ?", (now - max(1, int(benchmark_days or 14)) * 86400,))
            deleted["benchmarks"] = int(cur.rowcount or 0)
            try:
                conn.execute("VACUUM")
            except Exception:
                pass
        return deleted

    def clear_usage_logs(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM interactions")
            return int(cur.rowcount or 0)

    def clear_response_cache(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM response_cache")
            return int(cur.rowcount or 0)

    def clear_memories_all(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM memories")
            return int(cur.rowcount or 0)

    def clear_knowledge_base(self) -> Dict[str, int]:
        with self._connect() as conn:
            counts = {
                "documents": int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] or 0),
                "chunks": int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] or 0),
            }
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM documents")
            try:
                conn.execute("DELETE FROM chunks_fts")
            except Exception:
                pass
            return counts

    def add_benchmark(self, model: str, task: str, score: float, latency_seconds: float, success: bool, error: str = "", meta: Optional[Dict[str, Any]] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO benchmarks(ts,model,task,score,latency_seconds,success,error,meta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_utc_ts(), model, task, float(score or 0), float(latency_seconds or 0), 1 if success else 0, str(error or "")[:1000], _safe_json(meta or {})[:4000]),
            )
        self.update_model_score(model=model, intent=task, success=success, latency_seconds=latency_seconds, quality_score=score, cost_idr=0)
        if success:
            self.register_model_success(model)
        else:
            self.register_model_failure(model, error=error)

    def latest_benchmarks(self, limit: int = 50) -> List[Dict[str, Any]]:
        columns = ["id", "ts", "model", "task", "score", "latency_seconds", "success", "error", "meta_json"]
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM benchmarks ORDER BY ts DESC LIMIT ?", (max(1, int(limit or 50)),)).fetchall()
        return [_row_to_dict(row, columns) for row in rows]


_STORE_CACHE: Dict[str, PowerStore] = {}


def get_power_store(db_path: str = DEFAULT_POWER_DB) -> PowerStore:
    path = str(db_path or DEFAULT_POWER_DB)
    if path not in _STORE_CACHE:
        _STORE_CACHE[path] = PowerStore(path)
    return _STORE_CACHE[path]


# =========================
# Intent & prompt policy
# =========================

def classify_intent_text(text: str) -> str:
    t = str(text or "").lower().strip()
    wc = len(t.split())
    if not t:
        return "empty"
    if t.startswith(("/", "!")):
        return "admin_command"
    if any(x in t for x in ["```", "def ", "class ", "traceback", "error", "bug", "streamlit", "api", "vercel", "github", "kode", "coding", "python", "javascript", "patch"]):
        return "coding"
    if any(x in t for x in ["skripsi", "jurnal", "bab ", "metode", "kutipan", "referensi", "smartpls", "penelitian", "akademik", "tesis"]):
        return "academic"
    if any(x in t for x in ["hitung", "rumus", "berapa", "persentase", "kalkulasi", "calculate"]):
        return "calculation"
    if any(x in t for x in ["dokumen", "file", "pdf", "rag", "knowledge", "sumber", "berdasarkan file", "berdasarkan dokumen"]):
        return "document_question"
    if any(x in t for x in ["riset", "cari data", "terbaru", "berita", "validasi", "cek sumber", "jurnal terbaru"]):
        return "research"
    if any(x in t for x in ["caption", "konten", "desain", "copywriting", "promosi", "judul produk", "iklan"]):
        return "creative"
    if any(x in t for x in ["analisis", "analisa", "evaluasi", "strategi", "arsitektur", "bandingkan", "solusi terbaik", "algoritma", "optimasi"]):
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
    rag_top_k: int = 5,
    enable_persistent_memory: bool = True,
    memory_top_k: int = 8,
) -> str:
    sections: List[str] = []
    base = sanitize_non_instruction_context(base_memory, limit=3500)
    if base:
        sections.append(base)
    if enable_persistent_memory:
        memories = store.search_memories(user_text, user_id=user_id, limit=memory_top_k)
        if memories:
            lines = [f"- {sanitize_non_instruction_context(m.get('text', ''), limit=500)}" for m in memories if str(m.get("text", "")).strip()]
            if lines:
                sections.append("MEMORY SQLITE RELEVAN (konteks non-instruksi):\n" + "\n".join(lines)[:3000])
    if enable_rag:
        docs = store.search_documents(user_text, limit=rag_top_k)
        if docs:
            lines = []
            for idx, doc in enumerate(docs, start=1):
                content = re.sub(r"\s+", " ", sanitize_non_instruction_context(str(doc.get("content", "")), limit=1200)).strip()
                lines.append(f"[KB{idx}] {doc.get('title')} (chunk {doc.get('chunk_index')}): {content}")
            sections.append("KONTEKS KNOWLEDGE BASE/RAG NON-INSTRUKSI:\n" + "\n\n".join(lines))
    return "\n\n".join([s for s in sections if s.strip()])[:10000]


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


# =========================
# Main answer wrapper
# =========================

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
    rag_top_k: int = 5,
    enable_persistent_memory: bool = True,
    enable_prompt_templates: bool = True,
    enable_self_verification: bool = False,
    daily_cost_limit_idr: float = 0,
    max_expensive_calls_per_day: int = 0,
    enable_response_cache: bool = True,
    response_cache_ttl_seconds: int = 1800,
    enable_adaptive_scoring: bool = True,
    enable_circuit_breaker: bool = True,
    circuit_max_failures: int = 3,
    circuit_cooldown_seconds: int = 1800,
) -> Tuple[str, Dict[str, Any]]:
    store = store or get_power_store()
    intent = classify_intent_text(user_text)

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

    rag_sources: List[Dict[str, Any]] = []
    if enable_rag:
        try:
            rag_sources = store.search_documents(user_text, limit=max(1, int(rag_top_k or 5)))
        except Exception:
            rag_sources = []

    memory_text = build_power_context(
        store=store,
        user_text=user_text,
        base_memory=base_memory_text,
        user_id=user_id,
        enable_rag=enable_rag,
        rag_top_k=max(1, int(rag_top_k or 5)),
        enable_persistent_memory=enable_persistent_memory,
    )
    routed_user_text = enhance_prompt_for_intent(user_text, intent, enable_templates=enable_prompt_templates)

    # Adaptive policy: rank candidate models using historical success, quality, latency, cost, and circuit breaker.
    cheap_candidates = list(dict.fromkeys([m for m in (fallback_models or []) if m]))
    expensive_candidates = list(dict.fromkeys([m for m in (expensive_fallback_models or []) if m]))
    all_candidates = list(dict.fromkeys([model] + cheap_candidates + expensive_candidates))
    if enable_circuit_breaker:
        all_candidates = store.filter_blocked_models(all_candidates)
    if enable_adaptive_scoring:
        ranked_all = store.rank_models_for_intent(all_candidates, intent)
    else:
        ranked_all = all_candidates
    selected_model = ranked_all[0] if ranked_all else model

    ranked_cheap = [m for m in ranked_all if m != selected_model and model_cost_tier(m) == "cheap"]
    ranked_expensive = [m for m in ranked_all if m != selected_model and model_cost_tier(m) in {"medium", "expensive", "ultra"}]
    # Preserve any still unranked fallback if all candidates were filtered weirdly.
    ranked_cheap.extend([m for m in cheap_candidates if m != selected_model and m not in ranked_cheap])
    ranked_expensive.extend([m for m in expensive_candidates if m != selected_model and m not in ranked_expensive])

    adjusted_max_tokens = adaptive_token_budget_for_intent(intent, user_text, base=max_completion_tokens)
    route_signature = ",".join(ranked_all[:8])
    cache_key = make_response_cache_key(
        model=selected_model,
        system_prompt=system_prompt,
        user_text=user_text,
        memory_text=memory_text,
        intent=intent,
        route_signature=route_signature,
    )
    if enable_response_cache and intent not in {"admin_command", "coding"}:
        cached = store.get_cached_response(cache_key)
        if cached:
            answer, meta = cached
            meta["power_intent"] = intent
            meta["active_model_final"] = meta.get("active_model_final") or selected_model
            return answer, meta

    started = time.time()
    answer = ""
    meta: Dict[str, Any] = {}
    success = False
    final_model = selected_model
    last_error = ""

    def _call_once(primary: str, cheap_pool: List[str], expensive_pool: List[str]) -> Tuple[str, Dict[str, Any]]:
        return __import__("ai_core").generate_answer(
            api_url=api_url,
            api_key=api_key,
            model=primary,
            system_prompt=system_prompt,
            user_text=routed_user_text,
            memory_text=memory_text,
            recent_messages=recent_messages or [],
            fallback_models=cheap_pool,
            expensive_fallback_models=expensive_pool,
            allow_expensive_fallback=allow_expensive_fallback and bool(expensive_pool),
            max_expensive_models=max_expensive_models,
            temperature=temperature,
            max_completion_tokens=adjusted_max_tokens,
            timeout=timeout,
            smart_model_router=smart_model_router,
            return_to_primary=return_to_primary,
            max_smart_models=max_smart_models,
        )

    try:
        try:
            answer, meta = _call_once(selected_model, ranked_cheap, ranked_expensive)
        except Exception as exc:
            last_error = str(exc)
            if enable_circuit_breaker:
                store.register_model_failure(selected_model, error=last_error, max_failures=circuit_max_failures, cooldown_seconds=circuit_cooldown_seconds)
            # Retry once with the next ranked model, if available.
            alternates = [m for m in ranked_all if m != selected_model]
            if not alternates:
                raise
            retry_model = alternates[0]
            retry_cheap = [m for m in ranked_cheap if m != retry_model]
            retry_expensive = [m for m in ranked_expensive if m != retry_model]
            answer, meta = _call_once(retry_model, retry_cheap, retry_expensive)
            meta = meta or {}
            meta["power_auto_retry_from"] = selected_model
            meta["power_auto_retry_to"] = retry_model
            meta["power_auto_retry_error"] = last_error[:800]
            selected_model = retry_model

        meta = meta or {}
        final_model = str(meta.get("active_model_final") or meta.get("model_requested") or selected_model)
        meta["power_intent"] = intent
        meta["power_rag_enabled"] = bool(enable_rag)
        meta["power_kb_sources"] = [
            {
                "doc_id": item.get("doc_id"),
                "title": item.get("title"),
                "source": item.get("source"),
                "chunk_index": item.get("chunk_index"),
                "score": item.get("score"),
            }
            for item in (rag_sources or [])[:5]
        ]
        meta["power_persistent_memory_enabled"] = bool(enable_persistent_memory)
        meta["power_prompt_template_enabled"] = bool(enable_prompt_templates)
        meta["power_adaptive_scoring_enabled"] = bool(enable_adaptive_scoring)
        meta["power_circuit_breaker_enabled"] = bool(enable_circuit_breaker)
        meta["power_ranked_models"] = ranked_all[:10]
        meta["power_selected_model"] = selected_model
        meta["power_adjusted_max_tokens"] = adjusted_max_tokens
        meta["power_latency_seconds"] = round(time.time() - started, 3)

        if should_self_verify(intent, user_text, enabled=enable_self_verification):
            verifier = ""
            if ranked_expensive:
                verifier = ranked_expensive[0]
            elif ranked_cheap:
                verifier = ranked_cheap[0]
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
                    max_completion_tokens=max(adjusted_max_tokens, 2200),
                    timeout=timeout,
                )
                if verified_answer:
                    answer = verified_answer
                    meta["self_verification"] = verify_meta
                    meta["self_verified_by"] = verifier
            except Exception as exc:
                meta["self_verification_error"] = str(exc)[:500]
        success = True
        if enable_response_cache and intent not in {"admin_command", "coding"}:
            store.set_cached_response(cache_key, answer, meta, ttl_seconds=response_cache_ttl_seconds)
        return answer, meta
    finally:
        try:
            if not success and last_error:
                meta["error"] = last_error[:1000]
            final_model = str((meta or {}).get("active_model_final") or (meta or {}).get("model_requested") or final_model or selected_model or model)
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


# =========================
# Commands / benchmark
# =========================

def handle_power_command(text: str, store: PowerStore, user_id: str = "global", is_admin: bool = False) -> str:
    raw = str(text or "").strip()
    lower = raw.lower()
    if not raw.startswith("/"):
        return ""

    admin_only_prefixes = (
        "/ingat", "/lupa", "/rag", "/kb", "/biaya", "/usage", "/dokumen", "/benchmark",
        "/model skor", "/model score", "/circuit", "/cache bersih", "/cache clear",
    )
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

    if lower in {"/kb", "/rag", "/kb bantuan", "/rag bantuan", "/kb help", "/rag help"}:
        return (
            "Perintah Knowledge Base:\n"
            "/kb statistik - lihat jumlah dokumen/chunk\n"
            "/kb list - lihat dokumen terakhir\n"
            "/kb cari <query> - cari isi knowledge base\n"
            "/kb detail <doc_id> - preview dokumen\n"
            "/kb hapus <doc_id> - hapus dokumen\n"
            "/kb rebuild - bangun ulang index FTS\n"
            "/kb tambah <judul>\n<isi dokumen> - tambah dokumen manual"
        )

    if lower in {"/kb statistik", "/rag statistik", "/kb stats", "/rag stats"}:
        stats = store.knowledge_stats()
        return (
            "📚 Statistik Knowledge Base:\n"
            f"Dokumen: {stats.get('documents', 0)}\n"
            f"Chunks: {stats.get('chunks', 0)}\n"
            f"Karakter: {stats.get('characters', 0)}\n"
            f"Terakhir: {stats.get('latest_title', '-')} {stats.get('latest_at_wib', '')}"
        )

    if lower in {"/kb list", "/rag list", "/kb daftar", "/rag daftar"}:
        docs = store.list_documents(limit=15)
        if not docs:
            return "Knowledge base masih kosong."
        lines = ["📚 Dokumen Knowledge Base terakhir:"]
        for doc in docs:
            lines.append(
                f"- ID {doc.get('id')} | {doc.get('title')} | {doc.get('chunks')} chunk | {doc.get('created_at_wib', '')}"
            )
        return "\n".join(lines)

    if lower.startswith(("/kb detail ", "/rag detail ")):
        doc_id = raw.split(" ", 2)[2].strip()
        doc = store.get_document(int(doc_id), max_chars=2200) if doc_id.isdigit() else {}
        if not doc:
            return "Dokumen tidak ditemukan."
        preview = re.sub(r"\s+", " ", str(doc.get("preview") or "")).strip()[:1800]
        return f"📄 {doc.get('title')}\nID: {doc.get('id')} | Source: {doc.get('source')} | Chunks: {doc.get('chunks')}\n\n{preview}"

    if lower.startswith(("/kb hapus ", "/rag hapus ", "/kb delete ", "/rag delete ")):
        doc_id = raw.split(" ", 2)[2].strip()
        ok = store.delete_document(int(doc_id)) if doc_id.isdigit() else False
        return f"✅ Dokumen ID {doc_id} dihapus." if ok else "Dokumen tidak ditemukan/gagal dihapus."

    if lower in {"/kb rebuild", "/rag rebuild", "/kb index", "/rag index"}:
        docs, chunks = store.rebuild_knowledge_index()
        return f"✅ Index knowledge base dibangun ulang. Dokumen: {docs}, chunks terindex: {chunks}."

    if lower.startswith(("/rag cari ", "/kb cari ")):
        query = raw.split(" ", 2)[2].strip()
        docs = store.search_documents(query, limit=6)
        if not docs:
            return "Belum ada potongan knowledge base yang cocok."
        lines = ["🔎 Hasil Knowledge Base:"]
        for idx, doc in enumerate(docs, start=1):
            snippet = re.sub(r"\s+", " ", doc.get("content", "")).strip()[:420]
            lines.append(f"{idx}. Doc {doc.get('doc_id')} | {doc.get('title')} | chunk {doc.get('chunk_index')} | score {doc.get('score')}\n{snippet}")
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

    if lower in {"/model skor", "/model score"}:
        rows = store.model_score_rows(limit=15)
        if not rows:
            return "Belum ada data skor model. Gunakan AI beberapa kali atau jalankan benchmark dulu."
        lines = ["🏆 Skor model adaptif:"]
        for row in rows[:15]:
            lines.append(
                f"- {row.get('model')} [{row.get('intent')}]: score {row.get('computed_score')} | success {row.get('success_rate')} | latency {row.get('avg_latency')}s | quality {row.get('avg_quality')}"
            )
        return "\n".join(lines)

    if lower in {"/circuit", "/circuit status"}:
        rows = store.circuit_breaker_status(limit=20)
        if not rows:
            return "Circuit breaker masih kosong."
        lines = ["🧯 Circuit breaker:"]
        for row in rows[:20]:
            status = "BLOCKED" if row.get("blocked") else "OK"
            lines.append(f"- {row.get('model')}: {status} | gagal {row.get('failure_count')} | buka sampai {row.get('open_until_wib')}")
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
        ("quick_chat", "Jawab satu kalimat: apa fungsi utama router model AI?"),
        ("deep_reasoning", "Berikan 3 langkah prioritas memperbaiki aplikasi chatbot yang lambat dan sering fallback."),
        ("coding", "Sebutkan bug umum pada Python f-string jika tanda kutip nested salah, lalu berikan contoh perbaikannya."),
        ("academic", "Buat 3 poin latar belakang akademik singkat tentang pentingnya akurasi klaim BPJS."),
    ]
    ranked = store.rank_models_for_intent(list(dict.fromkeys([m for m in models if m])), "general")
    results: List[Dict[str, Any]] = []
    for model in ranked[: max(1, int(max_models or 8))]:
        for task, prompt in tests:
            started = time.time()
            try:
                answer, meta = call_api_once(
                    api_url=api_url,
                    api_key=api_key,
                    model=model,
                    messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
                    temperature=0,
                    max_completion_tokens=900,
                    timeout=timeout,
                )
                latency = round(time.time() - started, 3)
                score = 0.0
                if answer and len(answer.split()) >= 8:
                    score += 0.40
                if any(x in answer.lower() for x in ["router", "model", "fallback", "f-string", "kutip", "langkah", "klaim", "akurasi"]):
                    score += 0.35
                if len(answer) < 1800:
                    score += 0.10
                if latency <= 12:
                    score += 0.15
                score = round(min(score, 1.0), 3)
                store.add_benchmark(model, task, score, latency, True, meta=meta)
                results.append({"model": model, "task": task, "score": score, "latency_seconds": latency, "success": True, "error": ""})
            except Exception as exc:
                latency = round(time.time() - started, 3)
                store.add_benchmark(model, task, 0.0, latency, False, error=str(exc))
                results.append({"model": model, "task": task, "score": 0.0, "latency_seconds": latency, "success": False, "error": str(exc)[:260]})
    return results
