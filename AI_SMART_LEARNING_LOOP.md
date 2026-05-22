# Adioranye AI Smart Learning Loop

Paket ini menambahkan lapisan agar Adioranye tidak hanya mengambil knowledge base harian, tetapi juga belajar dari kualitas jawaban dan kekurangan sumber.

## Fitur baru

1. **Feedback jawaban**
   - Tombol `👍 Bagus` dan `👎 Kurang` pada jawaban web.
   - Feedback disimpan ke tabel `user_feedback`.
   - Feedback positif/negatif ikut menyesuaikan skor model per intent.

2. **Knowledge Gap Detector**
   - Jika Strict RAG aktif dan sumber KB tidak cukup, sistem menyimpan gap ke tabel `knowledge_gaps`.
   - Jika user memberi feedback negatif, sistem juga bisa mencatat gap.
   - Admin bisa melihat dan menandai gap selesai dari panel `Learning Loop`.

3. **Source Quality Ranking**
   - Dokumen KB punya `source_quality` 0–100.
   - Sumber resmi, jurnal, SCImago/SINTA, Kemenkes, Kementan, WHO/FAO/WOAH, NASA/MIT diberi prioritas lebih tinggi.
   - Search RAG sekarang memberi bobot pada relevansi teks, pin dokumen, kualitas sumber, dan recency.

4. **Strict RAG Mode**
   - Jika `POWER_STRICT_RAG_MODE = true`, AI hanya menjawab jika KB memiliki sumber relevan minimal.
   - Cocok untuk mode akademik, jurnal, kesehatan, peternakan, hukum, SOP internal, atau data yang wajib berbasis sumber.
   - Default dibuat `false` agar chat umum tetap fleksibel.

5. **Auto Summarizer untuk Scraper**
   - Scraper menambahkan ringkasan otomatis, kata kunci, domain sumber, dan skor kualitas sumber ke tiap dokumen.
   - Ini membuat RAG lebih tajam daripada menyimpan artikel mentah saja.

6. **Answer Template Memory**
   - Admin bisa menyimpan jawaban bagus sebagai template.
   - Template relevan akan ikut dimasukkan sebagai konteks struktur/gaya jawaban.

7. **Domain Expert Mode**
   - Intent baru: `livestock` dan `health`.
   - Pertanyaan peternakan/kesehatan otomatis mendapat instruksi kehati-hatian dan prioritas sumber yang sesuai.

## Secrets penting

Tambahkan ke `.streamlit/secrets.toml` bila perlu:

```toml
POWER_STRICT_RAG_MODE = false
POWER_RAG_MIN_SOURCES = 1
POWER_RAG_MIN_SCORE = 0
POWER_PERSISTENT_MEMORY_ENABLED = true
POWER_PROMPT_TEMPLATES_ENABLED = true
POWER_SELF_VERIFICATION_ENABLED = false
POWER_RESPONSE_CACHE_ENABLED = true
POWER_ADAPTIVE_SCORING_ENABLED = true
POWER_CIRCUIT_BREAKER_ENABLED = true
```

## Panel admin baru

Buka:

```text
Admin → Power Features → Learning Loop
```

Di sana admin bisa melihat:

- feedback positif/negatif,
- intent yang paling sering digunakan,
- pertanyaan berulang,
- knowledge gap terbuka,
- interaksi terbaru,
- penyimpanan template jawaban manual.

## Perintah Telegram/admin tambahan

```text
/gap list
/gap selesai <id>
/feedback statistik
```

## Rekomendasi penggunaan

- Biarkan `POWER_STRICT_RAG_MODE = false` untuk chat publik umum.
- Aktifkan `POWER_STRICT_RAG_MODE = true` jika bot dipakai untuk menjawab hanya berdasarkan KB, misalnya SOP, jurnal, atau data internal.
- Gunakan feedback negatif untuk mengumpulkan gap, lalu tambahkan sumber ke KB.
- Simpan jawaban yang paling bagus sebagai template agar jawaban berikutnya makin konsisten.
