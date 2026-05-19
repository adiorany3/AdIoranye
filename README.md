# Streamlit Personal Assistant - Cost Aware

Aplikasi asisten pribadi AI berbasis Streamlit dengan API kompatibel OpenAI:

`https://api.slashai.my.id/v1/chat/completions`

## Fitur

- Chat AI seperti ChatGPT.
- API key aman memakai Streamlit Secrets.
- Pilihan model berdasarkan harga.
- Default model hemat: `slashai/gemini-3-flash`.
- Estimasi biaya per request.
- Mode Super Hemat, Cepat Seimbang, dan Lebih Pintar.
- Streaming jawaban.
- Auto fallback hanya ke model murah agar cepat.
- Riwayat chat dibatasi agar hemat token.

## Cara pakai lokal

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Secrets untuk Streamlit Online

Masukkan di menu Streamlit Cloud:

App > Settings > Secrets

```toml
SLASHAI_API_KEY = "ISI_API_KEY_KAMU"
SLASHAI_API_URL = "https://api.slashai.my.id/v1/chat/completions"
SLASHAI_MODEL = "slashai/gemini-3-flash"
```

## Rekomendasi model murah

Gunakan model harga Rp50 input / Rp200 output per 1M token:

- `slashai/gemini-3-flash`
- `slashai/gpt-5-nano`
- `slashai/gpt-5-mini`
- `slashai/mimo-v2-flash`
- `slashai/Step-3.5-Flash`
- `slashai/MiniMax-M2.5`
- `bai/deepseek-v4-flash`
- `bai/claude-haiku-4.5`

Catatan penting: `slashai/deepseek-v4-flash` pada daftar harga kamu tertulis Rp1.500/Rp6.000, jadi tidak dijadikan default hemat.
