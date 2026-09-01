# Adioranye AI + Daily Knowledge Base Auto Update

Jalankan:

```bash
pip install -r requirements.txt
streamlit run app.py
```

Update KB manual:

```bash
python daily_kb_scraper.py --db .adioranye_power.db --sources kb_sources.json --max-items 5
```

Atau pakai runner lokal tanpa GitHub Actions:

```bash
bash scripts/run_kb_update_local.sh
```

Kalau mau hasil update dari laptop Mac lalu dikirim berkala ke GitHub:

```bash
bash scripts/publish_kb_update.sh
```

Script ini akan:
- jalankan update KB lokal dulu
- `git add` file KB penting
- `git commit`
- `git push origin HEAD`

Untuk Streamlit online tanpa GitHub Actions:
- isi `GITHUB_TOKEN` di Streamlit secrets
- isi `GITHUB_REPO` mis. `owner/repo`
- opsional isi `GITHUB_BRANCH` bila bukan `main`
- di panel admin Knowledge Base, pakai tombol publish GitHub
- centang `Publish saja tanpa update ulang` bila ingin upload snapshot KB terakhir tanpa scrape ulang

Env penting untuk update lokal:
- `KB_SCRAPER_SOURCES_FILE`
- `KB_SCRAPER_STATE_FILE`
- `KB_SCRAPER_MAX_ITEMS_PER_SOURCE`
- `KB_SCRAPER_TIMEOUT`
- `KB_UPDATE_TIME_BUDGET_SECONDS`
- `KB_SCRAPER_SOURCE_LIMIT`
- `KB_SCRAPER_SOURCE_OFFSET`
- `KB_SCRAPER_NO_SOURCE_ROTATION=1`
- `KB_SCRAPER_REPORT_FILE=adioranye_kb_update_report.json`
- `KB_SCRAPER_FORCE=1`
- `KB_SCRAPER_DRY_RUN=1`

Contoh jalur lokal hemat risiko:
- `KB_SCRAPER_SOURCE_LIMIT=10 KB_UPDATE_TIME_BUDGET_SECONDS=180 bash scripts/run_kb_update_local.sh`
- `KB_SCRAPER_DRY_RUN=1 KB_SCRAPER_SOURCE_LIMIT=5 bash scripts/run_kb_update_local.sh`

Contoh publish berkala dari Mac:
- `KB_SCRAPER_SOURCE_LIMIT=10 KB_UPDATE_TIME_BUDGET_SECONDS=180 bash scripts/publish_kb_update.sh`
- `RUN_KB_UPDATE_FIRST=0 KB_GIT_COMMIT_MESSAGE="chore: publish KB snapshot" bash scripts/publish_kb_update.sh`

File yang dipublish script:
- `.adioranye_power.db`
- `.adioranye_kb_scrape_state.json`
- `.adioranye_kb_source_health.json`
- `daily_intelligence_briefing.md`
- `daily_kb_update_report.json` bila ada

## Update: Quality Control & Verifier System

Paket ini sudah dilengkapi `ai_quality_control.py`, mode jawaban `/mode`, dashboard `✅ Quality Control`, quality scoring, verifier model, export/import KB JSONL, dan evaluasi mingguan. Lihat `QUALITY_CONTROL_VERIFIER_SYSTEM.md`.
