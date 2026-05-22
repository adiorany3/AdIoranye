# Adioranye AI Quality Control & Verifier System

Paket ini menambahkan lapisan kontrol kualitas agar jawaban Adioranye lebih stabil, lebih berbasis bukti, dan lebih minim halusinasi.

## Fitur utama

1. **Mode jawaban**
   - `auto`: sistem memilih mode berdasarkan risiko pertanyaan.
   - `hemat`: jawaban cepat, pendek, dan hemat token.
   - `pintar`: jawaban lebih lengkap dan teliti.
   - `riset`: mengutamakan Knowledge Base/RAG dan sumber.
   - `kritis`: mode anti-halusinasi paling ketat untuk isu terkini, kesehatan, hukum, jurnal, angka, dan klaim berisiko.

2. **Quality scoring otomatis**
   Setiap jawaban diberi skor berdasarkan:
   - jumlah sumber KB,
   - kualitas sumber,
   - freshness/kebaruan sumber,
   - risiko klaim tanpa bukti,
   - catatan ketidakpastian,
   - panjang dan kecukupan jawaban.

3. **Verifier model**
   Jika skor jawaban rendah atau mode `riset/kritis` aktif, sistem dapat memakai model verifier untuk mengecek dan memperbaiki jawaban sebelum dikirim.

4. **Dashboard Quality Control**
   Di panel admin Streamlit tersedia tab `✅ Quality Control` untuk melihat:
   - skor rata-rata jawaban,
   - jawaban skor rendah,
   - kualitas per mode,
   - kualitas per intent,
   - evaluasi mingguan,
   - export/import Knowledge Base JSONL.

5. **Memory per user Telegram**
   Perintah `/mode` menyimpan preferensi mode jawaban per `chat_id`, sehingga pengguna Telegram bisa memilih mode sendiri.

6. **Evaluasi mingguan otomatis/manual**
   Admin bisa menjalankan `/laporan mingguan` di Telegram atau tombol evaluasi di dashboard.

## Secret baru

Tambahkan ke Streamlit Secrets jika belum ada:

```toml
POWER_QUALITY_CONTROL_ENABLED = true
POWER_QUALITY_VERIFIER_ENABLED = true
POWER_QUALITY_VERIFIER_MODEL = ""
POWER_QUALITY_MIN_SCORE = 0.72
POWER_QUALITY_APPEND_FOOTER = false
POWER_DEFAULT_ANSWER_MODE = "auto"
```

Catatan:
- `POWER_QUALITY_VERIFIER_MODEL` boleh dikosongkan. Sistem akan memilih model capable/expensive aktif sebagai verifier.
- Jika biaya ingin lebih hemat, set `POWER_QUALITY_VERIFIER_ENABLED = false`.
- Jika ingin semua jawaban memperlihatkan skor kualitas, set `POWER_QUALITY_APPEND_FOOTER = true`.

## Command Telegram baru

```text
/mode
/mode hemat
/mode pintar
/mode riset
/mode kritis
/mode auto
/kualitas
/laporan mingguan
```

Keterangan:
- `/mode` bisa dipakai user Telegram untuk mengatur mode jawaban miliknya sendiri.
- `/kualitas` dan `/laporan mingguan` hanya untuk admin.

## Rekomendasi konfigurasi

Untuk pemakaian umum:

```toml
POWER_DEFAULT_ANSWER_MODE = "auto"
POWER_QUALITY_VERIFIER_ENABLED = true
POWER_QUALITY_MIN_SCORE = 0.72
POWER_QUALITY_APPEND_FOOTER = false
```

Untuk riset/akademik/kritis:

```toml
POWER_DEFAULT_ANSWER_MODE = "riset"
POWER_STRICT_RAG_MODE = false
POWER_ANTI_HALLUCINATION_AUTO_STRICT = true
POWER_QUALITY_VERIFIER_ENABLED = true
POWER_QUALITY_MIN_SCORE = 0.78
```

Untuk hemat biaya:

```toml
POWER_DEFAULT_ANSWER_MODE = "hemat"
POWER_QUALITY_VERIFIER_ENABLED = false
POWER_RESPONSE_CACHE_ENABLED = true
```

## Cara kerja ringkas

```text
Pertanyaan user
→ deteksi intent + mode jawaban
→ ambil sumber KB/RAG jika diperlukan
→ anti-halusinasi/evidence gate
→ model utama menjawab
→ quality scoring
→ verifier memperbaiki jika skor rendah
→ simpan quality report + interaction log
→ feedback admin/user dapat menaikkan/menurunkan skor model
```

## Export/Import KB

Di dashboard `✅ Quality Control`, admin bisa:

- download KB sebagai JSONL,
- download log interaksi sebagai JSONL,
- import KB dari JSONL.

Ini berguna untuk backup pengetahuan, migrasi, dan recovery jika database SQLite perlu direset.
