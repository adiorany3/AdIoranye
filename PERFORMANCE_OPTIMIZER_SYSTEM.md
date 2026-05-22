# Adioranye AI Performance Optimizer System

Paket ini menambahkan lapisan optimasi performa tanpa dependency berat dan tetap kompatibel dengan Streamlit Cloud.

## Fitur Baru

1. **Query Rewriter**
   - Pertanyaan pendek diubah menjadi query RAG yang lebih kaya.
   - Contoh: `AI terbaru di Indonesia?` diperluas dengan istilah seperti `artificial intelligence`, `teknologi Indonesia`, `research`, dan `latest`.

2. **KB Reranker**
   - Hasil pencarian Knowledge Base tidak langsung dipakai.
   - Sistem mengambil kandidat lebih banyak, lalu memilih ulang berdasarkan:
     - relevansi teks,
     - kualitas sumber,
     - freshness,
     - criticality,
     - keragaman domain/dokumen.

3. **Semantic Cache Ringan**
   - Pertanyaan yang mirip secara makna bisa memakai jawaban cache.
   - Cache tidak aktif untuk mode `riset`, `kritis`, atau strict RAG agar tidak memakai jawaban lama pada pertanyaan berisiko.

4. **Latency Budget**
   - Mode hemat/sapaan diberi batas waktu lebih pendek.
   - Mode riset/kritis diberi batas lebih panjang.

5. **Retrieval Evaluation**
   - Setiap pencarian RAG dicatat:
     - search query,
     - jumlah sumber,
     - estimasi precision,
     - similarity rata-rata,
     - latency retrieval.

6. **Dashboard Performance**
   - Admin web mendapat tab baru: `⚡ Performance`.
   - Berisi retrieval precision, semantic cache, latency per intent, retrieval terbaru, sumber lambat/bermasalah, dan maintenance DB.

7. **Command Telegram Admin**
   - `/performa` → ringkasan performa AI.
   - `/optimasi db` → menjalankan `PRAGMA optimize` dan `ANALYZE`.

8. **Maintenance SQLite**
   - Tombol admin untuk:
     - `PRAGMA optimize + ANALYZE`,
     - bersihkan cache respons,
     - `VACUUM DB`.

## Konfigurasi Secrets

Tambahkan ke Streamlit Secrets jika ingin mengubah default:

```toml
POWER_PERFORMANCE_OPTIMIZER_ENABLED = true
POWER_QUERY_REWRITER_ENABLED = true
POWER_RERANKER_ENABLED = true
POWER_SEMANTIC_CACHE_ENABLED = true
POWER_SEMANTIC_CACHE_THRESHOLD = 0.78
POWER_SEMANTIC_CACHE_TTL_SECONDS = 86400
POWER_LATENCY_BUDGET_ENABLED = true
POWER_RETRIEVAL_EVAL_ENABLED = true
```

## Rekomendasi Penggunaan

- Untuk chat ringan, fitur casual fast path tetap menghindari RAG.
- Untuk pertanyaan riset/kritis, semantic cache otomatis tidak dipakai.
- Untuk KB besar, biarkan reranker aktif karena hasil RAG lebih selektif.
- Jalankan `/optimasi db` atau tombol admin `PRAGMA optimize + ANALYZE` minimal 1 minggu sekali.

## Catatan Keamanan

Semantic cache tidak memakai embedding eksternal. Perhitungan kemiripan dilakukan lokal dengan token similarity agar aman, murah, dan ringan untuk Streamlit.
