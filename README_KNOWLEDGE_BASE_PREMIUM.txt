PATCH: Knowledge Base Premium untuk Adioranye AI

Tujuan:
Meningkatkan Knowledge Base agar lebih mirip AI populer: dokumen bisa dikelompokkan, dicari lebih akurat, diberi sitasi, diprioritaskan, dan dikelola dari admin maupun Telegram.

Fitur baru:
1. Koleksi/workspace dokumen
   - Setiap dokumen punya collection, contoh: Default, Produk, SOP, Skripsi, E-learning.
   - Admin bisa filter pencarian dan daftar dokumen berdasarkan koleksi.

2. Tags dokumen
   - Dokumen bisa diberi tags seperti produk, sop, bpjs, jalan, jembatan.
   - Tags ikut dipakai untuk ranking pencarian.

3. Deduplikasi dokumen
   - Dokumen yang sama tidak akan otomatis diduplikasi.
   - Admin bisa memilih Replace jika dokumen sama ingin diganti.

4. Hybrid search
   - Menggunakan SQLite FTS5 jika tersedia.
   - Jika FTS5 tidak tersedia, fallback ke lexical search.
   - Ranking menggabungkan title, heading, tags, collection, isi chunk, dan pinned document.

5. Citation-ready chunks
   - Chunk menyimpan heading, label halaman, collection, tags, dan citation.
   - Jawaban bisa menyebut sumber [KB1], [KB2], dan metadata sumber tampil di caption admin.

6. Pin/unpin dokumen
   - Dokumen penting bisa diprioritaskan dalam search.
   - Bisa dari UI web atau Telegram.

7. UI Knowledge Base lebih lengkap
   - Upload File: collection, tags, pin, replace duplicate.
   - Tambah Manual: collection, tags, pin.
   - Cari: filter koleksi dan lihat citation.
   - Koleksi: lihat daftar workspace.
   - Kelola: update metadata, pin/unpin, preview, delete, rebuild index.

8. Telegram command baru
   - /kb koleksi
   - /kb pin <doc_id>
   - /kb unpin <doc_id>
   - /kb set <doc_id> <koleksi> | <tag1,tag2>

Secret yang disarankan:
POWER_RAG_ENABLED = true
POWER_RAG_TOP_K = 7
POWER_KB_MAX_FILE_MB = 12

Catatan:
- Jika database lama sudah ada, patch ini akan melakukan migration ringan otomatis saat app start.
- Jika hasil search belum muncul setelah upgrade, buka Admin > Knowledge Base > Kelola lalu klik Rebuild index Knowledge Base.
