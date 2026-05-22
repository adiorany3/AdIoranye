"""Performance optimizer for Adioranye AI.

Lightweight production helpers for faster and more relevant answers on
Streamlit/GitHub Actions without external vector DB dependencies.

Features:
- Query rewriting for RAG retrieval.
- Lightweight reranking and diversity control for KB chunks.
- Semantic response cache using token similarity.
- Latency budget per answer mode/intent.
- Retrieval evaluation helpers.
- SQLite maintenance recommendations.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

STOPWORDS = {
    "yang", "dan", "atau", "untuk", "dengan", "dari", "pada", "agar", "jadi", "ini", "itu",
    "dalam", "akan", "bisa", "saya", "anda", "kami", "kamu", "apa", "bagaimana", "karena",
    "ke", "di", "sebagai", "adalah", "sebuah", "tentang", "terkait", "secara", "saat", "ini",
    "the", "and", "or", "for", "with", "from", "that", "this", "are", "was", "were", "you", "your",
}

CASUAL_TERMS = {
    "halo", "hai", "hi", "hello", "pagi", "siang", "sore", "malam", "apa kabar", "makasih",
    "terima kasih", "thanks", "oke", "ok", "siap", "mantap", "tes", "test", "lanjut",
}

DOMAIN_EXPANSIONS: Dict[str, List[str]] = {
    "ai": ["artificial intelligence", "machine learning", "model AI", "generative AI", "LLM"],
    "teknologi": ["technology", "digital transformation", "innovation", "startup", "cybersecurity"],
    "indonesia": ["Indonesia", "pemerintah Indonesia", "riset Indonesia", "teknologi Indonesia"],
    "riset": ["research", "paper", "publikasi", "jurnal", "OpenAlex", "arXiv"],
    "jurnal": ["journal", "quartile", "Q1 Q2 Q3 Q4", "Scopus", "SINTA"],
    "kesehatan": ["health", "WHO", "public health", "medical", "epidemiology"],
    "hukum": ["law", "regulation", "legal", "kebijakan", "regulasi"],
    "peternakan": ["livestock", "animal science", "veterinary", "feed", "One Health"],
    "agro": ["agriculture", "agro", "food security", "crop", "agribusiness"],
    "akuakultur": ["aquaculture", "fisheries", "blue economy", "aquafeed"],
    "lingkungan": ["environment", "climate", "biodiversity", "pollution", "UNEP"],
    "sosial": ["social impact", "society", "inequality", "misinformation", "digital society"],
    "budaya": ["culture", "heritage", "UNESCO", "creative economy"],
}

INTENT_EXPANSIONS: Dict[str, List[str]] = {
    "health": ["WHO", "PubMed", "evidence", "latest update"],
    "livestock": ["livestock", "animal science", "veterinary", "feed", "disease"],
    "academic": ["research", "journal", "paper", "methodology", "evidence"],
    "research": ["research", "publication", "dataset", "trend", "latest"],
    "coding": ["documentation", "error", "fix", "implementation"],
    "deep_reasoning": ["analysis", "evidence", "impact", "risk"],
}

RISK_TERMS = {
    "terbaru", "saat ini", "valid", "benar", "hoaks", "risiko", "dampak", "aman", "bukti",
    "q1", "q2", "q3", "q4", "hukum", "kesehatan", "regulasi", "harga", "politik",
}

@dataclass
class QueryPlan:
    original_query: str
    rewritten_query: str
    extra_terms: List[str]
    is_casual: bool
    should_use_rag: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_query": self.original_query,
            "rewritten_query": self.rewritten_query,
            "extra_terms": self.extra_terms,
            "is_casual": self.is_casual,
            "should_use_rag": self.should_use_rag,
            "reason": self.reason,
        }


def _now() -> float:
    return time.time()


def normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def tokenize(text: Any, min_len: int = 3) -> List[str]:
    raw = normalize_text(text)
    terms = re.findall(r"[a-zA-Z0-9_\-]{%d,}" % int(min_len), raw)
    return [t for t in terms if t not in STOPWORDS]


def token_set(text: Any) -> set:
    return set(tokenize(text))


def semantic_similarity(a: Any, b: Any) -> float:
    """Fast Jaccard/cosine hybrid similarity for short Indonesian/English text."""
    ta = tokenize(a)
    tb = tokenize(b)
    if not ta or not tb:
        return 0.0
    sa, sb = set(ta), set(tb)
    jaccard = len(sa & sb) / max(1, len(sa | sb))
    # Cosine over term counts without importing numpy.
    counts_a: Dict[str, int] = {}
    counts_b: Dict[str, int] = {}
    for t in ta:
        counts_a[t] = counts_a.get(t, 0) + 1
    for t in tb:
        counts_b[t] = counts_b.get(t, 0) + 1
    dot = sum(counts_a.get(t, 0) * counts_b.get(t, 0) for t in set(counts_a) | set(counts_b))
    norm_a = math.sqrt(sum(v * v for v in counts_a.values())) or 1.0
    norm_b = math.sqrt(sum(v * v for v in counts_b.values())) or 1.0
    cosine = dot / (norm_a * norm_b)
    return round((jaccard * 0.55) + (cosine * 0.45), 4)


def is_casual_query(text: Any) -> bool:
    q = normalize_text(text)
    if not q:
        return True
    if len(q.split()) <= 4 and any(q == term or q.startswith(term + " ") for term in CASUAL_TERMS):
        return True
    if len(q) <= 18 and q in CASUAL_TERMS:
        return True
    return False


def rewrite_query(user_text: str, intent: str = "", answer_mode: str = "auto", max_terms: int = 16) -> QueryPlan:
    q = str(user_text or "").strip()
    if not q:
        return QueryPlan(q, q, [], True, False, "empty_query")
    if is_casual_query(q) and str(answer_mode or "auto") not in {"riset", "kritis"}:
        return QueryPlan(q, q, [], True, False, "casual_fast_path")

    lower = normalize_text(q)
    extra: List[str] = []
    for key, terms in DOMAIN_EXPANSIONS.items():
        if key in lower or any(term.lower() in lower for term in terms[:2]):
            extra.extend(terms)
    extra.extend(INTENT_EXPANSIONS.get(str(intent or "").lower(), []))

    if any(term in lower for term in RISK_TERMS) or str(answer_mode or "").lower() in {"riset", "kritis"}:
        extra.extend(["latest", "terbaru", "evidence", "official source", "2026"])

    # Keep unique terms while avoiding repeating words already present in q.
    seen = set(tokenize(q))
    clean_extra: List[str] = []
    for term in extra:
        key = normalize_text(term)
        term_tokens = token_set(key)
        if not key or key in seen:
            continue
        if term_tokens and term_tokens.issubset(seen):
            continue
        if key not in clean_extra:
            clean_extra.append(term[:80])
        if len(clean_extra) >= max_terms:
            break
    rewritten = q
    if clean_extra:
        rewritten = q + " " + " ".join(clean_extra)
    return QueryPlan(q, rewritten[:900], clean_extra, False, True, "expanded_query" if clean_extra else "original_query")


def _source_domain(source: Any) -> str:
    raw = str(source or "").lower()
    raw = re.sub(r"^https?://", "", raw)
    return raw.split("/", 1)[0].replace("www.", "")[:120]


def source_reliability_bonus(item: Dict[str, Any]) -> float:
    source = str(item.get("source") or item.get("source_domain") or item.get("title") or "").lower()
    tags = str(item.get("tags") or item.get("collection") or "").lower()
    joined = source + " " + tags
    if any(x in joined for x in ["who.int", "fao.org", "unep.org", "unesco.org", "worldbank.org", "adb.org", "brin.go.id", "kemdiktisaintek", "kemkes.go.id", "go.id"]):
        return 0.18
    if any(x in joined for x in ["pubmed", "ncbi", "openalex", "arxiv", "doi", "scopus", "sinta"]):
        return 0.16
    if any(x in joined for x in ["github.com", "docs.", "developer.", "official"]):
        return 0.12
    if any(x in joined for x in ["reuters", "apnews", "bbc", "cna", "antara", "kompas", "tempo"]):
        return 0.08
    return 0.0


def score_source(query: str, item: Dict[str, Any], rank_index: int = 0) -> float:
    hay = " ".join(str(item.get(k) or "") for k in ["title", "heading", "tags", "collection", "content", "source"])
    sim = semantic_similarity(query, hay)
    score = sim * 1.8
    try:
        score += min(1.0, max(0.0, float(item.get("score") or 0))) * 0.35
    except Exception:
        pass
    try:
        score += (float(item.get("source_quality") or 55) / 100.0) * 0.24
    except Exception:
        pass
    try:
        score += (float(item.get("freshness_score") or 45) / 100.0) * 0.16
    except Exception:
        pass
    try:
        score += (float(item.get("criticality_score") or 0) / 100.0) * 0.08
    except Exception:
        pass
    score += source_reliability_bonus(item)
    score -= min(0.08, rank_index * 0.006)
    return round(score, 5)


def rerank_sources(query: str, sources: Optional[List[Dict[str, Any]]], limit: int = 5, diversity: bool = True) -> List[Dict[str, Any]]:
    items = [dict(x) for x in (sources or []) if isinstance(x, dict)]
    if not items:
        return []
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for idx, item in enumerate(items):
        s = score_source(query, item, rank_index=idx)
        item["rerank_score"] = s
        item.setdefault("original_rank", idx + 1)
        scored.append((s, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    selected: List[Dict[str, Any]] = []
    seen_docs = set()
    seen_domains = set()
    for s, item in scored:
        doc_id = item.get("doc_id") or item.get("title")
        domain = _source_domain(item.get("source") or item.get("source_domain") or "")
        if diversity:
            if doc_id in seen_docs and len(selected) >= max(2, limit // 2):
                continue
            if domain and domain in seen_domains and len(selected) >= max(2, limit // 2):
                continue
        selected.append(item)
        seen_docs.add(doc_id)
        if domain:
            seen_domains.add(domain)
        if len(selected) >= max(1, int(limit or 5)):
            break
    if len(selected) < max(1, int(limit or 5)):
        for s, item in scored:
            if item not in selected:
                selected.append(item)
            if len(selected) >= max(1, int(limit or 5)):
                break
    return selected[: max(1, int(limit or 5))]


def latency_budget_seconds(answer_mode: str = "auto", intent: str = "", user_text: str = "") -> int:
    mode = str(answer_mode or "auto").lower().strip()
    intent = str(intent or "").lower().strip()
    if mode == "hemat" or intent == "quick_chat" or is_casual_query(user_text):
        return 8
    if mode == "pintar":
        return 18
    if mode == "riset":
        return 32
    if mode == "kritis":
        return 45
    if intent in {"coding", "academic", "research", "document_question"}:
        return 25
    if intent in {"health", "livestock", "deep_reasoning"}:
        return 35
    return 18


def retrieval_precision_estimate(query: str, sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not sources:
        return {"precision": 0.0, "avg_similarity": 0.0, "relevant_count": 0, "source_count": 0}
    sims = []
    relevant = 0
    for item in sources:
        hay = " ".join(str(item.get(k) or "") for k in ["title", "heading", "tags", "content"])
        sim = semantic_similarity(query, hay)
        sims.append(sim)
        if sim >= 0.08 or float(item.get("rerank_score") or item.get("score") or 0) >= 0.28:
            relevant += 1
    return {
        "precision": round(relevant / max(1, len(sources)), 3),
        "avg_similarity": round(sum(sims) / max(1, len(sims)), 3),
        "relevant_count": relevant,
        "source_count": len(sources),
    }


def source_pruning_decision(stats: Dict[str, Any]) -> Dict[str, Any]:
    total = int(stats.get("total") or stats.get("requests") or 0)
    failures = int(stats.get("failures") or 0)
    avg_latency = float(stats.get("avg_latency") or 0)
    avg_quality = float(stats.get("avg_quality") or 0)
    if total >= 5 and failures / max(1, total) >= 0.65:
        return {"action": "disable_temporarily", "reason": "failure_rate_high"}
    if total >= 5 and avg_latency >= 12:
        return {"action": "lower_priority", "reason": "source_slow"}
    if total >= 5 and avg_quality and avg_quality < 35:
        return {"action": "lower_priority", "reason": "low_quality"}
    return {"action": "keep", "reason": "ok"}


def compact_meta(meta: Optional[Dict[str, Any]], limit: int = 8000) -> str:
    try:
        return json.dumps(meta or {}, ensure_ascii=False, default=str)[:limit]
    except Exception:
        return "{}"


def build_performance_report(metrics: Dict[str, Any]) -> str:
    lines = ["# Laporan Performance Optimizer", ""]
    lines.append(f"Dibuat: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    for key, value in metrics.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)
