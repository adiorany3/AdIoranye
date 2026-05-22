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
- `static` — catatan kurasi langsung dari JSON, cocok untuk panduan Q-level/manual reference.

## Catatan Penting

- Jangan scrape situs yang melarang scraping atau butuh login.
- Lebih aman pakai RSS/API resmi daripada mengambil HTML penuh.
- Isi knowledge base dipakai sebagai konteks RAG, bukan instruksi system permanen.
- File `.adioranye_kb_scrape_state.json` dipakai untuk deduplikasi URL agar artikel tidak masuk berkali-kali.
- Kalau memakai Streamlit Cloud, database runtime bisa berubah saat app restart. GitHub Actions membantu menjaga DB terbaru tersimpan di repo.


## Preset sumber populer dan current issue

Paket ini sudah ditambah preset sumber untuk mengambil informasi yang sedang populer/terbaru setiap hari:

- **Populer saat ini**: Google Trends Indonesia dan Google News Indonesia.
- **Isu terkini**: agregasi berita terbaru Indonesia dari Google News.
- **Teknologi & AI**: Google News teknologi/AI Indonesia, Google AI Blog, The Verge, Hacker News, Python Insider, NASA Technology.
- **Kesehatan**: RSS resmi Kementerian Kesehatan RI, termasuk rilis berita, tips kesehatan, dan artikel kesehatan.
- **Pengetahuan & sains**: MIT News, MIT Research, NASA Recently Published, serta Google News sains/riset/inovasi Indonesia.

Catatan operasional:

1. Feed Google Trends/Google News dipakai sebagai sinyal topik populer, jadi `fetch_article` dibuat `false` agar sistem menyimpan judul/ringkasan dan tidak agresif mengambil banyak halaman media.
2. Sumber resmi seperti Kemenkes, MIT, NASA, Google AI Blog, dan Python Insider memakai `fetch_article: true` agar knowledge base punya isi yang lebih substantif.
3. Jika database membesar, turunkan `max_items` di `kb_sources.json` atau jalankan update mingguan untuk sumber global.
4. Untuk topik kesehatan, tetap prioritaskan sumber resmi di koleksi **Auto Update - Kesehatan Resmi** ketika memberi jawaban yang berisiko.

File `kb_sources_popular_current.json` juga disediakan sebagai salinan preset sumber populer/current jika kamu ingin memisahkan konfigurasi.

## Tambahan preset peternakan dan jurnal Q-level

Paket ini juga sudah ditambah sumber bidang **peternakan** dan **jurnal Q-level terkait**.

Cakupan tambahan:

- Berita/topik populer peternakan Indonesia dari Google News.
- Kesehatan hewan dan penyakit ternak: PMK, flu burung, rabies, dan isu serupa.
- Pakan dan nutrisi ternak: ransum, hijauan, formulasi pakan, dan bahan pakan.
- Rujukan resmi/akademik: SINTA, jurnal peternakan Indonesia, dan jurnal animal science global.
- Panduan cek Q-level untuk jurnal internasional melalui SCImago/Scopus/JCR.
- Panduan bedanya Q-level internasional dan akreditasi SINTA nasional.

File baru:

```text
kb_sources_peternakan_jurnal.json
PETERNAKAN_JURNAL_Q_LEVEL_SOURCES.md
JURNAL_Q_LEVEL_PETERNAKAN.md
```

Cara menjalankan hanya preset peternakan dan jurnal:

```bash
python daily_kb_scraper.py --db .adioranye_power.db --sources kb_sources_peternakan_jurnal.json --max-items 0
```

Tipe sumber baru:

- `static` — memasukkan catatan kurasi langsung dari JSON ke Knowledge Base. Ini dipakai untuk panduan Q-level karena sebagian situs ranking jurnal dapat membatasi scraping otomatis.

Catatan penting untuk Q-level:

- Q1/Q2/Q3/Q4 berubah mengikuti tahun dan kategori.
- Untuk jurnal internasional, verifikasi lewat SCImago, Scopus Sources, atau JCR.
- Untuk jurnal Indonesia, gunakan SINTA dan Garuda.
- AI sebaiknya tidak menyatakan Q-level sebagai fakta final tanpa menyebut tahun dan sumber pengecekan.
