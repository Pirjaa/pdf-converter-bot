# Discord File Converter Bot

Upload a file → the bot sends the converted result back in the same channel. Python + discord.py.

| Command    | From → To                              |
|------------|----------------------------------------|
| `/toword`  | PDF → Word (.docx)                     |
| `/topdf`   | Word → PDF                             |
| `/tomd`    | PDF → Markdown (.md)                   |
| `/summary` | PDF/Word/Markdown → AI summary (pick a style via buttons) |
| `/mergepdf`| Up to 5 PDFs → one merged PDF          |
| `/splitpdf`| PDF → selected pages (e.g. "1-3,7,10-12") |
| `/help`    | How to use                             |

## Usage

Type `/toword`, upload a PDF in the `file` parameter, wait, and the `.docx` is sent back.
20MB per-file limit (Discord's free limit).

`/splitpdf` takes a `pages` parameter: single pages (`5`), ranges (`1-5`), or a combination (`1-3,7,10-12`). Pages are counted from 1.

## VPS setup (Ubuntu)

```bash
# 1. LibreOffice (the Word <-> PDF conversion engine)
sudo apt update && sudo apt install -y libreoffice --no-install-recommends

# 2. Project + virtualenv
git clone <repo> converter-bot && cd converter-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Discord bot token
#    - Create an app at https://discord.com/developers/applications
#    - Bot tab -> Reset Token -> copy
#    - OAuth2 -> URL Generator -> check `bot` + `applications.commands`
#    - Open the generated URL to invite the bot to your server
cp .env.example .env
nano .env   # set DISCORD_TOKEN (+ NINE_ROUTER_API_KEY for /summary)
chmod 600 .env

# 4. Run as a service
sudo cp converter-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now converter-bot
sudo journalctl -u converter-bot -f   # watch the logs
```

> New slash commands can take a few minutes to appear (Discord caching).

`/summary` needs a 9router API key (`NINE_ROUTER_API_KEY` in `.env`, OpenAI-compatible endpoint, default model `ag/gemini-3.8-flash-high`).

## Quality notes

- **PDF → Word is lossy.** A PDF stores text position per line, not document structure — the result is fine for clean text documents, but complex layouts may shift. Don't promise pixel-perfect.
- **Scanned PDFs** (images, not text) can't be converted to Word/Markdown accurately — that needs OCR first (`ocrmypdf` + tesseract, not included yet).
- Word/PDF conversions run **one at a time** (LibreOffice can't handle parallel jobs), so concurrent users will queue up.

## Layout

```
bot.py                 Discord bot (slash commands, download/upload, validation)
converter/
  __init__.py
  office.py            docx<->pdf via headless LibreOffice (+ lock & timeout)
  markdown.py          pdf -> md via MarkItDown (PyMuPDF fallback)
  pdftools.py          pdf merge/split via pypdf
  summarize.py         AI summary via 9router (OpenAI-compatible API)
requirements.txt
.env.example
converter-bot.service  systemd unit example
```
