# Tambahan Knowledge Global: Agro, Peternakan, Teknik Pertanian/Biosistem, Lingkungan, Akuakultur, Hukum, Budaya, Teknologi, dan Dampak Sosial

Paket ini menambahkan **303 sumber baru** ke `kb_sources.json`.

## Cakupan baru

1. Agro dan ketahanan pangan global.
2. Peternakan, kesehatan hewan, pakan, One Health, AMR, dan penyakit ternak.
3. Teknik pertanian dan biosistem: sensor, IoT, drone, pascapanen, cold chain, greenhouse, bioenergi, dan biomass.
4. Lingkungan: iklim, biodiversitas, polusi, deforestasi, water security, disaster risk, carbon market, dan climate finance.
5. Akuakultur: budidaya ikan/udang/rumput laut, aquafeed, fish disease, fisheries, marine heatwaves, blue economy.
6. Hukum: hukum internasional, HAM, hukum lingkungan/iklim, hukum digital/AI/siber, data protection, IP, dan trade law.
7. Budaya: UNESCO, heritage, creative economy, digital culture, bahasa, museum, dan cultural policy.
8. Perkembangan teknologi terbaru: frontier AI, robotics, quantum, biotech, semiconductor, cybersecurity, battery, EV, clean energy, climate tech.
9. Dampak sosial global: ketimpangan, migrasi, konflik, pekerjaan, pendidikan, kesehatan publik, mis/disinformation, dan digital divide.

## Jenis sumber

- `Google News RSS`: sinyal berita terbaru lintas negara dan bahasa.
- `GDELT RSS`: pemantauan isu global berbasis liputan media dunia.
- `OpenAlex API`: metadata riset dan publikasi ilmiah terbaru.
- Sumber resmi/otoritatif: FAO, UNEP, UNESCO, ICJ, World Bank, ADB.
- `static`: panduan kurasi agar AI tidak menyamakan berita viral dengan fakta resmi.

## Catatan operasional

Jumlah sumber sekarang besar, sehingga workflow dinaikkan menjadi **75 menit**. Jika update terlalu lama, kurangi `max_items` atau nonaktifkan sebagian sumber di file preset:

```text
kb_sources_agro_livestock_biosystem_environment_aquaculture_law_culture_social_global.json
```

Untuk jawaban kritis, tetap pakai prinsip:

```text
status data → bukti terbaru → sumber paling kuat → hal yang belum pasti → kesimpulan aman
```
