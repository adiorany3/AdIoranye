# Asisten Pribadi AI Streamlit

Aplikasi Streamlit untuk menjalankan asisten pribadi memakai API OpenAI-compatible:

```text
https://api.slashai.my.id/v1/chat/completions
```

## Fitur

- Chat AI dengan model SlashAI/OpenAI-compatible.
- Persona asisten yang bisa diedit dari sidebar.
- Memory lokal agar AI mengingat hal penting tanpa mengirim seluruh riwayat chat.
- Perintah lokal tanpa memanggil API:
  - `/ingat ...`
  - `/memori`
  - `/lupa ...`
  - `/reset memori`
  - `/persona ...`
- Mode hemat token.
- Auto fallback ke model murah.
- Estimasi biaya dari usage API.
- Fix untuk kasus GPT-5 kosong karena `reasoning_tokens`.

## Cara menjalankan lokal

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Secrets

Isi di Streamlit Community Cloud > App > Settings > Secrets:

```toml
SLASHAI_API_KEY = "ISI_API_KEY_KAMU_DI_SINI"
SLASHAI_API_URL = "https://api.slashai.my.id/v1/chat/completions"
SLASHAI_MODEL = "slashai/gemini-3-flash"

ASSISTANT_PERSONA = "Kamu adalah asisten pribadi yang cepat, hemat token, ramah, dan to the point. Jawab dalam bahasa Indonesia yang natural."
MEMORY_FILE = "assistant_memory.json"
```

## Catatan Memory

Memory disimpan di file JSON lokal. Pada VPS, file ini relatif stabil. Pada Streamlit Community Cloud, file bisa hilang saat app restart/redeploy. Untuk memory permanen produksi, gunakan database seperti Supabase, Neon, atau Firebase.
