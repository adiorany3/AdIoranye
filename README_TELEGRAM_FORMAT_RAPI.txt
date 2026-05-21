# Patch Format Telegram Rapi

Perubahan utama:
- Footer model Telegram dibuat lebih ringkas dan tidak memenuhi chat.
- /help dibuat berbeda untuk admin dan pengguna biasa.
- Split pesan Telegram tidak memotong kata/paragraf jika memungkinkan.
- Output /speed, /rotate, /kb, /biaya, /model skor, dan /circuit lebih rapi.
- Tetap memakai plain text tanpa parse_mode agar kode HTML/XML tidak menyebabkan error Telegram.
