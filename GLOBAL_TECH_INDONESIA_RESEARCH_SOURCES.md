# Global Tech, Teknologi Indonesia, dan Riset Berjalan

Paket ini menambahkan preset sumber knowledge untuk menjawab pertanyaan kritis tentang:

1. berita teknologi global terbaru,
2. perkembangan teknologi di Indonesia,
3. riset/publikasi yang sedang berjalan atau baru dirilis,
4. sinyal topik yang sedang naik melalui Google News, Google Trends, GDELT, arXiv, dan OpenAlex.

## File yang ditambahkan

- `kb_sources_global_tech_indonesia_research.json` — preset khusus sumber baru.
- `kb_sources.json` — sudah digabung dengan preset baru.
- `critical_watchlist.json` — ditambah topik pemantauan teknologi global, teknologi Indonesia, dan riset.
- `daily_kb_scraper.py` — ditambah dukungan `type: "openalex_works"` untuk mengambil metadata publikasi ilmiah dari OpenAlex.

## Jumlah sumber

Setelah pembaruan ini, `kb_sources.json` berisi 292 sumber total.
Preset baru menambahkan 135 sumber, meliputi:

- RSS teknologi global: TechCrunch, The Verge, Ars Technica, Engadget, MIT Technology Review, WIRED, IEEE Spectrum, ScienceDaily, VentureBeat AI, NVIDIA Blog, AWS ML Blog, Microsoft AI Blog, Google Developers Blog, GitHub Changelog, Cloudflare Blog, BleepingComputer, Hacker News.
- Google News query untuk AI, chip, quantum, robotics, cybersecurity, data center, clean energy, space tech, biotech, autonomous vehicle, fintech, XR, open source, AI agents, dan enterprise AI.
- GDELT untuk isu global: AI, cybersecurity, semiconductor, space tech, energy tech, robotics.
- Sumber Indonesia resmi/semiterstruktur: Komdigi RSS, Infrastruktur Digital Komdigi, BRIN, Kemdiktisaintek.
- Google News query Indonesia: AI, riset AI, transformasi digital, cybersecurity, startup, fintech, data center, satelit internet, IKN smart city, EV battery, semikonduktor, healthtech, edtech, agritech, peternakan tech, robotika, bioteknologi, energi baru, digital identity, UMKM digital, dan lain-lain.
- arXiv RSS: cs.AI, cs.LG, cs.CL, cs.CV, cs.RO, cs.CR, cs.SE, cs.HC, eess.SY, stat.ML, q-bio.
- OpenAlex API untuk publikasi riset terbaru terkait Indonesia dan global.

## Cara menjalankan manual

```bash
python daily_kb_scraper.py \
  --db .adioranye_power.db \
  --sources kb_sources.json \
  --state .adioranye_kb_scrape_state.json \
  --max-items 0 \
  --watchlist critical_watchlist.json \
  --briefing-file daily_intelligence_briefing.md
```

`--max-items 0` berarti scraper memakai `max_items` masing-masing sumber.

## Catatan kualitas jawaban

Untuk pertanyaan kritis, sistem harus membedakan:

- **berita populer**: sinyal isu, belum tentu bukti final;
- **sumber resmi**: lebih kuat untuk kebijakan, program, atau klarifikasi;
- **preprint arXiv**: cepat dan teknis, tetapi belum tentu peer-reviewed;
- **OpenAlex**: metadata publikasi, berguna untuk menemukan riset terbaru dan penulis/jurnal;
- **media teknologi**: bagus untuk perkembangan industri, tetapi perlu verifikasi jika menyangkut klaim ilmiah atau hukum.

## Command Telegram yang relevan

- `/update` — memicu update KB via GitHub Actions.
- `/briefing` — melihat ringkasan update harian.
- `/trending` — melihat isu yang naik dari hasil update.
- `/cek isu <topik>` — cek isu/topik kritis dalam KB.
- `/pantau <topik>` — tambah topik pantauan.
- `/pantau list` — lihat topik pantauan.
