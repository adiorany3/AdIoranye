# Asisten Pribadi AI Streamlit + SlashAI API

Aplikasi ini menjalankan chatbot/asisten pribadi menggunakan Streamlit dan endpoint OpenAI-compatible:

```text
https://api.slashai.my.id/v1/chat/completions
```

## Cara menjalankan lokal

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Secrets untuk Streamlit Cloud

Masukkan melalui **App > Settings > Secrets**:

```toml
SLASHAI_API_KEY = "ISI_API_KEY_KAMU_DI_SINI"
SLASHAI_API_URL = "https://api.slashai.my.id/v1/chat/completions"
SLASHAI_MODEL = "slashai/deepseek-v4-flash"
```

## Jika muncul error 403 deposit required

Itu berarti model yang dipilih terkunci/premium di akun provider kamu. Aplikasi ini sudah menyediakan:

1. Kategori **Rekomendasi Hemat / Coba Dulu**.
2. Fitur **Auto coba model cadangan jika akses ditolak**.
3. Daftar model cadangan yang bisa diedit dari sidebar.

Jika semua model tetap 403, artinya akun API belum punya akses ke model tersebut. Solusinya adalah memilih model lain yang aktif, atau deposit/top up di provider SlashAI untuk membuka model premium.
