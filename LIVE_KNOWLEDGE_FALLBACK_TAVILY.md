# Live Knowledge Fallback dengan Tavily

Fitur ini membuat Adioranye tidak berhenti ketika Knowledge Base lokal belum punya data yang cukup.

## Alur kerja

```text
Pertanyaan user
→ cari Knowledge Base lokal
→ jika KB cukup: jawab dari KB
→ jika KB kurang / pertanyaan meminta data terbaru: cari Tavily
→ hasil Tavily diubah menjadi sumber RAG sementara
→ AI menjawab dengan sumber live
→ opsional: hasil live disimpan ke KB dengan TTL
```

## File yang ditambahkan/diubah

- `live_knowledge_fallback.py`
- `power_features.py`
- `app.py`
- `telegram_service.py`
- `.streamlit/secrets.toml.example`

## Secret yang wajib ditambahkan

Tambahkan di Streamlit Secrets:

```toml
LIVE_WEB_FALLBACK_ENABLED = true
LIVE_WEB_FALLBACK_PROVIDER = "tavily"
TAVILY_API_KEY = "tvly-ISI_API_KEY_TAVILY_KAMU"
LIVE_WEB_FALLBACK_MAX_RESULTS = 4
LIVE_WEB_FALLBACK_TIMEOUT_SECONDS = 10
LIVE_WEB_FALLBACK_MIN_SOURCES = 1
LIVE_WEB_FALLBACK_INCLUDE_RAW_CONTENT = true
LIVE_WEB_FALLBACK_MAX_CONTENT_CHARS = 3200
LIVE_WEB_FALLBACK_AUTO_SAVE_TO_KB = true
LIVE_WEB_FALLBACK_TTL_HOURS = 24
LIVE_WEB_FALLBACK_FORCE_FOR_CURRENT = true
LIVE_WEB_FALLBACK_TOPIC = "auto"
```

## Kapan Tavily dipakai?

Tavily dipakai jika:

- pertanyaan meminta data terbaru/saat ini/hari ini/update/trending/viral;
- Knowledge Base lokal belum memiliki sumber yang cukup;
- mode jawaban `riset` atau `kritis` butuh sumber tambahan;
- sumber KB yang ada terlalu lama untuk pertanyaan yang sifatnya current.

Tavily tidak dipakai untuk sapaan ringan seperti:

```text
halo
hai
apa kabar
terima kasih
oke
```

## Perilaku anti-halusinasi

Hasil live web dianggap sebagai **data tidak tepercaya**, bukan instruksi. Sistem akan menghapus pola prompt injection seperti:

```text
ignore previous instructions
abaikan instruksi
system prompt
reveal secret
```

Jika Tavily gagal atau API key belum diisi, sistem tidak mengarang. AI akan memakai KB yang ada atau menjelaskan bahwa sumber terbaru belum cukup.

## Penyimpanan ke KB sementara

Jika `LIVE_WEB_FALLBACK_AUTO_SAVE_TO_KB = true`, hasil live search akan disimpan ke koleksi:

```text
Live Web Fallback
```

Metadata berisi:

- query;
- provider;
- waktu pengambilan;
- tanggal sumber jika tersedia;
- source quality;
- freshness score;
- TTL jam.

Default TTL adalah 24 jam karena data current seperti berita, chart, harga, dan tren cepat berubah.

## Rekomendasi konfigurasi

Untuk Streamlit gratis:

```toml
LIVE_WEB_FALLBACK_MAX_RESULTS = 3
LIVE_WEB_FALLBACK_TIMEOUT_SECONDS = 8
LIVE_WEB_FALLBACK_MAX_CONTENT_CHARS = 2500
```

Untuk kualitas lebih baik:

```toml
LIVE_WEB_FALLBACK_MAX_RESULTS = 5
LIVE_WEB_FALLBACK_TIMEOUT_SECONDS = 12
LIVE_WEB_FALLBACK_MAX_CONTENT_CHARS = 4000
```

## Contoh pertanyaan yang sekarang bisa dijawab

```text
Tangga lagu terbaru di Indonesia apa saja?
Berita AI global terbaru apa?
Perkembangan teknologi di Indonesia saat ini apa?
Isu kesehatan terbaru di Asia apa?
Riset terbaru tentang peternakan unggas apa?
```

## Catatan

Tavily Search dipanggil lewat REST API `https://api.tavily.com/search`, sehingga tidak perlu package tambahan `tavily-python`. Package `requests` sudah tersedia dalam requirements aplikasi.
