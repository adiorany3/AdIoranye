# Music Chart Simple Current Fix

Patch ini memperbaiki kasus pertanyaan sederhana tetapi aktual seperti:

```text
Tangga lagu terbaru di Indonesia apa saja?
```

Masalah sebelumnya: kata seperti `terbaru`, `saat ini`, atau `chart` dapat dianggap sebagai pertanyaan aktual/kritis oleh anti-halusinasi. Jika Knowledge Base belum punya sumber chart musik, jawaban bisa diblokir sebagai `insufficient evidence`.

## Perubahan

1. Ditambahkan `music_chart_tools.py`.
2. Ditambahkan intent baru: `music_chart`.
3. Pertanyaan chart musik diperlakukan sebagai **low-risk current entertainment**, bukan topik risiko tinggi seperti kesehatan/hukum/keuangan.
4. Sistem mencoba mengambil konteks langsung dari sumber publik:
   - Billboard Indonesia Songs
   - Spotify Daily Chart Indonesia via Kworb mirror
   - Google News Musik Indonesia sebagai fallback
5. Jika fetch berhasil, jawaban memakai konteks chart live.
6. Jika fetch gagal, AI tidak mengarang daftar lagu; AI memberi pesan aman dan menyarankan sumber yang perlu diperbarui.
7. Response cache dan semantic cache dimatikan khusus untuk pertanyaan chart musik agar tidak menjawab dengan chart lama.
8. Ditambahkan preset sumber KB: `kb_sources_music_charts_indonesia.json`.

## Secrets opsional

```toml
LIVE_MUSIC_CHART_ENABLED = true
LIVE_MUSIC_CHART_LIMIT = 10
LIVE_MUSIC_CHART_TIMEOUT_SECONDS = 8
```

## Sumber tambahan di KB

`kb_sources.json` sudah ditambah sumber musik/chart Indonesia:

- Billboard Indonesia Songs Chart
- Spotify Daily Chart Indonesia - Kworb Mirror
- Spotify Top 50 Indonesia Playlist Public Page
- Apple Music Top Charts Indonesia
- YouTube Charts Indonesia Top Songs
- Google News - Tangga Lagu Indonesia Terbaru

## Catatan

Untuk pertanyaan seperti chart musik, AI tetap harus berhati-hati karena peringkat berubah harian/mingguan. Namun pertanyaan ini tidak perlu diblokir terlalu ketat seperti pertanyaan kesehatan, hukum, jurnal Q-level, atau berita politik.
