# Perbaikan Timeout GitHub Actions: Sequential KB Update

Masalah yang diperbaiki:

- `The job has exceeded the maximum execution time of 20m0s`
- `The operation was canceled`
- Warning Node.js 20 dari `actions/checkout@v4` dan `actions/setup-python@v5`
- Sumber knowledge base terlalu banyak sehingga satu workflow memproses ratusan feed sekaligus

## Perubahan utama

1. `kb_sources.json` dikurangi menjadi 96 sumber prioritas.
2. Sumber lengkap lama disimpan sebagai `kb_sources_full_595_archive.json`.
3. Workflow `daily-kb-update.yml` sekarang memproses batch kecil secara sekuensial:
   - default 8 sumber per run;
   - maksimal 1 item per sumber;
   - time budget internal 480 detik;
   - timeout job 18 menit.
4. Workflow tidak lagi memakai `actions/checkout` atau `actions/setup-python`, sehingga warning Node.js 20 dari workflow ini hilang.
5. Scraper punya hard cap:
   - `KB_SCRAPER_HARD_SOURCE_LIMIT=8`
   - `KB_SCRAPER_HARD_MAX_ITEMS=1`
6. Command Telegram `/update` juga memicu batch kecil, bukan update penuh.

## Cara kerja sekuensial

Scraper menyimpan cursor di:

```text
.adioranye_kb_scrape_state.json
```

Setiap workflow berjalan, sistem mengambil 8 sumber berikutnya. Run berikutnya lanjut dari cursor tersebut. Dengan 96 sumber prioritas, seluruh sumber selesai dalam sekitar 12 batch. Karena workflow dijadwalkan 4 kali sehari, satu siklus penuh selesai sekitar 3 hari.

## Jadwal workflow

Workflow berjalan 4 kali sehari:

```yaml
- cron: "5 17,23,5,11 * * *"
```

Dalam WIB kira-kira:

```text
00:05 WIB
06:05 WIB
12:05 WIB
18:05 WIB
```

## Penting setelah upload

Pastikan di repo GitHub hanya ada workflow update KB yang baru. Kalau masih ada workflow lama yang memakai:

```yaml
uses: actions/checkout@v4
uses: actions/setup-python@v5
```

warning Node.js 20 akan tetap muncul dari workflow lama tersebut. Hapus atau ganti workflow lama.

## Manual run

Dari GitHub Actions → Daily Knowledge Base Sequential Update → Run workflow.

Gunakan nilai aman:

```text
source_limit = 8
max_items = 1
```

Jangan isi besar seperti 100/5 karena bisa timeout. Workflow sudah punya hard cap agar tetap aman.
