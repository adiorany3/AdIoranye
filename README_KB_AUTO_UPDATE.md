# Adioranye AI — Daily Knowledge Base Auto Update

Paket ini menambahkan fitur **auto-update Knowledge Base harian** untuk Adioranye AI.

Isi utama:

- `daily_kb_scraper.py` — scraper RSS/HTML/sitemap dan penyimpan otomatis ke SQLite KB.
- `kb_sources.json` — daftar sumber yang akan diambil setiap hari.
- `.github/workflows/daily-kb-update.yml` — GitHub Actions agar KB update otomatis jam 00.05 WIB.
- `app.py` — sudah dipatch dengan tab admin **Knowledge Base → Auto Update**.
- `power_features.py` — memakai `PowerStore.add_document()` yang sudah ada untuk RAG/chunking.
- `memory_store.py` — fallback memory lokal agar paket tetap bisa jalan kalau file lama tidak ada.
- `requirements.txt` dan `requirements-kb-auto.txt`.

## Cara Pasang ke Repo

1. Backup repo lama terlebih dahulu.
2. Upload semua file dari ZIP ini ke root repo GitHub kamu.
3. Pastikan Streamlit Cloud memakai `requirements.txt`.
4. Isi secrets di Streamlit Cloud menggunakan contoh dari `.streamlit/secrets.toml.example`.
5. Edit `kb_sources.json` sesuai sumber yang kamu mau.

## Cara Menjalankan Manual di Lokal

```bash
pip install -r requirements.txt
python daily_kb_scraper.py --db .adioranye_power.db --sources kb_sources.json --max-items 5
streamlit run app.py
```

Untuk tes tanpa menyimpan ke database:

```bash
python daily_kb_scraper.py --dry-run --sources kb_sources.json --max-items 2
```

## Cara Menjalankan dari Admin Web

1. Login admin di sidebar.
2. Buka panel **Pusat Fitur Pintar**.
3. Masuk ke tab **Knowledge Base**.
4. Buka subtab **Auto Update**.
5. Klik **Update Knowledge Base dari sumber online sekarang**.

## Cara Auto Update Harian dengan GitHub Actions

Workflow sudah tersedia di:

```text
.github/workflows/daily-kb-update.yml
```

Default jadwal:

```text
00.05 WIB setiap hari
```

Action akan:

1. checkout repo,
2. install dependency ringan,
3. menjalankan `daily_kb_scraper.py`,
4. menyimpan hasil ke `.adioranye_power.db`,
5. commit database dan state deduplikasi ke GitHub.

Agar workflow bisa commit, buka GitHub repo:

```text
Settings → Actions → General → Workflow permissions → Read and write permissions
```

## Mengatur Sumber

Edit `kb_sources.json`.

Contoh RSS:

```json
{
  "name": "Nama Sumber",
  "url": "https://example.com/rss.xml",
  "type": "rss",
  "enabled": true,
  "collection": "Auto Update - AI",
  "tags": "auto-update,ai,teknologi",
  "max_items": 5,
  "fetch_article": true,
  "min_chars": 300,
  "max_chars": 25000,
  "delay_seconds": 1
}
```

Tipe yang didukung:

- `rss` / `atom` — feed RSS atau Atom.
- `html` — satu halaman HTML disimpan sebagai satu dokumen.
- `html_index` — halaman daftar link, lalu artikel internal diambil.
- `sitemap` — sitemap XML, lalu URL diambil satu per satu.

## Catatan Penting

- Jangan scrape situs yang melarang scraping atau butuh login.
- Lebih aman pakai RSS/API resmi daripada mengambil HTML penuh.
- Isi knowledge base dipakai sebagai konteks RAG, bukan instruksi system permanen.
- File `.adioranye_kb_scrape_state.json` dipakai untuk deduplikasi URL agar artikel tidak masuk berkali-kali.
- Kalau memakai Streamlit Cloud, database runtime bisa berubah saat app restart. GitHub Actions membantu menjaga DB terbaru tersimpan di repo.
