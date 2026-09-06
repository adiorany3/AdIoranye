# Benchmark jawaban AdIoranye

Dataset: `benchmark_test_set.json`, 40 kasus berbahasa Indonesia.

**Status: draft sintetis realistis, bukan pertanyaan dari log pengguna dan belum menjadi gold dataset tervalidasi ahli.** Jawaban acuan berupa hasil hitungan atau poin semantik yang wajib dipenuhi, bukan teks yang harus identik. Belum ada hasil pengujian model.

## Cakupan dan acuan

- 5 `fast`, 15 `balanced`, 10 `current`, 10 `deep`.
- Peternakan, pakan, akuakultur, agronomi, lingkungan, teknologi, RAG, keamanan, dan keandalan sistem.
- Kasus hitungan menggunakan angka pada pertanyaan sebagai acuan; kasus dokumen menggunakan S1/S2 yang disertakan, bukan dokumen eksternal.
- Kasus teknis menggunakan jawaban acuan untuk ditinjau reviewer; sebelum rilis, verifikasi terhadap dokumentasi resmi SQLite, Python, OpenClash, dan HTTP sesuai topik.
- Kasus kesehatan dan regulasi perlu tinjauan ahli bidang terkait sebelum menjadi gold dataset.
- `live_or_abstain`: periksa sumber live jika tersedia; simpan URL, waktu pengambilan, tanggal publikasi/periode data, dan cuplikan pendukung. Tanpa bukti memadai, penolakan memberi fakta pasti merupakan perilaku benar, tetapi bukan keberhasilan menjawab fakta.
- `provided_only`: hanya bukti dalam pertanyaan; larangan akses internet wajib dipatuhi.
- `expected_profile` merupakan target evaluasi, bukan klaim bahwa router saat ini pasti memilih profil tersebut.

## Prosedur penilaian

1. Catat commit aplikasi, model/provider, konfigurasi, waktu dan zona waktu pengujian, versi KB, ketersediaan web, serta status cache. Gunakan sesi baru per kasus; skenario lintas pengguna diuji terpisah pada data uji, bukan data pribadi produksi.
2. Kirim `question` tanpa membocorkan `expected_answer` atau `criteria` kepada model. Gunakan data uji dan tool sandbox; jangan menjalankan backup atau perubahan produksi untuk benchmark.
3. Simpan ID kasus, jawaban utuh, profil aktual, bukti/tool trace yang telah disanitasi, latensi end-to-end, token, biaya bila tersedia, dan error. Nilai tidak tersedia dicatat null, bukan nol. Jangan simpan kunci atau data pribadi.
4. Reviewer membandingkan makna jawaban dengan acuan dan memberi setiap kriteria skor 0 atau 1. Kasus lulus jika kedua kriteria terpenuhi. Kesalahan numerik, klaim sumber/tindakan palsu, kebocoran data, atau diagnosis/dosis tanpa dasar membuat kasus gagal meskipun gaya bahasanya baik.
5. Untuk data terkini, buat acuan bertanggal dari sumber yang diperiksa pada run tersebut. Bedakan hasil `answered`, `abstained`, dan `error`; abstain dapat lulus guardrail, tetapi jangan dihitung sebagai fakta yang berhasil dijawab. Abstain tanpa mencoba pencarian saat akses live tersedia tidak memenuhi kebijakan `live_or_abstain`.
6. Laporkan kasus lulus/40, skor kriteria terpenuhi/80, hasil per profil/kategori, jumlah abstain/error, serta cakupan jawaban faktual live terverifikasi dari 9 kasus `live_or_abstain`. Laporkan kesesuaian routing secara terpisah. Latensi p50/p95 harus menyebut jumlah sampel dan cache dingin/hangat, bukan digabung.
7. Untuk membandingkan konfigurasi, jalankan setiap konfigurasi pada kasus dan kondisi sama, idealnya tiga pengulangan. Nilai tiap run terpisah; jangan menganggap pengulangan sebagai pertanyaan unik. Reviewer kedua memeriksa kasus berisiko dan ketidaksepakatan.

Target awal yang diusulkan, bukan hasil: minimal 36/40 kasus lulus dan tidak ada kegagalan kritis keamanan atau fabrikasi. Laporkan cakupan live tersendiri agar banyak abstain tidak memberi kesan kemampuan faktual tinggi.

## Batas evaluasi yang sudah ada

`adioranye_quality_eval.py` dapat membaca dataset ini melalui opsi `--test-set benchmark_test_set.json`, tetapi hanya memeriksa jumlah/cakupan profil dan kesehatan pipeline. Script tersebut **tidak menjalankan model, menilai jawaban, atau mengukur akurasi benchmark**. `performance_test_set.json` tetap dipertahankan untuk kompatibilitas.

## Menjadi dataset pertanyaan nyata

Ganti atau tambah kasus dengan pertanyaan pengguna yang diizinkan dan dianonimkan. Catat asal, tanggal, izin penggunaan, reviewer, serta bukti acuan tanpa identitas pribadi. Bekukan versi dataset dan pisahkan data pengembangan dari holdout sebelum menyetel prompt berdasarkan hasil. Jangan menyebut dataset sintetis ini sebagai bukti performa produksi.