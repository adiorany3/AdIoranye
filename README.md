# Adioranye Streamlit Online + Telegram Bot

Project ini menjalankan:

1. Dashboard Streamlit.
2. Bot Telegram dari dalam Streamlit Online menggunakan background thread.
3. API SlashAI OpenAI-compatible.
4. Persona system prompt `adioranye`.
5. Memory lokal sederhana.

## Isi Streamlit Secrets

Di Streamlit Cloud:

`App -> Settings -> Secrets`

Isi:

```toml
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "Admin"

TELEGRAM_BOT_TOKEN = "ISI_TOKEN_BOT_DARI_BOTFATHER"
SLASHAI_API_KEY = "ISI_API_KEY_SLASHAI_KAMU"
SLASHAI_API_URL = "https://api.slashai.my.id/v1/chat/completions"
SLASHAI_MODEL = "slashai/gpt-5-nano"

ASSISTANT_PERSONA = "Nama kamu adalah adioranye. Kamu adalah asisten pribadi yang pintar, cepat, ramah, dan dapat membantu menjawab berbagai pertanyaan pengguna. Jawab dalam bahasa Indonesia yang natural, jelas, praktis, dan tidak bertele-tele."
MEMORY_FILE = "assistant_memory.json"

TELEGRAM_AUTO_START = true
```

## Proteksi Admin

Dashboard Streamlit, setting model, persona, memory, tes AI, dan kontrol Bot Telegram hanya bisa dibuka setelah login admin.

Default login pada contoh secrets:

```text
Username: admin
Password: Admin
```

Sebaiknya ganti `ADMIN_PASSWORD` dengan password yang lebih kuat sebelum deploy publik.

## Deploy ke Streamlit Online

1. Upload folder ini ke GitHub.
2. Buka Streamlit Community Cloud.
3. Pilih repo.
4. Main file: `app.py`.
5. Isi Secrets.
6. Deploy.
7. Buka app, klik `Start Bot` di sidebar jika belum auto start.
8. Chat bot di Telegram dengan `/start`.

## Perintah Telegram

```text
/start
/help
/ingat nama saya Adi
/memori
/lupa Adi
/reset memori
```

Selain perintah itu, langsung kirim pertanyaan.

## Catatan Penting

Streamlit Online bisa tidur saat tidak ada aktivitas. Kalau app tidur, bot Telegram juga berhenti.
Untuk 24 jam nonstop yang benar-benar stabil, gunakan VPS + systemd.

Kalau muncul error `Conflict`, berarti token bot yang sama sedang dijalankan di tempat lain.
Matikan bot lama atau deploy hanya di satu tempat.
