---
name: ml-agent-prd
description: 'Membuat, meninjau, atau memperbarui PRD untuk agen ML/AI, termasuk tujuan produk, pengguna, alur agen, data, model, evaluasi, guardrail, observabilitas, rollout, dan acceptance criteria. Gunakan saat diminta membuat PRD, product requirements, spesifikasi produk, atau rencana implementasi agen machine learning, LLM, RAG, maupun sistem agentic.'
argument-hint: 'Jelaskan agen ML, pengguna, masalah, dan batasan utama'
user-invocable: true
disable-model-invocation: false
---

# PRD Agen ML

Buat PRD ringkas, terukur, dan siap dipakai tim produk, ML, data, backend, keamanan, serta QA. Gunakan [template PRD](./assets/prd-template.md) sebagai struktur dasar.

## Prinsip

- Pisahkan kebutuhan produk dari pilihan implementasi.
- Jangan mengarang fakta, metrik baseline, sumber data, anggaran, atau batas regulasi.
- Tandai informasi belum tersedia sebagai `TBD` dan masukkan ke pertanyaan terbuka.
- Utamakan solusi paling sederhana. Jangan memakai agen ML bila aturan deterministik atau pencarian biasa cukup.
- Definisikan keberhasilan lewat metrik produk, kualitas model, keamanan, biaya, dan latensi.
- Perlakukan output model sebagai tidak tepercaya sampai divalidasi.
- Hindari klaim “100% akurat”, “tanpa halusinasi”, atau jaminan absolut lain.

## Prosedur

1. **Kumpulkan konteks minimum**
   - Masalah dan alasan perlu diselesaikan sekarang.
   - Pengguna utama dan pekerjaan yang ingin diselesaikan.
   - Input, output, kanal, bahasa, serta frekuensi penggunaan.
   - Data tersedia, izin penggunaan, sensitivitas, dan retensi.
   - Batas biaya, latensi, regulasi, keamanan, serta tanggal target.
   - Pemilik produk dan pihak pemberi persetujuan.

   Jika konteks penting hilang, ajukan maksimal lima pertanyaan yang paling memengaruhi desain. Jika pengguna meminta draf langsung, lanjutkan dengan asumsi eksplisit dan `TBD`.

2. **Uji kebutuhan ML/agen**
   - Bandingkan dengan baseline tanpa ML: aturan, template, SQL/search, atau workflow tetap.
   - Nyatakan alasan ML diperlukan.
   - Nyatakan alasan pola agen diperlukan: penggunaan alat, perencanaan multi-langkah, atau keputusan adaptif.
   - Jika baseline sederhana cukup, rekomendasikan baseline tersebut dan jadikan ML sebagai tahap lanjutan.

3. **Tetapkan ruang lingkup**
   - Tulis tujuan, non-tujuan, persona, use case utama, dan journey normal.
   - Definisikan batas otonomi: tindakan yang boleh otomatis, perlu konfirmasi, atau dilarang.
   - Batasi MVP pada kemampuan terkecil yang menghasilkan nilai terukur.

4. **Rancang perilaku agen**
   - Jelaskan pemicu, konteks, langkah keputusan, alat, memori, dan output.
   - Tetapkan kontrak input/output serta penanganan input kosong, ambigu, berbahaya, dan konflik instruksi.
   - Tentukan fallback, timeout, retry terbatas, idempotensi, penghentian loop, dan eskalasi manusia.
   - Untuk tindakan berdampak tinggi atau irreversible, wajibkan persetujuan manusia sebelum eksekusi.

5. **Spesifikasikan data dan ML**
   - Inventaris data, provenance, kualitas, akses, PII, consent, retensi, serta penghapusan.
   - Nyatakan baseline, kandidat model, strategi prompting/fine-tuning/RAG hanya bila relevan.
   - Pisahkan dataset train, validation, test, dan evaluasi produksi untuk mencegah leakage.
   - Dokumentasikan versi model, prompt, indeks, alat, dan dataset agar hasil dapat direproduksi.

6. **Definisikan evaluasi**
   - Metrik produk: penyelesaian tugas, waktu hemat, adopsi, atau kepuasan.
   - Metrik model: kualitas tugas yang spesifik, bukan hanya satu skor agregat.
   - Metrik operasional: p50/p95 latency, error rate, availability, token/compute, biaya per tugas.
   - Metrik keselamatan: policy violation, kebocoran data, tool misuse, prompt injection success, dan harmful-action rate.
   - Tetapkan baseline, target, ambang gagal, ukuran sampel, evaluator, serta metode pengukuran.
   - Sertakan golden set, kasus tepi, red-team set, dan pengujian regresi.

7. **Tentukan rollout dan operasi**
   - Gunakan tahapan offline, shadow, internal, canary, lalu general availability sesuai risiko.
   - Tetapkan feature flag, kill switch, rollback, audit log, tracing, alert, serta incident owner.
   - Definisikan pemantauan drift, perubahan biaya/latensi, feedback pengguna, dan jadwal evaluasi ulang.

8. **Tulis requirement dan acceptance criteria**
   - Beri ID stabil: `FR-*`, `NFR-*`, `SAFE-*`, `DATA-*`, `OBS-*`.
   - Gunakan kata `MUST`, `SHOULD`, dan `MAY` secara konsisten.
   - Setiap requirement wajib dapat diuji dan punya acceptance criteria berbentuk Given/When/Then atau ambang numerik.
   - Hubungkan requirement risiko tinggi dengan kontrol dan tes terkait.

9. **Periksa kualitas PRD**
   - Semua target punya baseline atau berstatus `TBD`.
   - Scope MVP dan non-goals jelas.
   - Tidak ada metrik tanpa metode ukur.
   - Data, keamanan, privasi, fallback, human oversight, dan rollback tercakup.
   - Pertanyaan terbuka punya owner dan tanggal keputusan bila tersedia.
   - Asumsi terpisah dari fakta.

## Bentuk keluaran

- Gunakan Bahasa Indonesia kecuali pengguna meminta bahasa lain.
- Mulai dengan ringkasan keputusan, lalu isi template.
- Gunakan tabel untuk requirement, metrik, risiko, dan pertanyaan terbuka.
- Akhiri dengan `Keputusan MVP`, `Blocker`, dan `Langkah berikutnya`.
- Jangan menambahkan diagram, arsitektur detail, atau backlog panjang kecuali diminta.
