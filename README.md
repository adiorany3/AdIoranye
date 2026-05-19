# Adioranye AI - Streamlit Online + Telegram Bot (Safe Single Bot)

Versi ini dibuat untuk mengurangi masalah jawaban Telegram double/triple saat dijalankan dari Streamlit Online.

## Prinsip penting

Streamlit Online bisa rerun/restart, dan jika token Telegram yang sama masih dipakai di beberapa deploy/tab/laptop/VPS, satu pesan Telegram dapat dibalas beberapa kali. Karena itu versi ini memakai mode aman:

- `TELEGRAM_AUTO_START = false` secara default.
- Bot Telegram hanya dijalankan lewat tombol admin.
- Lock OS `fcntl.flock` mencegah lebih dari satu worker dalam container yang sama.
- Tombol reset koneksi Telegram menghapus pending updates.

## Secrets Streamlit Cloud

Masukkan di **Settings → Secrets**:

```toml
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "GANTI_PASSWORD_ADMIN_YANG_KUAT"

TELEGRAM_BOT_TOKEN = "ISI_TOKEN_BOT_DARI_BOTFATHER"
SLASHAI_API_KEY = "ISI_API_KEY_SLASHAI_KAMU"
SLASHAI_API_URL = "https://api.slashai.my.id/v1/chat/completions"
SLASHAI_MODEL = "slashai/gpt-5-nano"

ASSISTANT_PERSONA = "Nama kamu adalah adioranye. Kamu adalah asisten pribadi yang pintar, cepat, ramah, dan dapat membantu menjawab berbagai pertanyaan pengguna. Jawab dalam bahasa Indonesia yang natural, jelas, praktis, dan tidak bertele-tele."
MEMORY_FILE = "assistant_memory.json"

TELEGRAM_AUTO_START = false
TELEGRAM_DROP_PENDING_UPDATES = true
TELEGRAM_SEND_PROCESSING_MESSAGE = false
TELEGRAM_LOCK_FILE = ".telegram_bot_worker.lock"

TEMPERATURE = 0.3
MAX_COMPLETION_TOKENS = 2200
```

## Cara deploy aman

1. Upload folder ini ke GitHub.
2. Deploy ke Streamlit Cloud dengan main file `app.py`.
3. Isi Secrets seperti di atas.
4. Buka app Streamlit.
5. Login admin di sidebar.
6. Masuk tab Telegram.
7. Klik **Reset koneksi Telegram / hapus pending update**.
8. Klik **Start Bot** satu kali.

## Jika masih double/triple

Itu berarti token Telegram masih aktif di tempat lain. Lakukan ini:

1. Buka BotFather di Telegram.
2. Jalankan `/revoke`.
3. Pilih bot kamu.
4. Ambil token baru.
5. Ganti `TELEGRAM_BOT_TOKEN` di Streamlit Secrets.
6. Reboot app Streamlit.
7. Login admin → Telegram → Reset koneksi → Start Bot.

Tombol Stop di Streamlit hanya bisa mematikan worker di app yang sedang dibuka. Ia tidak bisa mematikan deploy lama, laptop, atau VPS lain yang masih memakai token lama.
