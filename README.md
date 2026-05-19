# Adioranye AI — Active Model + Cost-Aware Router

Versi ini menambahkan:

- Chat publik Streamlit yang mobile friendly.
- Admin Settings tetap diproteksi password.
- Bot Telegram dari Streamlit Online dengan plain text fix.
- Panel model aktif: model utama, model jawaban terakhir, model yang dikonsultasikan, dan status apakah model menengah/mahal dipakai.
- Router biaya: model hemat dicoba dulu, model menengah/mahal hanya dipanggil jika jawaban dari model hemat kosong, terlalu lemah, atau mengaku tidak tahu.

## Deploy Streamlit Cloud

1. Upload folder ini ke GitHub.
2. Di Streamlit Cloud pilih `app.py` sebagai main file.
3. Isi `Settings -> Secrets` memakai contoh di `.streamlit/secrets.toml.example`.
4. Klik reboot app setelah mengganti secrets.

## Alur AI

1. Model utama menjawab dulu.
2. Jawaban dinilai lokal memakai quality scoring.
3. Jika cukup bagus, jawaban langsung ditampilkan.
4. Jika tidak cukup, sistem konsultasi ke model hemat lain.
5. Jika model hemat masih tidak cukup dan `ALLOW_EXPENSIVE_FALLBACK = true`, sistem baru memanggil maksimal `MAX_EXPENSIVE_MODELS` model menengah/mahal.
6. Jika `RETURN_TO_PRIMARY_MODEL = true`, hasil referensi disusun ulang oleh model utama.

## Konfigurasi penting

```toml
SLASHAI_MODEL = "slashai/gpt-5-nano"
SMART_MODEL_ROUTER = true
RETURN_TO_PRIMARY_MODEL = true
MAX_SMART_MODELS = 2
ALLOW_EXPENSIVE_FALLBACK = true
MAX_EXPENSIVE_MODELS = 1
TELEGRAM_SHOW_MODEL_INFO = true
```

## Catatan biaya

Model hemat default memakai harga Rp50 input / Rp200 output per 1M token. Model menengah/mahal tidak dipanggil untuk semua pertanyaan, hanya saat jawaban murah tidak memadai.

Untuk benar-benar 24 jam stabil, VPS tetap lebih aman daripada Streamlit Online karena Streamlit dapat tidur/restart.
