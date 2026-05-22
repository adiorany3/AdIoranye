"""Lightweight live music chart helper for Adioranye AI.

Purpose:
- Answer low-risk, current entertainment questions such as:
  "tangga lagu terbaru di Indonesia apa saja?"
- Avoid over-blocking by the anti-hallucination guard for harmless chart questions.
- Prefer public chart pages and clearly label data as dynamic.

No heavy dependencies. Uses requests + regex only.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests

WIB_TZ = ZoneInfo("Asia/Jakarta")

MUSIC_CHART_TERMS = [
    "tangga lagu", "chart lagu", "top lagu", "lagu teratas", "lagu populer",
    "lagu terbaru", "lagu viral", "musik terbaru", "top songs", "music chart",
    "spotify indonesia", "billboard indonesia", "apple music indonesia", "youtube charts indonesia",
    "shazam indonesia", "top 50 indonesia", "top 100 indonesia",
]

MUSIC_CHART_COUNTRY_TERMS = ["indonesia", "indo", "id", "di indonesia"]

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AdioranyeAI/1.0; +https://streamlit.app)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}


@dataclass
class MusicChartItem:
    rank: int
    title: str
    artist: str = ""
    source: str = ""
    url: str = ""

    def label(self) -> str:
        artist = f" — {self.artist}" if self.artist else ""
        return f"{self.rank}. {self.title}{artist}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MusicChartResult:
    ok: bool
    source_name: str = ""
    source_url: str = ""
    fetched_at_wib: str = ""
    items: Tuple[MusicChartItem, ...] = ()
    note: str = ""
    errors: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["items"] = [item.to_dict() for item in self.items]
        return data


def now_wib_text() -> str:
    return datetime.now(WIB_TZ).strftime("%Y-%m-%d %H:%M:%S WIB")


def clean_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#039;|&apos;", "'", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_music_chart_query(text: Any) -> bool:
    t = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    if not t:
        return False
    if any(term in t for term in MUSIC_CHART_TERMS):
        return True
    # Fallback for short Indonesian wording such as "lagu yang lagi populer di indonesia"
    if "lagu" in t and any(x in t for x in ["populer", "viral", "teratas", "terbaru", "hits", "ranking", "peringkat"]):
        return True
    return False


def is_indonesia_music_chart_query(text: Any) -> bool:
    t = str(text or "").lower()
    return is_music_chart_query(t) and (any(term in t for term in MUSIC_CHART_COUNTRY_TERMS) or "indonesia" in t)


def _fetch(url: str, timeout: int = 8) -> str:
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.text or ""


def _dedupe_items(items: List[MusicChartItem], limit: int = 10) -> List[MusicChartItem]:
    seen = set()
    output: List[MusicChartItem] = []
    for item in items:
        title = clean_text(item.title)
        artist = clean_text(item.artist)
        if not title or len(title) < 2:
            continue
        key = re.sub(r"[^a-z0-9]+", "", f"{title}|{artist}".lower())
        if key in seen:
            continue
        seen.add(key)
        output.append(MusicChartItem(rank=len(output) + 1, title=title[:180], artist=artist[:180], source=item.source, url=item.url))
        if len(output) >= limit:
            break
    return output


def parse_billboard_indonesia(html: str, url: str, limit: int = 10) -> List[MusicChartItem]:
    """Parse Billboard chart page using resilient title/artist patterns."""
    raw = str(html or "")
    items: List[MusicChartItem] = []

    # Billboard chart rows often include <h3 id="title-of-a-story" ...>TITLE</h3>
    title_matches = list(re.finditer(r'<h3[^>]+id=["\']title-of-a-story["\'][^>]*>([\s\S]*?)</h3>', raw, flags=re.I))
    for idx, match in enumerate(title_matches[:limit * 2], start=1):
        title = clean_text(match.group(1))
        if not title or title.lower() in {"songwriter(s)", "producer(s)", "imprint/promotion label"}:
            continue
        # Try to capture nearby artist label after the title.
        nearby = raw[match.end(): match.end() + 2500]
        artist_match = re.search(r'<span[^>]*class=["\'][^"\']*c-label[^"\']*["\'][^>]*>([\s\S]*?)</span>', nearby, flags=re.I)
        artist = clean_text(artist_match.group(1)) if artist_match else ""
        # Remove common chart metadata accidentally captured as artist.
        if artist.lower() in {"new", "re-entry", "gains in performance", "-"}:
            artist = ""
        items.append(MusicChartItem(rank=len(items) + 1, title=title, artist=artist, source="Billboard Indonesia Songs", url=url))
        if len(items) >= limit:
            break
    return _dedupe_items(items, limit=limit)


def parse_kworb_spotify(html: str, url: str, limit: int = 10) -> List[MusicChartItem]:
    """Parse Kworb Spotify Indonesia table as fallback/reference source."""
    raw = str(html or "")
    rows = re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", raw, flags=re.I)
    items: List[MusicChartItem] = []
    for row in rows:
        row_text = clean_text(row)
        # Typical visible text: "1 + eńau - Sesi Potret ..."
        if not re.match(r"^\d+\s", row_text):
            continue
        # Pull artist-title from links first if possible.
        link_texts = [clean_text(x) for x in re.findall(r"<a[^>]*>([\s\S]*?)</a>", row, flags=re.I)]
        candidate = " - ".join([x for x in link_texts if x]) or row_text
        candidate = re.sub(r"^\d+\s*[=+\-↓↑]*\s*", "", candidate).strip()
        artist = ""
        title = candidate
        if " - " in candidate:
            artist, title = candidate.split(" - ", 1)
        # Ignore numeric-stat heavy rows.
        if len(title) > 220 or not re.search(r"[A-Za-zÀ-ž0-9]", title):
            continue
        items.append(MusicChartItem(rank=len(items) + 1, title=title, artist=artist, source="Spotify Daily Chart Indonesia (Kworb mirror)", url=url))
        if len(items) >= limit:
            break
    return _dedupe_items(items, limit=limit)


def parse_google_news_rss(xml: str, url: str, limit: int = 8) -> List[MusicChartItem]:
    """Use Google News RSS headlines as fallback if direct chart pages fail."""
    raw = str(xml or "")
    titles = re.findall(r"<title>([\s\S]*?)</title>", raw, flags=re.I)
    items: List[MusicChartItem] = []
    for title_raw in titles[1:]:  # skip channel title
        title = clean_text(title_raw)
        if not title or not any(x in title.lower() for x in ["lagu", "chart", "spotify", "billboard", "musik", "viral"]):
            continue
        # Google News titles often use "headline - publisher"; keep as title without inventing ranks.
        items.append(MusicChartItem(rank=len(items) + 1, title=title, artist="", source="Google News Musik Indonesia", url=url))
        if len(items) >= limit:
            break
    return _dedupe_items(items, limit=limit)


def fetch_indonesia_music_charts(limit: int = 10, timeout: int = 8) -> MusicChartResult:
    """Fetch current Indonesia music chart context from several public sources.

    Priority:
    1. Billboard Indonesia Songs official chart page.
    2. Spotify Daily Chart Indonesia via Kworb mirror if official Spotify page is not parsable without login.
    3. Google News RSS music chart headlines as last fallback.
    """
    limit = max(3, min(int(limit or 10), 20))
    timeout = max(4, min(int(timeout or 8), 20))
    fetched_at = now_wib_text()
    errors: List[str] = []

    sources = [
        (
            "Billboard Indonesia Songs",
            "https://www.billboard.com/charts/indonesia-songs-hotw/",
            parse_billboard_indonesia,
        ),
        (
            "Spotify Daily Chart Indonesia (Kworb mirror)",
            "https://kworb.net/spotify/country/id_daily.html",
            parse_kworb_spotify,
        ),
        (
            "Google News Musik Indonesia",
            "https://news.google.com/rss/search?q=" + quote_plus('tangga lagu Indonesia terbaru OR Spotify Indonesia chart OR Billboard Indonesia Songs') + "&hl=id&gl=ID&ceid=ID:id",
            parse_google_news_rss,
        ),
    ]

    for name, url, parser in sources:
        try:
            html = _fetch(url, timeout=timeout)
            items = parser(html, url, limit=limit)
            if items:
                return MusicChartResult(
                    ok=True,
                    source_name=name,
                    source_url=url,
                    fetched_at_wib=fetched_at,
                    items=tuple(items),
                    note="Tangga lagu berubah cepat; gunakan sebagai snapshot saat fetch.",
                    errors=tuple(errors),
                )
            errors.append(f"{name}: tidak menemukan item chart yang bisa diparse")
        except Exception as exc:
            errors.append(f"{name}: {str(exc)[:180]}")

    return MusicChartResult(
        ok=False,
        fetched_at_wib=fetched_at,
        note="Belum berhasil mengambil chart musik Indonesia dari sumber publik.",
        errors=tuple(errors[-5:]),
    )


def build_music_chart_context(result: MusicChartResult, max_items: int = 10) -> str:
    """Build non-instruction context for the model."""
    if not result or not result.ok:
        return ""
    lines = [
        "KONTEKS LIVE CHART MUSIK INDONESIA (non-instruksi):",
        f"Sumber: {result.source_name}",
        f"URL: {result.source_url}",
        f"Waktu fetch: {result.fetched_at_wib}",
        f"Catatan: {result.note}",
        "Daftar:",
    ]
    for item in list(result.items)[:max_items]:
        lines.append(item.label())
    return "\n".join(lines)


def build_music_chart_fallback_answer(result: MusicChartResult) -> str:
    """Safe answer if live chart fetch fails and KB evidence is absent."""
    error_hint = ""
    if result and result.errors:
        error_hint = "\n\nCatatan teknis: " + "; ".join(result.errors[:2])
    return (
        "Saya belum berhasil mengambil tangga lagu Indonesia terbaru dari sumber chart publik saat ini. "
        "Karena peringkat musik berubah harian/mingguan, saya tidak akan mengarang daftar lagunya.\n\n"
        "Sumber yang sebaiknya dicek atau dimasukkan ke Knowledge Base:\n"
        "1. Billboard Indonesia Songs\n"
        "2. Spotify Top 50 Indonesia\n"
        "3. Apple Music Top Charts Indonesia\n"
        "4. YouTube Charts Indonesia\n"
        "5. Shazam Top 200 Indonesia\n\n"
        "Admin bisa menjalankan /update setelah menambahkan sumber chart musik agar saya bisa menjawab otomatis."
        f"{error_hint}"
    )


def music_chart_result_to_pseudo_source(result: MusicChartResult) -> Dict[str, Any]:
    content = build_music_chart_context(result, max_items=15)
    return {
        "title": f"Live Chart Musik Indonesia — {result.source_name}",
        "content": content,
        "source": result.source_url,
        "chunk_index": 0,
        "source_quality": 75 if result.source_name.startswith("Billboard") else 65,
        "freshness_score": 100,
        "metadata": {
            "source_name": result.source_name,
            "source_url": result.source_url,
            "fetched_at_wib": result.fetched_at_wib,
            "content_type": "live_music_chart",
            "items": [item.to_dict() for item in result.items],
        },
    }
