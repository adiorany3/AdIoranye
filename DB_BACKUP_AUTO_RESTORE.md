# Database Backup & Auto Restore Adioranye

Patch ini menambahkan perlindungan untuk error SQLite seperti:

```text
Maaf, Adioranye belum bisa menjawab saat ini.
Detail ringkas: database disk image is malformed
```

## Cara kerja

1. Sebelum update knowledge base, sistem mengecek integritas `.adioranye_power.db`.
2. Jika database sehat, sistem membuat backup konsisten ke folder `.db_backups/` memakai SQLite online backup API.
3. Update knowledge base berjalan seperti biasa.
4. Setelah update selesai, sistem melakukan checkpoint WAL agar isi `.db-wal` masuk ke file utama `.db`.
5. Sistem menjalankan `PRAGMA quick_check`.
6. Jika database rusak, file rusak dipindahkan ke `.db_backups/corrupt/`, lalu database dikembalikan dari backup valid terbaru.
7. Backup lama otomatis dirotasi sesuai `DB_BACKUP_MAX_COUNT`.

## File baru

```text
db_guard.py
DB_BACKUP_AUTO_RESTORE.md
```

## File yang ikut diperbarui

```text
power_features.py
app.py
daily_kb_scraper.py
.github/workflows/daily-kb-update.yml
.streamlit/secrets.toml.example
```

## Secret / konfigurasi opsional

Tambahkan di Streamlit Secrets jika ingin mengubah default:

```toml
DB_BACKUP_ENABLED = true
DB_AUTO_RESTORE_ENABLED = true
DB_BACKUP_DIR = ".db_backups"
DB_BACKUP_MAX_COUNT = 10
DB_BACKUP_MIN_INTERVAL_SECONDS = 21600
```

Default-nya fitur ini sudah aktif meskipun secret di atas tidak diisi.

## Command manual di lokal

Cek dan auto-restore jika rusak:

```bash
python db_guard.py check --db .adioranye_power.db --backup-dir .db_backups --restore
```

Buat backup manual:

```bash
python db_guard.py backup --db .adioranye_power.db --backup-dir .db_backups --label manual
```

Restore backup valid terbaru:

```bash
python db_guard.py restore --db .adioranye_power.db --backup-dir .db_backups
```

Checkpoint WAL:

```bash
python db_guard.py checkpoint --db .adioranye_power.db --backup-dir .db_backups
```

## Catatan penting

- Backup disimpan di `.db_backups/` dan ikut di-commit oleh GitHub Actions.
- File database rusak tidak langsung dihapus permanen, tetapi dipindahkan ke `.db_backups/corrupt/` agar masih bisa dianalisis.
- Jika tidak ada backup valid, database rusak akan dikarantina dan SQLite membuat database kosong baru saat aplikasi start.
- Jumlah backup dijaga dengan `DB_BACKUP_MAX_COUNT` agar repo tidak cepat membesar.
