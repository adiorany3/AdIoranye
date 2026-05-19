# Asisten Pribadi AI Streamlit + SlashAI

Versi ini memperbaiki kasus respons kosong seperti:

- `finish_reason: "length"`
- `message.content: ""`
- `completion_tokens_details.reasoning_tokens` memenuhi semua output token

Penyebabnya biasanya model GPT-5 menghabiskan batas output untuk reasoning token internal, sehingga tidak ada token tersisa untuk jawaban yang terlihat.

## Jalankan lokal

```bash
pip install -r requirements.txt
streamlit run app.py
```

Buat file `.streamlit/secrets.toml`:

```toml
SLASHAI_API_KEY = "ISI_API_KEY_KAMU"
SLASHAI_API_URL = "https://api.slashai.my.id/v1/chat/completions"
SLASHAI_MODEL = "slashai/gemini-3-flash"
```

## Deploy Streamlit Community Cloud

1. Upload folder ini ke GitHub.
2. Deploy di Streamlit Community Cloud.
3. Masuk ke App > Settings > Secrets.
4. Paste konfigurasi TOML.
5. Save dan rerun app.

## Rekomendasi model hemat

- `slashai/gemini-3-flash`
- `slashai/gemini-3.1-pro`
- `slashai/gpt-5-nano`
- `slashai/gpt-5-mini`
- `slashai/mimo-v2-flash`
- `slashai/Step-3.5-Flash`

## Catatan penting

Jika memakai GPT-5 lalu jawaban kosong, pilih mode **Stabil GPT-5**. Mode ini menaikkan `max_completion_tokens` dan mengirim `reasoning_effort = "minimal"` supaya output tidak habis untuk reasoning token saja.
