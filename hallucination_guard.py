"""Anti-hallucination guard for Adioranye AI.

This module is intentionally lightweight and dependency-free. It helps the app
reduce hallucinations by enforcing evidence-aware behaviour for critical/current
questions, lowering temperature, adding explicit uncertainty policy, and adding a
small post-answer source/uncertainty note when needed.

It does not guarantee factual correctness. It is a guardrail that makes the model
less likely to invent facts when the Knowledge Base/RAG evidence is weak.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

WIB_TZ = ZoneInfo("Asia/Jakarta") if ZoneInfo else timezone.utc

HIGH_RISK_KEYWORDS = [
    # current/fresh facts
    "terbaru", "terkini", "saat ini", "hari ini", "kemarin", "update", "perkembangan",
    "berita", "isu", "viral", "trending", "sekarang", "tahun ini",
    # verification/critical framing
    "apakah benar", "benarkah", "valid", "bukti", "sumber", "cek fakta", "fact check",
    "hoaks", "hoax", "klarifikasi", "kontroversi", "klaim", "data resmi",
    # high impact domains
    "kesehatan", "medis", "penyakit", "obat", "vaksin", "diagnosis", "terapi", "gejala",
    "hukum", "regulasi", "aturan", "undang-undang", "legal", "pidana", "perdata",
    "keuangan", "investasi", "harga", "saham", "crypto", "pajak", "ekonomi",
    "politik", "pemilu", "presiden", "menteri", "kebijakan",
    "q1", "q2", "q3", "q4", "quartile", "scopus", "sinta", "jurnal", "impact factor",
    "riset terbaru", "paper terbaru", "studi terbaru",
]

EVIDENCE_WORDS = ["menurut", "berdasarkan", "data", "laporan", "studi", "riset", "jurnal", "rilis", "mengumumkan", "menyatakan"]
UNSAFE_CERTAINTY_PHRASES = [
    "pasti", "sudah pasti", "jelas benar", "terbukti pasti", "tidak mungkin salah",
    "100%", "dijamin", "selalu", "tidak ada risiko", "tanpa risiko",
]

@dataclass
class HallucinationGuardResult:
    enabled: bool = True
    is_high_risk: bool = False
    risk_score: float = 0.0
    mode: str = "normal"
    allow_answer: bool = True
    reason: str = ""
    min_sources: int = 1
    min_quality: float = 0.0
    min_freshness: float = 0.0
    usable_sources: int = 0
    best_quality: float = 0.0
    best_freshness: float = 0.0
    matched_keywords: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _source_quality(item: Dict[str, Any]) -> float:
    return max(
        _to_float(item.get("source_quality"), 0.0),
        _to_float((item.get("metadata") or {}).get("source_quality") if isinstance(item.get("metadata"), dict) else 0.0, 0.0),
    )


def _source_freshness(item: Dict[str, Any]) -> float:
    return max(
        _to_float(item.get("freshness_score"), 0.0),
        _to_float((item.get("metadata") or {}).get("freshness_score") if isinstance(item.get("metadata"), dict) else 0.0, 0.0),
    )


def detect_high_risk_question(text: str, intent: str = "") -> Dict[str, Any]:
    lowered = str(text or "").lower()
    matches = [kw for kw in HIGH_RISK_KEYWORDS if kw in lowered]
    score = len(matches) * 10
    if any(x in lowered for x in ["apakah benar", "benarkah", "valid", "bukti", "hoaks", "hoax", "cek fakta"]):
        score += 25
    if any(x in lowered for x in ["terbaru", "terkini", "saat ini", "hari ini", "update", "perkembangan"]):
        score += 22
    if str(intent or "") in {"health", "academic", "research", "critical_current", "document_question", "livestock"}:
        score += 15
    if str(intent or "") in {"coding", "creative", "quick_chat"} and not matches:
        score = max(0, score - 20)
    score = min(100.0, float(score))
    return {
        "is_high_risk": score >= 30,
        "score": score,
        "matched_keywords": matches[:16],
        "mode": "critical_evidence" if score >= 55 else ("evidence_aware" if score >= 30 else "normal"),
    }


def evaluate_evidence_gate(
    user_text: str,
    rag_sources: Optional[List[Dict[str, Any]]] = None,
    *,
    intent: str = "",
    enabled: bool = True,
    auto_strict: bool = True,
    strict_rag_mode: bool = False,
    min_sources: int = 1,
    min_quality: float = 0.0,
    min_freshness: float = 0.0,
) -> HallucinationGuardResult:
    if not enabled:
        return HallucinationGuardResult(enabled=False, allow_answer=True, reason="disabled")

    risk = detect_high_risk_question(user_text, intent=intent)
    sources = list(rag_sources or [])
    usable = []
    for item in sources:
        q = _source_quality(item)
        f = _source_freshness(item)
        # If quality/freshness metadata is absent, do not discard it by default.
        q_ok = True if min_quality <= 0 else q >= min_quality
        f_ok = True if min_freshness <= 0 else (f >= min_freshness or f <= 0)
        if q_ok and f_ok:
            usable.append(item)

    best_quality = max([_source_quality(i) for i in sources] + [0.0])
    best_freshness = max([_source_freshness(i) for i in sources] + [0.0])
    required_sources = max(1, int(min_sources or 1))
    should_require_evidence = bool(strict_rag_mode or (auto_strict and risk.get("is_high_risk")))
    allow_answer = True
    reason = "ok"
    if should_require_evidence and len(usable) < required_sources:
        allow_answer = False
        reason = "insufficient_evidence_for_high_risk_question" if risk.get("is_high_risk") else "strict_rag_insufficient_sources"

    return HallucinationGuardResult(
        enabled=True,
        is_high_risk=bool(risk.get("is_high_risk")),
        risk_score=float(risk.get("score") or 0.0),
        mode=str(risk.get("mode") or "normal"),
        allow_answer=allow_answer,
        reason=reason,
        min_sources=required_sources,
        min_quality=float(min_quality or 0.0),
        min_freshness=float(min_freshness or 0.0),
        usable_sources=len(usable),
        best_quality=round(best_quality, 2),
        best_freshness=round(best_freshness, 2),
        matched_keywords=tuple(risk.get("matched_keywords") or ()),
    )


def build_guard_system_instruction(
    user_text: str,
    rag_sources: Optional[List[Dict[str, Any]]] = None,
    guard: Optional[HallucinationGuardResult] = None,
) -> str:
    guard = guard or evaluate_evidence_gate(user_text, rag_sources)
    if not guard.enabled:
        return ""
    source_count = len(rag_sources or [])
    risk_line = "PERTANYAAN BERISIKO/TERKINI" if guard.is_high_risk else "PERTANYAAN UMUM"
    return f"""

ATURAN ANTI-HALUSINASI ADIORANYE:
- Status: {risk_line}; mode: {guard.mode}; sumber KB tersedia: {source_count}.
- Jangan mengarang nama orang, jabatan, tanggal, angka, harga, regulasi, jurnal, quartile, URL, institusi, atau klaim faktual yang tidak ada pada konteks/sumber.
- Jika bukti di Knowledge Base tidak cukup, jawab secara jujur bahwa data belum cukup kuat dan beri langkah verifikasi aman.
- Pisahkan fakta dari dugaan/opini. Gunakan frasa seperti "berdasarkan sumber yang tersedia" atau "data yang saya punya belum cukup" bila perlu.
- Untuk kesehatan/hukum/keuangan/regulasi/jurnal Q-level/berita terkini, hindari kepastian berlebihan dan sarankan verifikasi ke sumber resmi.
- Jika memakai konteks KB, rujuk sumber dengan label KB/CLAIM yang tersedia; jangan membuat sitasi palsu.
- Jangan menutupi ketidakpastian. Lebih baik mengatakan "belum dapat dipastikan" daripada membuat jawaban seolah-olah pasti.
""".strip()


def build_insufficient_evidence_answer(user_text: str, guard: HallucinationGuardResult, intent: str = "") -> str:
    keywords = ", ".join(guard.matched_keywords[:6]) if guard.matched_keywords else "pertanyaan kritis/terkini"
    return (
        "Data belum cukup kuat di Knowledge Base untuk menjawab pertanyaan ini secara aman.\n\n"
        f"Alasan: sistem mendeteksi topik berisiko/terkini ({keywords}), tetapi sumber relevan yang memenuhi batas minimal belum cukup.\n\n"
        "Yang bisa dilakukan admin:\n"
        "1. Jalankan /update untuk memperbarui Knowledge Base.\n"
        "2. Tambahkan sumber resmi/jurnal/dokumen tepercaya untuk topik tersebut.\n"
        "3. Coba tanyakan ulang setelah update selesai.\n\n"
        "Saya tidak akan mengarang jawaban untuk topik ini tanpa bukti yang cukup."
    )


def apply_temperature_policy(temperature: float, guard: Optional[HallucinationGuardResult] = None) -> float:
    try:
        temp = float(temperature)
    except Exception:
        temp = 0.3
    if guard and guard.enabled and guard.is_high_risk:
        return max(0.0, min(temp, 0.15))
    if guard and guard.enabled and guard.mode == "evidence_aware":
        return max(0.0, min(temp, 0.25))
    return temp


def format_source_note(rag_sources: Optional[List[Dict[str, Any]]], limit: int = 4) -> str:
    sources = list(rag_sources or [])[: max(1, int(limit or 4))]
    if not sources:
        return ""
    lines = []
    for idx, item in enumerate(sources, start=1):
        title = str(item.get("title") or item.get("citation") or "Sumber KB").strip()
        source = str(item.get("source") or "").strip()
        quality = item.get("source_quality")
        freshness = item.get("freshness_score")
        meta = []
        if quality not in (None, ""):
            meta.append(f"kualitas {quality}")
        if freshness not in (None, ""):
            meta.append(f"freshness {freshness}")
        suffix = f" ({'; '.join(meta)})" if meta else ""
        if source:
            lines.append(f"{idx}. {title} — {source}{suffix}")
        else:
            lines.append(f"{idx}. {title}{suffix}")
    return "\n".join(lines)


def append_guard_note(
    answer: str,
    rag_sources: Optional[List[Dict[str, Any]]] = None,
    *,
    guard: Optional[HallucinationGuardResult] = None,
    append_sources: bool = True,
) -> str:
    text = str(answer or "").strip()
    if not text or not guard or not guard.enabled:
        return text
    if not guard.is_high_risk and not append_sources:
        return text
    note_parts: List[str] = []
    if guard.is_high_risk:
        note_parts.append("Catatan: jawaban ini memakai mode kehati-hatian karena pertanyaan terdeteksi sebagai topik kritis/terkini.")
    if append_sources and rag_sources:
        src_note = format_source_note(rag_sources, limit=4)
        if src_note:
            note_parts.append("Sumber KB yang dipakai:\n" + src_note)
    if not note_parts:
        return text
    # Avoid duplicating notes if a verifier/model already appended them.
    lowered = text.lower()
    if "sumber kb yang dipakai" in lowered or "mode kehati-hatian" in lowered:
        return text
    return text + "\n\n---\n" + "\n\n".join(note_parts)


def answer_has_overconfident_language(answer: str) -> bool:
    lowered = str(answer or "").lower()
    return any(phrase in lowered for phrase in UNSAFE_CERTAINTY_PHRASES)


def lightweight_claim_risk(answer: str) -> Dict[str, Any]:
    text = str(answer or "")
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    claim_like = []
    for s in sentences:
        sl = s.strip()
        if len(sl) < 35:
            continue
        low = sl.lower()
        score = 0
        if any(ch.isdigit() for ch in sl):
            score += 1
        if any(w in low for w in EVIDENCE_WORDS):
            score += 1
        if any(w in low for w in ["adalah", "merupakan", "menyebabkan", "berdampak", "meningkat", "menurun"]):
            score += 1
        if score >= 2:
            claim_like.append(sl[:180])
    return {"claim_like_count": len(claim_like), "examples": claim_like[:5], "overconfident": answer_has_overconfident_language(answer)}
