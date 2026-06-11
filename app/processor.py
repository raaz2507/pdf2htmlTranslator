from __future__ import annotations

from pathlib import Path

from .services import PackageBuilder


def process_pdf(pdf_path: Path, output_dir: Path, source_name: str) -> Path:
    return PackageBuilder().build_reader_package(pdf_path, output_dir, source_name)
