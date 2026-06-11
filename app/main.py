from __future__ import annotations

import shutil
import uuid
import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .models import Job, JobKind
from .services import LANGUAGES, PackageBuilder


ROOT = Path(__file__).resolve().parent.parent
UPLOADS_DIR = ROOT / "uploads"
OUTPUTS_DIR = ROOT / "outputs"
STATIC_DIR = ROOT / "static"

UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="PDF Layout Translator")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/outputs", StaticFiles(directory=OUTPUTS_DIR), name="outputs")

jobs: dict[str, Job] = {}
package_builder = PackageBuilder()


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/languages")
def list_languages() -> list[dict]:
    return [language.__dict__ for language in LANGUAGES]


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
        kind="html_structure",
        status="queued",
        message="Queued",
        output_dir=job_dir,
    )
    save_job(jobs[job_id])

    asyncio.create_task(asyncio.to_thread(run_html_job, job_id, upload_path, file.filename))
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
    if job.status != "done" or not job.zip_path or not job.zip_path.exists():
        raise HTTPException(status_code=404, detail="ZIP is not ready yet.")

    return FileResponse(
        job.zip_path,
        filename=f"{Path(job.filename).stem}-reader.zip",
        media_type="application/zip",
    )


@app.post("/api/jobs/{job_id}/translate")
async def translate_job(
    job_id: str,
    source_language: str = Form("en"),
    target_language: str = Form("hi"),
) -> JSONResponse:
    job = require_job(job_id)
    if job.kind != "reader":
        raise HTTPException(status_code=400, detail="Only reader jobs can be translated.")
    if job.status != "done":
        raise HTTPException(status_code=400, detail="Job must be completed before translation.")

    job.status = "processing"
    job.message = f"Translating {source_language} to {target_language}"
    save_job(job)
    asyncio.create_task(
        asyncio.to_thread(run_translation_job, job_id, source_language, target_language)
    )
    return JSONResponse({"jobId": job_id})


def run_pdf_job(job_id: str, upload_path: Path, filename: str, kind: JobKind) -> None:
    job = jobs[job_id]
    job.status = "processing"
    job.message = "Rendering pages and extracting positioned text"
    save_job(job)

    try:
        if kind == "pdf_html":
            zip_path = package_builder.build_html_package(upload_path, job.output_dir, filename)
            job.message = "PDF to HTML ZIP ready"
        else:
            zip_path = package_builder.build_reader_package(upload_path, job.output_dir, filename)
            job.message = "Reader ZIP ready"
        job.status = "done"
        job.zip_path = zip_path
    except Exception as exc:
        job.status = "failed"
        job.message = "Failed"
        job.error = str(exc)
    save_job(job)


def run_html_job(job_id: str, upload_path: Path, filename: str) -> None:
    job = jobs[job_id]
    job.status = "processing"
    job.message = "Extracting HTML structure"
    save_job(job)

    try:
        job.zip_path = package_builder.build_html_structure_package(upload_path, job.output_dir, filename)
        job.status = "done"
        job.message = "HTML structure ZIP ready"
    except Exception as exc:
        job.status = "failed"
        job.message = "Failed"
        job.error = str(exc)
    save_job(job)


def run_translation_job(job_id: str, source_language: str, target_language: str) -> None:
    job = jobs[job_id]
    try:
        job.zip_path = package_builder.translate_reader_package(
            job.output_dir,
            source_language,
            target_language,
        )
        job.status = "done"
        job.message = f"Translated {source_language} to {target_language}"
    except Exception as exc:
        job.status = "failed"
        job.message = "Translation failed"
        job.error = str(exc)
    save_job(job)


def require_job(job_id: str) -> Job:
    job = jobs.get(job_id)
    if not job:
        job = load_job_from_disk(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


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
        "downloadUrl": f"/api/jobs/{job.id}/download" if job.status == "done" else None,
        "readerUrl": f"/outputs/{job.id}/reader/index.html"
        if job.status == "done" and (job.output_dir / "reader" / "index.html").exists()
        else None,
        "error": job.error,
    }
