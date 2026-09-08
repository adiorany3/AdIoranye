import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram_formatting import format_telegram_message, split_telegram_message

formatted = format_telegram_message("# Berita\n\n- [Judul](https://example.com)\n\n**Sumber:** Google News")
assert formatted == "Berita\n\n• Judul — https://example.com\n\nSumber: Google News", formatted

chunks = split_telegram_message("\n\n".join(f"Berita {index}" for index in range(1000)))
assert len(chunks) > 1
assert all(0 < len(chunk) <= 4000 for chunk in chunks)
assert "Berita 999" in chunks[-1]
print("Telegram formatting and message splitting passed.")
