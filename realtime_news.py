"""On-demand Indonesian news lookup via public Google News RSS."""

from __future__ import annotations

import html
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List
import requests

RSS_URL = "https://news.google.com/rss/search"
CACHE_TTL_SECONDS = 60
MAX_ITEMS = 5
_cache: Dict[str, tuple[float, List[Dict[str, str]]]] = {}
_cache_lock = threading.Lock()


def _text(node: ET.Element, name: str) -> str:
    for child in list(node):
        if child.tag.rsplit("}", 1)[-1].lower() == name.lower():
            return " ".join("".join(child.itertext()).split())
    return ""


def _published(value: str) -> str:
    try:
        date = parsedate_to_datetime(value).astimezone(timezone.utc)
        return date.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return value[:80]


def _parse(xml: str) -> List[Dict[str, str]]:
    root = ET.fromstring(xml)
    items: List[Dict[str, str]] = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].lower() != "item":
            continue
        title = html.unescape(_text(node, "title"))
        link = _text(node, "link")
        source = _text(node, "source") or "Google News"
        date = _published(_text(node, "pubDate"))
        if title and link:
            items.append({"title": title, "url": link, "source": source, "published": date})
        if len(items) >= MAX_ITEMS:
            break
    return items


def news_query(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"\b(berita|news|terbaru|terkini|hari ini|real[ -]?time|realtime)\b", " ", value, flags=re.I)
    value = re.sub(r"\b(tentang|mengenai|soal|update)\b", " ", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip() or "Indonesia"


def fetch_news(query: str, timeout: int = 8) -> List[Dict[str, str]]:
    key = query.strip().lower()
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
    params = {"q": query, "hl": "id", "gl": "ID", "ceid": "ID:id"}
    response = requests.get(RSS_URL, params=params, timeout=timeout, headers={"User-Agent": "AdioranyeAI/1.0"})
    response.raise_for_status()
    items = _parse(response.text)
    with _cache_lock:
        _cache[key] = (now, items)
    return items


def build_news_answer(user_text: str, timeout: int = 8) -> tuple[str, Dict[str, Any]]:
    query = news_query(user_text)
    items = fetch_news(query, timeout=timeout)
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if not items:
        return f"Belum ada hasil berita untuk: {query}\n\nDiambil: {fetched_at}", {"realtime_news": True, "news_query": query}
    lines = [f"Berita terbaru untuk: {query}", ""]
    for index, item in enumerate(items, 1):
        lines.extend([f"{index}. {item['title']}", f"   {item['source']} | {item['published']}", f"   {item['url']}", ""])
    lines.append(f"Diambil: {fetched_at} | Sumber agregasi: Google News RSS")
    return "\n".join(lines).strip(), {"realtime_news": True, "news_query": query, "news_count": len(items)}


if __name__ == "__main__":
    print(build_news_answer("berita terbaru Indonesia")[0])
    # ponytail: lookup on demand only; add watchlist persistence and scheduler for push alerts.
  