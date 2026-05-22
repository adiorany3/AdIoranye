"""Quality control and verifier helpers for Adioranye AI.

This module is intentionally lightweight: SQLite stays in power_features.py,
while this file only handles answer modes, heuristic scoring, evidence notes,
and optional verifier-model repair.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    from ai_core import call_api_once, model_cost_tier
except Exception:  # pragma: no cover
    call_api_once = None  # type: ignore
    def model_cost_tier(model: str) -> str:  # type: ignore
        return "unknown"


VALID_ANSWER_MODES = {"auto", "hemat", "pintar", "riset", "kritis"}

MODE_DESCRIPTIONS = {
    "auto": "Otomatis menyesuaikan risiko pertanyaan.",
    "hemat": "Jawaban singkat, model murah/cepat, verifikasi minimal.",
    "pintar": "Jawaban lebih lengkap, boleh memakai model lebih kuat.",
    "riset": "Wajib mengutamakan sumber/KB, cocok untuk akademik dan data terbaru.",
    "kritis": "Anti-halusinasi ketat, wajib bukti cukup dan catatan ketidakpastian.",
}

CRITICAL_TERMS = [
    "apakah benar", "benarkah", "valid", "hoaks", "hoax", "fakta", "bukti", "sumber",
    "terbaru", "saat ini", "hari ini", "risiko", "dampak", "aman", "bahaya", "hukum",
    "regulasi", "kesehatan", "obat", "dosis", "jurnal", "q1", "q2", "q3", "q4",
    "sinta", "scopus", "ranking", "biaya", "harga", "politik", "konflik", "krisis",
]

RESEARCH_TERMS = [
    "riset", "penelitian", "paper", "jurnal", "scopus", "sinta", "pubmed", "arxiv",
    "metode", "analisis", "literatur", "referensi", "sitasi", "studi", "review",
]


@dataclass
class QualityResult:
    score: float
    level: str
    needs_verification: bool
    reasons: List[str]
    metrics: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": round(float(self.score), 3),
            "level": self.level,
            "needs_verification": bool(self.needs_verification),
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
        }


def normalize_answer_mode(value: Any = "auto") -> str:
    mode = str(value or "auto").strip().lower()
    aliases = {
        "smart": "pintar", "cerdas": "pintar", "deep": "pintar", "mahal": "pintar",
        "cheap": "hemat", "murah": "hemat", "cepat": "hemat", "fast": "hemat",
        "research": "riset", "sumber": "riset", "source": "riset", "akademik": "riset",
        "critical": "kritis", "strict": "kritis", "aman": "kritis", "anti-halusinasi": "kritis",
    }
    mode = aliases.get(mode, mode)
    return mode if mode in VALID_ANSWER_MODES else "auto"


def parse_mode_command(text: str) -> Optional[str]:
    raw = str(text or "").strip().lower()
    if not raw.startswith("/mode"):
        return None
    parts = raw.split()
    if len(parts) == 1:
        return "list"
    return normalize_answer_mode(parts[1])


def infer_answer_mode(user_text: str, requested_mode: str = "auto", intent: str = "") -> str:
    requested = normalize_answer_mode(requested_mode)
    if requested != "auto":
        return requested
    lower = str(user_text or "").lower()
    if any(term in lower for term in CRITICAL_TERMS):
        return "kritis"
    if any(term in lower for term in RESEARCH_TERMS) or str(intent or "") in {"research", "academic", "health", "livestock"}:
        return "riset"
    if len(str(user_text or "")) > 700 or str(intent or "") in {"coding", "document_question"}:
        return "pintar"
    return "hemat"


def mode_policy(mode: str) -> Dict[str, Any]:
    mode = normalize_answer_mode(mode)
    if mode == "hemat":
        return {
            "temperature_cap": 0.30,
            "token_multiplier": 0.65,
            "force_rag": False,
            "strict_rag": False,
            "verifier": False,
            "min_sources": 0,
            "format": "ringkas",
        }
    if mode == "pintar":
        return {
            "temperature_cap": 0.35,
            "token_multiplier": 1.10,
            "force_rag": False,
            "strict_rag": False,
            "verifier": True,
            "min_sources": 0,
            "format": "lengkap",
        }
    if mode == "riset":
        return {
            "temperature_cap": 0.22,
            "token_multiplier": 1.25,
            "force_rag": True,
            "strict_rag": False,
            "verifier": True,
            "min_sources": 1,
            "format": "berbasis_sumber",
        }
    if mode == "kritis":
        return {
            "temperature_cap": 0.15,
            "token_multiplier": 1.30,
            "force_rag": True,
            "strict_rag": True,
            "verifier": True,
            "min_sources": 1,
            "format": "kritis",
        }
    return {
        "temperature_cap": 0.30,
        "token_multiplier": 1.0,
        "force_rag": False,
        "strict_rag": False,
        "verifier": False,
        "min_sources": 0,
        "format": "auto",
    }


def build_mode_system_instruction(mode: str, user_text: str = "") -> str:
    mode = normalize_answer_mode(mode)
    if mode == "hemat":
        return (
            "MODE JAWABAN: HEMAT. Jawab langsung, singkat, dan praktis. "
            "Jangan membuat klaim faktual spesifik yang tidak diperlukan."
        )
    if mode == "pintar":
        return (
            "MODE JAWABAN: PINTAR. Beri jawaban jelas, terstruktur, dan bernilai praktis. "
            "Untuk klaim faktual, jelaskan batas kepastian dan jangan mengarang sumber."
        )
    if mode == "riset":
        return (
            "MODE JAWABAN: RISET. Prioritaskan konteks Knowledge Base/RAG dan sumber yang tersedia. "
            "Pisahkan temuan, bukti, keterbatasan, dan rekomendasi. Jangan menyebut jurnal, angka, tanggal, "
            "regulasi, atau sumber jika tidak ada bukti di konteks."
        )
    if mode == "kritis":
        return (
            "MODE JAWABAN: KRITIS. Jawaban harus konservatif dan berbasis bukti. "
            "Jika bukti tidak cukup, katakan data belum cukup. Tampilkan status kepastian, bukti, kontraindikasi/risiko "
            "jika relevan, dan hal yang belum pasti. Jangan membuat kesimpulan tunggal jika sumber bertentangan."
        )
    return ""


def _safe_len(value: Any) -> int:
    try:
        return len(str(value or ""))
    except Exception:
        return 0


def score_answer_quality(
    *,
    question: str,
    answer: str,
    rag_sources: Optional[List[Dict[str, Any]]] = None,
    mode: str = "auto",
    intent: str = "",
    guard_meta: Optional[Dict[str, Any]] = None,
) -> QualityResult:
    sources = rag_sources or []
    answer_text = str(answer or "").strip()
    question_text = str(question or "").strip()
    mode = normalize_answer_mode(mode)
    lower_answer = answer_text.lower()
    lower_question = question_text.lower()

    score = 0.62
    reasons: List[str] = []
    metrics: Dict[str, Any] = {
        "source_count": len(sources),
        "answer_chars": len(answer_text),
        "mode": mode,
        "intent": intent,
    }

    if not answer_text:
        return QualityResult(0.0, "kosong", True, ["jawaban_kosong"], metrics)

    if len(answer_text) < 80 and len(question_text) > 120:
        score -= 0.10
        reasons.append("jawaban_terlalu_pendek_untuk_pertanyaan_panjang")
    elif len(answer_text) > 250:
        score += 0.06

    high_risk = any(term in lower_question for term in CRITICAL_TERMS) or mode in {"riset", "kritis"}
    metrics["high_risk"] = high_risk

    if sources:
        score += min(0.20, 0.055 * len(sources))
        qualities = []
        freshness = []
        for item in sources:
            try:
                qualities.append(float(item.get("source_quality") or 0))
            except Exception:
                pass
            try:
                freshness.append(float(item.get("freshness_score") or 0))
            except Exception:
                pass
        if qualities:
            avg_quality = sum(qualities) / len(qualities)
            metrics["avg_source_quality"] = round(avg_quality, 2)
            if avg_quality >= 75:
                score += 0.08
            elif avg_quality < 35:
                score -= 0.06
                reasons.append("kualitas_sumber_rendah")
        if freshness:
            avg_freshness = sum(freshness) / len(freshness)
            metrics["avg_freshness"] = round(avg_freshness, 2)
            if avg_freshness >= 70:
                score += 0.05
    elif high_risk:
        score -= 0.24
        reasons.append("pertanyaan_kritis_tanpa_sumber_kb")

    unsupported_patterns = [
        r"\bmenurut\s+(?:jurnal|penelitian|riset|studi)\b",
        r"\bdata\s+(?:terbaru|menunjukkan)\b",
        r"\bberdasarkan\s+(?:sumber|laporan|regulasi|undang|peraturan)\b",
        r"\bq[1-4]\b",
        r"\b\d{4}\b",
    ]
    unsupported_hits = 0
    if not sources:
        for pattern in unsupported_patterns:
            unsupported_hits += len(re.findall(pattern, lower_answer))
    metrics["unsupported_claim_markers"] = unsupported_hits
    if unsupported_hits:
        penalty = min(0.18, unsupported_hits * 0.035)
        score -= penalty
        reasons.append("ada_klaim_spesifik_tanpa_sumber")

    uncertainty_markers = ["belum cukup", "perlu diverifikasi", "tidak pasti", "berdasarkan konteks", "data yang tersedia"]
    if high_risk and any(marker in lower_answer for marker in uncertainty_markers):
        score += 0.06
    elif high_risk:
        score -= 0.05
        reasons.append("kurang_catatan_ketidakpastian")

    if "Sumber Knowledge Base".lower() in lower_answer or "sumber:" in lower_answer:
        score += 0.04

    if guard_meta:
        if guard_meta.get("allow_answer") is False:
            score -= 0.20
            reasons.append("guard_menilai_bukti_tidak_cukup")
        if guard_meta.get("is_high_risk"):
            metrics["guard_high_risk"] = True

    score = max(0.0, min(1.0, score))
    level = "baik" if score >= 0.78 else ("cukup" if score >= 0.58 else "rendah")
    needs = score < 0.74 and (high_risk or mode in {"pintar", "riset", "kritis"})
    if score < 0.58:
        needs = True
    return QualityResult(score, level, needs, reasons or ["ok"], metrics)


def build_quality_footer(result: QualityResult, mode: str) -> str:
    mode = normalize_answer_mode(mode)
    return (
        "\n\n—\n"
        f"Kontrol kualitas: {result.level} ({result.score:.2f}) | Mode: {mode}"
    )


def _format_sources_for_verifier(sources: List[Dict[str, Any]], limit: int = 6) -> str:
    lines: List[str] = []
    for idx, item in enumerate((sources or [])[:limit], start=1):
        title = str(item.get("title") or item.get("citation") or "Sumber KB")[:180]
        source = str(item.get("source") or "")[:220]
        snippet = str(item.get("content") or item.get("snippet") or item.get("heading") or "")[:900]
        quality = item.get("source_quality", "")
        freshness = item.get("freshness_score", "")
        lines.append(f"[{idx}] {title}\nURL/Sumber: {source}\nQuality: {quality} | Freshness: {freshness}\nKutipan konteks: {snippet}")
    return "\n\n".join(lines).strip() or "Tidak ada sumber KB yang tersedia."


def verify_and_repair_answer(
    *,
    api_url: str,
    api_key: str,
    verifier_model: str,
    system_prompt: str,
    question: str,
    answer: str,
    rag_sources: Optional[List[Dict[str, Any]]] = None,
    mode: str = "kritis",
    timeout: int = 60,
    max_completion_tokens: int = 2200,
) -> Tuple[str, Dict[str, Any]]:
    if call_api_once is None or not verifier_model:
        return answer, {"skipped": True, "reason": "verifier_unavailable"}

    sources_text = _format_sources_for_verifier(rag_sources or [])
    verifier_system = (
        "Anda adalah verifier kualitas jawaban. Tugas Anda: periksa jawaban terhadap pertanyaan dan sumber KB. "
        "Perbaiki jawaban agar lebih akurat, konservatif, dan tidak mengarang. "
        "Jika sumber tidak cukup, ubah jawaban menjadi 'data belum cukup' dengan saran verifikasi. "
        "Jangan menambahkan sumber atau fakta baru yang tidak tersedia di konteks. Jawab hanya dengan versi final yang sudah diperbaiki."
    )
    messages = [
        {"role": "system", "content": verifier_system + "\n\n" + str(system_prompt or "")[:1500]},
        {
            "role": "user",
            "content": (
                f"MODE: {normalize_answer_mode(mode)}\n\n"
                f"PERTANYAAN:\n{str(question or '')[:3000]}\n\n"
                f"SUMBER KB:\n{sources_text}\n\n"
                f"JAWABAN AWAL:\n{str(answer or '')[:6000]}\n\n"
                "Tulis ulang jawaban final yang lebih aman dan berbasis bukti."
            ),
        },
    ]
    started = time.time()
    try:
        repaired, meta = call_api_once(
            api_url=api_url,
            api_key=api_key,
            model=verifier_model,
            messages=messages,
            temperature=0.05,
            max_completion_tokens=max(800, int(max_completion_tokens or 2200)),
            timeout=timeout,
        )
        meta = meta or {}
        meta["quality_verifier_model"] = verifier_model
        meta["quality_verifier_latency_seconds"] = round(time.time() - started, 3)
        if repaired and repaired.strip():
            return repaired.strip(), meta
        meta["quality_verifier_empty"] = True
        return answer, meta
    except Exception as exc:
        return answer, {"error": str(exc)[:800], "quality_verifier_model": verifier_model}


def mode_help_text(current_mode: str = "auto") -> str:
    lines = ["Mode jawaban Adioranye", "", f"Mode aktif: {normalize_answer_mode(current_mode)}", ""]
    lines.append("Perintah:")
    lines.append("/mode hemat  — cepat dan murah")
    lines.append("/mode pintar — lebih lengkap dan teliti")
    lines.append("/mode riset  — wajib mengutamakan sumber/KB")
    lines.append("/mode kritis — anti-halusinasi paling ketat")
    lines.append("/mode auto   — otomatis")
    lines.append("")
    lines.append("Keterangan:")
    for key in ["hemat", "pintar", "riset", "kritis", "auto"]:
        lines.append(f"- {key}: {MODE_DESCRIPTIONS[key]}")
    return "\n".join(lines)
