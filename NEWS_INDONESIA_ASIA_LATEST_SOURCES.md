# Paket Sumber Berita Terbaru Indonesia & Asia

Paket ini menambahkan **121 sumber baru** untuk memperkuat knowledge base Adioranye pada isu terkini Indonesia dan Asia.

## Cakupan

- ANTARA RSS resmi: terkini, top news, politik, hukum, ekonomi, bisnis, dunia, ASEAN, tekno, lingkungan, anti-hoax.
- CNA/CNA.id RSS: berita terbaru, Asia, bisnis, dunia, Singapore.
- Google News top headlines untuk negara-negara Asia/Asia Tenggara.
- Google Trends trending untuk Indonesia dan beberapa negara Asia.
- Google News query topik kritis: politik, ekonomi, hukum, teknologi, AI, keamanan siber, kesehatan, pendidikan, sains, bencana, pangan, peternakan, IKN, hubungan luar negeri, hoaks.
- Google News query regional Asia: ASEAN, Indo-Pacific, Laut China Selatan, China, Jepang, Korea, India, Myanmar, Taiwan, ekonomi Asia, teknologi Asia, kesehatan, bencana, US-China.
- GDELT RSS untuk isu kritis Indonesia/Asia.

## File yang ditambahkan

- `kb_sources_indonesia_asia_latest.json` — preset khusus sumber berita terbaru Indonesia & Asia.
- `kb_sources.json` — sudah digabung dengan seluruh sumber baru.
- `critical_watchlist.json` — ditambah topik pemantauan Indonesia & Asia.

## Catatan operasional

Workflow harian tetap memakai `kb_sources.json`. Karena sumber bertambah banyak, update pertama bisa lebih lama. Jika GitHub Actions terlalu lama, kurangi `max_items` pada beberapa sumber Google News/Google Trends atau jalankan preset tertentu saja.

## Rekomendasi penggunaan

Untuk pertanyaan kritis, gunakan command:

```text
/update
/briefing
/trending
/cek isu <topik>
/pantau <topik>
```

Contoh:

```text
/cek isu Laut China Selatan
/cek isu keamanan siber Indonesia
/cek isu PMK ternak Indonesia
```
