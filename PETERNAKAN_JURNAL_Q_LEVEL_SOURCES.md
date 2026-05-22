# Preset Peternakan dan Jurnal Q-Level

Preset ini menambahkan sumber Knowledge Base untuk bidang peternakan dan referensi jurnal terkait.

## Cakupan topik

- Peternakan umum dan isu ternak harian di Indonesia
- Kesehatan hewan, penyakit ternak, PMK, flu burung, rabies
- Pakan dan nutrisi ternak
- Sapi potong/sapi perah, unggas, kambing/domba, ruminansia
- Riset animal science, livestock science, poultry science, dairy science, veterinary science
- Referensi jurnal Indonesia melalui SINTA
- Panduan cek Q-level internasional melalui SCImago/Scopus/JCR

## File yang ditambahkan

- `kb_sources_peternakan_jurnal.json` — preset khusus peternakan dan jurnal.
- `JURNAL_Q_LEVEL_PETERNAKAN.md` — panduan cek Q-level dan SINTA.
- `daily_kb_scraper.py` kini mendukung `type: "static"` untuk memasukkan catatan kurasi langsung dari JSON ke KB.

## Cara menjalankan hanya preset ini

```bash
python daily_kb_scraper.py --db .adioranye_power.db --sources kb_sources_peternakan_jurnal.json --max-items 0
```

## Catatan Q-Level

Q-level jurnal **berubah per tahun dan kategori**. Untuk jawaban AI, jangan membuat klaim final seperti “jurnal ini Q1” tanpa menyebut tahun dan sumber pengecekan. Gunakan SCImago/Scopus/JCR untuk jurnal internasional dan SINTA untuk jurnal Indonesia.

## Rekomendasi operasional

- Biarkan sumber Google News `fetch_article: false` agar update harian tetap ringan.
- Sumber SCImago dibuat `enabled: false` karena sebagian halaman ranking dapat membatasi scraping otomatis. Gunakan sebagai link referensi/manual, sedangkan panduan statis tetap masuk ke KB.
- Untuk kebutuhan akademik, prioritaskan sumber jurnal/SINTA dibanding agregator berita.
