from pathlib import Path
import py_compile

app = Path("app.py")
if not app.exists():
    raise SystemExit("app.py tidak ditemukan. Jalankan dari root repo.")

text = app.read_text(encoding="utf-8")
required = [
    "def add_session_memory",
    "def handle_session_memory_command",
    "MEMORY SESI CHAT AKTIF",
    "SESSION_MEMORY_ENABLED",
]
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit("Patch belum lengkap. Missing: " + ", ".join(missing))

py_compile.compile(str(app), doraise=True)
print("OK: session memory patch terpasang dan app.py valid secara sintaks.")
