# Tamandata API

## Tier chat otomatis

`config/adioranye_runtime_policy.json` mengatur preferensi standar
`gemini/gemini-2.5-flash`, reasoning menengah `tamandata`, dan tugas berat
`cx/gpt-6-astra`. Penanda leksikal atau input lebih dari 120 kata memicu tier berat;
mode kritis dan risiko tinggi juga memprioritaskan Astra pada jalur power.
Pertanyaan current saja tidak memaksa Astra; pemeriksaan sumber tetap berlaku.
Model mahal masih boleh menjadi fallback jika jawaban murah tidak memadai dan
admin mengizinkan. Matikan smart router untuk mempertahankan pilihan model manual.

`tamandata.py` hanya transport API: tidak ada mesin reasoning deterministik lokal.
Nama `tamandata` diteruskan melalui jalur chat provider yang sudah ada, bukan
dianggap kalkulator lokal atau LLM yang kemampuannya sudah teruji. ID model berasal
dari katalog konfigurasi; ketersediaan, kemampuan, dan biaya inference belum
diverifikasi melalui panggilan live. Kandidat yang diblokir tidak dipulihkan.

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