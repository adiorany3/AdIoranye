PATCH DESAIN MUDAH - ADIORANYE AI

Tujuan:
Merapikan tampilan aplikasi agar pengguna dan admin lebih mudah memahami status sistem, fitur, dan navigasi.

Perubahan utama:
1. Sidebar otomatis terbuka saat aplikasi dibuka.
2. Panel status model publik sekarang tampil, tidak kosong.
3. Ditambahkan panel 'Mulai cepat' di halaman chat.
4. Panel Power Features dibuat terbuka secara default setelah admin login.
5. Nama tab Power Features diberi ikon:
   - Knowledge Base
   - Memory
   - Usage
   - Optimizer
   - Benchmark
6. Admin Settings diberi kotak 'Pusat Kontrol' agar fungsi utama lebih jelas.
7. Placeholder input chat dibuat lebih informatif.
8. Tambahan CSS responsif agar panel bantuan rapi di desktop dan mobile.

Cara pasang:
1. Replace file lama dengan isi ZIP ini.
2. Restart/redeploy aplikasi Streamlit.
3. Login admin dari sidebar.
4. Cek panel Pusat Fitur Pintar di halaman utama.

File yang diubah utama:
- app.py

File lain disertakan agar bundle tetap lengkap:
- ai_core.py
- telegram_service.py
- power_features.py
- slashai_model_catalog.json
