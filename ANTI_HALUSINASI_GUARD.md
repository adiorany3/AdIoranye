# Anti-Halusinasi Guard Adioranye

Paket ini menambahkan lapisan anti-halusinasi ringan untuk web dan Telegram.
Tujuannya bukan membuat AI pasti benar, tetapi mengurangi jawaban yang mengarang fakta saat sumber Knowledge Base belum cukup.

## Fitur yang ditambahkan

1. **High-risk question detector**
   - Mendeteksi pertanyaan kritis/terkini seperti kesehatan, hukum, regulasi, jurnal Q1-Q4, berita terbaru, harga, politik, dan klaim “apakah benar”.

2. **Evidence gate**
   - Untuk pertanyaan kritis, sistem mengecek apakah ada sumber KB yang cukup.
   - Jika sumber tidak cukup, sistem menolak menjawab secara spekulatif dan meminta admin menjalankan `/update` atau menambah sumber.

3. **Temperature policy**
   - Untuk pertanyaan kritis, suhu model otomatis diturunkan agar jawaban lebih konservatif.

4. **Guard instruction**
   - Model mendapat instruksi eksplisit agar tidak membuat tanggal, angka, nama jurnal, regulasi, sumber, atau klaim faktual palsu.

5. **Source note**
   - Untuk jawaban kritis, sistem dapat menambahkan catatan sumber KB yang dipakai.

6. **Knowledge gap logging**
   - Jika jawaban diblokir karena bukti kurang, pertanyaan otomatis masuk ke Knowledge Gap agar admin tahu topik mana yang perlu ditambah.

## File baru

```text
hallucination_guard.py
ANTI_HALUSINASI_GUARD.md
```

## Konfigurasi Streamlit Secrets

Tambahkan atau biarkan default berikut:

```toml
POWER_ANTI_HALLUCINATION_ENABLED = true
POWER_ANTI_HALLUCINATION_AUTO_STRICT = true
POWER_ANTI_HALLUCINATION_MIN_SOURCES = 1
POWER_ANTI_HALLUCINATION_MIN_QUALITY = 0
POWER_ANTI_HALLUCINATION_MIN_FRESHNESS = 0
POWER_ANTI_HALLUCINATION_APPEND_SOURCES = true
```

### Penjelasan

- `POWER_ANTI_HALLUCINATION_ENABLED`: mengaktifkan guard.
- `POWER_ANTI_HALLUCINATION_AUTO_STRICT`: untuk pertanyaan kritis, jawaban diblokir jika sumber tidak cukup.
- `POWER_ANTI_HALLUCINATION_MIN_SOURCES`: minimal sumber KB untuk menjawab pertanyaan kritis.
- `POWER_ANTI_HALLUCINATION_MIN_QUALITY`: skor kualitas sumber minimal. Biarkan `0` jika belum yakin.
- `POWER_ANTI_HALLUCINATION_MIN_FRESHNESS`: skor freshness minimal. Biarkan `0` jika belum yakin.
- `POWER_ANTI_HALLUCINATION_APPEND_SOURCES`: tampilkan sumber KB yang dipakai di akhir jawaban.

## Rekomendasi setting awal

Pakai setting aman tapi tidak terlalu ketat:

```toml
POWER_ANTI_HALLUCINATION_ENABLED = true
POWER_ANTI_HALLUCINATION_AUTO_STRICT = true
POWER_ANTI_HALLUCINATION_MIN_SOURCES = 1
POWER_ANTI_HALLUCINATION_MIN_QUALITY = 0
POWER_ANTI_HALLUCINATION_MIN_FRESHNESS = 0
POWER_ANTI_HALLUCINATION_APPEND_SOURCES = true
```

Kalau Knowledge Base sudah banyak dan rapi, bisa diperketat:

```toml
POWER_ANTI_HALLUCINATION_MIN_SOURCES = 2
POWER_ANTI_HALLUCINATION_MIN_QUALITY = 60
POWER_ANTI_HALLUCINATION_MIN_FRESHNESS = 40
```

## Dampak perilaku jawaban

Jika user bertanya:

```text
Apakah benar ada regulasi AI terbaru di Indonesia?
```

Jika KB belum punya sumber relevan, AI tidak akan mengarang. AI akan menjawab bahwa data belum cukup dan menyarankan update KB.

Jika KB punya sumber, AI akan menjawab lebih hati-hati dan menampilkan sumber KB yang dipakai.

## Catatan penting

- Guard ini paling efektif jika RAG/Knowledge Base aktif.
- Untuk pertanyaan umum/kreatif/coding, guard tidak terlalu ketat.
- Untuk topik sangat berubah cepat, tetap perlu update KB harian lewat GitHub Actions atau `/update` Telegram.
