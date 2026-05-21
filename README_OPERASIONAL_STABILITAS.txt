# Patch Operasional & Stabilitas Adioranye

Patch ini menambahkan:

1. Validator Secrets di Admin > Health.
2. AI Health Center untuk melihat status API, Telegram, model aktif, KB, cache, biaya, dan database.
3. Maintenance panel untuk backup/restore database SQLite, cleanup log lama, dan reset terarah.
4. Mode operasional AI: Hemat, Seimbang, Maksimal.
5. Error boundary untuk Power Features agar chat utama tetap aktif meskipun panel admin error.
6. requirements.txt untuk Knowledge Base PDF/DOCX/XLSX.

Secret opsional:

```toml
AI_OPERATION_MODE = "Seimbang"
POWER_LOG_RETENTION_DAYS = 30
POWER_CACHE_RETENTION_DAYS = 7
POWER_BENCHMARK_RETENTION_DAYS = 14
```

Mode:
- Hemat: prioritaskan model murah dan tahan fallback mahal.
- Seimbang: murah dulu, naik ke model capable jika perlu.
- Maksimal: lebih agresif memakai model capable.
