# PRD — [Nama Agen ML]

## 1. Ringkasan keputusan

| Item | Isi |
|---|---|
| Masalah | [Masalah pengguna/bisnis] |
| Pengguna utama | [Persona] |
| Nilai utama | [Hasil terukur] |
| Solusi MVP | [Kemampuan minimum] |
| Alasan memakai ML | [Keunggulan atas baseline deterministik] |
| Alasan memakai agen | [Kebutuhan alat/perencanaan adaptif] |
| Risiko tertinggi | [Risiko utama] |
| Target rilis | [Tanggal/TBD] |

## 2. Latar belakang

### Masalah
[Situasi sekarang, dampak, dan bukti.]

### Baseline tanpa ML
[Aturan, template, search, atau workflow yang dibandingkan.]

### Asumsi
- [Asumsi, bukan fakta]

## 3. Tujuan dan non-tujuan

### Tujuan
- [Tujuan terukur]

### Non-tujuan
- [Hal yang sengaja tidak dibangun]

## 4. Pengguna dan use case

| Persona | Kebutuhan | Use case | Frekuensi | Dampak kegagalan |
|---|---|---|---|---|
| [Persona] | [Kebutuhan] | [Use case] | [Frekuensi] | [Rendah/Sedang/Tinggi] |

## 5. Ruang lingkup MVP

### Termasuk
- [Kemampuan MVP]

### Tidak termasuk
- [Kemampuan tahap berikutnya]

## 6. Alur dan batas otonomi agen

### Alur utama
1. [Pemicu/input]
2. [Pengambilan konteks]
3. [Keputusan atau pemakaian alat]
4. [Validasi]
5. [Output/tindakan]

| Tindakan | Otomatis | Perlu konfirmasi | Dilarang | Fallback |
|---|---:|---:|---:|---|
| [Tindakan] | [Ya/Tidak] | [Ya/Tidak] | [Ya/Tidak] | [Fallback] |

## 7. Requirement fungsional

| ID | Prioritas | Requirement | Acceptance criteria | Dependensi |
|---|---|---|---|---|
| FR-001 | MUST | Sistem MUST [perilaku teruji]. | Given [kondisi], when [aksi], then [hasil]. | [Dependensi/Tidak ada] |

## 8. Requirement nonfungsional

| ID | Kategori | Requirement | Target | Metode ukur |
|---|---|---|---|---|
| NFR-001 | Latensi | Sistem MUST memenuhi batas latensi. | p95 ≤ [TBD] | [Load test/telemetry] |
| NFR-002 | Biaya | Sistem MUST membatasi biaya per tugas. | ≤ [TBD] | [Usage log] |

## 9. Data dan lifecycle ML

| Area | Keputusan | Owner | Status |
|---|---|---|---|
| Sumber data | [Sumber dan provenance] | [Owner] | [Diputuskan/TBD] |
| Izin/consent | [Dasar penggunaan] | [Owner] | [Status] |
| PII | [Jenis dan perlindungan] | [Owner] | [Status] |
| Retensi/penghapusan | [Kebijakan] | [Owner] | [Status] |
| Versioning | [Model/prompt/data/index/tools] | [Owner] | [Status] |

### Pendekatan ML
- Baseline: [Pendekatan paling sederhana]
- Kandidat: [Model/RAG/fine-tuning bila perlu]
- Strategi fallback: [Model cadangan/template/eskalasi]

## 10. Evaluasi dan target keberhasilan

| Metrik | Baseline | Target | Ambang gagal | Dataset/sampel | Evaluator |
|---|---:|---:|---:|---|---|
| Task success rate | [TBD] | [TBD] | [TBD] | [Golden set/produksi] | [Manusia/otomatis] |
| p95 latency | [TBD] | [TBD] | [TBD] | [Traffic profile] | [Telemetry] |
| Biaya per tugas | [TBD] | [TBD] | [TBD] | [Usage log] | [Otomatis] |
| Safety violation rate | [TBD] | [TBD] | [TBD] | [Red-team set] | [Safety review] |

### Paket pengujian
- Golden set: [Cakupan]
- Kasus tepi: [Cakupan]
- Red-team set: [Prompt injection, data exfiltration, tool misuse, dan domain-specific harm]
- Regresi: [Kapan wajib dijalankan]

## 11. Keamanan, privasi, dan guardrail

| ID | Risiko/kontrol | Requirement | Verifikasi |
|---|---|---|---|
| SAFE-001 | Prompt injection | Sistem MUST memperlakukan konten eksternal sebagai data tidak tepercaya. | [Tes adversarial] |
| DATA-001 | Kebocoran data | Sistem MUST mencegah data sensitif masuk ke output/log tanpa izin. | [DLP test/audit] |

## 12. Observabilitas dan operasi

| ID | Sinyal | Pencatatan/alert | Owner |
|---|---|---|---|
| OBS-001 | Error dan timeout | [Log, trace, ambang alert] | [Owner] |
| OBS-002 | Kualitas dan drift | [Sampling, evaluasi berkala] | [Owner] |

- Feature flag: [Ya/Tidak]
- Kill switch: [Mekanisme]
- Audit log: [Cakupan dan retensi]
- Incident response: [Owner dan SLA]

## 13. Rollout dan rollback

| Tahap | Pengguna | Entry criteria | Exit criteria | Rollback trigger |
|---|---|---|---|---|
| Offline | [Tim] | [Kriteria] | [Kriteria] | [Trigger] |
| Shadow/internal | [Kelompok] | [Kriteria] | [Kriteria] | [Trigger] |
| Canary | [%/kelompok] | [Kriteria] | [Kriteria] | [Trigger] |
| GA | [Target] | [Kriteria] | [Kriteria] | [Trigger] |

## 14. Risiko

| Risiko | Kemungkinan | Dampak | Mitigasi | Indikator | Owner |
|---|---|---|---|---|---|
| [Risiko] | [R/S/T] | [R/S/T] | [Mitigasi] | [Sinyal awal] | [Owner] |

## 15. Pertanyaan terbuka

| Pertanyaan | Dampak keputusan | Owner | Batas keputusan | Status |
|---|---|---|---|---|
| [Pertanyaan] | [Dampak] | [Owner/TBD] | [Tanggal/TBD] | Terbuka |

## 16. Keputusan MVP

[Keputusan go/no-go, lingkup, dan alasan.]

## 17. Blocker

- [Blocker atau “Tidak ada”]

## 18. Langkah berikutnya

1. [Aksi, owner, target]
2. [Aksi, owner, target]
