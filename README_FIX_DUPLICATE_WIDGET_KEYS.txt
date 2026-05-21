# Fix StreamlitDuplicateElementId

Patch ini menambahkan `key` unik pada semua `st.button()` yang belum memiliki key di `app.py`.

Masalah yang diperbaiki:
- Tombol dengan label sama seperti `🔁 Cek model sekarang` muncul di lebih dari satu tab/panel Streamlit.
- Streamlit membuat element ID otomatis dari label dan parameter, sehingga tombol duplikat memicu `StreamlitDuplicateElementId`.

Cara pakai:
1. Replace file lama dengan isi ZIP ini.
2. Restart/redeploy Streamlit.

Catatan:
- Fitur model router, knowledge base, power features, dan Telegram tidak diubah.
- Perubahan hanya pada key widget agar UI tidak crash.
