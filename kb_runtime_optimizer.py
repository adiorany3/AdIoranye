#!/usr/bin/env python3
"""Runtime optimizer for AdIoranye Knowledge Base updates and answer routing.

This file is intentionally dependency-free. It improves the existing
`daily_kb_scraper.py` flow without changing that scraper:

1. Classifies sources into hot/warm/cold tiers.
2. Skips sources that are repeatedly failing through a cooldown policy.
3. Creates an effective, smaller `kb_sources_effective.json` for each run.
4. Updates source-health history after the scraper report is produced.
5. Exposes lightweight answer-profile helpers for future app/Telegram routing.

The module can be used from GitHub Actions and can also be imported by app.py or
telegram_service.py later if you want stricter routing integration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

WIB = ZoneInfo("Asia/Jakarta") if ZoneInfo else timezone(timedelta(hours=7))

DEFAULT_HEALTH_FILE = ".adioranye_kb_source_health.json"
DEFAULT_EFFECTIVE_SOURCES_FILE = "kb_sources_effective.json"
DEFAULT_POLICY_FILE = "config/adioranye_runtime_policy.json"

CURRENT_INTENT_PATTERNS = [
    r"\bterbaru\b",
    r"\bterkini\b",
    r"\bhari ini\b",
    r"\bsekarang\b",
    r"\bbaru saja\b",
    r"\bupdate\b",
    r"\bjadwal\b",
    r"\bharga\b",
    r"\brate\b",
    r"\bkurs\b",
    r"\bsiapa .* sekarang\b",
    r"\blatest\b",
    r"\bcurrent\b",
    r"\btoday\b",
    r"\bthis week\b",
    r"\b2026\b",
]

DEEP_INTENT_PATTERNS = [
    r"\banalisis\b",
    r"\briset\b",
    r"\bbandingkan\b",
    r"\bkomprehensif\b",
    r"\bmendalam\b",
    r"\bdebug\b",
    r"\bperbaiki\b",
    r"\baudit\b",
    r"\broot cause\b",
    r"\bdetail\b",
    r"\btechnical\b",
    r"\barchitecture\b",
]

FAST_INTENT_PATTERNS = [
    r"\bhalo\b",
    r"\bhai\b",
    r"\bhi\b",
    r"\bthanks?\b",
    r"\bterima kasih\b",
    r"\bapa kabar\b",
    r"\bok(e)?\b",
]

HOT_HINTS = [
    "news", "berita", "rss", "antaranews", "detik", "tempo", "kompas",
    "cnn", "reuters", "bbc", "theverge", "techcrunch", "wired", "trend",
    "trending", "google news", "politik", "ekonomi", "ai", "teknologi",
]
WARM_HINTS = [
    "jurnal", "journal", "research", "riset", "paper", "openalex", "sinta",
    "scopus", "springer", "wiley", "elsevier", "arxiv", "pubmed", "regulasi",
    "peraturan", "kementerian", "go.id", ".gov", "who.int", "nasa.gov",
]
COLD_HINTS = [
    "static", "manual", "curated", "referensi", "panduan", "guide", "docs",
    "documentation", "arsip", "archive", "glossary", "kamus",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds")


def now_wib_text() -> str:
    return datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")


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


def stable_hash(text: str, length: int = 16) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", "ignore")).hexdigest()[:length]


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


def load_sources(path: str | Path) -> List[Dict[str, Any]]:
    data = read_json(path, [])
    if isinstance(data, dict):
        data = data.get("sources", [])
    if not isinstance(data, list):
        raise ValueError(f"File sumber tidak valid: {path}")
    return [item for item in data if isinstance(item, dict)]


def source_key(source: Dict[str, Any]) -> str:
    name = str(source.get("name") or source.get("title") or "").strip().lower()
    url = str(source.get("url") or "").strip().lower()
    return stable_hash(f"{name}|{url}")


def source_name(source: Dict[str, Any]) -> str:
    return str(source.get("name") or source.get("title") or source.get("url") or "Sumber").strip()


def source_domain(url: str) -> str:
    try:
        return urlparse(str(url or "")).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def text_blob(source: Dict[str, Any]) -> str:
    values = [
        source.get("name"),
        source.get("title"),
        source.get("url"),
        source.get("type"),
        source.get("collection"),
        source.get("tags"),
        source.get("update_profile"),
        source.get("source_class"),
    ]
    return " ".join(str(v or "") for v in values).lower()


def classify_source_tier(source: Dict[str, Any]) -> str:
    explicit = str(
        source.get("update_profile")
        or source.get("source_class")
        or source.get("tier")
        or source.get("freshness_tier")
        or ""
    ).strip().lower()
    if explicit in {"hot", "warm", "cold"}:
        return explicit

    hay = text_blob(source)
    source_type = str(source.get("type") or "rss").lower()
    if source_type == "static":
        return "cold"
    if any(hint in hay for hint in COLD_HINTS):
        return "cold"
    if any(hint in hay for hint in WARM_HINTS):
        return "warm"
    if any(hint in hay for hint in HOT_HINTS):
        return "hot"
    if source_type in {"rss", "html_index", "sitemap", "openalex_works"}:
        return "warm" if source_type == "openalex_works" else "hot"
    return "warm"


def quality_score(source: Dict[str, Any]) -> float:
    try:
        value = float(source.get("source_quality"))
        if math.isfinite(value):
            return max(0.0, min(100.0, value))
    except Exception:
        pass

    hay = text_blob(source)
    score = 55.0
    if any(x in hay for x in [".go.id", ".gov", "who.int", "nih.gov", "nasa.gov", "kemkes"]):
        score = max(score, 92.0)
    if any(x in hay for x in ["journal", "jurnal", "openalex", "scopus", "sinta", "pubmed", "springer"]):
        score = max(score, 88.0)
    if any(x in hay for x in ["reuters", "bbc", "antaranews", "kompas", "tempo", "techcrunch", "theverge"]):
        score = max(score, 76.0)
    if any(x in hay for x in ["blogspot", "wordpress", "facebook", "instagram", "tiktok", "x.com"]):
        score = min(score, 45.0)
    return score


def load_health(path: str | Path = DEFAULT_HEALTH_FILE) -> Dict[str, Any]:
    data = read_json(path, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 2)
    data.setdefault("updated_at", "")
    data.setdefault("sources", {})
    data.setdefault("runs", [])
    if not isinstance(data.get("sources"), dict):
        data["sources"] = {}
    if not isinstance(data.get("runs"), list):
        data["runs"] = []
    return data


def health_record(health: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
    key = source_key(source)
    records = health.setdefault("sources", {})
    record = records.get(key)
    if not isinstance(record, dict):
        record = {}
        records[key] = record
    record.setdefault("key", key)
    record["name"] = source_name(source)
    record["url"] = str(source.get("url") or "")
    record["domain"] = source_domain(str(source.get("url") or ""))
    record["tier"] = classify_source_tier(source)
    record.setdefault("consecutive_failures", 0)
    record.setdefault("total_failures", 0)
    record.setdefault("total_success", 0)
    record.setdefault("last_success_at", "")
    record.setdefault("last_error_at", "")
    record.setdefault("cooldown_until", "")
    record.setdefault("avg_elapsed_seconds", 0.0)
    record.setdefault("last_status", "never")
    record.setdefault("last_message", "")
    return record


def is_in_cooldown(record: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    cooldown_until = parse_iso(record.get("cooldown_until"))
    if not cooldown_until:
        return False
    return cooldown_until > (now or now_utc())


def current_hour_wib() -> int:
    return int(datetime.now(WIB).strftime("%H"))


def tier_quota(source_limit: int, profile: str = "auto") -> Dict[str, int]:
    source_limit = max(1, int(source_limit or 1))
    profile = str(profile or "auto").strip().lower()
    if profile == "hot":
        return {"hot": source_limit, "warm": 0, "cold": 0}
    if profile == "warm":
        return {"hot": max(1, source_limit // 4), "warm": source_limit, "cold": 0}
    if profile == "cold":
        return {"hot": 0, "warm": max(1, source_limit // 4), "cold": source_limit}
    if profile == "all":
        return {"hot": source_limit, "warm": source_limit, "cold": source_limit}

    hour = current_hour_wib()
    # Auto: hot every run, warm often, cold only small slice. Overnight runs can
    # afford a little more warm/cold work.
    if hour in {0, 1, 2, 3, 4, 5}:
        hot = max(1, round(source_limit * 0.45))
        warm = max(1, round(source_limit * 0.40))
        cold = max(0, source_limit - hot - warm)
    else:
        hot = max(1, round(source_limit * 0.65))
        warm = max(1, round(source_limit * 0.30))
        cold = max(0, source_limit - hot - warm)
    return {"hot": hot, "warm": warm, "cold": cold}


def source_priority_score(source: Dict[str, Any], record: Dict[str, Any], profile: str) -> float:
    tier = classify_source_tier(source)
    base = {"hot": 40.0, "warm": 30.0, "cold": 20.0}.get(tier, 25.0)
    if str(profile or "").lower() == tier:
        base += 25.0
    if str(source.get("pinned", "")).lower() in {"1", "true", "yes"} or bool(source.get("pinned")):
        base += 35.0
    base += quality_score(source) / 5.0

    failures = int(record.get("consecutive_failures") or 0)
    base -= min(30.0, failures * 8.0)

    last_success = parse_iso(record.get("last_success_at"))
    if last_success:
        age_hours = max(0.0, (now_utc() - last_success).total_seconds() / 3600.0)
        base += min(18.0, age_hours / 8.0)
    else:
        base += 8.0

    # Stable daily jitter prevents the same equal-score source from always winning.
    day_key = datetime.now(WIB).strftime("%Y-%m-%d")
    jitter = int(stable_hash(day_key + source_key(source), 8), 16) % 1000 / 1000.0
    return base + jitter


def select_sources(
    sources: List[Dict[str, Any]],
    health: Dict[str, Any],
    source_limit: int,
    max_items: int,
    profile: str = "auto",
    force_cooldown: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    enabled: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    now = now_utc()

    for source in sources:
        if not bool(source.get("enabled", True)):
            continue
        if not str(source.get("url") or source.get("static_content") or "").strip():
            continue
        record = health_record(health, source)
        if is_in_cooldown(record, now) and not force_cooldown:
            skipped.append({
                "name": source_name(source),
                "url": str(source.get("url") or ""),
                "reason": "cooldown",
                "cooldown_until": record.get("cooldown_until", ""),
            })
            continue
        enabled.append(source)

    source_limit = max(1, int(source_limit or 8))
    max_items = max(1, int(max_items or 1))
    profile = str(profile or "auto").strip().lower()
    quotas = tier_quota(source_limit, profile)

    buckets: Dict[str, List[Dict[str, Any]]] = {"hot": [], "warm": [], "cold": []}
    for source in enabled:
        tier = classify_source_tier(source)
        buckets.setdefault(tier, []).append(source)

    for tier, bucket in buckets.items():
        bucket.sort(
            key=lambda item: source_priority_score(item, health_record(health, item), profile),
            reverse=True,
        )

    selected: List[Dict[str, Any]] = []
    selected_keys = set()

    def add_candidate(item: Dict[str, Any]) -> None:
        key = source_key(item)
        if key in selected_keys or len(selected) >= source_limit:
            return
        copy = dict(item)
        copy["update_profile"] = classify_source_tier(item)
        if max_items:
            try:
                copy["max_items"] = min(max_items, max(1, int(copy.get("max_items") or max_items)))
            except Exception:
                copy["max_items"] = max_items
        selected.append(copy)
        selected_keys.add(key)

    for tier in ["hot", "warm", "cold"]:
        for item in buckets.get(tier, [])[: max(0, quotas.get(tier, 0))]:
            add_candidate(item)

    leftovers: List[Dict[str, Any]] = []
    for bucket in buckets.values():
        leftovers.extend(bucket)
    leftovers.sort(
        key=lambda item: source_priority_score(item, health_record(health, item), profile),
        reverse=True,
    )
    for item in leftovers:
        add_candidate(item)
        if len(selected) >= source_limit:
            break

    stats = {
        "profile": profile,
        "source_limit": source_limit,
        "max_items": max_items,
        "total_sources": len(sources),
        "enabled_candidates": len(enabled),
        "selected_sources": len(selected),
        "skipped_cooldown": len(skipped),
        "quota": quotas,
        "tier_counts_all": {tier: len(bucket) for tier, bucket in buckets.items()},
        "tier_counts_selected": {
            "hot": sum(1 for item in selected if classify_source_tier(item) == "hot"),
            "warm": sum(1 for item in selected if classify_source_tier(item) == "warm"),
            "cold": sum(1 for item in selected if classify_source_tier(item) == "cold"),
        },
        "skipped": skipped[:50],
        "selected": [
            {
                "name": source_name(item),
                "url": str(item.get("url") or ""),
                "tier": classify_source_tier(item),
                "quality": quality_score(item),
                "score": round(source_priority_score(item, health_record(health, item), profile), 3),
            }
            for item in selected
        ],
    }
    return selected, stats


def write_effective_sources(selected: List[Dict[str, Any]], output: str | Path) -> None:
    write_json(output, selected)


def prepare_command(args: argparse.Namespace) -> int:
    sources = load_sources(args.sources)
    health = load_health(args.health)
    selected, stats = select_sources(
        sources=sources,
        health=health,
        source_limit=args.source_limit,
        max_items=args.max_items,
        profile=args.profile,
        force_cooldown=args.force_cooldown,
    )
    health["updated_at"] = now_iso()
    health.setdefault("runs", []).append({
        "type": "prepare",
        "at": now_iso(),
        "at_wib": now_wib_text(),
        "profile": args.profile,
        "selected_sources": len(selected),
        "source_limit": args.source_limit,
        "max_items": args.max_items,
    })
    health["runs"] = health.get("runs", [])[-80:]

    write_effective_sources(selected, args.output)
    write_json(args.health, health)
    write_json(args.report, {
        "created_at": now_iso(),
        "created_at_wib": now_wib_text(),
        "effective_sources_file": str(args.output),
        **stats,
    })

    print(f"Prepared {len(selected)} effective KB sources from {len(sources)} total sources.")
    print(f"Profile={args.profile}; cooldown skipped={stats['skipped_cooldown']}; output={args.output}")
    return 0


def status_is_error(status: Any) -> bool:
    raw = str(status or "").strip().lower()
    return raw.startswith("error") or raw in {"failed", "timeout", "exception"}


def status_is_success(status: Any) -> bool:
    raw = str(status or "").strip().lower()
    if not raw:
        return False
    if status_is_error(raw):
        return False
    return raw in {
        "added",
        "dry_run",
        "skipped_existing",
        "skipped_duplicate_hash",
        "skipped_short",
        "no_items",
        "ok",
        "success",
    } or raw.startswith("skipped")


def register_success(record: Dict[str, Any], status: str, elapsed: Optional[float] = None) -> None:
    record["consecutive_failures"] = 0
    record["total_success"] = int(record.get("total_success") or 0) + 1
    record["last_success_at"] = now_iso()
    record["last_status"] = status or "success"
    record["last_message"] = ""
    record["cooldown_until"] = ""
    if elapsed is not None and elapsed >= 0:
        current = float(record.get("avg_elapsed_seconds") or 0.0)
        record["avg_elapsed_seconds"] = round(elapsed if current <= 0 else (current * 0.7 + elapsed * 0.3), 3)


def register_failure(record: Dict[str, Any], status: str, message: str = "") -> None:
    failures = int(record.get("consecutive_failures") or 0) + 1
    record["consecutive_failures"] = failures
    record["total_failures"] = int(record.get("total_failures") or 0) + 1
    record["last_error_at"] = now_iso()
    record["last_status"] = status or "error"
    record["last_message"] = str(message or "")[:500]
    if failures >= 5:
        cooldown_hours = 24
    elif failures >= 3:
        cooldown_hours = 12
    elif failures >= 2:
        cooldown_hours = 4
    else:
        cooldown_hours = 0
    if cooldown_hours:
        record["cooldown_until"] = (now_utc() + timedelta(hours=cooldown_hours)).isoformat(timespec="seconds")


def update_health_command(args: argparse.Namespace) -> int:
    health = load_health(args.health)
    selected = load_sources(args.effective_sources) if Path(args.effective_sources).exists() else []
    report = read_json(args.scraper_report, {})
    items = report.get("items") if isinstance(report, dict) else []
    if not isinstance(items, list):
        items = []

    selected_by_name = {source_name(source): source for source in selected}
    selected_by_key = {source_key(source): source for source in selected}

    by_source: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("source") or "").strip()
        if not name:
            continue
        by_source.setdefault(name, []).append(item)

    workflow_failed = bool(args.workflow_failed)
    touched = 0
    failures = 0
    successes = 0

    for source in selected:
        name = source_name(source)
        record = health_record(health, source)
        source_items = by_source.get(name, [])
        if workflow_failed and not source_items:
            register_failure(record, "workflow_failed", "Scraper step failed before this source produced an item.")
            touched += 1
            failures += 1
            continue
        if not source_items:
            record["last_status"] = "selected_no_report_item"
            record["last_message"] = "Source was selected but scraper report had no item for it. Not counted as failure."
            touched += 1
            continue
        if any(status_is_error(item.get("status")) for item in source_items):
            err_item = next((item for item in source_items if status_is_error(item.get("status"))), source_items[0])
            register_failure(record, str(err_item.get("status") or "error"), str(err_item.get("message") or err_item.get("title") or ""))
            failures += 1
        else:
            register_success(record, str(source_items[-1].get("status") or "success"))
            successes += 1
        touched += 1

    health["updated_at"] = now_iso()
    health.setdefault("runs", []).append({
        "type": "update-health",
        "at": now_iso(),
        "at_wib": now_wib_text(),
        "selected_sources": len(selected),
        "touched_sources": touched,
        "successes": successes,
        "failures": failures,
        "workflow_failed": workflow_failed,
        "scraper_added_documents": report.get("added_documents") if isinstance(report, dict) else None,
        "scraper_errors": report.get("errors") if isinstance(report, dict) else None,
    })
    health["runs"] = health.get("runs", [])[-80:]
    write_json(args.health, health)
    print(f"Updated health: touched={touched}, successes={successes}, failures={failures}.")
    return 0


def question_matches(patterns: Iterable[str], question: str) -> bool:
    text = str(question or "").strip().lower()
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def classify_answer_profile(question: str) -> Dict[str, Any]:
    """Return a lightweight answer profile for routing.

    Suggested usage from app.py/telegram_service.py later:
      profile = classify_answer_profile(user_text)
      if profile["mode"] == "fast": disable heavy RAG and cap tokens.
      if profile["force_live_web"]: require current-source/live-web path.
    """
    q = str(question or "").strip()
    q_lower = q.lower()
    words = re.findall(r"[\wÀ-ÿ]+", q_lower)
    current = question_matches(CURRENT_INTENT_PATTERNS, q)
    deep = question_matches(DEEP_INTENT_PATTERNS, q) or len(words) > 45
    fast = question_matches(FAST_INTENT_PATTERNS, q) and len(words) <= 12

    if current:
        mode = "current"
    elif deep:
        mode = "deep"
    elif fast or len(words) <= 10:
        mode = "fast"
    else:
        mode = "balanced"

    return {
        "mode": mode,
        "force_live_web": current,
        "rag_top_k": {"fast": 0, "balanced": 5, "deep": 8, "current": 5}[mode],
        "max_tokens_hint": {"fast": 450, "balanced": 1100, "deep": 2200, "current": 1300}[mode],
        "need_sources": mode in {"balanced", "deep", "current"},
        "freshness_guard": current,
        "question_length_words": len(words),
        "signals": {
            "current": current,
            "deep": deep,
            "fast": fast,
        },
    }


def profile_question_command(args: argparse.Namespace) -> int:
    write_json(args.output, classify_answer_profile(args.question))
    print(json.dumps(classify_answer_profile(args.question), ensure_ascii=False, indent=2))
    return 0


def policy_command(args: argparse.Namespace) -> int:
    policy = {
        "version": 1,
        "created_at": now_iso(),
        "created_at_wib": now_wib_text(),
        "answer_profiles": {
            "fast": {
                "description": "Pertanyaan ringan/santai: respons cepat, tanpa RAG berat.",
                "power_rag_top_k": 0,
                "max_tokens": 450,
                "prefer_fast_cheap_model": True,
            },
            "balanced": {
                "description": "Pertanyaan normal: RAG ringan dan sumber terbaik.",
                "power_rag_top_k": 5,
                "max_tokens": 1100,
                "prefer_fast_cheap_model": True,
            },
            "deep": {
                "description": "Pertanyaan teknis/riset: RAG lebih banyak, reranking, self-check.",
                "power_rag_top_k": 8,
                "max_tokens": 2200,
                "prefer_capable_model": True,
            },
            "current": {
                "description": "Pertanyaan terbaru/current: freshness guard dan live-web fallback wajib.",
                "power_rag_top_k": 5,
                "max_tokens": 1300,
                "force_live_web": True,
                "require_source_date": True,
            },
        },
        "recommended_streamlit_config": {
            "POWER_PERFORMANCE_OPTIMIZER_ENABLED": "true",
            "POWER_QUERY_REWRITER_ENABLED": "true",
            "POWER_RERANKER_ENABLED": "true",
            "POWER_SEMANTIC_CACHE_ENABLED": "true",
            "POWER_RESPONSE_CACHE_ENABLED": "true",
            "POWER_LATENCY_BUDGET_ENABLED": "true",
            "POWER_CIRCUIT_BREAKER_ENABLED": "true",
            "LIVE_WEB_FALLBACK_ENABLED": "true",
            "LIVE_WEB_FALLBACK_FORCE_FOR_CURRENT": "true",
            "TELEGRAM_HISTORY_LIMIT": "6",
            "TELEGRAM_MEMORY_CONTEXT_MAX_CHARS": "2200",
            "TELEGRAM_LIVE_CONTEXT_MAX_CHARS": "3800",
            "MAX_TOKENS_CASUAL": "450",
            "MAX_TOKENS_NORMAL": "1100",
            "MAX_TOKENS_TECHNICAL": "2000",
            "QUESTION_QUICK_CHECK_COOLDOWN_SECONDS": "600",
            "GITHUB_UPDATE_SOURCE_LIMIT": "8",
            "GITHUB_UPDATE_MAX_ITEMS": "1",
        },
        "source_update_strategy": {
            "hot": "news/trends/current topics; every scheduled run",
            "warm": "journals/gov/regulations; often, especially low-traffic hours",
            "cold": "static/reference docs; small slice or manual profile=cold",
            "cooldown": "after repeated source errors: 4h, 12h, then 24h",
        },
    }
    write_json(args.output, policy)
    print(f"Wrote runtime policy to {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AdIoranye KB runtime optimizer")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare", help="Create effective sources file with health/cooldown/tier policy")
    p_prepare.add_argument("--sources", default="kb_sources.json")
    p_prepare.add_argument("--health", default=DEFAULT_HEALTH_FILE)
    p_prepare.add_argument("--output", default=DEFAULT_EFFECTIVE_SOURCES_FILE)
    p_prepare.add_argument("--report", default="kb_runtime_optimizer_prepare.json")
    p_prepare.add_argument("--source-limit", type=int, default=int(os.getenv("KB_UPDATE_SOURCE_LIMIT", "8") or 8))
    p_prepare.add_argument("--max-items", type=int, default=int(os.getenv("KB_UPDATE_MAX_ITEMS", "1") or 1))
    p_prepare.add_argument("--profile", default=os.getenv("KB_UPDATE_PROFILE", "auto"), choices=["auto", "hot", "warm", "cold", "all"])
    p_prepare.add_argument("--force-cooldown", action="store_true")
    p_prepare.set_defaults(func=prepare_command)

    p_health = sub.add_parser("update-health", help="Update source health from scraper report")
    p_health.add_argument("--health", default=DEFAULT_HEALTH_FILE)
    p_health.add_argument("--effective-sources", default=DEFAULT_EFFECTIVE_SOURCES_FILE)
    p_health.add_argument("--scraper-report", default="daily_kb_update_report.json")
    p_health.add_argument("--workflow-failed", action="store_true")
    p_health.set_defaults(func=update_health_command)

    p_profile = sub.add_parser("profile-question", help="Classify a question into fast/balanced/deep/current")
    p_profile.add_argument("question")
    p_profile.add_argument("--output", default="question_profile.json")
    p_profile.set_defaults(func=profile_question_command)

    p_policy = sub.add_parser("write-policy", help="Write recommended runtime policy JSON")
    p_policy.add_argument("--output", default=DEFAULT_POLICY_FILE)
    p_policy.set_defaults(func=policy_command)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
