# Preset Sumber Populer dan Terkini

Preset ini ditujukan untuk memperkaya Knowledge Base Adioranye dengan informasi terbaru harian dalam kategori:

1. Pengetahuan dan sains
2. Kesehatan
3. Teknologi dan AI
4. Isu terkini Indonesia
5. Topik populer dari Google Trends

## Cara pakai

Default sudah memakai `kb_sources.json` yang diperluas. Jika ingin menjalankan hanya preset populer/current:

```bash
python daily_kb_scraper.py --sources kb_sources_popular_current.json --db .adioranye_power.db
```

## Rekomendasi batas aman

- Google Trends: 10-12 item/hari
- Google News per kategori: 8-10 item/hari
- Sumber resmi: 3-5 item/hari
- Sumber teknologi global: 3-5 item/hari

Untuk Streamlit Cloud/GitHub Actions, jangan terlalu besar agar proses update tidak timeout.
