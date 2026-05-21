Patch: Fix PowerStore.model_score_rows

Masalah:
Power Features gagal dimuat dengan error:
'PowerStore' object has no attribute 'model_score_rows'

Penyebab:
UI Optimizer dan command Telegram memanggil method optimizer:
- model_score_rows()
- circuit_breaker_status()
- rank_models_for_intent()
- filter_blocked_models()
- get_cached_response()
- set_cached_response()
- register_model_failure()/success()
- update_model_score()

Pada patch Knowledge Base Premium, beberapa method kompatibilitas optimizer belum ikut masuk ke class PowerStore.

Perbaikan:
- Menambahkan kembali semua method optimizer yang hilang ke power_features.py.
- Menambahkan persistent response cache SQLite.
- Menambahkan adaptive model scoring per intent.
- Menambahkan circuit breaker status dan filter model yang sedang dikarantina.
- Menambahkan dashboard rows untuk tab Optimizer.
- Menambahkan helper timestamp WIB untuk tampilan admin.

Cara pasang:
1. Replace file lama dengan isi ZIP ini.
2. Redeploy/restart Streamlit.
3. Buka Admin -> Power Features / Optimizer.

Catatan:
Patch ini tidak mengubah UI chat, knowledge base, Telegram, atau router utama. Fokusnya memperbaiki method PowerStore yang hilang agar Power Features dapat dimuat kembali.
