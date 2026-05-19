# Adioranye AI - Public Chat + Protected Admin Settings

Project ini berisi aplikasi Streamlit Online yang bisa digunakan untuk chat AI secara publik, sementara bagian pengaturan dilindungi password admin.

## Fitur

- Halaman utama langsung bisa dipakai chat dengan AI.
- Admin Settings diproteksi username dan password.
- Pengaturan model, persona, token status, memory, debug, dan kontrol Telegram hanya muncul setelah login admin.
- Bot Telegram dapat dijalankan dari Streamlit Online menggunakan background thread single-worker agar tidak menjawab dobel.
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
TELEGRAM_DROP_PENDING_UPDATES = true
TELEGRAM_SEND_PROCESSING_MESSAGE = false
TELEGRAM_LOCK_FILE = ".telegram_bot_worker.lock"
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


## Tema ramah mata

Versi ini sudah memakai CSS adaptif agar teks tetap terbaca pada mode light maupun dark. Komponen yang disesuaikan meliputi latar aplikasi, kartu pembuka, contoh prompt, bubble chat, input chat, sidebar, tombol, dan border. File `.streamlit/config.toml` juga ditambahkan sebagai default tema terang dengan kontras tinggi.

## Fix jawaban Telegram dobel

Versi ini memakai `single-worker lock` supaya Streamlit tidak menyalakan dua instance polling Telegram saat app rerun. Pesan status `Sedang diproses...` juga dimatikan secara default dan diganti dengan typing indicator, sehingga pengguna Telegram hanya menerima satu balasan akhir dari AI.

Jika sebelumnya bot masih membalas dobel, pastikan tidak ada deployment lain, script lokal, atau VPS lain yang masih menjalankan token bot Telegram yang sama. Setelah update, lakukan restart/reboot app dari Streamlit Cloud.
