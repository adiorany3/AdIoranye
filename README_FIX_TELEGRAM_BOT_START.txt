PATCH FIX TELEGRAM BOT START

Masalah yang diperbaiki:
1. telegram_service.py memakai re.sub() pada formatter Telegram, tetapi import re belum ada pada patch sebelumnya. Ini bisa membuat bot gagal membalas saat _send_message dijalankan.
2. Start Bot sebelumnya bisa terlihat berhasil walaupun token Telegram invalid, karena validasi token baru terjadi di thread polling.
3. Error Telegram 401/404/409 sekarang dibuat lebih jelas:
   - 401: token invalid/revoked
   - 404: format token salah
   - 409: token dipakai instance lain/getUpdates lain
4. Loop polling tidak lagi terus berputar untuk error fatal seperti invalid token atau 409 conflict.
5. Admin UI mendapat tombol "Tes koneksi Telegram" untuk cek getMe/getWebhookInfo.
6. Jika Start Bot gagal, UI sekarang menampilkan detail last_error, bukan hanya "bot sudah berjalan".

Cara pakai:
- Replace app.py dan telegram_service.py dengan file dari ZIP ini.
- Restart/redeploy Streamlit.
- Masuk Admin > Telegram.
- Klik "Tes koneksi Telegram".
- Jika OK, klik "Reset koneksi Telegram / hapus pending update".
- Klik "Start Bot".

Catatan penting:
Jika muncul 409 Conflict, artinya token bot masih dipakai di deploy/laptop/VPS lain. Matikan instance lama atau revoke token di BotFather lalu update TELEGRAM_BOT_TOKEN.
Jika token pernah ditempel di chat publik, sebaiknya buat token baru di BotFather.
