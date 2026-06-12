# PDF to HTML

Upload a PDF, preserve its visual layout, track page-by-page progress, open the universal reader, and download a `.phjz` data package.

## Features

- Browser dashboard for PDF upload
- PDF to HTML data conversion for the universal reader
- Page count and percentage progress while the PDF is processed
- HTML to `.phjz` package conversion
- Separate Translation section that updates `translations.json` after a PDF job is complete
- Open any `.phjz` package in the universal reader from the dashboard or reader toolbar
- PDF embedded images extracted into `images/`
- PDF vector lines, rectangles, fills, and curves stored in `document.json`
- Text extracted into `data/document.json` with page, image, text, and position metadata
- `data/translations.json` ready for reader language switching
- Universal reader with `.phjz` browse, language selector, text translation editor, print, theme selector, and zoom controls
- Reader lazy-renders pages near the viewport for large PDFs
- `.phjz` package download after PDF processing

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

## Output Package Structure

PDF to HTML `.phjz`:

```text
images/
  page-1-image-1.png
  page-2-image-1.jpg
data/
  document.json
  translations.json
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

Use the dashboard Translation section with a `.phjz` package. It updates `data/translations.json`, saves completed items after every successful translation, and skips already translated items when the same package is uploaded again.

IndicTrans2 is the default translation provider. Run it as a secondary local service:

```powershell
python -m pip install --target indictrans_service\vendor -r indictrans_service\requirements.txt
.\run_indictrans.ps1
```

The first model setup needs internet once to download/cache model files from Hugging Face. After the model cache exists, set local-only mode for fully offline use:

```powershell
.\cache_indictrans.ps1
```

```text
INDICTRANS2_API_URL=http://127.0.0.1:9000/translate
INDICTRANS2_EN_INDIC_MODEL=ai4bharat/indictrans2-en-indic-dist-200M
INDICTRANS2_INDIC_EN_MODEL=ai4bharat/indictrans2-indic-en-dist-200M
INDICTRANS2_INDIC_INDIC_MODEL=ai4bharat/indictrans2-indic-indic-dist-320M
HF_TOKEN=your-huggingface-token-with-model-access
INDICTRANS2_LOCAL_FILES_ONLY=1
```

The `INDICTRANS2_*_MODEL` values can also be local directories, for example `models\indictrans2-en-indic-dist-200M`, if you already have the model files on disk.

On Windows, the service uses a built-in lightweight pre/post-processor if `IndicTransToolkit` is not installed. For the official AI4Bharat preprocessing pipeline, install Microsoft C++ Build Tools and then install `IndicTransToolkit` into the same environment.

ChatGPT can use an OpenAI-compatible chat endpoint when these environment variables are set:

```text
AI_TRANSLATION_API_URL=https://api.openai.com/v1/chat/completions
AI_TRANSLATION_API_KEY=your-key
AI_TRANSLATION_MODEL=gpt-4o-mini
```

Reader retranslation also has a provider selector:

- `IndicTrans2` is the default offline provider. It uses `INDICTRANS2_API_URL`.
- `Google` uses `GOOGLE_TRANSLATION_API_URL` and `GOOGLE_TRANSLATION_API_KEY`; the endpoint should return JSON with `translation` or `translatedText`.
- `ChatGPT` uses `AI_TRANSLATION_API_URL`, `AI_TRANSLATION_API_KEY`, and `AI_TRANSLATION_MODEL`.
- `Local` is an offline placeholder fallback for testing.

Store keys in a local `.env` file at the project root. `.env` is ignored by git:

```text
INDICTRANS2_API_URL=http://127.0.0.1:9000/translate
INDICTRANS2_LOCAL_FILES_ONLY=1
GOOGLE_TRANSLATION_API_URL=https://your-google-translation-endpoint
GOOGLE_TRANSLATION_API_KEY=your-google-key
```

If the API fails, returns invalid JSON, or hits a limit, the job is marked failed, the completed translations remain saved, and a partial `.phjz` package is available to download and resume later.
