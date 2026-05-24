
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", "ignore")).hexdigest()


def _clean_text(text: str) -> str:
    text = html.unescape(str(text or ""))
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_title_from_html(raw_html: str, fallback: str = "") -> str:
    match = re.search(r"<title[^>]*>([\s\S]*?)</title>", raw_html or "", flags=re.I)
    if match:
        return _clean_text(match.group(1))[:240]
    h1 = re.search(r"<h1[^>]*>([\s\S]*?)</h1>", raw_html or "", flags=re.I)
    if h1:
        return _clean_text(h1.group(1))[:240]
    return str(fallback or "Tanpa judul")[:240]


def _split_heading_chunks(text: str, max_chars: int = 1800, overlap: int = 180) -> List[Dict[str, Any]]:
    """Chunk berbasis heading bila ada, fallback paragraph windows."""
    clean = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    if not clean:
        return []

    lines = clean.splitlines()
    sections: List[Tuple[str, List[str]]] = []
    current_heading = "Ringkasan"
    current_lines: List[str] = []

    heading_pattern = re.compile(
        r"^\s{0,3}(#{1,6}\s+.+|[A-Z0-9][A-Za-z0-9\s:;,.()/%+\-]{0,100})\s*$"
    )

    for line in lines:
        raw = line.strip()
        if not raw:
            current_lines.append("")
            continue

        looks_heading = bool(heading_pattern.match(raw)) and len(raw) <= 120

        if looks_heading and len(" ".join(current_lines).strip()) > 250:
            sections.append((current_heading, current_lines))
            current_heading = raw.lstrip("#").strip()
            current_lines = []
        elif looks_heading and not current_lines:
            current_heading = raw.lstrip("#").strip()
        else:
            current_lines.append(raw)

    if current_lines:
        sections.append((current_heading, current_lines))

    if not sections:
        sections = [("Isi", clean.split("\n"))]

    chunks: List[Dict[str, Any]] = []
    for heading, section_lines in sections:
        section_text = re.sub(r"\n{3,}", "\n\n", "\n".join(section_lines)).strip()
        if not section_text:
            continue

        if len(section_text) <= max_chars:
            chunks.append(
                {
                    "heading": heading[:240],
                    "content": section_text,
                    "char_count": len(section_text),
                }
            )
            continue

        start = 0
        while start < len(section_text):
            part = section_text[start:start + max_chars].strip()
            if part:
                chunks.append(
                    {
                        "heading": heading[:240],
                        "content": part,
                        "char_count": len(part),
                    }
                )
            if start + max_chars >= len(section_text):
                break
            start = max(0, start + max_chars - overlap)

    return chunks


def _summarize_text(text: str, max_chars: int = 900) -> str:
    clean = _clean_text(text)
    if len(clean) <= max_chars:
        return clean
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    summary = ""
    for sentence in sentences:
        if len(summary) + len(sentence) + 1 > max_chars:
            break
        summary += (sentence + " ")
    return (summary.strip() or clean[:max_chars]).strip()


def init_kb_manager_schema(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS kb_documents_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT,
                source_type TEXT,
                collection TEXT,
                tags TEXT,
                content_hash TEXT NOT NULL,
                summary TEXT,
                status TEXT DEFAULT 'active',
                version INTEGER DEFAULT 1,
                confidence TEXT DEFAULT 'medium',
                freshness TEXT DEFAULT 'recent',
                created_at TEXT,
                updated_at TEXT,
                metadata_json TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_kb_documents_v2_source_hash
            ON kb_documents_v2(source_id, content_hash)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS kb_chunks_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER,
                source_id TEXT,
                heading TEXT,
                chunk_index INTEGER,
                content TEXT,
                char_count INTEGER,
                content_hash TEXT,
                status TEXT DEFAULT 'active',
                created_at TEXT,
                FOREIGN KEY(document_id) REFERENCES kb_documents_v2(id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS kb_summaries_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER,
                source_id TEXT,
                title TEXT,
                summary TEXT,
                key_points_json TEXT,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY(document_id) REFERENCES kb_documents_v2(id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS kb_update_log_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT,
                finished_at TEXT,
                source_id TEXT,
                action TEXT,
                status TEXT,
                message TEXT,
                documents_added INTEGER DEFAULT 0,
                documents_updated INTEGER DEFAULT 0,
                documents_skipped INTEGER DEFAULT 0,
                chunks_added INTEGER DEFAULT 0,
                metadata_json TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS live_cache_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT,
                query_hash TEXT,
                provider TEXT,
                answer TEXT,
                sources_json TEXT,
                expires_at TEXT,
                created_at TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def kb_manager_overview(db_path: str) -> Dict[str, Any]:
    init_kb_manager_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        def count(table: str, where: str = "") -> int:
            sql = f"SELECT COUNT(*) FROM {table} {where}"
            cur.execute(sql)
            return int(cur.fetchone()[0] or 0)

        cur.execute(
            "SELECT started_at, source_id, action, status, message FROM kb_update_log_v2 ORDER BY id DESC LIMIT 8"
        )
        logs = [
            {
                "waktu": row[0],
                "source_id": row[1],
                "aksi": row[2],
                "status": row[3],
                "pesan": row[4],
            }
            for row in cur.fetchall()
        ]
        return {
            "documents_active": count("kb_documents_v2", "WHERE status='active'"),
            "documents_archived": count("kb_documents_v2", "WHERE status='archived'"),
            "chunks_active": count("kb_chunks_v2", "WHERE status='active'"),
            "summaries": count("kb_summaries_v2"),
            "live_cache": count("live_cache_v2"),
            "recent_logs": logs,
        }
    finally:
        conn.close()


def load_kb_sources(sources_path: str) -> List[Dict[str, Any]]:
    path = str(sources_path or "kb_sources.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("sources", [])
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def ensure_kb_sources_file(sources_path: str, sources: List[Dict[str, Any]]) -> bool:
    path = str(sources_path or "kb_sources.json")
    if os.path.exists(path):
        return False
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"sources": sources}, f, ensure_ascii=False, indent=2)
    return True


def _fetch_url(url: str, timeout: int = 20) -> Tuple[str, str]:
    headers = {
        "User-Agent": "AdioranyeKBUpdater/1.0 (+https://streamlit.io)",
        "Accept": "text/html,application/rss+xml,application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
    }
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.text, response.url


def _parse_feed_items(raw_text: str, base_url: str, limit: int = 5) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(raw_text.encode("utf-8", "ignore"))
    except Exception:
        return items

    # RSS item
    for item in root.findall(".//item"):
        title = _clean_text(item.findtext("title") or "")
        link = _clean_text(item.findtext("link") or "")
        description = _clean_text(item.findtext("description") or "")
        pub_date = _clean_text(item.findtext("pubDate") or "")
        if link:
            link = urljoin(base_url, link)
        items.append(
            {
                "title": title or link or "Feed item",
                "url": link or base_url,
                "content": description,
                "published_at": pub_date,
            }
        )
        if len(items) >= limit:
            return items

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//atom:entry", ns) + root.findall(".//entry"):
        title = _clean_text(entry.findtext("atom:title", default="", namespaces=ns) or entry.findtext("title") or "")
        summary = _clean_text(entry.findtext("atom:summary", default="", namespaces=ns) or entry.findtext("summary") or entry.findtext("content") or "")
        link = ""
        for link_node in entry.findall("atom:link", ns) + entry.findall("link"):
            href = link_node.attrib.get("href")
            if href:
                link = href
                break
        if link:
            link = urljoin(base_url, link)
        items.append(
            {
                "title": title or link or "Feed entry",
                "url": link or base_url,
                "content": summary,
                "published_at": _clean_text(entry.findtext("atom:updated", default="", namespaces=ns) or entry.findtext("updated") or ""),
            }
        )
        if len(items) >= limit:
            break

    return items


def _source_item_to_document(
    source: Dict[str, Any],
    raw_text: str,
    final_url: str,
    item: Optional[Dict[str, Any]] = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    if item and item.get("url"):
        item_url = str(item.get("url"))
        try:
            item_raw, item_final_url = _fetch_url(item_url, timeout=timeout)
            title = _extract_title_from_html(item_raw, fallback=item.get("title") or item_url)
            content = _clean_text(item_raw)
            final = item_final_url
        except Exception:
            title = str(item.get("title") or item_url)
            content = _clean_text(item.get("content") or "")
            final = item_url
    else:
        title = _extract_title_from_html(raw_text, fallback=source.get("name") or final_url)
        content = _clean_text(raw_text)
        final = final_url

    return {
        "source_id": str(source.get("id") or source.get("name") or final).strip(),
        "title": title[:240],
        "url": final,
        "text": content,
        "source_type": str(source.get("source_type") or source.get("type") or "html"),
        "collection": str(source.get("collection") or "Auto Update"),
        "tags": ",".join(source.get("tags", [])) if isinstance(source.get("tags"), list) else str(source.get("tags") or ""),
        "confidence": str(source.get("confidence") or "medium"),
        "freshness": str(source.get("freshness") or "recent"),
        "metadata": {
            "source_name": source.get("name"),
            "source_url": source.get("url"),
            "published_at": (item or {}).get("published_at", ""),
            "ingested_by": "kb_manager_v2",
        },
    }


def _archive_previous_for_source(conn: sqlite3.Connection, source_id: str, url: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM kb_documents_v2 WHERE source_id=? AND url=? AND status='active'",
        (source_id, url),
    )
    ids = [int(row[0]) for row in cur.fetchall()]
    if not ids:
        return
    cur.execute(
        "UPDATE kb_documents_v2 SET status='archived', updated_at=? WHERE source_id=? AND url=? AND status='active'",
        (_utc_now(), source_id, url),
    )
    for doc_id in ids:
        cur.execute(
            "UPDATE kb_chunks_v2 SET status='archived' WHERE document_id=? AND status='active'",
            (doc_id,),
        )


def _insert_document_v2(
    conn: sqlite3.Connection,
    document: Dict[str, Any],
    max_chunk_chars: int = 1800,
) -> Tuple[int, int]:
    now = _utc_now()
    text = str(document.get("text") or "").strip()
    content_hash = _sha256(text)
    source_id = str(document.get("source_id") or document.get("url") or document.get("title"))
    url = str(document.get("url") or "")

    _archive_previous_for_source(conn, source_id, url)

    cur = conn.cursor()
    summary = _summarize_text(text)
    cur.execute(
        """
        INSERT INTO kb_documents_v2 (
            source_id,title,url,source_type,collection,tags,content_hash,summary,status,
            version,confidence,freshness,created_at,updated_at,metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            source_id,
            document.get("title") or "Tanpa judul",
            url,
            document.get("source_type") or "html",
            document.get("collection") or "Auto Update",
            document.get("tags") or "",
            content_hash,
            summary,
            "active",
            1,
            document.get("confidence") or "medium",
            document.get("freshness") or "recent",
            now,
            now,
            json.dumps(document.get("metadata") or {}, ensure_ascii=False),
        ),
    )
    doc_id = int(cur.lastrowid)
    chunks = _split_heading_chunks(text, max_chars=max_chunk_chars)
    for idx, chunk in enumerate(chunks):
        content = str(chunk.get("content") or "")
        cur.execute(
            """
            INSERT INTO kb_chunks_v2 (
                document_id,source_id,heading,chunk_index,content,char_count,content_hash,status,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                doc_id,
                source_id,
                chunk.get("heading") or "",
                idx,
                content,
                len(content),
                _sha256(content),
                "active",
                now,
            ),
        )

    key_points = []
    for sentence in re.split(r"(?<=[.!?])\s+", summary)[:5]:
        if sentence.strip():
            key_points.append(sentence.strip())

    cur.execute(
        """
        INSERT INTO kb_summaries_v2 (
            document_id,source_id,title,summary,key_points_json,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            doc_id,
            source_id,
            document.get("title") or "Tanpa judul",
            summary,
            json.dumps(key_points, ensure_ascii=False),
            now,
            now,
        ),
    )
    return doc_id, len(chunks)


def advanced_incremental_kb_update(
    db_path: str,
    sources_path: str,
    power_store: Any = None,
    max_items_per_source: int = 3,
    timeout: int = 20,
    dry_run: bool = False,
    force: bool = False,
    max_chunk_chars: int = 1800,
) -> Dict[str, Any]:
    """Incremental KB update dengan hash, arsip, summary, dan audit log.

    Jika `power_store` diberikan, dokumen baru/berubah juga dimirror ke KB lama
    lewat `power_store.add_document()` agar langsung bisa dipakai oleh RAG existing.
    """
    init_kb_manager_schema(db_path)
    started = _utc_now()
    sources = load_kb_sources(sources_path)

    report: Dict[str, Any] = {
        "started_at": started,
        "finished_at": "",
        "sources": len(sources),
        "documents_added": 0,
        "documents_updated": 0,
        "documents_skipped": 0,
        "chunks_added": 0,
        "errors": 0,
        "items": [],
    }

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        for source in sources:
            if not bool(source.get("enabled", True)):
                continue

            source_id = str(source.get("id") or source.get("name") or source.get("url") or "").strip()
            source_url = str(source.get("url") or "").strip()
            source_type = str(source.get("type") or "html").lower().strip()
            source_started = _utc_now()

            if not source_url:
                continue

            try:
                raw_text, final_url = _fetch_url(source_url, timeout=timeout)
                documents: List[Dict[str, Any]] = []

                if source_type in {"rss", "atom", "feed"}:
                    feed_items = _parse_feed_items(
                        raw_text,
                        final_url,
                        limit=int(source.get("max_items") or max_items_per_source or 3),
                    )
                    for item in feed_items[: int(max_items_per_source or 3)]:
                        documents.append(
                            _source_item_to_document(
                                source,
                                raw_text,
                                final_url,
                                item=item,
                                timeout=timeout,
                            )
                        )
                else:
                    documents.append(
                        _source_item_to_document(
                            source,
                            raw_text,
                            final_url,
                            item=None,
                            timeout=timeout,
                        )
                    )

                if not documents:
                    raise RuntimeError("Tidak ada dokumen yang dapat diambil.")

                for document in documents:
                    text = str(document.get("text") or "").strip()
                    if len(text) < 80:
                        continue

                    content_hash = _sha256(text)
                    cur.execute(
                        """
                        SELECT id FROM kb_documents_v2
                        WHERE source_id=? AND url=? AND content_hash=? AND status='active'
                        LIMIT 1
                        """,
                        (
                            document.get("source_id") or source_id,
                            document.get("url") or "",
                            content_hash,
                        ),
                    )
                    exists = cur.fetchone() is not None

                    item_row = {
                        "source": source.get("name") or source_url,
                        "title": document.get("title"),
                        "url": document.get("url"),
                        "status": "skipped" if exists and not force else "updated",
                        "chunks": 0,
                    }

                    if exists and not force:
                        report["documents_skipped"] += 1
                        report["items"].append(item_row)
                        continue

                    if dry_run:
                        item_row["status"] = "would_update"
                        report["items"].append(item_row)
                        continue

                    doc_id, chunk_count = _insert_document_v2(
                        conn,
                        document,
                        max_chunk_chars=max_chunk_chars,
                    )

                    # Mirror ke KB existing agar RAG lama langsung bisa membaca.
                    if power_store is not None:
                        try:
                            power_store.add_document(
                                title=document.get("title") or "Tanpa judul",
                                text=text,
                                source=f"kb_manager_v2:{document.get('url') or source_url}",
                                collection=document.get("collection") or source.get("collection") or "Auto Update",
                                tags=document.get("tags") or "",
                                metadata={
                                    **(document.get("metadata") or {}),
                                    "kb_manager_document_id": doc_id,
                                    "content_hash": content_hash,
                                    "freshness": document.get("freshness") or "recent",
                                    "confidence": document.get("confidence") or "medium",
                                },
                                replace_existing=True,
                                pinned=bool(source.get("pinned", False)),
                            )
                        except Exception as mirror_exc:
                            item_row["mirror_error"] = str(mirror_exc)[:300]

                    report["documents_updated"] += 1
                    report["chunks_added"] += chunk_count
                    item_row["chunks"] = chunk_count
                    report["items"].append(item_row)

                conn.commit()
                cur.execute(
                    """
                    INSERT INTO kb_update_log_v2 (
                        started_at,finished_at,source_id,action,status,message,
                        documents_added,documents_updated,documents_skipped,chunks_added,metadata_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        source_started,
                        _utc_now(),
                        source_id,
                        "incremental_update",
                        "ok",
                        "Sumber selesai diproses.",
                        0,
                        report["documents_updated"],
                        report["documents_skipped"],
                        report["chunks_added"],
                        json.dumps({"url": source_url}, ensure_ascii=False),
                    ),
                )
                conn.commit()

            except Exception as exc:
                report["errors"] += 1
                report["items"].append(
                    {
                        "source": source.get("name") or source_url,
                        "title": "",
                        "url": source_url,
                        "status": "error",
                        "error": str(exc)[:600],
                        "chunks": 0,
                    }
                )
                cur.execute(
                    """
                    INSERT INTO kb_update_log_v2 (
                        started_at,finished_at,source_id,action,status,message,metadata_json
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        source_started,
                        _utc_now(),
                        source_id,
                        "incremental_update",
                        "error",
                        str(exc)[:900],
                        json.dumps({"url": source_url}, ensure_ascii=False),
                    ),
                )
                conn.commit()

        report["finished_at"] = _utc_now()
        return report
    finally:
        conn.close()


def write_live_cache(
    db_path: str,
    query: str,
    provider: str,
    answer: str,
    sources: List[Dict[str, Any]],
    ttl_seconds: int = 86400,
) -> int:
    init_kb_manager_schema(db_path)
    now_ts = time.time()
    expires = datetime.fromtimestamp(now_ts + int(ttl_seconds or 86400), tz=timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO live_cache_v2(query,query_hash,provider,answer,sources_json,expires_at,created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                query,
                _sha256(query),
                provider,
                answer,
                json.dumps(sources or [], ensure_ascii=False),
                expires,
                _utc_now(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()
