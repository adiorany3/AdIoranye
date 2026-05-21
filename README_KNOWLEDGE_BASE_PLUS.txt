# Patch Knowledge Base Plus - Adioranye AI

Patch ini menambahkan knowledge base/RAG yang lebih lengkap untuk web dan Telegram.

## Fitur baru

1. Upload knowledge base multi-format dari panel Admin:
   - txt, md, markdown, csv, json, jsonl
   - pdf jika tersedia pypdf/PyPDF2
   - docx jika tersedia python-docx
   - xlsx/xlsm jika tersedia openpyxl
   - log, py, js, ts, html, css, xml

2. Manajemen knowledge base:
   - statistik jumlah dokumen, chunk, dan karakter
   - list dokumen terakhir
   - preview dokumen berdasarkan Doc ID
   - hapus dokumen berdasarkan Doc ID
   - rebuild index FTS5

3. Search lebih kuat:
   - SQLite FTS5 jika tersedia
   - fallback lexical search jika FTS5 tidak tersedia

4. Integrasi otomatis ke jawaban:
   - jawaban web dan Telegram mengambil potongan KB relevan
   - sumber KB masuk ke metadata `power_kb_sources`
   - caption web menampilkan jumlah sumber KB yang dipakai

5. Perintah Telegram admin:
   - /kb bantuan
   - /kb statistik
   - /kb list
   - /kb cari <query>
   - /kb detail <doc_id>
   - /kb hapus <doc_id>
   - /kb rebuild
   - /kb tambah <judul>\n<isi dokumen>

## Secret tambahan yang disarankan

```toml
POWER_RAG_ENABLED = true
POWER_RAG_TOP_K = 5
POWER_KB_MAX_FILE_MB = 12
```

## Catatan dependency opsional

Agar ekstraksi file lebih lengkap di Streamlit Cloud, tambahkan ke `requirements.txt` bila belum ada:

```txt
pypdf
python-docx
openpyxl
```

Jika dependency tidak ada, app tidak crash. File terkait hanya akan memberi pesan gagal ekstrak.

## Cara pasang

1. Replace file lama dengan isi ZIP ini.
2. Tambahkan secret baru bila diperlukan.
3. Redeploy/restart aplikasi.
4. Login admin > Power Features > Knowledge Base.
5. Upload file atau tambah dokumen manual.
6. Test pertanyaan seperti: `berdasarkan knowledge base, jelaskan ...`.
