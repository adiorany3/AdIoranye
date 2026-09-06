"""Offline checks: .venv/bin/python scripts/verify_public_article.py"""

import sys
import tempfile
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daily_kb_scraper as kb
import public_article as article


def rejected(call):
    try:
        call()
    except (ValueError, TimeoutError):
        return
    raise AssertionError("Unsafe input accepted")


for url in ("http://example.com", "https://user:pass@example.com", "https://example.com:8443",
            "file:///etc/passwd", "https://example.com\\@localhost", "https://example.com/\n"):
    rejected(lambda: article._public_target(url))

public = (2, 1, 6, "", ("93.184.216.34", 443))
for ip in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "100.64.0.1", "224.0.0.1", "::1", "::ffff:127.0.0.1"):
    with patch.object(article.socket, "getaddrinfo", return_value=[public, (2, 1, 6, "", (ip, 443))]):
        rejected(lambda: article._resolve("example.com", 1))


def response(body=b"<html><title>Article</title><p>Public text</p></html>", status=200, **headers):
    result = MagicMock()
    result.status = status
    result.headers = Message()
    result.headers["Content-Type"] = "text/html; charset=utf-8"
    for key, value in headers.items():
        result.headers[key.replace("_", "-")] = value
    result.getheader.side_effect = lambda key, default=None: result.headers.get(key, default)
    result.read.return_value = body
    return result


def fetch(responses):
    with patch.object(article.socket, "getaddrinfo", return_value=[public]) as dns, \
            patch.object(article.socket, "socket") as socket_factory, \
            patch.object(article.ssl, "create_default_context") as context, \
            patch.object(article.http.client, "HTTPSConnection") as connection:
        connection.return_value.getresponse.side_effect = responses
        result = article.fetch_public_article("https://example.com/news")
        socket_factory.return_value.connect.assert_called_with(public[4])
        context.return_value.wrap_socket.assert_called_with(
            socket_factory.return_value, server_hostname="example.com", do_handshake_on_connect=False)
        context.return_value.wrap_socket.return_value.do_handshake.assert_called()
        assert dns.call_count == len(responses)
        connection.return_value.request.assert_called_with("GET", "/news", headers={
            "User-Agent": kb.DEFAULT_USER_AGENT, "Accept": "text/html,application/xhtml+xml",
            "Accept-Encoding": "identity", "Connection": "close"})
        return result


assert fetch([response()])[0] == "https://example.com/news"
rejected(lambda: fetch([response(status=302, Location="http://localhost/")]))
rejected(lambda: fetch([response(status=403)]))
rejected(lambda: fetch([response(Content_Length=str(article.MAX_BYTES + 1))]))
rejected(lambda: fetch([response(body=b"x" * (article.MAX_BYTES + 1))]))
rejected(lambda: fetch([response(Content_Encoding="gzip")]))
rejected(lambda: fetch([response(Content_Length="999")]))
with patch.object(article.socket, "getaddrinfo", side_effect=[
        [public], [(2, 1, 6, "", ("127.0.0.1", 443))]]), \
        patch.object(article.socket, "socket"), patch.object(article.ssl, "create_default_context"), \
        patch.object(article.http.client, "HTTPSConnection") as connection:
    connection.return_value.getresponse.return_value = response(status=302, Location="https://example.com/next")
    rejected(lambda: article.fetch_public_article("https://example.com/news"))

for text in ('<title>Just a moment</title>', '"isAccessibleForFree":false',
             '<p>Berlangganan untuk membaca</p>', '<p>captcha</p>'):
    with patch.object(article, "fetch_public_article", return_value=("https://example.com/news", text)):
        rejected(lambda: article.public_article_source("https://example.com/news"))

content = " ".join(f"Informasi pertanian topik{i} membahas data riset lingkungan." for i in range(100))
with patch.object(article, "fetch_public_article", return_value=(
        "https://example.com/news", f"<title>Penelitian pertanian</title><article>{content}</article>")):
    source = article.public_article_source("https://example.com/news")
assert source["url"] == "https://example.com/news" and source["type"] == "static"

with tempfile.TemporaryDirectory() as directory, \
        patch.object(kb, "get_power_store") as get_store, \
        patch.object(kb, "make_session") as session, \
        patch.object(kb, "load_sources", side_effect=AssertionError("Config unexpectedly loaded")), \
        patch.object(kb, "ensure_database_ready") as ready, \
        patch.object(kb, "create_sqlite_backup") as backup, \
        patch.object(kb, "sqlite_checkpoint"), patch.object(kb, "sqlite_integrity_check") as integrity:
    integrity.return_value.ok = True
    get_store.return_value.add_document.return_value = (1, 2)
    options = dict(db_path=f"{directory}/test.db", state_path=f"{directory}/state.json",
                   watchlist_path=f"{directory}/watch.json", briefing_file="", sources=[source])
    report = kb.run_daily_kb_update(**options)
    assert report["added_documents"] == 1, report
    assert ready.call_count == 1 and backup.call_count == 2
    assert get_store.return_value.add_document.call_args.kwargs["source"] == source["url"]
    assert kb.run_daily_kb_update(**options)["skipped_existing"] == 1
    assert get_store.return_value.add_document.call_count == 1
    options["state_path"] = f"{directory}/duplicate.json"
    get_store.return_value.add_document.return_value = (1, 0)
    assert kb.run_daily_kb_update(**options)["items"][0]["status"] == "skipped_duplicate_hash"
    options["sources"] = [dict(source, static_content="too short")]
    assert kb.run_daily_kb_update(**options)["skipped_short"] == 1
    options["sources"] = [dict(source, static_content="A repeated sentence. " * 100)]
    options["state_path"] = f"{directory}/quality.json"
    assert kb.run_daily_kb_update(**options)["items"][0]["status"] == "skipped_low_quality"
    options["sources"] = [source]
    with patch.object(kb, "scrape_source", side_effect=ValueError("fetch failed")):
        assert kb.run_daily_kb_update(**options)["errors"] == 1
    get_store.return_value.add_document.side_effect = RuntimeError("write failed")
    try:
        kb.run_daily_kb_update(**options)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Write failure hidden")
    assert not kb.load_state(options["state_path"])["processed"]
    session.return_value.get.assert_not_called()

print("PASS: pinned HTTPS, URL/DNS/redirect safety, bounds, paywall, ingestion, backups, dedupe, quality, failures")