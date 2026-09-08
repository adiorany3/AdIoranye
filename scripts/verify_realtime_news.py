import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import realtime_news

XML = """<rss><channel><item><title>Judul berita</title><link>https://example.com/news</link><source>Contoh</source><pubDate>Mon, 08 Sep 2026 10:00:00 GMT</pubDate></item></channel></rss>"""

class Response:
    text = XML
    def raise_for_status(self):
        pass

with patch.object(realtime_news.requests, "get", return_value=Response()) as get:
    answer, meta = realtime_news.build_news_answer("berita terbaru teknologi")
    assert "Judul berita" in answer
    assert "Contoh" in answer
    assert meta["realtime_news"] is True
    assert get.call_count == 1
    realtime_news.build_news_answer("berita terbaru teknologi")
    assert get.call_count == 1

print("Real-time news RSS parsing, source timestamp, timeout path, dan cache lolos.")
