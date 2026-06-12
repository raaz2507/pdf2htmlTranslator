from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


JobStatus = Literal["queued", "processing", "done", "failed", "canceled"]
JobKind = Literal["reader", "pdf_html", "html_phjz", "translation"]


@dataclass
class Job:
    id: str
    filename: str
    kind: JobKind
    status: JobStatus
    message: str
    output_dir: Path
    zip_path: Path | None = None
    error: str | None = None
    current_page: int = 0
    total_pages: int = 0
    percent: int = 0
    current_item: int = 0
    total_items: int = 0
    cancel_requested: bool = False
