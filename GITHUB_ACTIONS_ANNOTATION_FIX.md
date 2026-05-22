# GitHub Actions Annotation Fix

Patch ini dibuat untuk mengurangi/menghilangkan annotation berikut:

```text
The job has exceeded the maximum execution time of 20m0s
The operation was canceled
Node.js 20 is deprecated ... actions/checkout@v4, actions/setup-python@v5
```

## Perubahan utama

### 1. Tidak memakai JavaScript actions

Workflow lama memakai:

```yaml
uses: actions/checkout@v4
uses: actions/setup-python@v5
```

Keduanya menargetkan Node.js 20 sehingga GitHub menampilkan warning. Workflow baru tidak memakai action tersebut. Checkout dilakukan lewat `git fetch`, dan Python memakai Python bawaan runner + `venv`.

### 2. Update KB dibuat bertahap

Karena `kb_sources.json` sudah berisi ratusan sumber, tidak realistis memproses semua feed dalam satu workflow 20 menit. Workflow baru memakai default:

```yaml
KB_SCRAPER_SOURCE_LIMIT: 35
KB_SCRAPER_MAX_ITEMS_PER_SOURCE: 1
KB_UPDATE_TIME_BUDGET_SECONDS: 840
KB_SCRAPER_TIMEOUT: 8
```

Artinya setiap run memproses 35 sumber saja, maksimal 1 item per sumber, dan berhenti aman sebelum timeout GitHub. Sumber yang diproses akan berputar otomatis memakai cursor di `.adioranye_kb_scrape_state.json`.

### 3. Log dibuat lebih pendek

`daily_kb_scraper.py` sekarang punya:

```bash
--quiet
--report-file daily_kb_update_report.json
```

Log GitHub hanya menampilkan ringkasan, sedangkan laporan lengkap disimpan ke file JSON.

## Cara pakai

Upload semua isi ZIP ini ke repository, terutama file berikut:

```text
.github/workflows/daily-kb-update.yml
daily_kb_scraper.py
```

Lalu jalankan manual:

```text
GitHub → Actions → Daily Knowledge Base Update → Run workflow
```

Untuk run manual, kamu bisa ubah input:

```text
source_limit = 35
max_items = 1
```

Jika ingin lebih banyak, naikkan perlahan:

```text
source_limit = 50
max_items = 1
```

Jangan langsung memproses semua sumber karena bisa terkena timeout lagi.

## Catatan untuk command Telegram /update

Command `/update` tetap aman karena workflow masih menerima input:

```text
source
chat_id
requested_at
```

Jika `/update` dipanggil dari Telegram, GitHub Actions akan berjalan dalam mode cepat/default.
