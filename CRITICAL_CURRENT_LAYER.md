# Critical Current Knowledge Layer

Modul ini menambahkan lapisan pengetahuan terkini untuk menjawab pertanyaan kritis seperti:

- "Apakah isu ini benar?"
- "Apa bukti terbarunya?"
- "Apakah jurnal ini Q1/Q2/Q3/Q4?"
- "Apakah teknologi/aturan/kesehatan ini masih valid saat ini?"
- "Apa risiko dan sumber resminya?"

## File utama

- `critical_current_layer.py` — helper scoring dan ekstraksi klaim/fakta.
- `daily_kb_scraper.py` — sekarang menambahkan freshness score, source quality, criticality score, dan claim ledger.
- `power_features.py` — schema SQLite baru: `current_claims`, `issue_watchlist`, dan `issue_events`.
- `critical_watchlist.json` — daftar topik yang dipantau.
- `kb_sources_critical_current.json` — preset sumber current/critical.
- `daily_intelligence_briefing.md` — ringkasan harian yang dibuat otomatis setelah update.

## Command Telegram/Admin baru

```text
/briefing
/trending
/cek isu <topik>
/pantau <topik>
/pantau list
/pantau hapus <id>
```

Contoh:

```text
/cek isu AI di kesehatan
/cek isu PMK ternak terbaru
/pantau jurnal peternakan Q1 Q2 Scopus
/briefing
```

## Cara kerja

1. Scraper mengambil data dari RSS/HTML/static sources.
2. Setiap artikel diberi metadata:
   - `source_quality`
   - `freshness_score`
   - `criticality_score`
   - `keywords`
   - `claims`
3. Dokumen masuk ke Knowledge Base normal.
4. Klaim/fakta penting masuk ke tabel `current_claims`.
5. Saat user bertanya isu kritis, sistem otomatis mengambil klaim terkini + dokumen KB.
6. Telegram `/briefing` menampilkan ringkasan harian dari claim ledger.

## Catatan penting

- Source quality bukan jaminan kebenaran mutlak. Ini adalah skor prioritas retrieval.
- Freshness score membantu pertanyaan terkini, tetapi data lama masih bisa relevan untuk pengetahuan stabil.
- Untuk keputusan kesehatan, hukum, keuangan, atau kebijakan, tetap perlu verifikasi ke sumber resmi terbaru.
