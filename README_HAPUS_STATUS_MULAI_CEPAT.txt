Patch: hapus panel Status AI saat ini dan Mulai cepat

Perubahan:
1. Menghapus render build_public_model_status_html() dari halaman publik.
2. Menghapus render build_quick_help_html() dari halaman publik.
3. Menghapus variable last_public_meta yang tidak lagi dipakai.

Catatan:
- Fungsi dan CSS lama sengaja dibiarkan agar tidak mengganggu bagian admin/patch lain.
- Yang dihapus adalah tampilan publik yang diminta, bukan fitur router/model/knowledge base.
