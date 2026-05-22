"""Daily Knowledge Base scraper for Adioranye AI.

This module pulls fresh public information from configured RSS feeds or simple
HTML pages/static curated notes and stores the cleaned content in the existing Adioranye PowerStore
SQLite knowledge base. It is intentionally lightweight for Streamlit Cloud / GitHub
Actions: only `requests` is required, and the KB write path uses PowerStore.add_document().

Usage:
    python daily_kb_scraper.py --db .adioranye_power.db --sources kb_sources.json
    python daily_kb_scraper.py --dry-run --max-items 3
"""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, urlunparse
import xml.etree.ElementTree as ET

import requests

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python <3.9 fallback is not expected here.
    ZoneInfo = None  # type: ignore

from power_features import get_power_store

DEFAULT_DB_PATH = ".adioranye_power.db"
DEFAULT_SOURCES_FILE = "kb_sources.json"
DEFAULT_STATE_FILE = ".adioranye_kb_scrape_state.json"
DEFAULT_USER_AGENT = (
    "AdioranyeAI-KB-Updater/1.0 (+https://github.com/; respectful daily knowledge update)"
)
WIB_TZ = ZoneInfo("Asia/Jakarta") if ZoneInfo else timezone.utc


# =========================
# Generic helpers
# =========================

def now_wib_text() -> str:
    return datetime.now(WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", "ignore")).hexdigest()


def clean_spaces(text: str) -> str:
    text = html.unescape(str(text or ""))
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate(text: str, max_chars: int) -> str:
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 80)].rstrip() + "\n\n[Konten dipotong agar knowledge base tetap ringan.]"



def extract_keywords(text: str, limit: int = 12) -> List[str]:
    """Simple keyword extraction for KB metadata without heavy NLP dependencies."""
    raw = re.findall(r"[a-zA-ZÀ-ÿ0-9_\-]{4,}", str(text or "").lower())
    stop = {
        "yang", "dan", "atau", "untuk", "dengan", "dari", "pada", "dalam", "adalah", "sebagai", "karena", "akan", "lebih", "telah",
        "this", "that", "with", "from", "were", "have", "about", "their", "there", "which", "would", "could",
    }
    counts: Dict[str, int] = {}
    for word in raw:
        if word in stop or len(word) < 4:
            continue
        counts[word] = counts.get(word, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:max(1, int(limit or 12))]]


def summarize_for_kb(text: str, max_sentences: int = 5) -> str:
    """Extractive summary for cleaner RAG chunks.

    This avoids API cost and makes scraped articles easier for the retrieval layer.
    It selects sentences with frequent terms, while preserving source-neutral wording.
    """
    clean = clean_spaces(text)
    if not clean:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+|\n+", clean)
    sentences = [s.strip() for s in sentences if 50 <= len(s.strip()) <= 420]
    if not sentences:
        return clean[:900]
    keywords = set(extract_keywords(clean, limit=18))
    scored = []
    for idx, sentence in enumerate(sentences[:80]):
        words = set(re.findall(r"[a-zA-ZÀ-ÿ0-9_\-]{4,}", sentence.lower()))
        score = len(words & keywords) + (0.5 if idx < 8 else 0.0)
        scored.append((score, idx, sentence))
    picked = sorted(sorted(scored, reverse=True)[:max(1, int(max_sentences or 5))], key=lambda x: x[1])
    return clean_spaces(" ".join(item[2] for item in picked))[:1800]


def source_domain(url: str) -> str:
    try:
        return urlparse(str(url or "")).netloc.lower().replace("www.", "")[:160]
    except Exception:
        return ""


def estimate_source_quality_from_config(source: "SourceConfig") -> float:
    try:
        if source.source_quality is not None:
            return max(0.0, min(100.0, float(source.source_quality)))
    except Exception:
        pass
    hay = f"{source.name} {source.url} {source.tags} {source.collection}".lower()
    score = 55.0
    if any(x in hay for x in [".go.id", ".gov", "who.int", "fao.org", "woah.org", "nih.gov", "cdc.gov", "nasa.gov", "kemkes", "pertanian"]):
        score = max(score, 92.0)
    if any(x in hay for x in ["scimagojr", "scopus", "sinta", "pubmed", "journal", "jurnal", "springer", "elsevier", "wiley"]):
        score = max(score, 88.0)
    if any(x in hay for x in ["reuters", "bbc", "antaranews", "kompas", "tempo", "detik", "cnn", "techcrunch", "theverge", "wired"]):
        score = max(score, 76.0)
    if any(x in hay for x in ["trends", "trending", "google news"]):
        score = max(score, 70.0)
    if any(x in hay for x in ["blogspot", "wordpress", "facebook", "instagram", "tiktok", "twitter", "x.com"]):
        score = min(score, 45.0)
    return score


def canonical_url(url: str, base_url: str = "") -> str:
    raw = urljoin(base_url or "", str(url or "").strip())
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return ""
    # Remove fragments and common tracking parameters.
    query_parts = []
    for part in parsed.query.split("&") if parsed.query else []:
        key = part.split("=", 1)[0].lower()
        if key.startswith("utm_") or key in {"fbclid", "gclid", "mc_cid", "mc_eid"}:
            continue
        if part:
            query_parts.append(part)
    cleaned = parsed._replace(fragment="", query="&".join(query_parts))
    return urlunparse(cleaned)


def parse_date_text(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")
    except Exception:
        return raw[:120]


def safe_filename_slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(text or "").lower()).strip("-")
    return slug[:80] or "source"


# =========================
# HTML extraction helpers
# =========================

class ReadableHTMLExtractor(HTMLParser):
    """Small dependency-free readable text extractor.

    It is not a full Readability clone, but it is good enough for public blog/news
    pages and avoids requiring BeautifulSoup/readability-lxml on Streamlit Cloud.
    """

    BLOCK_TAGS = {
        "article", "main", "section", "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4",
        "h5", "h6", "blockquote", "pre", "table", "tr", "td", "th",
    }
    SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "form", "button", "input", "select", "textarea"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.skip_depth = 0
        self.title = ""
        self._in_title = False
        self._title_parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS and self.skip_depth > 0:
            self.skip_depth -= 1
        if tag == "title":
            self._in_title = False
            self.title = clean_spaces(" ".join(self._title_parts))
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = clean_spaces(data)
        if not text:
            return
        if self._in_title:
            self._title_parts.append(text)
        else:
            self.parts.append(text + " ")

    def get_text(self) -> str:
        text = "".join(self.parts)
        lines = []
        seen = set()
        for line in text.splitlines():
            clean = clean_spaces(line)
            if not clean:
                continue
            # Drop repeated boilerplate-like very short lines.
            key = clean.lower()
            if len(clean) < 35 and key in seen:
                continue
            seen.add(key)
            lines.append(clean)
        return clean_spaces("\n\n".join(lines))


class LinkExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: List[Dict[str, str]] = []
        self._active_href = ""
        self._active_text: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() != "a":
            return
        attr = dict(attrs)
        href = canonical_url(attr.get("href", "") or "", self.base_url)
        if href:
            self._active_href = href
            self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_href:
            text = clean_spaces(data)
            if text:
                self._active_text.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._active_href:
            text = clean_spaces(" ".join(self._active_text))
            self.links.append({"url": self._active_href, "title": text})
            self._active_href = ""
            self._active_text = []


def extract_html_text(html_text: str) -> Tuple[str, str]:
    parser = ReadableHTMLExtractor()
    parser.feed(str(html_text or ""))
    title = parser.title
    body = parser.get_text()
    return title, body


def extract_links(html_text: str, base_url: str) -> List[Dict[str, str]]:
    parser = LinkExtractor(base_url=base_url)
    parser.feed(str(html_text or ""))
    unique: List[Dict[str, str]] = []
    seen = set()
    for link in parser.links:
        url = link.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(link)
    return unique


# =========================
# RSS / Atom helpers
# =========================

def strip_namespace(tag: str) -> str:
    return str(tag or "").split("}")[-1].lower()


def child_text(node: ET.Element, names: Iterable[str]) -> str:
    wanted = {n.lower() for n in names}
    for child in list(node):
        if strip_namespace(child.tag) in wanted:
            text = "".join(child.itertext())
            if text and text.strip():
                return clean_spaces(text)
    return ""


def child_link(node: ET.Element) -> str:
    # RSS: <link>url</link>
    rss_link = child_text(node, ["link"])
    if rss_link.startswith("http"):
        return rss_link
    # Atom: <link href="url" rel="alternate" />
    for child in list(node):
        if strip_namespace(child.tag) == "link":
            href = child.attrib.get("href", "")
            rel = child.attrib.get("rel", "alternate")
            if href and rel in {"alternate", ""}:
                return href
    return rss_link


def parse_feed_items(xml_text: str, base_url: str = "") -> List[Dict[str, str]]:
    try:
        root = ET.fromstring(xml_text.encode("utf-8", "ignore"))
    except Exception:
        root = ET.fromstring(xml_text)

    nodes: List[ET.Element] = []
    for item in root.iter():
        tag = strip_namespace(item.tag)
        if tag in {"item", "entry"}:
            nodes.append(item)

    items: List[Dict[str, str]] = []
    for node in nodes:
        title = child_text(node, ["title"])
        link = canonical_url(child_link(node), base_url)
        summary = child_text(node, ["description", "summary", "content", "encoded"])
        published = child_text(node, ["pubDate", "published", "updated", "date"])
        guid = child_text(node, ["guid", "id"])
        if title or link or summary:
            items.append({
                "title": title or link or guid or "Tanpa judul",
                "url": link,
                "summary": clean_spaces(re.sub(r"<[^>]+>", " ", summary)),
                "published": parse_date_text(published),
                "guid": guid,
            })
    return items


# =========================
# Source config and state
# =========================

@dataclass
class SourceConfig:
    name: str
    url: str
    type: str = "rss"  # rss, html, html_index, sitemap, static
    enabled: bool = True
    collection: str = "Auto Update"
    tags: str = "auto-update"
    max_items: int = 5
    fetch_article: bool = True
    pinned: bool = False
    include_patterns: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None
    min_chars: int = 300
    max_chars: int = 30000
    delay_seconds: float = 1.0
    static_title: str = ""
    static_content: str = ""
    source_quality: Optional[float] = None
    summary_sentences: int = 5


def load_sources(path: str = DEFAULT_SOURCES_FILE) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("sources", [])
    if not isinstance(data, list):
        raise ValueError("Format sources harus berupa list JSON atau object {'sources': [...]}.")
    return [item for item in data if isinstance(item, dict)]


def normalize_source(raw: Dict[str, Any]) -> SourceConfig:
    return SourceConfig(
        name=str(raw.get("name") or raw.get("title") or raw.get("url") or "Sumber").strip(),
        url=str(raw.get("url") or "").strip(),
        type=str(raw.get("type") or "rss").strip().lower(),
        enabled=bool(raw.get("enabled", True)),
        collection=str(raw.get("collection") or "Auto Update").strip(),
        tags=str(raw.get("tags") or "auto-update").strip(),
        max_items=max(1, int(raw.get("max_items") or 5)),
        fetch_article=bool(raw.get("fetch_article", True)),
        pinned=bool(raw.get("pinned", False)),
        include_patterns=raw.get("include_patterns") if isinstance(raw.get("include_patterns"), list) else None,
        exclude_patterns=raw.get("exclude_patterns") if isinstance(raw.get("exclude_patterns"), list) else None,
        min_chars=max(0, int(raw.get("min_chars") or 300)),
        max_chars=max(1000, int(raw.get("max_chars") or 30000)),
        delay_seconds=max(0.0, float(raw.get("delay_seconds") or 1.0)),
        static_title=str(raw.get("static_title") or raw.get("title") or raw.get("name") or "").strip(),
        static_content=str(raw.get("static_content") or raw.get("content") or raw.get("text") or "").strip(),
        source_quality=(float(raw.get("source_quality")) if str(raw.get("source_quality", "")).strip() else None),
        summary_sentences=max(2, min(8, int(raw.get("summary_sentences") or 5))),
    )


def load_state(path: str = DEFAULT_STATE_FILE) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"version": 1, "processed": {}, "runs": []}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"version": 1, "processed": {}, "runs": []}
        data.setdefault("processed", {})
        data.setdefault("runs", [])
        return data
    except Exception:
        return {"version": 1, "processed": {}, "runs": []}


def save_state(path: str, state: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    runs = state.get("runs") or []
    if isinstance(runs, list):
        state["runs"] = runs[-60:]
    with p.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


# =========================
# Fetching
# =========================

def make_session(timeout: int = 20, user_agent: str = DEFAULT_USER_AGENT) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/xml;q=0.8,*/*;q=0.7",
        "Accept-Language": "id,en;q=0.8",
    })
    session.request_timeout = timeout  # type: ignore[attr-defined]
    return session


def fetch_text(session: requests.Session, url: str, timeout: Optional[int] = None) -> str:
    timeout_value = int(timeout or getattr(session, "request_timeout", 20) or 20)
    resp = session.get(url, timeout=timeout_value)
    resp.raise_for_status()
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def url_allowed_by_patterns(url: str, source: SourceConfig) -> bool:
    target = str(url or "")
    if source.include_patterns:
        if not any(re.search(pattern, target, flags=re.I) for pattern in source.include_patterns):
            return False
    if source.exclude_patterns:
        if any(re.search(pattern, target, flags=re.I) for pattern in source.exclude_patterns):
            return False
    return True


def same_domain(url: str, base_url: str) -> bool:
    try:
        return urlparse(url).netloc.lower().replace("www.", "") == urlparse(base_url).netloc.lower().replace("www.", "")
    except Exception:
        return False


# =========================
# Scraping strategies
# =========================

def scrape_rss(session: requests.Session, source: SourceConfig, limit: int) -> List[Dict[str, str]]:
    xml_text = fetch_text(session, source.url)
    items = parse_feed_items(xml_text, base_url=source.url)
    articles: List[Dict[str, str]] = []
    for item in items[:limit]:
        url = canonical_url(item.get("url", ""), source.url)
        if url and not url_allowed_by_patterns(url, source):
            continue
        article_text = item.get("summary", "")
        article_title = item.get("title", "")
        if source.fetch_article and url:
            try:
                time.sleep(source.delay_seconds)
                page = fetch_text(session, url)
                extracted_title, body = extract_html_text(page)
                if body and len(body) >= max(120, len(article_text)):
                    article_text = body
                if extracted_title and len(extracted_title) > 6:
                    article_title = extracted_title
            except Exception as exc:
                article_text = (article_text or "") + f"\n\n[Catatan: gagal mengambil halaman artikel penuh: {exc}]"
        articles.append({
            "title": article_title or item.get("title") or "Artikel tanpa judul",
            "url": url or source.url,
            "published": item.get("published", ""),
            "summary": item.get("summary", ""),
            "content": article_text,
        })
    return articles


def scrape_html(session: requests.Session, source: SourceConfig) -> List[Dict[str, str]]:
    page = fetch_text(session, source.url)
    title, body = extract_html_text(page)
    return [{
        "title": title or source.name,
        "url": source.url,
        "published": "",
        "summary": "",
        "content": body,
    }]


def scrape_html_index(session: requests.Session, source: SourceConfig, limit: int) -> List[Dict[str, str]]:
    page = fetch_text(session, source.url)
    links = extract_links(page, source.url)
    candidates = []
    for link in links:
        url = canonical_url(link.get("url", ""), source.url)
        if not url or not same_domain(url, source.url):
            continue
        if not url_allowed_by_patterns(url, source):
            continue
        candidates.append({"url": url, "title": link.get("title") or url})
    articles: List[Dict[str, str]] = []
    seen = set()
    for candidate in candidates:
        if len(articles) >= limit:
            break
        url = candidate["url"]
        if url in seen:
            continue
        seen.add(url)
        try:
            time.sleep(source.delay_seconds)
            detail = fetch_text(session, url)
            title, body = extract_html_text(detail)
            articles.append({
                "title": title or candidate.get("title") or url,
                "url": url,
                "published": "",
                "summary": "",
                "content": body,
            })
        except Exception as exc:
            articles.append({
                "title": candidate.get("title") or url,
                "url": url,
                "published": "",
                "summary": "",
                "content": f"[Gagal mengambil halaman: {exc}]",
            })
    return articles


def scrape_sitemap(session: requests.Session, source: SourceConfig, limit: int) -> List[Dict[str, str]]:
    xml_text = fetch_text(session, source.url)
    root = ET.fromstring(xml_text.encode("utf-8", "ignore"))
    urls: List[str] = []
    for node in root.iter():
        if strip_namespace(node.tag) == "loc":
            url = canonical_url("".join(node.itertext()), source.url)
            if url and url_allowed_by_patterns(url, source):
                urls.append(url)
    articles: List[Dict[str, str]] = []
    for url in urls[:limit]:
        try:
            time.sleep(source.delay_seconds)
            page = fetch_text(session, url)
            title, body = extract_html_text(page)
            articles.append({"title": title or url, "url": url, "published": "", "summary": "", "content": body})
        except Exception as exc:
            articles.append({"title": url, "url": url, "published": "", "summary": "", "content": f"[Gagal mengambil halaman: {exc}]"})
    return articles

def scrape_static(session: requests.Session, source: SourceConfig) -> List[Dict[str, str]]:
    """Return curated static knowledge embedded in kb_sources.json.

    Useful for reference notes such as journal Q-level verification guidance that
    should enter the KB even when the ranking site blocks automated scraping.
    The session argument is kept for a consistent scraping function signature.
    """
    content = clean_spaces(source.static_content)
    if not content:
        return []
    return [{
        "title": source.static_title or source.name,
        "url": source.url,
        "published": "",
        "summary": "",
        "content": content,
    }]


def scrape_source(session: requests.Session, source: SourceConfig, max_items_override: Optional[int] = None) -> List[Dict[str, str]]:
    limit = max(1, int(max_items_override or source.max_items or 5))
    if source.type in {"rss", "feed", "atom"}:
        return scrape_rss(session, source, limit=limit)
    if source.type in {"html", "page"}:
        return scrape_html(session, source)[:limit]
    if source.type in {"html_index", "index", "list"}:
        return scrape_html_index(session, source, limit=limit)
    if source.type in {"sitemap", "xml_sitemap"}:
        return scrape_sitemap(session, source, limit=limit)
    if source.type in {"static", "note", "curated"}:
        return scrape_static(session, source)[:limit]
    raise ValueError(f"Tipe sumber tidak dikenali: {source.type}")


# =========================
# KB ingestion
# =========================

def build_document_text(article: Dict[str, str], source: SourceConfig) -> str:
    title = clean_spaces(article.get("title") or "Artikel tanpa judul")
    url = clean_spaces(article.get("url") or source.url)
    published = clean_spaces(article.get("published") or "")
    summary = clean_spaces(article.get("summary") or "")
    content = clean_spaces(article.get("content") or "")
    sections = [
        f"Judul: {title}",
        f"Sumber: {source.name}",
        f"URL: {url}",
        f"Tanggal sumber: {published or '-'}",
        f"Tanggal masuk KB: {now_wib_text()}",
    ]
    if summary:
        sections.extend(["", "Ringkasan dari sumber:", summary])
    sections.extend(["", "Konten:", content])
    return truncate("\n".join(sections), source.max_chars)


def processed_key(article: Dict[str, str], source: SourceConfig) -> str:
    url = canonical_url(article.get("url", "") or source.url)
    if url:
        return stable_hash(source.name + "|" + url)
    return stable_hash(source.name + "|" + article.get("title", "") + "|" + article.get("published", ""))


def run_daily_kb_update(
    db_path: str = DEFAULT_DB_PATH,
    sources_path: str = DEFAULT_SOURCES_FILE,
    state_path: str = DEFAULT_STATE_FILE,
    max_items_per_source: Optional[int] = None,
    timeout: int = 20,
    dry_run: bool = False,
    force: bool = False,
    user_agent: str = DEFAULT_USER_AGENT,
) -> Dict[str, Any]:
    """Run the daily update and return a serializable report."""
    raw_sources = load_sources(sources_path)
    sources = [normalize_source(item) for item in raw_sources]
    enabled_sources = [s for s in sources if s.enabled and s.url]
    state = load_state(state_path)
    processed: Dict[str, Any] = state.setdefault("processed", {})
    session = make_session(timeout=timeout, user_agent=user_agent)
    store = None if dry_run else get_power_store(db_path)

    report: Dict[str, Any] = {
        "started_at": now_iso(),
        "started_at_wib": now_wib_text(),
        "db_path": db_path,
        "sources_path": sources_path,
        "dry_run": dry_run,
        "force": force,
        "sources_total": len(raw_sources),
        "sources_enabled": len(enabled_sources),
        "added_documents": 0,
        "added_chunks": 0,
        "skipped_existing": 0,
        "skipped_short": 0,
        "errors": 0,
        "items": [],
    }

    for source in enabled_sources:
        try:
            articles = scrape_source(session, source, max_items_override=max_items_per_source)
        except Exception as exc:
            report["errors"] += 1
            report["items"].append({
                "source": source.name,
                "status": "error_source",
                "title": source.name,
                "url": source.url,
                "message": str(exc)[:500],
            })
            continue

        for article in articles:
            key = processed_key(article, source)
            title = clean_spaces(article.get("title") or "Artikel tanpa judul")[:240]
            url = canonical_url(article.get("url", "") or source.url)
            content = clean_spaces(article.get("content") or article.get("summary") or "")
            if len(content) < source.min_chars:
                report["skipped_short"] += 1
                report["items"].append({
                    "source": source.name,
                    "status": "skipped_short",
                    "title": title,
                    "url": url,
                    "chars": len(content),
                })
                continue
            if key in processed and not force:
                report["skipped_existing"] += 1
                report["items"].append({
                    "source": source.name,
                    "status": "skipped_existing",
                    "title": title,
                    "url": url,
                    "chars": len(content),
                })
                continue

            document_text = build_document_text(article, source)
            doc_title = f"{source.name} — {title}"[:240]
            metadata = {
                "source_name": source.name,
                "source_type": source.type,
                "source_url": source.url,
                "article_url": url,
                "published": article.get("published", ""),
                "scraped_at": now_iso(),
                "scraped_at_wib": now_wib_text(),
                "processed_key": key,
                "auto_update": True,
                "source_quality": estimate_source_quality_from_config(source),
                "source_domain": source_domain(url or source.url),
                "auto_summary": summarize_for_kb(content, max_sentences=source.summary_sentences),
                "keywords": extract_keywords(" ".join([title, content]), limit=14),
            }

            if dry_run:
                doc_id, chunks = 0, 0
            else:
                assert store is not None
                doc_id, chunks = store.add_document(
                    title=doc_title,
                    text=document_text,
                    source=url or source.url,
                    collection=source.collection,
                    tags=source.tags,
                    metadata=metadata,
                    replace_existing=False,
                    pinned=source.pinned,
                    source_quality=estimate_source_quality_from_config(source),
                    summary=summarize_for_kb(content, max_sentences=source.summary_sentences),
                )

            if chunks:
                report["added_documents"] += 1
                report["added_chunks"] += int(chunks)
                processed[key] = {
                    "title": doc_title,
                    "url": url,
                    "source": source.name,
                    "doc_id": doc_id,
                    "chunks": chunks,
                    "created_at": now_iso(),
                }
                status = "added"
            elif dry_run:
                status = "dry_run"
            else:
                report["skipped_existing"] += 1
                status = "skipped_duplicate_hash"
                processed[key] = {
                    "title": doc_title,
                    "url": url,
                    "source": source.name,
                    "doc_id": doc_id,
                    "chunks": chunks,
                    "created_at": now_iso(),
                }

            report["items"].append({
                "source": source.name,
                "status": status,
                "title": doc_title,
                "url": url,
                "doc_id": doc_id,
                "chunks": chunks,
                "chars": len(document_text),
            })

    report["finished_at"] = now_iso()
    report["finished_at_wib"] = now_wib_text()
    state.setdefault("runs", []).append({
        "finished_at": report["finished_at"],
        "added_documents": report["added_documents"],
        "added_chunks": report["added_chunks"],
        "errors": report["errors"],
        "dry_run": dry_run,
    })
    if not dry_run:
        save_state(state_path, state)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Update Adioranye SQLite knowledge base from RSS/HTML sources.")
    parser.add_argument("--db", default=os.getenv("POWER_DB_PATH", DEFAULT_DB_PATH), help="Path database SQLite PowerStore.")
    parser.add_argument("--sources", default=os.getenv("KB_SCRAPER_SOURCES_FILE", DEFAULT_SOURCES_FILE), help="Path file sumber JSON.")
    parser.add_argument("--state", default=os.getenv("KB_SCRAPER_STATE_FILE", DEFAULT_STATE_FILE), help="Path state deduplikasi scraper.")
    parser.add_argument("--max-items", type=int, default=int(os.getenv("KB_SCRAPER_MAX_ITEMS_PER_SOURCE", "0") or 0), help="Override jumlah item per sumber.")
    parser.add_argument("--timeout", type=int, default=int(os.getenv("KB_SCRAPER_TIMEOUT", "20") or 20), help="HTTP timeout detik.")
    parser.add_argument("--dry-run", action="store_true", help="Ambil dan bersihkan data tanpa menyimpan ke database.")
    parser.add_argument("--force", action="store_true", help="Abaikan state URL dan coba ingest ulang.")
    args = parser.parse_args(argv)

    report = run_daily_kb_update(
        db_path=args.db,
        sources_path=args.sources,
        state_path=args.state,
        max_items_per_source=args.max_items or None,
        timeout=args.timeout,
        dry_run=bool(args.dry_run),
        force=bool(args.force),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # Non-zero only if every enabled source failed. One or two bad feeds should not break deploy.
    if report.get("sources_enabled", 0) > 0 and report.get("errors", 0) >= report.get("sources_enabled", 0):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
