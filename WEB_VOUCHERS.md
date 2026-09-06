# Voucher web

- Admin Telegram, chat pribadi: `/cekvoucher` menampilkan jumlah voucher aktif, total pertanyaan terpakai pada voucher aktif, total sisa pertanyaan, dan rincian tiap voucher. Daftar berisi 20 voucher per halaman; lanjut dengan `/cekvoucher 2`.
- Voucher habis otomatis hilang dari daftar, bukan dihapus dari database, agar jeda baca terakhir tetap aman. ID pemantauan adalah awalan hash, bukan kode penukaran; ID juga disertakan saat membuat voucher baru.
- Admin Telegram, chat pribadi: `/voucher 5 60` membuat voucher 5 pertanyaan dengan jeda baca 60 detik. `/voucher 5` memakai jeda default 60 detik.
- Batas: 1–10000 pertanyaan; jeda 1–3600 detik. Admin harus tercantum dalam `TELEGRAM_ADMIN_CHAT_IDS`.
- Jalankan `/lockweb` agar halaman publik meminta voucher. `/unlockweb MENIT` tetap membuka akses publik tanpa voucher; sesi yang sudah memakai voucher tetap dibatasi kuotanya.
- Voucher terikat ke satu sesi browser saat pertama digunakan. Sesi baru/reload penuh dapat memerlukan voucher baru; kode bukan login lintas perangkat.
- Fitur **Simpan Sisa Voucher** sudah dihapus dari halaman chat. Pemulihan kuota yang sebelumnya tersimpan tetap tersedia melalui URL yang memuat `voucher_token`; gunakan tombol **Gunakan voucher**. Jangan bagikan URL tersebut karena token memberikan akses ke kuota tersimpan.
- Setiap pertanyaan yang diterima mengurangi kuota, termasuk respons pembatasan laju/error. Kuota tidak dikembalikan otomatis.
- Pertanyaan terakhir tetap dijawab. Composer hilang setelah kuota habis; jeda dimulai setelah pemrosesan jawaban selesai. Pemeriksaan setiap 2 detik menutup riwayat ketika jeda habis. Salinan/download yang sudah dibuat pengguna tidak dapat ditarik kembali.
- Web dan Telegram harus memakai file SQLite yang sama pada penyimpanan lokal persisten. Opsional: `WEB_VOUCHER_DB_PATH` (default `.adioranye_web_vouchers.sqlite3`). Jangan memakai database terpisah antar replika atau filesystem jaringan.
- Membutuhkan Streamlit >=1.37. Uji mandiri: `scripts/verify_web_vouchers.py`.