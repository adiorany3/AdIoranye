# Asisten Pribadi AI Streamlit + SlashAI

Sistem ini menjalankan chatbot/asisten pribadi menggunakan Streamlit dan API yang kompatibel dengan OpenAI Chat Completions.

Endpoint default:

```text
https://api.slashai.my.id/v1/chat/completions
```

Model sudah tersedia di sidebar, jadi kamu cukup pilih model seperti:

```text
slashai/gpt-5.5-instant
slashai/gpt-5.5
slashai/claude-sonnet-4.7
slashai/gemini-3.1-pro
slashai/deepseek-v4-pro
```

## Struktur File

```text
streamlit_personal_assistant/
├── app.py
├── requirements.txt
├── .gitignore
├── README.md
└── .streamlit/
    └── secrets.toml.example
```

## Menjalankan Lokal

```bash
pip install -r requirements.txt
mkdir -p .streamlit
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
streamlit run app.py
```

Lalu isi `.streamlit/secrets.toml` dengan API key asli.

## Secrets untuk Streamlit Online

Pada Streamlit Community Cloud, buka:

```text
App > Settings > Secrets
```

Lalu paste:

```toml
SLASHAI_API_KEY = "ISI_API_KEY_KAMU_DI_SINI"
SLASHAI_API_URL = "https://api.slashai.my.id/v1/chat/completions"
SLASHAI_MODEL = "slashai/gpt-5.5-instant"
```

Jangan upload `secrets.toml` asli ke GitHub.

## Catatan Penting

- API key disimpan di Streamlit Secrets, bukan di kode.
- Model dikirim melalui field `model` di body request API.
- Jika model tertentu error, coba ganti model di sidebar.
- Untuk model cepat dan ringan, coba `slashai/gpt-5.5-instant` atau `slashai/gpt-5.4-mini`.
- Untuk coding, coba model Codex seperti `slashai/gpt-5.1-codex-mini-high`.
