#!/usr/bin/env python3
"""Lightweight quality/performance evaluation for AdIoranye.

This is a practical guardrail, not a model judge. It checks whether the KB
pipeline is healthy enough to produce better answers: fresh reports, valid DB,
source health, policy availability, and test-set coverage metadata.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

WIB = ZoneInfo("Asia/Jakarta") if ZoneInfo else timezone(timedelta(hours=7))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_wib_text() -> str:
    return datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")


def read_json(path: str | Path, default: Any) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_iso(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def sqlite_stats(db_path: str | Path) -> Dict[str, Any]:
    p = Path(db_path)
    if not p.exists():
        return {"exists": False, "ok": False, "message": "database file not found"}
    stats: Dict[str, Any] = {"exists": True, "ok": False, "path": str(p), "size_bytes": p.stat().st_size, "tables": {}}
    try:
        con = sqlite3.connect(str(p))
        cur = con.cursor()
        cur.execute("PRAGMA quick_check")
        quick_check = cur.fetchone()[0]
        stats["quick_check"] = quick_check
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cur.fetchall()]
        for table in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                stats["tables"][table] = int(cur.fetchone()[0])
            except Exception as exc:
                stats["tables"][table] = f"error: {exc}"
        con.close()
        stats["ok"] = str(quick_check).lower() == "ok"
    except Exception as exc:
        stats["message"] = str(exc)
    return stats


def evaluate(args: argparse.Namespace) -> Dict[str, Any]:
    report = read_json(args.kb_report, {})
    health = read_json(args.health, {})
    policy = read_json(args.policy, {})
    test_set = read_json(args.test_set, [])
    db = sqlite_stats(args.db)

    warnings: List[str] = []
    recommendations: List[str] = []

    if not db.get("ok"):
        warnings.append(f"SQLite DB belum sehat/terbaca: {db.get('message') or db.get('quick_check')}")
        recommendations.append("Cek .adioranye_power.db dan backup di .db_backups sebelum update besar.")

    report_finished = parse_iso(report.get("finished_at") if isinstance(report, dict) else "")
    if not report_finished:
        warnings.append("daily_kb_update_report.json belum tersedia atau belum punya finished_at.")
    else:
        age_hours = (datetime.now(timezone.utc) - report_finished).total_seconds() / 3600
        if age_hours > 48:
            warnings.append(f"Report KB terakhir sudah {age_hours:.1f} jam; freshness jawaban bisa turun.")
            recommendations.append("Jalankan workflow Daily Knowledge Base Optimized Update secara manual.")

    if isinstance(report, dict):
        errors = int(report.get("errors") or 0)
        selected = int(report.get("sources_selected") or 0)
        if selected and errors / max(1, selected) > 0.35:
            warnings.append(f"Error source tinggi: {errors}/{selected} pada update terakhir.")
            recommendations.append("Periksa .adioranye_kb_source_health.json; sumber yang gagal berulang akan masuk cooldown.")

    source_records = (health.get("sources") or {}) if isinstance(health, dict) else {}
    cooldown_count = 0
    failure_count = 0
    for record in source_records.values():
        if not isinstance(record, dict):
            continue
        cooldown_until = parse_iso(record.get("cooldown_until"))
        if cooldown_until and cooldown_until > datetime.now(timezone.utc):
            cooldown_count += 1
        if int(record.get("consecutive_failures") or 0) >= 2:
            failure_count += 1
    if source_records:
        cooldown_ratio = cooldown_count / max(1, len(source_records))
        if cooldown_ratio > 0.25:
            warnings.append(f"Banyak sumber sedang cooldown: {cooldown_count}/{len(source_records)}.")
            recommendations.append("Tambahkan sumber alternatif yang lebih stabil untuk kategori hot/warm.")

    recommended_cfg = (policy.get("recommended_streamlit_config") or {}) if isinstance(policy, dict) else {}
    required_flags = [
        "LIVE_WEB_FALLBACK_FORCE_FOR_CURRENT",
        "POWER_RERANKER_ENABLED",
        "POWER_SEMANTIC_CACHE_ENABLED",
        "POWER_RESPONSE_CACHE_ENABLED",
        "POWER_CIRCUIT_BREAKER_ENABLED",
    ]
    missing_policy = [flag for flag in required_flags if str(recommended_cfg.get(flag, "")).lower() != "true"]
    if missing_policy:
        warnings.append("Runtime policy belum lengkap: " + ", ".join(missing_policy))
        recommendations.append("Gunakan config/adioranye_runtime_policy.json sebagai acuan secrets/env Streamlit.")

    if not isinstance(test_set, list) or len(test_set) < 6:
        warnings.append("performance_test_set.json terlalu sedikit; evaluasi kualitas belum representatif.")
        recommendations.append("Tambahkan minimal 10 pertanyaan: casual, technical, current, RAG, dan long-form.")

    score = 100
    score -= min(40, len(warnings) * 8)
    if db.get("ok"):
        score += 5
    if isinstance(report, dict) and int(report.get("added_chunks") or 0) > 0:
        score += 5
    if source_records:
        score += 5
    score = max(0, min(100, score))

    return {
        "created_at": now_iso(),
        "created_at_wib": now_wib_text(),
        "score": score,
        "status": "good" if score >= 80 else "needs_attention" if score >= 55 else "critical",
        "warnings": warnings,
        "recommendations": recommendations,
        "db": db,
        "kb_report_summary": {
            "finished_at": report.get("finished_at") if isinstance(report, dict) else None,
            "added_documents": report.get("added_documents") if isinstance(report, dict) else None,
            "added_chunks": report.get("added_chunks") if isinstance(report, dict) else None,
            "errors": report.get("errors") if isinstance(report, dict) else None,
            "sources_selected": report.get("sources_selected") if isinstance(report, dict) else None,
        },
        "source_health_summary": {
            "sources_tracked": len(source_records),
            "cooldown_sources": cooldown_count,
            "sources_with_repeated_failures": failure_count,
        },
        "test_set_items": len(test_set) if isinstance(test_set, list) else 0,
    }


def write_markdown(path: str | Path, report: Dict[str, Any]) -> None:
    lines = [
        "# AdIoranye Quality Evaluation Report",
        "",
        f"Generated: {report.get('created_at_wib', '-')}",
        f"Score: **{report.get('score', '-')} / 100**",
        f"Status: **{report.get('status', '-')}**",
        "",
        "## Warnings",
    ]
    warnings = report.get("warnings") or []
    if warnings:
        lines.extend([f"- {item}" for item in warnings])
    else:
        lines.append("- Tidak ada warning utama.")
    lines.extend(["", "## Recommendations"])
    recs = report.get("recommendations") or []
    if recs:
        lines.extend([f"- {item}" for item in recs])
    else:
        lines.append("- Konfigurasi saat ini sudah memadai untuk baseline.")
    lines.extend([
        "",
        "## KB Report Summary",
        "```json",
        json.dumps(report.get("kb_report_summary") or {}, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Source Health Summary",
        "```json",
        json.dumps(report.get("source_health_summary") or {}, ensure_ascii=False, indent=2),
        "```",
        "",
        "## SQLite Summary",
        "```json",
        json.dumps(report.get("db") or {}, ensure_ascii=False, indent=2),
        "```",
        "",
    ])
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate AdIoranye KB quality/performance readiness")
    parser.add_argument("--db", default=".adioranye_power.db")
    parser.add_argument("--kb-report", default="daily_kb_update_report.json")
    parser.add_argument("--health", default=".adioranye_kb_source_health.json")
    parser.add_argument("--policy", default="config/adioranye_runtime_policy.json")
    parser.add_argument("--test-set", default="performance_test_set.json")
    parser.add_argument("--json-output", default="adioranye_quality_eval_report.json")
    parser.add_argument("--markdown-output", default="adioranye_quality_eval_report.md")
    parser.add_argument("--fail-under", type=int, default=55)
    args = parser.parse_args()

    report = evaluate(args)
    write_json(args.json_output, report)
    write_markdown(args.markdown_output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if int(report.get("score") or 0) < int(args.fail_under) else 0


if __name__ == "__main__":
    raise SystemExit(main())
