# Perbaikan Tampilan Sumber KB untuk Sapaan/Komunikasi Ringan

Versi ini menyesuaikan perilaku Adioranye agar **sapaan biasa dan komunikasi ringan tidak menampilkan “Sumber KB yang dipakai”**.

## Masalah yang diperbaiki

Sebelumnya, jika RAG menemukan potongan KB walaupun pertanyaan hanya sapaan seperti:

- `halo`
- `hai`
- `apa kabar`
- `terima kasih`
- `oke`
- komunikasi pendek dan ringan

jawaban bisa tetap menampilkan catatan sumber KB. Ini membuat respons terlihat terlalu formal dan tidak natural.

## Perilaku baru

Untuk sapaan/komunikasi ringan:

```text
User: halo
Adioranye: Halo, ada yang bisa saya bantu?
```

Tanpa:

```text
Sumber KB yang dipakai:
...
```

## Kapan sumber KB tetap ditampilkan?

Sumber KB tetap ditampilkan jika pertanyaan memang membutuhkan bukti, misalnya:

- mode `/mode riset`
- mode `/mode kritis`
- pertanyaan berisiko/terkini
- pertanyaan kesehatan, hukum, jurnal, riset, peternakan, dokumen
- user meminta sumber, referensi, bukti, sitasi, validasi, atau cek fakta

Contoh:

```text
User: /mode riset
User: apa perkembangan AI terbaru di Indonesia?
```

Sumber KB tetap boleh muncul.

## Secret baru

Tambahkan atau biarkan default:

```toml
POWER_HIDE_KB_SOURCES_FOR_CASUAL = true
POWER_DISABLE_RAG_FOR_CASUAL = true
```

Penjelasan:

- `POWER_HIDE_KB_SOURCES_FOR_CASUAL`: menyembunyikan tampilan sumber KB untuk sapaan/komunikasi ringan.
- `POWER_DISABLE_RAG_FOR_CASUAL`: tidak menjalankan RAG untuk sapaan/komunikasi ringan supaya lebih cepat dan tidak mengambil sumber tidak relevan.

## File yang berubah

- `power_features.py`
- `app.py`
- `telegram_service.py`
- `.streamlit/secrets.toml.example`

## Catatan

Admin web tetap bisa melihat detail sumber di metadata/caption saat login admin. Pengguna biasa tidak akan melihat sumber KB untuk komunikasi ringan.
