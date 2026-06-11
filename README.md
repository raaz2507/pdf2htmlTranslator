# PDF Layout Translator

Upload a PDF, preserve its visual layout as page images, extract positioned text into JSON, and download a ZIP containing an offline HTML reader.

## Features

- Browser dashboard for PDF upload
- PDF to HTML ZIP conversion
- PDF to reader ZIP with structured JSON, images, translations, and offline reader
- HTML to JSON ZIP structure extraction
- Source and target language selectors
- Translate action that updates `translations.json`
- PDF embedded images extracted into `images/`
- PDF vector lines, rectangles, fills, and curves stored in `document.json`
- Text extracted into `data/document.json` with page, image, text, and position metadata
- `data/translations.json` ready for English/Hindi and future languages
- Offline reader with language selector, theme selector, and zoom controls
- Reader lazy-renders pages near the viewport for large PDFs
- ZIP download after processing

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --reload
```

Or on Windows PowerShell:

```powershell
.\run.ps1
```

Open:

```text
http://localhost:8000
```

## Output ZIP Structure

Reader ZIP:

```text
images/
  page-1-image-1.png
  page-2-image-1.jpg
data/
  document.json
  translations.json
html/
  document.html
reader/
  index.html
  reader.js
  style.css
```

PDF to HTML ZIP:

```text
images/
data/
  document.json
html/
  document.html
```

HTML structure ZIP:

```text
images/
data/
  html-structure.json
html/
  source.html
```

## Translation

The first version creates placeholder translated values when you click the dashboard translate button:

```json
"hi": "[HI] Original text"
```

Replace those values manually or add an API translation step later. The reader already supports multiple languages through `translations.json`.
