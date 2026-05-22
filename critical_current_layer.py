"""Critical Current Knowledge Layer for Adioranye AI.

Lightweight utilities for making the knowledge base safer for critical/current
questions: source quality scoring, freshness scoring, claim extraction, issue
watchlist helpers, contradiction hints, and daily intelligence brief formatting.

This module intentionally uses only the Python standard library so it can run
inside Streamlit Community Cloud and GitHub Actions without heavy dependencies.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

WIB_TZ = ZoneInfo("Asia/Jakarta") if ZoneInfo else timezone.utc

CRITICAL_KEYWORDS = [
    "apakah benar", "benarkah", "bukti", "valid", "hoaks", "hoax", "klarifikasi",
    "terbaru", "terkini", "saat ini", "hari ini", "update", "perkembangan", "isu",
    "risiko", "dampak", "penyebab", "aman", "bahaya", "kontroversi", "kritik",
    "jurnal q", "q1", "q2", "q3", "q4", "scopus", "sinta", "quartile", "ranking",
    "wabah", "penyakit", "obat", "vaksin", "regulasi", "aturan", "kebijakan",
    "harga", "ekonomi", "politik", "model ai", "teknologi terbaru",
]

DOMAIN_QUALITY_RULES: List[Tuple[float, List[str], str]] = [
    (98.0, ["who.int", "fao.org", "woah.org", "nih.gov", "ncbi.nlm.nih.gov", "cdc.gov", "ecdc.europa.eu"], "lembaga kesehatan/ilmiah resmi"),
    (96.0, ["go.id", "gov", "badanpangan.go.id", "pertanian.go.id", "kemkes.go.id", "bpom.go.id", "bnpb.go.id"], "pemerintah/lembaga resmi"),
    (94.0, ["nature.com", "science.org", "thelancet.com", "nejm.org", "bmj.com", "pubmed.ncbi.nlm.nih.gov"], "jurnal/indeks ilmiah kuat"),
    (92.0, ["scimagojr.com", "scopus.com", "sinta.kemdikbud.go.id", "sinta.kemdiktisaintek.go.id", "doaj.org", "crossref.org"], "metadata jurnal/indeks"),
    (90.0, ["arxiv.org", "mit.edu", "stanford.edu", "harvard.edu", "ox.ac.uk", "cam.ac.uk"], "akademik/riset"),
    (88.0, ["openai.com", "anthropic.com", "deepmind.google", "googleblog.com", "microsoft.com", "github.blog"], "dokumentasi/produk resmi"),
    (82.0, ["reuters.com", "apnews.com", "bbc.com", "dw.com", "antaranews.com", "kompas.com", "tempo.co", "cnnindonesia.com"], "media arus utama"),
    (76.0, ["detik.com", "techcrunch.com", "theverge.com", "wired.com", "arstechnica.com"], "media/teknologi"),
    (70.0, ["trends.google", "news.google", "gdeltproject.org"], "sinyal tren/arus informasi"),
    (42.0, ["blogspot", "wordpress", "facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com"], "opini/UGC"),
]

WATCHLIST_DEFAULTS = [
    {"topic": "AI model terbaru", "category": "teknologi", "priority": 2},
    {"topic": "kesehatan masyarakat Indonesia", "category": "kesehatan", "priority": 1},
    {"topic": "PMK ternak dan kesehatan hewan", "category": "peternakan", "priority": 1},
    {"topic": "flu burung unggas", "category": "peternakan", "priority": 1},
    {"topic": "jurnal peternakan Q1 Q2 Scopus SINTA", "category": "akademik", "priority": 2},
    {"topic": "teknologi pendidikan dan e-learning", "category": "pendidikan", "priority": 3},
    {"topic": "regulasi digital Indonesia", "category": "isu-terkini", "priority": 2},
    {"topic": "harga pangan Indonesia", "category": "ekonomi", "priority": 2},
]


def now_wib_text() -> str:
    return datetime.now(WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")


def clean_text(text: str) -> str:
    text = str(text or "").replace("\x00", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_datetime_any(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    # ISO first
    try:
        normalized = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def datetime_to_ts(value: Any) -> float:
    dt = parse_datetime_any(value)
    if not dt:
        return 0.0
    return float(dt.timestamp())


def domain_from_url(url: str) -> str:
    try:
        return urlparse(str(url or "")).netloc.lower().replace("www.", "")[:180]
    except Exception:
        return ""


def calculate_source_quality(url: str = "", title: str = "", tags: str = "", source_name: str = "", configured_quality: Optional[float] = None) -> Dict[str, Any]:
    """Return quality score 0-100 with a short reason."""
    try:
        if configured_quality is not None:
            score = max(0.0, min(100.0, float(configured_quality)))
            return {"score": round(score, 2), "reason": "configured_source_quality", "domain": domain_from_url(url)}
    except Exception:
        pass

    hay = f"{url} {title} {tags} {source_name}".lower()
    domain = domain_from_url(url)
    best_score = 55.0
    best_reason = "sumber umum"
    for score, patterns, reason in DOMAIN_QUALITY_RULES:
        if any(pattern.lower() in hay for pattern in patterns):
            if score > best_score:
                best_score = score
                best_reason = reason
    if any(x in hay for x in ["jurnal", "journal", "scopus", "sinta", "quartile", "q1", "q2"]):
        best_score = max(best_score, 86.0)
        best_reason = "indikasi akademik/jurnal"
    if any(x in hay for x in ["official", "resmi", "press release", "rilis"]):
        best_score = max(best_score, 80.0)
    return {"score": round(best_score, 2), "reason": best_reason, "domain": domain}


def calculate_freshness_score(published_at: Any = "", scraped_at: Any = "") -> Dict[str, Any]:
    """Return freshness score 0-100 and age bucket.

    Critical/current questions should prefer high-freshness sources. Stable academic
    facts can still be useful, but this score helps the answer mention recency.
    """
    dt = parse_datetime_any(published_at) or parse_datetime_any(scraped_at)
    if not dt:
        return {"score": 45.0, "bucket": "tanggal tidak tersedia", "age_days": None, "published_ts": 0.0}
    now = datetime.now(timezone.utc)
    age_days = max(0.0, (now - dt.astimezone(timezone.utc)).total_seconds() / 86400.0)
    if age_days <= 1:
        score, bucket = 100.0, "0-24 jam"
    elif age_days <= 7:
        score, bucket = 92.0, "1-7 hari"
    elif age_days <= 30:
        score, bucket = 78.0, "8-30 hari"
    elif age_days <= 180:
        score, bucket = 58.0, "1-6 bulan"
    elif age_days <= 365:
        score, bucket = 42.0, "6-12 bulan"
    else:
        # Still non-zero for evergreen docs.
        score, bucket = 25.0, ">12 bulan / arsip"
    return {"score": round(score, 2), "bucket": bucket, "age_days": round(age_days, 2), "published_ts": float(dt.timestamp())}


def detect_critical_question(text: str) -> Dict[str, Any]:
    lowered = str(text or "").lower()
    matches = [kw for kw in CRITICAL_KEYWORDS if kw in lowered]
    question_marks = lowered.count("?")
    score = len(matches) * 16 + min(20, question_marks * 4)
    if any(x in lowered for x in ["terbaru", "terkini", "saat ini", "hari ini", "update"]):
        score += 18
    if any(x in lowered for x in ["apakah benar", "bukti", "valid", "hoaks", "hoax", "aman"]):
        score += 18
    score = min(100, score)
    return {
        "is_critical": score >= 30,
        "score": score,
        "matched_keywords": matches[:12],
        "mode": "critical_current" if score >= 30 else "normal",
    }


def split_sentences(text: str) -> List[str]:
    clean = clean_text(text)
    parts = re.split(r"(?<=[.!?])\s+|\n+", clean)
    return [p.strip() for p in parts if 35 <= len(p.strip()) <= 420]


def extract_keywords(text: str, limit: int = 12) -> List[str]:
    raw = re.findall(r"[a-zA-ZÀ-ÿ0-9_\-]{4,}", str(text or "").lower())
    stop = {
        "yang", "dan", "atau", "untuk", "dengan", "dari", "pada", "dalam", "adalah", "sebagai",
        "karena", "akan", "lebih", "telah", "oleh", "para", "this", "that", "with", "from",
        "were", "have", "about", "their", "there", "which", "would", "could", "said", "says",
    }
    counts: Dict[str, int] = {}
    for word in raw:
        if word in stop:
            continue
        counts[word] = counts.get(word, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:max(1, int(limit or 12))]]


def extract_claims(
    text: str,
    title: str = "",
    source_name: str = "",
    url: str = "",
    published_at: str = "",
    max_claims: int = 8,
) -> List[Dict[str, Any]]:
    """Extract concise fact-like statements from an article.

    This is heuristic, not a legal/factual verifier. It helps the KB store atomic
    claims that can later be searched and cited.
    """
    sentences = split_sentences(text)
    if not sentences:
        return []
    keywords = set(extract_keywords(" ".join([title, text]), limit=24))
    cue_words = [
        "mengumumkan", "menyatakan", "melaporkan", "menemukan", "menunjukkan", "meningkat", "menurun",
        "dirilis", "diperbarui", "berdasarkan", "data", "studi", "penelitian", "riset", "wabah",
        "kasus", "risiko", "regulasi", "kebijakan", "model", "jurnal", "quartile", "sinta", "scopus",
        "announced", "reported", "found", "shows", "study", "research", "released", "updated", "according",
    ]
    scored: List[Tuple[float, int, str]] = []
    for idx, sentence in enumerate(sentences[:120]):
        lowered = sentence.lower()
        words = set(re.findall(r"[a-zA-ZÀ-ÿ0-9_\-]{4,}", lowered))
        score = len(words & keywords)
        score += sum(2 for cue in cue_words if cue in lowered)
        if any(ch.isdigit() for ch in sentence):
            score += 1.5
        if idx < 8:
            score += 1.0
        if score >= 2.0:
            scored.append((score, idx, sentence))
    picked = sorted(sorted(scored, reverse=True)[:max(1, int(max_claims or 8))], key=lambda x: x[1])
    out: List[Dict[str, Any]] = []
    for rank, (_, idx, sentence) in enumerate(picked, start=1):
        out.append({
            "claim": clean_text(sentence)[:520],
            "title": clean_text(title)[:220],
            "source_name": clean_text(source_name)[:180],
            "url": url,
            "published_at": published_at,
            "rank": rank,
            "keywords": extract_keywords(sentence, limit=8),
        })
    return out


def detect_contradictions(claims: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flag potential contradictions using lightweight phrase heuristics."""
    items = list(claims or [])
    flags: List[Dict[str, Any]] = []
    neg_pairs = [
        ("aman", "berbahaya"), ("meningkat", "menurun"), ("naik", "turun"),
        ("disetujui", "ditolak"), ("benar", "tidak benar"), ("valid", "tidak valid"),
        ("wabah", "tidak ada wabah"), ("confirmed", "denied"), ("increase", "decrease"),
    ]
    for i, left in enumerate(items):
        ltext = str(left.get("claim") or "").lower()
        for right in items[i + 1:]:
            rtext = str(right.get("claim") or "").lower()
            shared = set(extract_keywords(ltext, 8)) & set(extract_keywords(rtext, 8))
            if len(shared) < 2:
                continue
            for a, b in neg_pairs:
                if (a in ltext and b in rtext) or (b in ltext and a in rtext):
                    flags.append({
                        "type": "potential_contradiction",
                        "terms": [a, b],
                        "shared_keywords": list(shared)[:6],
                        "claim_a": left,
                        "claim_b": right,
                    })
                    break
    return flags[:8]


def build_issue_timeline(items: Iterable[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    timeline: List[Tuple[float, Dict[str, Any]]] = []
    for item in items or []:
        published = item.get("published_at") or item.get("published") or item.get("created_at") or item.get("ts")
        ts = 0.0
        if isinstance(published, (int, float)):
            ts = float(published)
        else:
            ts = datetime_to_ts(published)
        clean_item = dict(item)
        clean_item["published_ts"] = ts
        if ts:
            try:
                clean_item["tanggal_wib"] = datetime.fromtimestamp(ts, timezone.utc).astimezone(WIB_TZ).strftime("%Y-%m-%d %H:%M WIB")
            except Exception:
                clean_item["tanggal_wib"] = ""
        timeline.append((ts, clean_item))
    timeline.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in timeline[:max(1, int(limit or 12))]]


def build_critical_answer_instruction(user_text: str, detection: Optional[Dict[str, Any]] = None) -> str:
    detection = detection or detect_critical_question(user_text)
    if not detection.get("is_critical"):
        return ""
    return (
        "Mode jawaban kritis-terkini aktif. Jawab dengan struktur: "
        "(1) status data dan tanggal cek, (2) jawaban ringkas, (3) bukti/sumber terkuat dari KB, "
        "(4) hal yang masih belum pasti atau perlu verifikasi, (5) kesimpulan aman. "
        "Bedakan fakta, klaim sumber, opini, dan asumsi. Jika data KB tidak cukup, katakan secara eksplisit."
    )


def format_claim_line(item: Dict[str, Any], idx: int = 1) -> str:
    claim = clean_text(item.get("claim") or item.get("content") or "")[:420]
    title = clean_text(item.get("title") or "")[:120]
    source = clean_text(item.get("source") or item.get("source_name") or item.get("url") or "")[:120]
    freshness = item.get("freshness_score") or item.get("freshness") or ""
    quality = item.get("source_quality") or item.get("quality") or ""
    suffix = []
    if quality != "":
        suffix.append(f"kualitas {quality}")
    if freshness != "":
        suffix.append(f"freshness {freshness}")
    meta = f" ({'; '.join(suffix)})" if suffix else ""
    return f"{idx}. {claim}\n   Sumber: {title or source}{meta}"


def format_issue_report(query: str, claims: List[Dict[str, Any]], docs: Optional[List[Dict[str, Any]]] = None) -> str:
    docs = docs or []
    if not claims and not docs:
        return f"Belum ada data KB yang cukup untuk isu: {query}"
    lines = [
        "🧭 Cek Isu Terkini",
        f"Topik: {query}",
        f"Tanggal cek: {now_wib_text()}",
        "",
    ]
    if claims:
        lines.append("Klaim/fakta relevan dari KB:")
        for i, item in enumerate(claims[:8], start=1):
            lines.append(format_claim_line(item, i))
    if docs:
        lines.extend(["", "Dokumen pendukung:"])
        for i, doc in enumerate(docs[:5], start=1):
            lines.append(f"{i}. {doc.get('title')} | skor {doc.get('score')} | sumber {doc.get('source')}")
    contradictions = detect_contradictions(claims)
    if contradictions:
        lines.extend(["", "⚠️ Potensi perbedaan klaim:"])
        for item in contradictions[:3]:
            lines.append(f"- Istilah berlawanan: {', '.join(item.get('terms') or [])}; keyword bersama: {', '.join(item.get('shared_keywords') or [])}")
    lines.extend(["", "Catatan: ringkasan ini berbasis isi Knowledge Base lokal. Untuk keputusan berisiko tinggi, verifikasi lagi ke sumber resmi terbaru."])
    return "\n".join(lines)


def build_daily_intelligence_brief(report: Dict[str, Any], items: Optional[List[Dict[str, Any]]] = None) -> str:
    items = items or report.get("items") or []
    added = [item for item in items if str(item.get("status")) in {"added", "dry_run"}]
    top = sorted(added, key=lambda x: (float(x.get("source_quality") or 0), float(x.get("freshness_score") or 0), int(x.get("chunks") or 0)), reverse=True)[:12]
    lines = [
        "📌 Daily Intelligence Briefing Adioranye",
        f"Waktu: {report.get('finished_at_wib') or now_wib_text()}",
        f"Dokumen baru: {report.get('added_documents', 0)} | Chunk baru: {report.get('added_chunks', 0)} | Error: {report.get('errors', 0)}",
        "",
    ]
    if top:
        lines.append("Top update penting:")
        for i, item in enumerate(top, start=1):
            title = clean_text(item.get("title") or "")[:160]
            source = clean_text(item.get("source") or "")[:80]
            q = item.get("source_quality", "-")
            f = item.get("freshness_score", "-")
            lines.append(f"{i}. {title}\n   Sumber: {source} | kualitas: {q} | freshness: {f}")
    else:
        lines.append("Belum ada dokumen baru pada run ini.")
    lines.extend(["", "Rekomendasi admin: cek /briefing dan /cek isu <topik> jika ada isu yang perlu dijawab secara kritis."])
    return "\n".join(lines)


def load_watchlist(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("watchlist", [])
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except Exception:
        pass
    return WATCHLIST_DEFAULTS.copy()


def save_default_watchlist(path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"watchlist": WATCHLIST_DEFAULTS}, f, ensure_ascii=False, indent=2)
