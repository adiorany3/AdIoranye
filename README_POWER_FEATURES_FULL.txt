PATCH FULL POWER FEATURES - Adioranye AI

Berbasis patch dynamic API model discovery sebelumnya. Isi patch:
- app.py
- ai_core.py
- telegram_service.py
- power_features.py
- slashai_model_catalog.json

FITUR BARU
1. SQLite persistent memory
   - File default: .adioranye_power.db
   - Perintah admin: /ingat <teks>, /lupa <keyword>
   - Memory relevan otomatis ikut masuk ke prompt.

2. Lightweight RAG / Knowledge Base
   - Tanpa dependency berat seperti Chroma/FAISS, aman untuk Streamlit Cloud.
   - Admin web bisa upload TXT/MD/CSV.
   - Telegram admin:
     /rag tambah <judul>
     <isi dokumen>
     /rag cari <query>
   - Konteks dokumen relevan otomatis ikut masuk ke prompt.

3. Intent router metadata + prompt template
   - Intent dideteksi: quick_chat, coding, academic, calculation, document_question,
     research, creative, deep_reasoning, general.
   - Prompt tambahan otomatis disesuaikan dengan intent.

4. Cost guard + usage logging
   - Semua request web/Telegram masuk tabel interactions.
   - Estimasi biaya dihitung dari usage token jika provider mengembalikan usage.
   - Perintah admin Telegram: /biaya
   - Web admin: panel Usage.

5. Auto benchmark ringan
   - Web admin dapat menjalankan benchmark singkat pada model aktif.
   - Hasil disimpan ke tabel benchmarks.

6. Self-correction / self-verification opsional
   - Default OFF agar hemat.
   - Jika ON, jawaban coding/akademik/hitung/riset/dokumen dicek ulang oleh model verifier.

7. Integrasi Telegram
   - Semua fitur power context juga aktif di bot Telegram.
   - Perintah model tetap admin-only jika TELEGRAM_ADMIN_CHAT_IDS diisi.

SECRET TAMBAHAN YANG DIREKOMENDASIKAN
POWER_FEATURES_ENABLED = true
POWER_DB_PATH = ".adioranye_power.db"
POWER_RAG_ENABLED = true
POWER_PERSISTENT_MEMORY_ENABLED = true
POWER_PROMPT_TEMPLATES_ENABLED = true
POWER_SELF_VERIFICATION_ENABLED = false
DAILY_COST_LIMIT_IDR = 0
MAX_EXPENSIVE_CALLS_PER_DAY = 0
BENCHMARK_MAX_MODELS = 8

CATATAN
- RAG versi ini adalah keyword/hybrid ringan, bukan embedding vector database. Stabil dan minim dependency.
- Kalau nanti ingin kualitas retrieval lebih tinggi, power_features.py bisa ditingkatkan ke FAISS/Chroma + embedding.
- DAILY_COST_LIMIT_IDR = 0 berarti tidak ada hard limit biaya.
- MAX_EXPENSIVE_CALLS_PER_DAY = 0 berarti tidak membatasi jumlah call medium/mahal.

CARA PASANG
1. Replace app.py, ai_core.py, telegram_service.py dengan file patch ini.
2. Tambahkan power_features.py di folder yang sama.
3. Pastikan slashai_model_catalog.json ikut di folder app.
4. Tambahkan secret baru sesuai kebutuhan.
5. Restart/redeploy.
6. Login admin di web, buka panel "Power Features".
7. Jalankan /help di Telegram untuk melihat perintah baru.

PERINTAH TELEGRAM ADMIN BARU
/ingat <teks>
/lupa <keyword>
/rag tambah <judul>
<isi>
/rag cari <query>
/biaya
/template list

PERINTAH MODEL SEBELUMNYA TETAP ADA
/speed 4321
/rotate
/ubah mahal
/ubah murah
