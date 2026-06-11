from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


JobStatus = Literal["queued", "processing", "done", "failed"]
JobKind = Literal["reader", "pdf_html", "html_structure"]


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
