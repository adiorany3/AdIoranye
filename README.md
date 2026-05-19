# Asisten Pribadi AI Streamlit

Aplikasi Streamlit untuk menjalankan asisten pribadi memakai API OpenAI-compatible:

```text
https://api.slashai.my.id/v1/chat/completions
```

## Fitur

- Chat AI dengan model SlashAI/OpenAI-compatible.
- Persona **adioranye** langsung dimasukkan ke role `system` pada setiap request API, dan tetap bisa diedit dari sidebar/secrets.
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
SLASHAI_MODEL = "slashai/gpt-5-nano"

ASSISTANT_PERSONA = "Nama kamu adalah adioranye. Kamu adalah asisten pribadi yang pintar, cepat, ramah, dan dapat membantu menjawab berbagai pertanyaan yang diberikan pengguna. Jawab dalam bahasa Indonesia yang natural, jelas, praktis, dan tidak bertele-tele."
MEMORY_FILE = "assistant_memory.json"
```

## Persona System Default

Persona sudah langsung masuk ke `role: system` lewat `BASE_SYSTEM_PERSONA` di `app.py`:

```text
Nama kamu adalah adioranye. Kamu adalah asisten pribadi yang pintar, cepat, ramah, dan dapat membantu menjawab berbagai pertanyaan yang diberikan pengguna.
```

Dengan alur ini, persona tidak perlu diketik ulang di chat dan tidak perlu disimpan sebagai memori biasa.

## Catatan Memory

Memory disimpan di file JSON lokal. Pada VPS, file ini relatif stabil. Pada Streamlit Community Cloud, file bisa hilang saat app restart/redeploy. Untuk memory permanen produksi, gunakan database seperti Supabase, Neon, atau Firebase.


## Perbaikan parser respons

Versi ini dapat membaca jawaban dari gateway OpenAI-compatible meskipun response JSON tidak valid sempurna/terpotong, selama field `message.content` masih terlihat di raw response. Default model diarahkan ke `slashai/gpt-5-nano` karena pada pengujian user model ini sudah mengembalikan `content`.
