PATCH OPTIMAL MODEL ROUTER - Adioranye AI

Isi patch:
- app.py
- ai_core.py
- telegram_service.py
- power_features.py
- slashai_model_catalog.json

Fitur baru yang ditambahkan:
1. Adaptive Model Scoring
   - Menyimpan performa model per intent ke SQLite.
   - Skor dihitung dari success rate, quality score, latency, biaya, dan error rate.
   - Router dapat memilih model yang historisnya paling cocok untuk quick chat, coding, academic, research, RAG, dan deep reasoning.

2. Circuit Breaker Model
   - Jika model gagal berulang, model dikarantina sementara.
   - Mengurangi percobaan berulang ke model yang sedang error/rate-limit/down.

3. Per-Intent Best Model Policy
   - Model diurutkan berdasarkan intent pertanyaan.
   - Coding cenderung memprioritaskan coder/codex/deepseek/qwen.
   - Quick chat cenderung memprioritaskan flash/nano/mini/haiku.
   - Academic/research/deep reasoning cenderung memprioritaskan model capable.

4. Persistent Response Cache SQLite
   - Jawaban prompt non-coding dapat dicache ke SQLite.
   - Cache tetap ada setelah Streamlit restart selama file database tidak terhapus.

5. Token Budget Otomatis per Intent
   - quick_chat: lebih hemat token.
   - coding/academic/RAG/research/deep reasoning: token lebih besar.

6. RAG lebih kuat
   - Tetap tanpa dependency berat.
   - Menggunakan SQLite FTS5 jika tersedia, fallback ke lexical scoring jika FTS5 tidak tersedia.
   - Memory/RAG disanitasi sebagai konteks non-instruksi untuk mengurangi prompt injection.

7. Dashboard Optimizer
   - Tab baru di panel Power Features: Optimizer.
   - Menampilkan skor model adaptif dan circuit breaker status.

8. Telegram ikut memakai optimizer
   - generate_power_answer di Telegram menerima adaptive scoring, response cache, dan circuit breaker.

Tambahan secrets yang direkomendasikan:

POWER_RESPONSE_CACHE_ENABLED = true
POWER_RESPONSE_CACHE_TTL_SECONDS = 1800
POWER_ADAPTIVE_SCORING_ENABLED = true
POWER_CIRCUIT_BREAKER_ENABLED = true
MODEL_CIRCUIT_MAX_FAILURES = 3
MODEL_CIRCUIT_COOLDOWN_SECONDS = 1800

Catatan:
- Patch sudah lolos python -m py_compile untuk app.py, ai_core.py, telegram_service.py, dan power_features.py.
- API SlashAI tidak dites dari lingkungan ini karena API key/runtime ada di Streamlit Anda.
- Setelah deploy, jalankan /speed 4321 atau /rotate untuk memperbarui model aktif.
- Untuk melihat skor model dari Telegram admin, pakai /model skor.
- Untuk melihat circuit breaker dari Telegram admin, pakai /circuit.
