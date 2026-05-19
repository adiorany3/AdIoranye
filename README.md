# Adioranye AI - Public Chat + Protected Admin Settings

Project ini berisi aplikasi Streamlit Online yang bisa digunakan untuk chat AI secara publik, sementara bagian pengaturan dilindungi password admin.

## Fitur

- Halaman utama langsung bisa dipakai chat dengan AI.
- Admin Settings diproteksi username dan password.
- Pengaturan model, persona, token status, memory, debug, dan kontrol Telegram hanya muncul setelah login admin.
- Bot Telegram dapat dijalankan dari Streamlit Online menggunakan background thread.
- Semua secret disimpan dalam format TOML melalui `.streamlit/secrets.toml` atau Streamlit Cloud Secrets.
- Memory command di chat publik dimatikan. Perintah `/ingat`, `/memori`, `/lupa`, dan `/reset memori` hanya aktif jika admin sedang login di Streamlit.

## Jalankan lokal

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Cloud Secrets

Masukkan ini di menu:

```text
Streamlit Cloud → App → Settings → Secrets
```

Isi:

```toml
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "GANTI_PASSWORD_ADMIN_YANG_KUAT"

TELEGRAM_BOT_TOKEN = "ISI_TOKEN_BOT_DARI_BOTFATHER"
SLASHAI_API_KEY = "ISI_API_KEY_SLASHAI_KAMU"
SLASHAI_API_URL = "https://api.slashai.my.id/v1/chat/completions"
SLASHAI_MODEL = "slashai/gpt-5-nano"

ASSISTANT_PERSONA = "Nama kamu adalah adioranye. Kamu adalah asisten pribadi yang pintar, cepat, ramah, dan dapat membantu menjawab berbagai pertanyaan pengguna. Jawab dalam bahasa Indonesia yang natural, jelas, praktis, dan tidak bertele-tele."
MEMORY_FILE = "assistant_memory.json"

TELEGRAM_AUTO_START = true
TEMPERATURE = 0.3
MAX_COMPLETION_TOKENS = 2200
```

## Cara pakai

1. Upload folder ini ke GitHub.
2. Deploy di Streamlit Community Cloud.
3. Main file: `app.py`.
4. Isi Secrets di Streamlit Cloud.
5. Buka halaman Streamlit, chat AI langsung bisa dipakai.
6. Buka sidebar untuk login admin.
7. Setelah login admin, kamu bisa mengatur model, persona, memory, debug, dan bot Telegram.

## Catatan penting

Streamlit Online bisa tidur ketika tidak ada pengunjung. Kalau aplikasi tidur, bot Telegram yang berjalan di background thread juga bisa berhenti. Untuk bot Telegram 24 jam nonstop, VPS tetap lebih stabil.

Jangan upload file `.streamlit/secrets.toml` yang berisi token asli ke GitHub. Gunakan Streamlit Cloud Secrets untuk produksi.
