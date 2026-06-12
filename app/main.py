from __future__ import annotations

import shutil
import uuid
import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .models import Job, JobKind
from .services import LANGUAGES, PACKAGE_EXTENSION, PackageBuilder


ROOT = Path(__file__).resolve().parent.parent
UPLOADS_DIR = ROOT / "uploads"
OUTPUTS_DIR = ROOT / "outputs"
STATIC_DIR = ROOT / "static"
READER_DIR = ROOT / "templates" / "reader"
LOGS_DIR = ROOT / "logs"
APP_LOG_PATH = LOGS_DIR / "app.log"

UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
APP_LOG_PATH.touch(exist_ok=True)

logging.basicConfig(
    filename=APP_LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    encoding="utf-8",
)
logger = logging.getLogger("pdf2html")

app = FastAPI(title="PDF to HTML")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/outputs", StaticFiles(directory=OUTPUTS_DIR), name="outputs")
app.mount("/reader-assets", StaticFiles(directory=READER_DIR), name="reader-assets")

jobs: dict[str, Job] = {}
package_builder = PackageBuilder()


@app.middleware("http")
async def log_unhandled_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        raise


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/reader", response_class=HTMLResponse)
def universal_reader_home() -> str:
    return render_reader()


@app.get("/reader/{job_id}", response_class=HTMLResponse)
def universal_reader(job_id: str) -> str:
    require_job(job_id)
    return render_reader()


def render_reader() -> str:
    html = (READER_DIR / "index.html").read_text(encoding="utf-8")
    return html.replace('href="style.css"', 'href="/reader-assets/style.css"').replace(
        'src="reader.js"',
        'src="/reader-assets/reader.js"',
    )


@app.get("/api/languages")
def list_languages() -> list[dict]:
    return [language.__dict__ for language in LANGUAGES]


@app.get("/api/providers/config")
def provider_config() -> dict:
    return package_builder.provider_config()


@app.post("/api/pdf/jobs")
async def create_pdf_job(
    kind: JobKind = Form("reader"),
    file: UploadFile = File(...),
) -> JSONResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = OUTPUTS_DIR / job_id
    upload_path = UPLOADS_DIR / f"{job_id}.pdf"
    job_dir.mkdir(parents=True, exist_ok=True)

    with upload_path.open("wb") as target:
        shutil.copyfileobj(file.file, target)

    jobs[job_id] = Job(
        id=job_id,
        filename=file.filename,
        kind=kind,
        status="queued",
        message="Queued",
        output_dir=job_dir,
    )
    save_job(jobs[job_id])

    asyncio.create_task(asyncio.to_thread(run_pdf_job, job_id, upload_path, file.filename, kind))
    return JSONResponse({"jobId": job_id})


@app.post("/api/html/jobs")
async def create_html_job(
    file: UploadFile = File(...),
) -> JSONResponse:
    if not file.filename or not file.filename.lower().endswith((".html", ".htm")):
        raise HTTPException(status_code=400, detail="Please upload an HTML file.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = OUTPUTS_DIR / job_id
    upload_path = UPLOADS_DIR / f"{job_id}.html"
    job_dir.mkdir(parents=True, exist_ok=True)

    with upload_path.open("wb") as target:
        shutil.copyfileobj(file.file, target)

    jobs[job_id] = Job(
        id=job_id,
        filename=file.filename,
        kind="html_phjz",
        status="queued",
        message="Queued",
        output_dir=job_dir,
    )
    save_job(jobs[job_id])

    asyncio.create_task(asyncio.to_thread(run_html_job, job_id, upload_path, file.filename))
    return JSONResponse({"jobId": job_id})


@app.post("/api/package/translate")
async def create_package_translation_job(
    source_language: str = Form("en"),
    target_language: str = Form("hi"),
    provider: str = Form("indictrans2"),
    file: UploadFile = File(...),
) -> JSONResponse:
    if not file.filename or not file.filename.lower().endswith(PACKAGE_EXTENSION):
        raise HTTPException(status_code=400, detail=f"Please upload a {PACKAGE_EXTENSION} package.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = OUTPUTS_DIR / job_id
    upload_path = UPLOADS_DIR / f"{job_id}{PACKAGE_EXTENSION}"
    job_dir.mkdir(parents=True, exist_ok=True)

    with upload_path.open("wb") as target:
        shutil.copyfileobj(file.file, target)

    jobs[job_id] = Job(
        id=job_id,
        filename=file.filename,
        kind="translation",
        status="queued",
        message="Queued",
        output_dir=job_dir,
    )
    save_job(jobs[job_id])

    asyncio.create_task(
        asyncio.to_thread(
            run_package_translation_job,
            job_id,
            upload_path,
            file.filename,
            source_language,
            target_language,
            provider,
        )
    )
    return JSONResponse({"jobId": job_id})


@app.post("/api/package/estimate")
async def estimate_package_translation(
    source_language: str = Form("en"),
    target_language: str = Form("hi_modern"),
    provider: str = Form("gemini"),
    file: UploadFile = File(...),
) -> JSONResponse:
    if not file.filename or not file.filename.lower().endswith(PACKAGE_EXTENSION):
        raise HTTPException(status_code=400, detail=f"Please upload a {PACKAGE_EXTENSION} package.")
    job_id = uuid.uuid4().hex[:12]
    job_dir = OUTPUTS_DIR / f"estimate-{job_id}"
    upload_path = UPLOADS_DIR / f"estimate-{job_id}{PACKAGE_EXTENSION}"
    job_dir.mkdir(parents=True, exist_ok=True)
    with upload_path.open("wb") as target:
        shutil.copyfileobj(file.file, target)
    try:
        package_builder.extract_reader_package(upload_path, job_dir)
        estimate = package_builder.estimate_translation(job_dir, source_language, target_language, provider)
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
        upload_path.unlink(missing_ok=True)
    return JSONResponse(estimate)


@app.post("/api/package/open")
async def open_package_job(
    file: UploadFile = File(...),
) -> JSONResponse:
    if not file.filename or not file.filename.lower().endswith(PACKAGE_EXTENSION):
        raise HTTPException(status_code=400, detail=f"Please upload a {PACKAGE_EXTENSION} package.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = OUTPUTS_DIR / job_id
    upload_path = UPLOADS_DIR / f"{job_id}{PACKAGE_EXTENSION}"
    job_dir.mkdir(parents=True, exist_ok=True)

    with upload_path.open("wb") as target:
        shutil.copyfileobj(file.file, target)

    job = Job(
        id=job_id,
        filename=file.filename,
        kind="reader",
        status="processing",
        message="Opening .phjz package",
        output_dir=job_dir,
        zip_path=upload_path,
    )
    jobs[job_id] = job
    save_job(job)

    try:
        package_builder.extract_reader_package(upload_path, job_dir)
        job.status = "done"
        job.message = "Package ready in universal reader"
    except Exception as exc:
        job.status = "failed"
        job.message = "Package open failed"
        job.error = str(exc)
    save_job(job)
    return JSONResponse({"jobId": job_id})


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = require_job(job_id)
    return serialize_job(job)


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    load_jobs_from_disk()
    return [serialize_job(job) for job in reversed(list(jobs.values()))]


@app.get("/api/jobs/{job_id}/download")
def download_zip(job_id: str) -> FileResponse:
    job = require_job(job_id)
    if not job.zip_path or not job.zip_path.exists():
        raise HTTPException(status_code=404, detail="Package is not ready yet.")

    return FileResponse(
        job.zip_path,
        filename=download_filename(job),
        media_type="application/octet-stream" if job.zip_path.suffix == PACKAGE_EXTENSION else "application/zip",
    )


@app.post("/api/jobs/{job_id}/translate")
async def translate_job(
    job_id: str,
    source_language: str = Form("en"),
    target_language: str = Form("hi"),
    provider: str = Form("indictrans2"),
) -> JSONResponse:
    job = require_job(job_id)
    if job.kind != "reader":
        raise HTTPException(status_code=400, detail="Only reader jobs can be translated.")
    if job.status in {"queued", "processing"}:
        raise HTTPException(status_code=400, detail="Job must be completed before translation.")
    if not (job.output_dir / "data" / "document.json").exists():
        raise HTTPException(status_code=400, detail="Reader data is not ready for translation.")

    job.status = "processing"
    job.cancel_requested = False
    job.message = f"Translating {source_language} to {target_language}"
    save_job(job)
    asyncio.create_task(
        asyncio.to_thread(run_translation_job, job_id, source_language, target_language, provider)
    )
    return JSONResponse({"jobId": job_id})


@app.get("/api/jobs/{job_id}/estimate")
def estimate_reader_translation(
    job_id: str,
    source_language: str = "en",
    target_language: str = "hi_modern",
    provider: str = "gemini",
) -> JSONResponse:
    job = require_job(job_id)
    if not (job.output_dir / "data" / "translations.json").exists():
        raise HTTPException(status_code=400, detail="Reader translations data is not ready.")
    return JSONResponse(package_builder.estimate_translation(job.output_dir, source_language, target_language, provider))


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> JSONResponse:
    job = require_job(job_id)
    if job.status != "processing":
        raise HTTPException(status_code=400, detail="Only a running job can be aborted.")
    job.cancel_requested = True
    job.message = "Abort requested. Finishing the current text..."
    save_job(job)
    return JSONResponse({"jobId": job_id, "cancelRequested": True})


@app.post("/api/reader/{job_id}/retranslate")
def retranslate_reader_item(
    job_id: str,
    text_id: str = Form(...),
    source_language: str = Form("en"),
    target_language: str = Form("hi"),
    provider: str = Form("indictrans2"),
) -> JSONResponse:
    job = require_job(job_id)
    try:
        value = package_builder.retranslate_item(
            job.output_dir,
            text_id,
            source_language,
            target_language,
            provider,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"textId": text_id, "translation": value})


@app.post("/api/reader/{job_id}/save-translation")
def save_reader_translation(
    job_id: str,
    text_id: str = Form(...),
    target_language: str = Form("hi"),
    value: str = Form(...),
) -> JSONResponse:
    job = require_job(job_id)
    try:
        job.zip_path = package_builder.save_translation(
            job.output_dir,
            text_id,
            target_language,
            value,
        )
        job.message = f"Saved {target_language} translation"
        save_job(job)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"textId": text_id, "saved": True})


def run_pdf_job(job_id: str, upload_path: Path, filename: str, kind: JobKind) -> None:
    job = jobs[job_id]
    job.status = "processing"
    job.cancel_requested = False
    job.message = "Preparing PDF"
    job.current_page = 0
    job.total_pages = 0
    job.percent = 0
    save_job(job)

    try:
        if kind == "pdf_html":
            zip_path = package_builder.build_html_package(
                upload_path,
                job.output_dir,
                filename,
                lambda current, total: update_pdf_progress(job, current, total),
            )
            job.message = "PDF to HTML package ready"
        else:
            zip_path = package_builder.build_reader_package(
                upload_path,
                job.output_dir,
                filename,
                lambda current, total: update_pdf_progress(job, current, total),
            )
            job.message = "PDF to .phjz package ready"
        job.status = "done"
        job.zip_path = zip_path
        job.percent = 100
        if job.total_pages and job.current_page < job.total_pages:
            job.current_page = job.total_pages
    except Exception as exc:
        job.status = "failed"
        job.message = "Failed"
        job.error = str(exc)
    save_job(job)


def update_pdf_progress(job: Job, current_page: int, total_pages: int) -> None:
    job.total_pages = total_pages
    job.current_page = current_page
    job.percent = round((current_page / total_pages) * 100) if total_pages else 0
    if total_pages:
        job.message = f"Processing page {current_page} of {total_pages}"
    else:
        job.message = "Reading PDF"
    save_job(job)


def run_html_job(job_id: str, upload_path: Path, filename: str) -> None:
    job = jobs[job_id]
    job.status = "processing"
    job.message = "Converting HTML to .phjz"
    save_job(job)

    try:
        job.zip_path = package_builder.build_html_package_from_html(upload_path, job.output_dir, filename)
        job.status = "done"
        job.percent = 100
        job.message = "HTML .phjz package ready"
    except Exception as exc:
        job.status = "failed"
        job.message = "Failed"
        job.error = str(exc)
    save_job(job)


def run_translation_job(
    job_id: str,
    source_language: str,
    target_language: str,
    provider: str = "indictrans2",
) -> None:
    job = jobs[job_id]
    job.status = "processing"
    job.current_item = 0
    job.total_items = 0
    job.percent = 0
    job.message = f"Translating {source_language} to {target_language}"
    save_job(job)
    try:
        logger.info("Translation started job=%s source=%s target=%s provider=%s", job_id, source_language, target_language, provider)
        job.zip_path = package_builder.translate_reader_package(
            job.output_dir,
            source_language,
            target_language,
            provider,
            lambda current, total: update_translation_progress(job, current, total),
            lambda: jobs[job_id].cancel_requested,
        )
        if job.cancel_requested:
            job.status = "canceled"
            job.message = "Translation aborted. Completed items were saved."
            logger.info("Translation canceled job=%s current=%s total=%s", job_id, job.current_item, job.total_items)
        else:
            job.status = "done"
            job.percent = 100
            job.message = f"Translated {source_language} to {target_language}"
            logger.info("Translation finished job=%s total=%s", job_id, job.total_items)
    except Exception as exc:
        job.status = "failed"
        job.message = "Translation failed"
        job.error = str(exc)
        logger.exception("Translation failed job=%s", job_id)
    save_job(job)


def run_package_translation_job(
    job_id: str,
    upload_path: Path,
    filename: str,
    source_language: str,
    target_language: str,
    provider: str = "indictrans2",
) -> None:
    job = jobs[job_id]
    job.status = "processing"
    job.cancel_requested = False
    job.message = "Reading .phjz package"
    job.current_item = 0
    job.total_items = 0
    job.percent = 0
    save_job(job)

    try:
        logger.info("Package translation started job=%s source=%s target=%s provider=%s", job_id, source_language, target_language, provider)
        package_builder.extract_reader_package(upload_path, job.output_dir)
        job.message = f"Translating {source_language} to {target_language}"
        save_job(job)
        job.zip_path = package_builder.translate_extracted_package(
            job.output_dir,
            source_language,
            target_language,
            provider,
            lambda current, total: update_translation_progress(job, current, total),
            lambda: jobs[job_id].cancel_requested,
        )
        if job.cancel_requested:
            job.status = "canceled"
            job.message = "Translation aborted. Completed items were saved."
            logger.info("Package translation canceled job=%s current=%s total=%s", job_id, job.current_item, job.total_items)
        else:
            job.status = "done"
            job.percent = 100
            job.message = f"Translated {source_language} to {target_language}"
            logger.info("Package translation finished job=%s total=%s", job_id, job.total_items)
    except Exception as exc:
        try:
            partial_path = job.output_dir / f"partial{PACKAGE_EXTENSION}"
            if (job.output_dir / "data" / "translations.json").exists():
                job.zip_path = package_builder.create_zip(job.output_dir, partial_path)
        except Exception:
            pass
        job.status = "failed"
        job.message = "Translation failed. Completed items were saved; upload the partial package to resume."
        job.error = str(exc)
        logger.exception("Package translation failed job=%s", job_id)
    save_job(job)


def update_translation_progress(job: Job, current_item: int, total_items: int) -> None:
    job.current_item = current_item
    job.total_items = total_items
    job.percent = round((current_item / total_items) * 100) if total_items else 100
    if total_items:
        job.message = f"Translating text {current_item} of {total_items}"
    else:
        job.message = "No pending text for this language"
    save_job(job)


def require_job(job_id: str) -> Job:
    job = jobs.get(job_id)
    if not job:
        job = load_job_from_disk(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


def download_filename(job: Job) -> str:
    suffix = job.zip_path.suffix if job.zip_path else ".zip"
    stem = Path(job.filename).stem
    if suffix == PACKAGE_EXTENSION:
        if job.kind == "translation":
            label = "translated" if job.status == "done" else "partial"
            return f"{stem}-{label}{PACKAGE_EXTENSION}"
        return f"{stem}{PACKAGE_EXTENSION}"
    return f"{stem}.zip"


def save_job(job: Job) -> None:
    job.output_dir.mkdir(parents=True, exist_ok=True)
    data = serialize_job(job)
    data["zipPath"] = str(job.zip_path) if job.zip_path else None
    (job.output_dir / "job.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_job_from_disk(job_id: str) -> Job | None:
    job_path = OUTPUTS_DIR / job_id / "job.json"
    if not job_path.exists():
        return None

    data = json.loads(job_path.read_text(encoding="utf-8"))
    job = Job(
        id=data["id"],
        filename=data["filename"],
        kind=data["kind"],
        status=data["status"],
        message=data["message"],
        output_dir=OUTPUTS_DIR / data["id"],
        zip_path=Path(data["zipPath"]) if data.get("zipPath") else None,
        error=data.get("error"),
        current_page=data.get("currentPage", 0),
        total_pages=data.get("totalPages", 0),
        percent=data.get("percent", 100 if data["status"] == "done" else 0),
        current_item=data.get("currentItem", 0),
        total_items=data.get("totalItems", 0),
        cancel_requested=data.get("cancelRequested", False),
    )
    jobs[job.id] = job
    return job


def load_jobs_from_disk() -> None:
    if not OUTPUTS_DIR.exists():
        return
    for job_dir in OUTPUTS_DIR.iterdir():
        if job_dir.is_dir() and job_dir.name not in jobs:
            load_job_from_disk(job_dir.name)


def serialize_job(job: Job) -> dict:
    return {
        "id": job.id,
        "filename": job.filename,
        "kind": job.kind,
        "status": job.status,
        "message": job.message,
        "downloadUrl": f"/api/jobs/{job.id}/download" if job.zip_path and job.zip_path.exists() else None,
        "readerUrl": f"/reader/{job.id}"
        if (job.output_dir / "data" / "document.json").exists()
        and (job.output_dir / "data" / "translations.json").exists()
        else None,
        "error": job.error,
        "currentPage": job.current_page,
        "totalPages": job.total_pages,
        "percent": job.percent,
        "currentItem": job.current_item,
        "totalItems": job.total_items,
        "cancelRequested": job.cancel_requested,
    }
