# Perintah Telegram `/update`

Paket ini menambahkan command admin-only `/update` pada bot Telegram Adioranye.

## Cara kerja

`/update` tidak menjalankan scraper langsung di Streamlit. Command ini memicu GitHub Actions workflow `daily-kb-update.yml` lewat endpoint `workflow_dispatch`, lalu GitHub Actions menjalankan `daily_kb_scraper.py` dan meng-commit database KB terbaru.

Alur:

```text
Telegram /update -> Streamlit bot -> GitHub Actions workflow_dispatch -> daily_kb_scraper.py -> .adioranye_power.db -> commit -> notifikasi Telegram selesai/gagal
```

## Secret yang perlu diisi di Streamlit

Tambahkan ke Streamlit Cloud Secrets atau `.streamlit/secrets.toml`:

```toml
GITHUB_ACTIONS_TOKEN = "ISI_TOKEN_GITHUB_UNTUK_WORKFLOW_DISPATCH"
GITHUB_REPO = "username/nama-repo"
GITHUB_WORKFLOW_FILE = "daily-kb-update.yml"
GITHUB_BRANCH = "main"
```

Token GitHub disarankan memakai fine-grained personal access token dengan akses repository terkait dan permission **Actions: Read and write**.

## Secret yang perlu diisi di GitHub Actions

Agar workflow mengirim pesan ketika selesai/gagal, tambahkan repository secrets:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_ADMIN_CHAT_ID
```

## Cara tes

Kirim ke bot Telegram:

```text
/update
```

Jika konfigurasi benar, bot akan membalas bahwa update KB diterima dan GitHub Actions sedang berjalan.
