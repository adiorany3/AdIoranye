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

Panduan lengkap ada di `README_KB_AUTO_UPDATE.md`.

## Update: Quality Control & Verifier System

Paket ini sudah dilengkapi `ai_quality_control.py`, mode jawaban `/mode`, dashboard `✅ Quality Control`, quality scoring, verifier model, export/import KB JSONL, dan evaluasi mingguan. Lihat `QUALITY_CONTROL_VERIFIER_SYSTEM.md`.
