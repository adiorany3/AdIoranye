# Asisten Pribadi AI Cepat - Streamlit + SlashAI

Aplikasi ini memakai Streamlit dan API kompatibel OpenAI dari SlashAI.

## Fitur efisiensi

- Default model ringan: `slashai/deepseek-v4-flash`.
- Streaming jawaban agar respons tampil bertahap.
- Riwayat yang dikirim ke API dibatasi agar hemat token.
- Fallback model dibatasi agar tidak membuang waktu mencoba terlalu banyak model.
- Timeout API bisa diatur.
- Profil cepat, seimbang, dan lengkap.

## Jalankan lokal

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Secrets

Untuk lokal, buat file:

```text
.streamlit/secrets.toml
```

Isi:

```toml
SLASHAI_API_KEY = "ISI_API_KEY_KAMU_DI_SINI"
SLASHAI_API_URL = "https://api.slashai.my.id/v1/chat/completions"
SLASHAI_MODEL = "slashai/deepseek-v4-flash"
```

Untuk Streamlit Community Cloud, jangan upload API key ke GitHub. Masukkan isi TOML di atas ke menu **Settings > Secrets**.

## Catatan 403

Jika muncul `403 access_denied` atau `deposit required`, berarti model tersebut dikunci oleh provider. Pilih model lain atau lakukan deposit/top up sesuai aturan provider.
