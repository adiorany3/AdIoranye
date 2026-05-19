# Adioranye AI - Fast Accurate Router

Versi ini dibuat untuk Streamlit Online dengan halaman chat publik, Admin Settings yang terkunci password, Bot Telegram opsional, desain ramah HP, dan algoritma jawaban yang lebih cepat serta lebih akurat.

## Perbaikan algoritma

- **Fast-first**: model utama menjawab lebih dulu. Jika jawabannya sudah bagus, sistem langsung mengembalikan jawaban tanpa memanggil model lain.
- **Quality scoring lokal**: jawaban dicek dari panjang, tanda tidak yakin, kesesuaian dengan tugas, sinyal langkah/solusi, dan konteks pertanyaan.
- **Router hanya saat perlu**: model cadangan hanya dipakai jika jawaban utama kosong, error, terlalu pendek, atau terlalu tidak yakin.
- **Parallel fallback terbatas**: maksimal 1-3 model cadangan dicoba secara paralel agar lebih cepat, bukan dicoba satu per satu terlalu lama.
- **Kembali ke model utama**: jika fallback menemukan jawaban lebih kuat, hasilnya dikirim kembali ke model utama untuk disusun menjadi jawaban final.
- **Konteks lebih hemat**: memory dan riwayat chat disaring agar prompt pendek, aman, dan tidak boros token.
- **Cache jawaban**: pertanyaan yang sama dalam satu sesi bisa dijawab ulang tanpa memanggil API lagi.
- **GPT-5 reasoning fix**: token output dinaikkan otomatis untuk GPT-5 agar jawaban tidak kosong karena habis di reasoning token.
- **Tidak bypass safety filter**: jika prompt ditolak content filter, sistem tidak memutar ke model lain untuk menghindari aturan keamanan.

## Streamlit Secrets

Isi di Streamlit Cloud: `App > Settings > Secrets`.

```toml
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "GANTI_PASSWORD_ADMIN_YANG_KUAT"

TELEGRAM_BOT_TOKEN = "ISI_TOKEN_BOT_DARI_BOTFATHER"
SLASHAI_API_KEY = "ISI_API_KEY_SLASHAI_KAMU"
SLASHAI_API_URL = "https://api.slashai.my.id/v1/chat/completions"
SLASHAI_MODEL = "slashai/gpt-5-nano"

ASSISTANT_PERSONA = "Nama kamu adalah adioranye. Kamu adalah asisten pribadi yang pintar, cepat, ramah, dan dapat membantu menjawab berbagai pertanyaan yang aman dan bermanfaat. Jawab dalam bahasa Indonesia yang natural, jelas, praktis, dan tidak bertele-tele. Jika permintaan berbahaya atau melanggar aturan, tolak dengan singkat dan arahkan ke alternatif yang aman."
MEMORY_FILE = "assistant_memory.json"

TELEGRAM_AUTO_START = false
TELEGRAM_DROP_PENDING_UPDATES = true
TELEGRAM_SEND_PROCESSING_MESSAGE = false
TELEGRAM_ALLOW_MEMORY_COMMANDS = false
TELEGRAM_LOCK_FILE = ".telegram_bot_worker.lock"

TEMPERATURE = 0.3
MAX_COMPLETION_TOKENS = 2600
SMART_MODEL_ROUTER = true
RETURN_TO_PRIMARY_MODEL = true
MAX_SMART_MODELS = 2
FAST_ACCURATE_ROUTER = true
```

## Cara deploy

1. Upload semua file ke GitHub.
2. Deploy ke Streamlit Community Cloud.
3. Main file: `app.py`.
4. Isi Secrets seperti contoh di atas.
5. Klik `Reboot app` setelah mengganti secrets.

## Catatan Telegram

Agar bot Telegram tidak double/triple, default `TELEGRAM_AUTO_START = false`. Login admin dulu, lalu tekan tombol Start Bot satu kali dari tab Telegram.

Kalau masih double/triple, revoke token dari BotFather, ganti token di Streamlit Secrets, lalu reboot app.
