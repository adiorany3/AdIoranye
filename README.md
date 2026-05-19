# Adioranye AI - Mobile Friendly + Safe Prompt Fix

Versi ini dibuat untuk Streamlit Online dengan halaman chat publik, Admin Settings yang terkunci password, Bot Telegram opsional, desain ramah HP, dan perbaikan untuk error `content_filter` dari provider API.

## Perbaikan penting

- Persona dibuat lebih aman: tidak lagi memakai kalimat "menjawab semua pertanyaan" tanpa batas.
- Memory tidak dimasukkan mentah-mentah ke prompt; memory disanitasi dan dibatasi.
- Riwayat chat error/debug tidak ikut dikirim ke model.
- Pertanyaan user tidak lagi terkirim dobel ke API.
- Jika provider menolak prompt karena `content_filter`, aplikasi menampilkan pesan yang jelas, bukan error panjang.
- Telegram memory command dimatikan default agar pengguna Telegram tidak bisa sembarang mengubah memory global.

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


## Router Model Cerdas

Fitur ini membuat model utama tetap menjadi pusat jawaban. Alurnya:

1. Pertanyaan dijawab dulu oleh model utama dari `SLASHAI_MODEL`.
2. Jika jawaban kosong, terlalu pendek, atau mengandung tanda tidak yakin seperti "tidak tahu" / "tidak memiliki informasi", aplikasi akan berkonsultasi ke 1-2 model cadangan.
3. Setelah mendapat referensi, aplikasi kembali ke model utama untuk menyusun jawaban akhir.
4. Jika model utama gagal menyusun ulang, jawaban terbaik dari model cadangan digunakan.

Konfigurasi di Streamlit Secrets:

```toml
SMART_MODEL_ROUTER = true
RETURN_TO_PRIMARY_MODEL = true
MAX_SMART_MODELS = 2
```
