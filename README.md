# Discord File Converter Bot

Upload file → bot balikin hasil convert-nya di channel yang sama. Python + discord.py.

| Command  | Dari → Ke                          |
|----------|--------------------------------------|
| `/toword`| PDF → Word (.docx)                   |
| `/topdf` | Word → PDF                           |
| `/tomd`  | PDF → Markdown (.md)                 |
| `/summary`| PDF/Word/Markdown → ringkasan AI (pilih mode via tombol) |
| `/help`  | bantuan cara pakai                   |

## Cara pakai (user)

Ketik `/toword`, upload PDF di parameter `file`, tunggu, file `.docx` dikirim balik.
Limit 20MB per file (limit Discord gratis).

## Setup di VPS (Ubuntu)

```bash
# 1. LibreOffice (mesin convert Word <-> PDF)
sudo apt update && sudo apt install -y libreoffice --no-install-recommends

# 2. Project + virtualenv
git clone <repo> converter-bot && cd converter-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Token bot Discord
#    - Buat aplikasi di https://discord.com/developers/applications
#    - Tab Bot -> Reset Token -> copy
#    - Tab OAuth2 -> URL Generator -> centang `bot` + `applications.commands`
#    - Buka URL itu buat invite bot ke server
cp .env.example .env
nano .env   # isi DISCORD_TOKEN (+ NINE_ROUTER_API_KEY kalau mau pakai /summary)
chmod 600 .env

# 4. Jalan sebagai service
sudo cp converter-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now converter-bot
sudo journalctl -u converter-bot -f   # lihat log
```

> Slash command butuh beberapa menit muncul setelah bot pertama kali online (Discord caching).

## Catatan kualitas

- **PDF → Word itu lossy.** PDF menyimpan posisi teks per baris, bukan struktur
  dokumen — hasil convert cukup buat dokumen teks rapi, layout kompleks bisa
  geser. Jangan janjiin pixel-perfect.
- **PDF hasil scan** (gambar, bukan teks) tidak bisa di-convert ke Word/Markdown
  dengan akurat — butuh OCR dulu (`ocrmypdf` + tesseract, belum termasuk di sini).
- Convert Word/PDF dijalankan **satu per satu** (LibreOffice tidak tahan paralel),
  jadi kalau banyak yang pakai barengan bakal antre.

## Struktur

```
bot.py                 Discord bot (slash commands, download/upload, validasi)
converter/
  __init__.py
  office.py            docx<->pdf via LibreOffice headless (+ lock & timeout)
  markdown.py          pdf -> md via MarkItDown (fallback PyMuPDF)
requirements.txt
.env.example
converter-bot.service  contoh systemd unit
```
