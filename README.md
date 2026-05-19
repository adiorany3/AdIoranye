# Adioranye AI - Streamlit Online + Telegram Bot + Mobile Friendly

Versi ini dibuat untuk tampilan handphone yang lebih nyaman, ringan, dan mudah dipakai.

## Fitur utama

- Halaman utama dapat langsung dipakai chat AI tanpa login.
- Admin Settings tetap diproteksi username/password.
- Tampilan mobile friendly: tombol besar, bubble chat nyaman dibaca, input chat aman di layar kecil.
- Tema ramah mata untuk light/dark mode.
- Bot Telegram dapat dijalankan dari Admin Settings.
- Persona `adioranye` langsung masuk ke system prompt.
- Memory lokal tersedia untuk konteks ringkas.
- Proteksi agar Telegram bot tidak mudah berjalan dobel/triple.

## Secrets Streamlit Cloud

Masukkan konfigurasi berikut di **Streamlit Cloud → App → Settings → Secrets**:

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

## Deploy

1. Upload folder project ini ke GitHub.
2. Deploy ke Streamlit Community Cloud.
3. Pilih main file: `app.py`.
4. Isi secrets seperti contoh di atas.
5. Reboot app setelah mengganti secrets.

## Catatan Telegram

Untuk menghindari balasan double/triple, gunakan `TELEGRAM_AUTO_START = false`, lalu start bot satu kali dari Admin Settings. Jika masih double/triple, revoke token bot lewat BotFather karena kemungkinan token lama masih berjalan di deploy/laptop/VPS lain.
