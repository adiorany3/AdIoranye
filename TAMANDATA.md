# Tamandata API

Konfigurasi backend memakai `SLASHAI_API_URL=https://ai.tamandata.com/v1` dan
`SLASHAI_API_KEY` pada secrets yang sudah dipakai aplikasi. Jangan masukkan key ke chat.

Panel `/admin`, bagian **Tamandata — seluruh endpoint API**, menyediakan 20 endpoint
yang terdaftar di https://ai.tamandata.com/docs.html. Pilih endpoint, isi objek JSON
sesuai dokumentasi provider, lalu kirim. GET memakai JSON sebagai query parameters.
Transkripsi mendukung upload multipart atau raw. Audio dan JSON dapat diunduh;
gambar base64 ditampilkan, URL gambar dibuka sebagai tautan tanpa server fetch.

Telegram pribadi admin: `/tamandata models` atau `/tamandata responses {"model":"tamandata","input":"Halo","store":false}`.
Endpoint gambar, speech, dan transkripsi diarahkan ke panel web; transfer media Telegram
belum tersedia. Respons Telegram dibatasi 3000 karakter; hasil lengkap tersedia di web.

Ini antarmuka API khusus admin, bukan semua fitur media untuk pengguna publik.
Gerbang voucher dan kuota chat publik tidak berubah. Schema TTS, STT, pencarian,
fetch, embedding, dan metadata tetap mengikuti router provider. Ketersediaan endpoint
tidak menjamin model tersedia atau gratis. POST dapat memakai saldo.
Tidak ada retry otomatis atau redirect. Stream dikumpulkan sampai selesai, bukan
ditampilkan token demi token. Tidak ada eksekusi tool otomatis atau pengelolaan
`previous_response_id`; admin memasukkan state secara eksplisit dan bertanggung jawab
memisahkan percakapan. Health bukan jaminan inference siap.

Validasi offline: jalankan `scripts/verify_tamandata.py` dengan interpreter proyek.
Uji ini tidak memakai key asli atau memanggil provider.