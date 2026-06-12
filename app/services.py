from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

import fitz


ROOT = Path(__file__).resolve().parent.parent
READER_TEMPLATE_DIR = ROOT / "templates" / "reader"
PACKAGE_EXTENSION = ".phjz"


def load_env_file() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()
EMBEDDED_INDICTRANS_LOCK = threading.Lock()
RATE_LIMIT_LOCK = threading.Lock()
RATE_LIMIT_STATE: dict[str, dict] = {}


@dataclass(frozen=True)
class Language:
    code: str
    label: str


LANGUAGES = [
    Language("en", "English"),
    Language("hi_modern", "Hindi - Modern (Recommended)"),
    Language("hi_pure", "Hindi - Pure"),
    Language("es", "Spanish"),
    Language("fr", "French"),
    Language("de", "German"),
    Language("ar", "Arabic"),
]

LANGUAGE_MODEL_ALIASES = {
    "hi_modern": "hi",
    "hi_pure": "hi",
}

HINDI_MODE_PROMPTS = {
    "hi_modern": (
        "Modern Hindi Mode (Recommended): translate into natural Hindi but keep common technical/product terms "
        "in English when users normally recognize them. Examples: Project -> Project, File -> File, Download -> Download."
    ),
    "hi_pure": (
        "Pure Hindi Mode: translate technical/product terms into Hindi wherever reasonable. "
        "Examples: Project -> परियोजना, File -> फ़ाइल, Download -> डाउनलोड."
    ),
}

MODERN_HINDI_PROMPT_VERSION = "modern-hi-rewriter-v1"

MODERN_HINDI_TERM_MAP = {
    "feature": ["सुविधा", "विशेषता"],
    "users": ["उपयोगकर्ताओं", "उपयोगकर्ता"],
    "user": ["उपयोगकर्ता"],
    "language": ["भाषा"],
    "change": ["परिवर्तित", "बदल"],
    "project": ["परियोजना"],
    "file": ["फ़ाइल", "फाइल"],
    "download": ["डाउनलोड"],
    "upload": ["अपलोड"],
    "reader": ["रीडर", "पाठक"],
    "browser": ["ब्राउज़र"],
    "page": ["पृष्ठ"],
    "button": ["बटन"],
    "api": ["एपीआई"],
    "html": ["एचटीएमएल"],
    "css": ["सीएसएस"],
    "javascript": ["जावास्क्रिप्ट"],
}

JUNK_TEXT_RE = re.compile(r"^[\W\d_]+$", re.UNICODE)


class PdfDocumentExtractor:
    def extract(
        self,
        pdf_path: Path,
        images_dir: Path,
        source_name: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict:
        images_dir.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(pdf_path)
        pages = []
        total_pages = len(doc)

        try:
            if progress_callback:
                progress_callback(0, total_pages)
            for page_index, page in enumerate(doc, start=1):
                pages.append(self._extract_page(page, page_index, images_dir))
                if progress_callback:
                    progress_callback(page_index, total_pages)
        finally:
            doc.close()

        return {
            "source": source_name,
            "version": 1,
            "layout": "page-image-with-positioned-text-layer",
            "languages": [language.code for language in LANGUAGES],
            "pages": pages,
        }

    def _extract_page(self, page: fitz.Page, page_index: int, images_dir: Path) -> dict:
        rect = page.rect
        texts = []
        images = []
        drawings = self._extract_drawings(page)
        text_index = 1
        image_index = 1
        raw = page.get_text("dict")

        for block_index, block in enumerate(raw.get("blocks", []), start=1):
            block_type = block.get("type")

            if block_type == 1:
                image_item = self._save_image_block(
                    block,
                    page_index,
                    image_index,
                    images_dir,
                )
                if image_item:
                    images.append(image_item)
                    image_index += 1
                continue

            if block_type != 0:
                continue

            for line_index, line in enumerate(block.get("lines", []), start=1):
                for span_index, span in enumerate(line.get("spans", []), start=1):
                    span_text = span.get("text", "")
                    if not span_text.strip():
                        continue

                    x0, y0, x1, y1 = span["bbox"]
                    font_name = span.get("font", "")
                    flags = int(span.get("flags", 0))
                    color = self._css_color(int(span.get("color", 0)))

                    texts.append(
                        {
                            "id": f"p{page_index}_t{text_index}",
                            "text": span_text,
                            "x": round(x0, 2),
                            "y": round(y0, 2),
                            "w": round(x1 - x0, 2),
                            "h": round(y1 - y0, 2),
                            "fontSize": round(float(span.get("size", 12)), 2),
                            "font": font_name,
                            "fontWeight": self._font_weight(font_name, flags),
                            "fontStyle": self._font_style(font_name, flags),
                            "color": color,
                            "block": block_index,
                            "line": line_index,
                            "span": span_index,
                        }
                    )
                    text_index += 1

        return {
            "page": page_index,
            "width": round(rect.width, 2),
            "height": round(rect.height, 2),
            "images": images,
            "drawings": drawings,
            "texts": texts,
        }

    def _extract_drawings(self, page: fitz.Page) -> list[dict]:
        drawings = []

        for drawing_index, drawing in enumerate(page.get_drawings(), start=1):
            items = []
            for item in drawing.get("items", []):
                command = item[0]
                if command == "l":
                    p1, p2 = item[1], item[2]
                    items.append(
                        {
                            "type": "line",
                            "x1": round(p1.x, 2),
                            "y1": round(p1.y, 2),
                            "x2": round(p2.x, 2),
                            "y2": round(p2.y, 2),
                        }
                    )
                elif command == "re":
                    rect = item[1]
                    items.append(
                        {
                            "type": "rect",
                            "x": round(rect.x0, 2),
                            "y": round(rect.y0, 2),
                            "w": round(rect.width, 2),
                            "h": round(rect.height, 2),
                        }
                    )
                elif command == "c":
                    p1, p2, p3, p4 = item[1], item[2], item[3], item[4]
                    items.append(
                        {
                            "type": "curve",
                            "x1": round(p1.x, 2),
                            "y1": round(p1.y, 2),
                            "cx1": round(p2.x, 2),
                            "cy1": round(p2.y, 2),
                            "cx2": round(p3.x, 2),
                            "cy2": round(p3.y, 2),
                            "x2": round(p4.x, 2),
                            "y2": round(p4.y, 2),
                        }
                    )
                elif command == "qu":
                    quad = item[1]
                    items.append(
                        {
                            "type": "quad",
                            "points": [
                                [round(quad.ul.x, 2), round(quad.ul.y, 2)],
                                [round(quad.ur.x, 2), round(quad.ur.y, 2)],
                                [round(quad.lr.x, 2), round(quad.lr.y, 2)],
                                [round(quad.ll.x, 2), round(quad.ll.y, 2)],
                            ],
                        }
                    )

            if not items:
                continue

            drawings.append(
                {
                    "id": f"d{drawing_index}",
                    "items": items,
                    "stroke": self._optional_color(drawing.get("color")),
                    "fill": self._optional_color(drawing.get("fill")),
                    "strokeWidth": round(float(drawing.get("width") or 1), 2),
                    "opacity": round(float(drawing.get("opacity") or 1), 3),
                    "fillOpacity": round(float(drawing.get("fill_opacity") or 1), 3),
                    "strokeOpacity": round(float(drawing.get("stroke_opacity") or 1), 3),
                    "closePath": bool(drawing.get("closePath")),
                    "evenOdd": bool(drawing.get("even_odd")),
                }
            )

        return drawings

    def _font_weight(self, font_name: str, flags: int) -> str:
        lowered = font_name.lower()
        if "bold" in lowered or "black" in lowered or "heavy" in lowered or flags & 16:
            return "700"
        return "400"

    def _font_style(self, font_name: str, flags: int) -> str:
        lowered = font_name.lower()
        if "italic" in lowered or "oblique" in lowered or flags & 2:
            return "italic"
        return "normal"

    def _css_color(self, color: int) -> str:
        red = (color >> 16) & 255
        green = (color >> 8) & 255
        blue = color & 255
        return f"#{red:02x}{green:02x}{blue:02x}"

    def _optional_color(self, color: tuple[float, float, float] | None) -> str | None:
        if color is None:
            return None
        red, green, blue = color[:3]
        return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"

    def _save_image_block(
        self,
        block: dict,
        page_index: int,
        image_index: int,
        images_dir: Path,
    ) -> dict | None:
        image_bytes = block.get("image")
        if not image_bytes:
            return None

        ext = block.get("ext") or "png"
        image_name = f"page-{page_index}-image-{image_index}.{ext}"
        image_path = images_dir / image_name
        image_path.write_bytes(image_bytes)
        x0, y0, x1, y1 = block["bbox"]

        return {
            "id": f"p{page_index}_i{image_index}",
            "src": f"../images/{image_name}",
            "x": round(x0, 2),
            "y": round(y0, 2),
            "w": round(x1 - x0, 2),
            "h": round(y1 - y0, 2),
            "ext": ext,
        }


class TranslationService:
    def create_shell(self, document: dict, source_language: str = "en") -> dict:
        items = {}
        for page in document["pages"]:
            for text in page["texts"]:
                items[text["id"]] = {source_language: text["text"]}

        return {
            "version": 1,
            "defaultLanguage": source_language,
            "availableLanguages": [language.__dict__ for language in LANGUAGES],
            "items": items,
        }

    def translate_file(
        self,
        translations_path: Path,
        source_language: str,
        target_language: str,
        provider: str = "indictrans2",
        progress_callback: Callable[[int, int], None] | None = None,
        cancel_callback: Callable[[], bool] | None = None,
        document_path: Path | None = None,
        log_path: Path | None = None,
        cache_path: Path | None = None,
    ) -> None:
        translations = json.loads(translations_path.read_text(encoding="utf-8"))
        text_meta = self._text_metadata(document_path)
        cache = self._load_translation_cache(cache_path)
        self.apply_duplicate_translations(translations, source_language, target_language)
        pending_items = [
            (text_id, item) for text_id, item in translations["items"].items()
            if not item.get(target_language) and not self.should_skip_text(item.get(source_language) or item.get("en") or "")
        ]
        total = len(pending_items)
        if progress_callback:
            progress_callback(0, total)

        if (provider or "").lower() == "gemini":
            self.translate_file_with_gemini_batches(
                translations,
                translations_path,
                pending_items,
                source_language,
                target_language,
                text_meta,
                cache,
                cache_path,
                log_path,
                progress_callback,
                cancel_callback,
            )
            return

        completed = 0
        parallelism = self.translation_parallelism(provider)
        next_index = 0
        futures = {}

        def save_progress() -> None:
            self._ensure_language(translations, target_language)
            translations_path.write_text(
                json.dumps(translations, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._save_translation_cache(cache_path, cache)
            if progress_callback:
                progress_callback(completed, total)

        def submit_next(executor: ThreadPoolExecutor) -> bool:
            nonlocal next_index
            if next_index >= total:
                return False
            text_id, item = pending_items[next_index]
            next_index += 1
            source = item.get(source_language) or item.get("en") or ""
            cache_key = self.cache_key(source, source_language, target_language, provider)
            if cache_key in cache:
                futures[executor.submit(lambda value=dict(cache[cache_key]): value)] = (text_id, source, True)
                return True
            self._append_translation_log(
                log_path,
                "started",
                text_id,
                source_language,
                target_language,
                source,
                text_meta.get(text_id, {}),
            )
            futures[executor.submit(self.translate_item, source, source_language, target_language, provider, dict(cache))] = (text_id, source, False)
            return True

        executor = ThreadPoolExecutor(max_workers=parallelism)
        aborted = False
        try:
            while len(futures) < parallelism and submit_next(executor):
                pass

            while futures:
                if cancel_callback and cancel_callback():
                    aborted = True
                    for future in futures:
                        future.cancel()
                    if next_index < total:
                        text_id, item = pending_items[next_index]
                        self._append_translation_log(
                            log_path,
                            "aborted",
                            text_id,
                            source_language,
                            target_language,
                            item.get(source_language) or item.get("en") or "",
                            text_meta.get(text_id, {}),
                            "Abort requested. Pending queue stopped.",
                        )
                    break

                done, _ = wait(futures, timeout=0.5, return_when=FIRST_COMPLETED)
                if not done:
                    continue

                for future in done:
                    text_id, source, from_cache = futures.pop(future)
                    item = translations["items"][text_id]
                    try:
                        result = future.result()
                        item[target_language] = result["translation"]
                        if result.get("raw"):
                            item[f"{target_language}_raw"] = result["raw"]
                        if result.get("rewrite_error"):
                            item[f"{target_language}_rewrite_error"] = result["rewrite_error"]
                    except Exception as exc:
                        self._append_translation_log(
                            log_path,
                            "failed",
                            text_id,
                            source_language,
                            target_language,
                            source,
                            text_meta.get(text_id, {}),
                            str(exc),
                        )
                        raise

                    cache[self.cache_key(source, source_language, target_language, provider)] = result
                    completed += 1
                    save_progress()
                    if not from_cache:
                        self._append_translation_log(
                            log_path,
                            "translated",
                            text_id,
                            source_language,
                            target_language,
                            source,
                            text_meta.get(text_id, {}),
                            translated=item[target_language],
                            raw=item.get(f"{target_language}_raw"),
                            error=item.get(f"{target_language}_rewrite_error"),
                        )

                    while len(futures) < parallelism and not (cancel_callback and cancel_callback()) and submit_next(executor):
                        pass
        finally:
            executor.shutdown(wait=not aborted, cancel_futures=True)

        self._ensure_language(translations, target_language)
        translations_path.write_text(
            json.dumps(translations, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._save_translation_cache(cache_path, cache)

    def translate_file_with_gemini_batches(
        self,
        translations: dict,
        translations_path: Path,
        pending_items: list[tuple[str, dict]],
        source_language: str,
        target_language: str,
        text_meta: dict[str, dict],
        cache: dict,
        cache_path: Path | None,
        log_path: Path | None,
        progress_callback: Callable[[int, int], None] | None,
        cancel_callback: Callable[[], bool] | None,
    ) -> None:
        total = len(pending_items)
        completed = 0
        batch_size = self.gemini_batch_size()
        max_batch_tokens = self.env_int("GEMINI_BATCH_MAX_TOKENS", 6000)

        def save_progress() -> None:
            self._ensure_language(translations, target_language)
            translations_path.write_text(
                json.dumps(translations, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._save_translation_cache(cache_path, cache)
            if progress_callback:
                progress_callback(completed, total)

        queue = list(pending_items)
        while queue:
            if cancel_callback and cancel_callback():
                text_id, item = queue[0]
                self._append_translation_log(
                    log_path,
                    "aborted",
                    text_id,
                    source_language,
                    target_language,
                    item.get(source_language) or item.get("en") or "",
                    text_meta.get(text_id, {}),
                    "Abort requested. Pending batch queue stopped.",
                )
                break

            batch, queue = self.take_gemini_batch(queue, source_language, max_batch_tokens, batch_size)
            request_items = []
            cached_results = []
            for text_id, item in batch:
                source = item.get(source_language) or item.get("en") or ""
                cache_key = self.cache_key(source, source_language, target_language, "gemini")
                if cache_key in cache:
                    cached_results.append((text_id, source, dict(cache[cache_key])))
                    continue
                request_items.append({"id": text_id, "text": source})
                self._append_translation_log(
                    log_path,
                    "started",
                    text_id,
                    source_language,
                    target_language,
                    source,
                    text_meta.get(text_id, {}),
                )

            for text_id, source, result in cached_results:
                translations["items"][text_id][target_language] = result["translation"]
                completed += 1
                save_progress()

            if not request_items:
                continue

            try:
                results = self.translate_batch_with_gemini(
                    request_items,
                    self.model_language(source_language),
                    self.model_language(target_language),
                    self.mode_instruction(target_language),
                )
            except Exception:
                if len(request_items) > 1:
                    half = max(1, len(request_items) // 2)
                    queue = [(entry["id"], translations["items"][entry["id"]]) for entry in request_items[half:]] + queue
                    queue = [(entry["id"], translations["items"][entry["id"]]) for entry in request_items[:half]] + queue
                    batch_size = max(1, batch_size // 2)
                    continue
                text_id = request_items[0]["id"]
                source = request_items[0]["text"]
                self._append_translation_log(
                    log_path,
                    "failed",
                    text_id,
                    source_language,
                    target_language,
                    source,
                    text_meta.get(text_id, {}),
                    "Gemini batch failed for single item.",
                )
                raise

            missing = []
            for entry in request_items:
                text_id = entry["id"]
                source = entry["text"]
                translation = results.get(text_id)
                if not isinstance(translation, str) or not translation.strip():
                    missing.append(entry)
                    continue
                result = {"translation": translation.strip()}
                translations["items"][text_id][target_language] = result["translation"]
                cache[self.cache_key(source, source_language, target_language, "gemini")] = result
                completed += 1
                save_progress()
                self._append_translation_log(
                    log_path,
                    "translated",
                    text_id,
                    source_language,
                    target_language,
                    source,
                    text_meta.get(text_id, {}),
                    translated=result["translation"],
                )

            if missing:
                if len(missing) == len(request_items) and len(missing) == 1:
                    entry = missing[0]
                    self._append_translation_log(
                        log_path,
                        "failed",
                        entry["id"],
                        source_language,
                        target_language,
                        entry["text"],
                        text_meta.get(entry["id"], {}),
                        "Gemini response missed this id.",
                    )
                    raise RuntimeError("Gemini response did not include requested translation id.")
                queue = [(entry["id"], translations["items"][entry["id"]]) for entry in missing] + queue
                batch_size = max(1, batch_size // 2)

        save_progress()

    def _ensure_language(self, translations: dict, target_language: str) -> None:
        if target_language not in [language["code"] for language in translations["availableLanguages"]]:
            translations["availableLanguages"].append({"code": target_language, "label": target_language})

    def apply_duplicate_translations(self, translations: dict, source_language: str, target_language: str) -> None:
        seen = {}
        for item in translations["items"].values():
            source = (item.get(source_language) or item.get("en") or "").strip()
            if not source:
                continue
            if item.get(target_language):
                seen[source] = item[target_language]
            elif source in seen:
                item[target_language] = seen[source]

    def should_skip_text(self, text: str) -> bool:
        stripped = " ".join(text.split())
        if not stripped:
            return True
        if len(stripped) <= 2:
            return True
        if JUNK_TEXT_RE.match(stripped):
            return True
        if re.fullmatch(r"(page\s*)?\d+", stripped, flags=re.IGNORECASE):
            return True
        return False

    def estimate_package_translation(
        self,
        translations_path: Path,
        source_language: str,
        target_language: str,
        provider: str,
    ) -> dict:
        translations = json.loads(translations_path.read_text(encoding="utf-8"))
        self.apply_duplicate_translations(translations, source_language, target_language)
        items = []
        skipped = 0
        duplicates = 0
        seen_sources = set()
        for item in translations["items"].values():
            source = item.get(source_language) or item.get("en") or ""
            if item.get(target_language):
                continue
            if self.should_skip_text(source):
                skipped += 1
                continue
            normalized = " ".join(source.split())
            if normalized in seen_sources:
                duplicates += 1
                continue
            seen_sources.add(normalized)
            items.append(source)
        words = sum(len(text.split()) for text in items)
        input_tokens = sum(self.estimate_tokens(text) for text in items)
        output_tokens = round(input_tokens * 1.5)
        provider = (provider or "indictrans2").lower()
        batch_size = self.gemini_batch_size() if provider == "gemini" else 1
        requests = (len(items) + batch_size - 1) // batch_size if items else 0
        seconds = self.estimate_provider_seconds(provider, requests, input_tokens + output_tokens)
        return {
            "provider": provider,
            "pendingItems": len(items),
            "skippedItems": skipped,
            "duplicateItems": duplicates,
            "words": words,
            "estimatedInputTokens": input_tokens,
            "estimatedOutputTokens": output_tokens,
            "estimatedTotalTokens": input_tokens + output_tokens,
            "batchSize": batch_size,
            "estimatedRequests": requests,
            "estimatedSeconds": seconds,
            "geminiQuota": self.gemini_quota_status() if provider == "gemini" else None,
        }

    def estimate_provider_seconds(self, provider: str, requests: int, tokens: int) -> int:
        if requests <= 0:
            return 0
        if provider == "gemini":
            rpm = self.env_int("GEMINI_RPM", 15)
            return round((requests / max(rpm, 1)) * 60)
        if provider == "google":
            return max(5, round(requests * 0.8))
        if provider == "chatgpt":
            return max(10, round(requests * 2))
        return max(30, round(requests * 15))

    def gemini_quota_status(self) -> dict:
        quota_path = ROOT / "logs" / "gemini-quota.json"
        today = date.today().isoformat()
        used = 0
        if quota_path.exists():
            quota = json.loads(quota_path.read_text(encoding="utf-8"))
            if quota.get("date") == today:
                used = int(quota.get("requests", 0))
        rpd = self.env_int("GEMINI_RPD", 1500)
        return {"usedToday": used, "dailyLimit": rpd, "remainingToday": max(0, rpd - used)}

    def _text_metadata(self, document_path: Path | None) -> dict[str, dict]:
        if not document_path or not document_path.exists():
            return {}
        document = json.loads(document_path.read_text(encoding="utf-8"))
        metadata = {}
        for page in document.get("pages", []):
            for text in page.get("texts", []):
                metadata[text["id"]] = {
                    "page": page.get("page"),
                    "paragraph": text.get("block"),
                    "line": text.get("line"),
                    "span": text.get("span"),
                }
        return metadata

    def _append_translation_log(
        self,
        log_path: Path | None,
        status: str,
        text_id: str,
        source_language: str,
        target_language: str,
        source_text: str,
        metadata: dict,
        error: str | None = None,
        translated: str | None = None,
        raw: str | None = None,
    ) -> None:
        if not log_path:
            return
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists():
            data = json.loads(log_path.read_text(encoding="utf-8"))
        else:
            data = {"version": 1, "entries": []}
        data["entries"].append(
            {
                "time": datetime.now().isoformat(timespec="seconds"),
                "status": status,
                "textId": text_id,
                "sourceLanguage": source_language,
                "targetLanguage": target_language,
                "page": metadata.get("page"),
                "paragraph": metadata.get("paragraph"),
                "line": metadata.get("line"),
                "span": metadata.get("span"),
                "text": source_text,
                "raw": raw,
                "translated": translated,
                "error": error,
            }
        )
        log_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def translate_text(
        self,
        text: str,
        source_language: str,
        target_language: str,
        provider: str = "indictrans2",
    ) -> str:
        if source_language == target_language:
            return text
        model_source_language = self.model_language(source_language)
        model_target_language = self.model_language(target_language)
        provider = (provider or "indictrans2").lower()
        if provider == "local":
            return f"[{target_language.upper()}] {text}"
        if provider == "indictrans2":
            api_url = os.getenv("INDICTRANS2_API_URL", "http://127.0.0.1:9000/translate")
            try:
                return self.translate_with_local_service(text, model_source_language, model_target_language, api_url)
            except RuntimeError as exc:
                if "connection failed" not in str(exc).lower():
                    raise
                return self.translate_with_embedded_indictrans(text, model_source_language, model_target_language)
        if provider == "google":
            api_url = os.getenv("GOOGLE_TRANSLATION_API_URL")
            api_key = os.getenv("GOOGLE_TRANSLATION_API_KEY")
            if not api_url or not api_key:
                raise RuntimeError("Google translation provider is not configured.")
            return self.translate_with_json_api(
                text,
                model_source_language,
                model_target_language,
                api_url,
                api_key,
                self.mode_instruction(target_language),
            )
        if provider == "gemini":
            api_key = os.getenv("GEMINI_API_KEY")
            model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
            if not api_key:
                raise RuntimeError("Gemini provider is not configured. Set GEMINI_API_KEY in .env.")
            return self.translate_with_gemini(
                text,
                model_source_language,
                model_target_language,
                api_key,
                model,
                self.mode_instruction(target_language),
            )

        api_url = os.getenv("AI_TRANSLATION_API_URL")
        api_key = os.getenv("AI_TRANSLATION_API_KEY")
        model = os.getenv("AI_TRANSLATION_MODEL", "gpt-4o-mini")
        if not api_url or not api_key:
            raise RuntimeError("ChatGPT translation provider is not configured.")
        return self.translate_with_ai(text, model_source_language, model_target_language, api_url, api_key, model, target_language)

    def translate_item(
        self,
        text: str,
        source_language: str,
        target_language: str,
        provider: str,
        cache: dict,
    ) -> dict:
        cache_key = self.cache_key(text, source_language, target_language, provider)
        if cache_key in cache:
            return dict(cache[cache_key])

        if target_language != "hi_modern":
            translation = self.translate_text(text, source_language, target_language, provider)
            result = {"translation": translation}
            cache[cache_key] = result
            return result

        raw_key = self.cache_key(text, source_language, "hi_modern_raw", provider)
        if raw_key in cache:
            raw = cache[raw_key]["translation"]
        else:
            raw = self.translate_text(text, source_language, "hi", provider)
            cache[raw_key] = {"translation": raw}

        try:
            rewritten = self.rewrite_modern_hindi(raw, text)
            rewritten = self.restore_modern_terms(text, rewritten)
            result = {"translation": rewritten, "raw": raw}
        except Exception as exc:
            result = {
                "translation": self.restore_modern_terms(text, raw),
                "raw": raw,
                "rewrite_error": str(exc),
            }
        cache[cache_key] = result
        return result

    def rewrite_modern_hindi(self, hindi_text: str, source_text: str) -> str:
        api_url = os.getenv("HINDI_REWRITER_API_URL", "http://127.0.0.1:11434/v1/chat/completions")
        model = os.getenv("HINDI_REWRITER_MODEL", "qwen2.5:1.5b")
        timeout = int(os.getenv("HINDI_REWRITER_TIMEOUT", "90"))
        prompt = (
            "You are a Hindi style rewriter.\n\n"
            "Rewrite the given Hindi text into modern natural Hindi.\n\n"
            "Rules:\n"
            "- Preserve the original meaning exactly.\n"
            "- Convert formal/classical/government-style Hindi into modern conversational Hindi.\n"
            "- Keep technical terms, brand names, programming words, file names, APIs, HTML tags, CSS, JavaScript code, numbers, URLs, and proper nouns unchanged.\n"
            "- Maintain paragraph breaks, lists, punctuation, and formatting.\n"
            "- Do not add explanations, comments, examples, or extra sentences.\n"
            "- Return only the rewritten Hindi text.\n\n"
            f"Original source text for protected terms:\n{source_text}\n\n"
            f"Hindi text:\n{hindi_text}"
        )
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Return only the rewritten Hindi text. No explanations."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": int(os.getenv("HINDI_REWRITER_MAX_TOKENS", "512")),
        }
        request = urllib.request.Request(
            api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Hindi rewriter failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Hindi rewriter connection failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError("Hindi rewriter timed out.") from exc

        content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
        content = content.strip()
        if not content:
            raise RuntimeError("Hindi rewriter returned empty output.")
        return content

    def restore_modern_terms(self, source_text: str, rewritten: str) -> str:
        source_lower = source_text.lower()
        result = rewritten
        for english, hindi_terms in MODERN_HINDI_TERM_MAP.items():
            if english not in source_lower:
                continue
            for hindi in hindi_terms:
                result = result.replace(hindi, english)
        return result

    def cache_key(self, text: str, source_language: str, target_language: str, provider: str) -> str:
        payload = json.dumps(
            {
                "text": text,
                "source": source_language,
                "target": target_language,
                "provider": provider,
                "prompt": MODERN_HINDI_PROMPT_VERSION if target_language.startswith("hi_modern") else "",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _load_translation_cache(self, cache_path: Path | None) -> dict:
        if not cache_path or not cache_path.exists():
            return {}
        return json.loads(cache_path.read_text(encoding="utf-8"))

    def _save_translation_cache(self, cache_path: Path | None, cache: dict) -> None:
        if not cache_path:
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    def model_language(self, language: str) -> str:
        return LANGUAGE_MODEL_ALIASES.get(language, language)

    def translation_parallelism(self, provider: str) -> int:
        provider_key = f"{provider.upper()}_PARALLELISM"
        provider_defaults = {
            "indictrans2": 1,
            "gemini": 1,
            "google": 4,
            "chatgpt": 2,
            "local": 4,
        }
        default = provider_defaults.get(provider, self.env_int("TRANSLATION_PARALLELISM", 2))
        value = os.getenv(provider_key, str(default))
        try:
            parallelism = int(value)
        except ValueError:
            parallelism = 2
        return max(1, min(parallelism, 4))

    def translate_with_ai(
        self,
        text: str,
        source_language: str,
        target_language: str,
        api_url: str,
        api_key: str,
        model: str,
        display_target_language: str,
    ) -> str:
        mode_instruction = self.mode_instruction(display_target_language)
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Translate the user's text only. Return strict JSON in this exact shape: "
                        "{\"translation\":\"...\"}. Do not add explanations. "
                        f"{mode_instruction}"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "source_language": source_language,
                            "target_language": target_language,
                            "text": text,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0.2,
        }
        request = urllib.request.Request(
            api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=int(os.getenv("AI_TRANSLATION_TIMEOUT", "90"))) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"AI translation API failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"AI translation API connection failed: {exc.reason}") from exc

        content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError("AI translation returned invalid JSON output.") from exc

        translation = parsed.get("translation")
        if not isinstance(translation, str) or not translation.strip():
            raise RuntimeError("AI translation returned an empty or wrong output.")
        return translation.strip()

    def translate_with_json_api(
        self,
        text: str,
        source_language: str,
        target_language: str,
        api_url: str,
        api_key: str,
        mode_instruction: str,
    ) -> str:
        payload = {
            "source_language": source_language,
            "target_language": target_language,
            "text": text,
            "instruction": mode_instruction,
        }
        request = urllib.request.Request(
            api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=int(os.getenv("GOOGLE_TRANSLATION_TIMEOUT", "60"))) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Google translation API failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Google translation API connection failed: {exc.reason}") from exc

        translation = response_data.get("translation") or response_data.get("translatedText")
        if not isinstance(translation, str) or not translation.strip():
            raise RuntimeError("Google translation returned an empty or wrong output.")
        return translation.strip()

    def translate_with_gemini(
        self,
        text: str,
        source_language: str,
        target_language: str,
        api_key: str,
        model: str,
        mode_instruction: str,
    ) -> str:
        prompt = (
            "Translate the JSON field `text` only. Return strict JSON in this exact shape: "
            "{\"translation\":\"...\"}. Do not add explanations. "
            f"{mode_instruction}\n\n"
            f"source_language={source_language}\n"
            f"target_language={target_language}\n"
            f"text={json.dumps(text, ensure_ascii=False)}"
        )
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": float(os.getenv("GEMINI_TEMPERATURE", "0.1")),
                "responseMimeType": "application/json",
            },
        }
        request_tokens = self.estimate_tokens(prompt)
        try:
            response_data = self.call_gemini(payload, model, api_key, request_tokens)
            content = response_data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise RuntimeError("Gemini returned invalid JSON output.") from exc
        translation = parsed.get("translation")
        if not isinstance(translation, str) or not translation.strip():
            raise RuntimeError("Gemini returned an empty or wrong output.")
        return translation.strip()

    def translate_batch_with_gemini(
        self,
        items: list[dict],
        source_language: str,
        target_language: str,
        mode_instruction: str,
    ) -> dict[str, str]:
        api_key = os.getenv("GEMINI_API_KEY")
        model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
        if not api_key:
            raise RuntimeError("Gemini provider is not configured. Set GEMINI_API_KEY in .env.")
        prompt = (
            "Translate each item in this JSON array. Return strict JSON only in this shape: "
            "{\"translations\":{\"id\":\"translated text\"}}.\n"
            "Rules:\n"
            "- Keep every input id exactly unchanged.\n"
            "- Return one translated string for every id.\n"
            "- Do not add explanations.\n"
            f"- {mode_instruction}\n"
            f"- source_language={source_language}\n"
            f"- target_language={target_language}\n\n"
            f"Items:\n{json.dumps(items, ensure_ascii=False)}"
        )
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": float(os.getenv("GEMINI_TEMPERATURE", "0.1")),
                "responseMimeType": "application/json",
            },
        }
        response_data = self.call_gemini(payload, model, api_key, self.estimate_tokens(prompt))
        content = response_data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(content)
        translations = parsed.get("translations")
        if not isinstance(translations, dict):
            raise RuntimeError("Gemini batch returned invalid JSON shape.")
        return translations

    def gemini_batch_size(self) -> int:
        return max(1, min(self.env_int("GEMINI_BATCH_SIZE", 10), 30))

    def take_gemini_batch(
        self,
        queue: list[tuple[str, dict]],
        source_language: str,
        max_tokens: int,
        max_items: int,
    ) -> tuple[list[tuple[str, dict]], list[tuple[str, dict]]]:
        batch = []
        tokens = 0
        remaining = list(queue)
        while remaining and len(batch) < max_items:
            text_id, item = remaining[0]
            source = item.get(source_language) or item.get("en") or ""
            estimated = self.estimate_tokens(source)
            if batch and tokens + estimated > max_tokens:
                break
            batch.append((text_id, item))
            tokens += estimated
            remaining.pop(0)
        if not batch and remaining:
            batch.append(remaining.pop(0))
        return batch, remaining

    def call_gemini(self, payload: dict, model: str, api_key: str, request_tokens: int) -> dict:
        retries = int(os.getenv("GEMINI_RETRIES", "3"))
        timeout = int(os.getenv("GEMINI_TIMEOUT", "90"))
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )

        for attempt in range(retries + 1):
            self.apply_gemini_limits(request_tokens)
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")
                if exc.code == 429 and attempt < retries:
                    time.sleep(int(os.getenv("GEMINI_429_WAIT_SECONDS", "60")))
                    continue
                raise RuntimeError(f"Gemini API failed ({exc.code}): {detail}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < retries:
                    time.sleep(min(2 ** attempt, 10))
                    continue
                raise RuntimeError(f"Gemini API connection failed: {exc}") from exc

        raise RuntimeError("Gemini request failed after retries.")

    def apply_gemini_limits(self, estimated_tokens: int) -> None:
        rpm = self.env_int("GEMINI_RPM", 15)
        tpm = self.env_int("GEMINI_TPM", 1_000_000)
        rpd = self.env_int("GEMINI_RPD", 1500)
        min_interval = 60 / max(rpm, 1)
        quota_path = ROOT / "logs" / "gemini-quota.json"

        with RATE_LIMIT_LOCK:
            quota_path.parent.mkdir(exist_ok=True)
            today = date.today().isoformat()
            if quota_path.exists():
                quota = json.loads(quota_path.read_text(encoding="utf-8"))
            else:
                quota = {"date": today, "requests": 0}
            if quota.get("date") != today:
                quota = {"date": today, "requests": 0}
            if quota["requests"] >= rpd:
                raise RuntimeError(f"Gemini daily request limit reached ({rpd} RPD).")

            state = RATE_LIMIT_STATE.setdefault(
                "gemini",
                {"last_request": 0.0, "minute_start": time.time(), "minute_tokens": 0},
            )
            now = time.time()
            if now - state["minute_start"] >= 60:
                state["minute_start"] = now
                state["minute_tokens"] = 0
            if state["minute_tokens"] + estimated_tokens > tpm:
                sleep_for = max(0, 60 - (now - state["minute_start"]))
                time.sleep(sleep_for)
                now = time.time()
                state["minute_start"] = now
                state["minute_tokens"] = 0
            sleep_for = max(0, min_interval - (now - state["last_request"]))
            if sleep_for:
                time.sleep(sleep_for)
                now = time.time()

            state["last_request"] = now
            state["minute_tokens"] += estimated_tokens
            quota["requests"] += 1
            quota_path.write_text(json.dumps(quota, ensure_ascii=False, indent=2), encoding="utf-8")

    def estimate_tokens(self, text: str) -> int:
        return max(1, round(len(text) / 4))

    def env_int(self, name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except ValueError:
            return default

    def translate_with_local_service(
        self,
        text: str,
        source_language: str,
        target_language: str,
        api_url: str,
    ) -> str:
        payload = {
            "source_language": source_language,
            "target_language": target_language,
            "text": text,
        }
        request = urllib.request.Request(
            api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=int(os.getenv("INDICTRANS2_TIMEOUT", "300"))) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"IndicTrans2 service failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"IndicTrans2 service connection failed: {exc.reason}") from exc

        translation = response_data.get("translation") or response_data.get("translatedText")
        if not isinstance(translation, str) or not translation.strip():
            raise RuntimeError("IndicTrans2 service returned an empty or wrong output.")
        return translation.strip()

    def mode_instruction(self, target_language: str) -> str:
        return HINDI_MODE_PROMPTS.get(
            target_language,
            "Use a natural, accurate translation style for the target language.",
        )

    def translate_with_embedded_indictrans(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        vendor_dir = ROOT / "indictrans_service" / "vendor"
        if vendor_dir.exists() and str(vendor_dir) not in sys.path:
            sys.path.insert(0, str(vendor_dir))

        try:
            from indictrans_service.main import normalize_language
        except Exception as exc:
            raise RuntimeError("IndicTrans2 embedded fallback is not available.") from exc

        translator = get_embedded_indictrans_translator()
        with EMBEDDED_INDICTRANS_LOCK:
            return translator.translate(
                text,
                normalize_language(source_language),
                normalize_language(target_language),
            )


class ReaderTemplate:
    def copy_to(self, reader_dir: Path) -> None:
        reader_dir.mkdir(parents=True, exist_ok=True)
        for source in READER_TEMPLATE_DIR.iterdir():
            if source.is_file():
                shutil.copy2(source, reader_dir / source.name)


@lru_cache(maxsize=1)
def get_embedded_indictrans_translator():
    from indictrans_service.main import IndicTrans2Translator

    return IndicTrans2Translator()


class PdfHtmlConverter:
    def build_html(self, document: dict, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pages = "\n".join(self._render_page(page) for page in document["pages"])
        html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(document["source"])}</title>
    <style>
      body {{ margin: 0; background: #eef1f5; font-family: Arial, Helvetica, sans-serif; }}
      .page {{ position: relative; margin: 24px auto; background: #fff; box-shadow: 0 8px 28px rgba(20,24,32,.16); overflow: hidden; }}
      .vector-layer {{ position: absolute; inset: 0; width: 100%; height: 100%; }}
      .image {{ position: absolute; object-fit: fill; }}
      .text {{ position: absolute; overflow: visible; white-space: nowrap; line-height: 1; transform-origin: top left; }}
    </style>
  </head>
  <body>
    {pages}
  </body>
</html>
"""
        output_path.write_text(html, encoding="utf-8")

    def _render_page(self, page: dict) -> str:
        vectors = self._render_vectors(page)
        images = "\n".join(self._render_image(item) for item in page.get("images", []))
        texts = "\n".join(self._render_text(item) for item in page["texts"])
        return f"""<section class="page" style="width:{page["width"]}px;height:{page["height"]}px">
  {vectors}
  {images}
  {texts}
</section>"""

    def _render_vectors(self, page: dict) -> str:
        paths = "\n".join(self._render_drawing(drawing) for drawing in page.get("drawings", []))
        return (
            f'<svg class="vector-layer" viewBox="0 0 {page["width"]} {page["height"]}" '
            f'preserveAspectRatio="none" aria-hidden="true">{paths}</svg>'
        )

    def _render_drawing(self, drawing: dict) -> str:
        commands = []
        for item in drawing["items"]:
            if item["type"] == "line":
                commands.append(f'M {item["x1"]} {item["y1"]} L {item["x2"]} {item["y2"]}')
            elif item["type"] == "rect":
                x, y, w, h = item["x"], item["y"], item["w"], item["h"]
                commands.append(f'M {x} {y} H {x + w} V {y + h} H {x} Z')
            elif item["type"] == "curve":
                commands.append(
                    f'M {item["x1"]} {item["y1"]} C {item["cx1"]} {item["cy1"]} '
                    f'{item["cx2"]} {item["cy2"]} {item["x2"]} {item["y2"]}'
                )
            elif item["type"] == "quad":
                points = item["points"]
                commands.append(
                    f'M {points[0][0]} {points[0][1]} L {points[1][0]} {points[1][1]} '
                    f'L {points[2][0]} {points[2][1]} L {points[3][0]} {points[3][1]} Z'
                )

        stroke = drawing.get("stroke") or "none"
        fill = drawing.get("fill") or "none"
        attrs = [
            f'd="{escape(" ".join(commands))}"',
            f'stroke="{stroke}"',
            f'fill="{fill}"',
            f'stroke-width="{drawing.get("strokeWidth", 1)}"',
            f'stroke-opacity="{drawing.get("strokeOpacity", 1)}"',
            f'fill-opacity="{drawing.get("fillOpacity", 1)}"',
        ]
        if drawing.get("evenOdd"):
            attrs.append('fill-rule="evenodd"')
        return f"<path {' '.join(attrs)} />"

    def _render_image(self, item: dict) -> str:
        return (
            f'<img class="image" src="{escape(item["src"])}" alt="" '
            f'style="left:{item["x"]}px;top:{item["y"]}px;'
            f'width:{max(item["w"], 1)}px;height:{max(item["h"], 1)}px">'
        )

    def _render_text(self, item: dict) -> str:
        return (
            f'<span class="text" data-id="{item["id"]}" '
            f'style="left:{item["x"]}px;top:{item["y"]}px;'
            f'width:{max(item["w"], 8)}px;height:{max(item["h"] * 1.35, item["fontSize"] * 1.35)}px;'
            f'font-size:{item["fontSize"]}px;'
            f'font-weight:{item.get("fontWeight", "400")};'
            f'font-style:{item.get("fontStyle", "normal")};'
            f'color:{item.get("color", "#15171c")};'
            f'font-family:{self._font_stack(item.get("font", ""))}">{escape(item["text"])}</span>'
        )

    def _font_stack(self, font_name: str) -> str:
        family = font_name.split("+")[-1]
        for suffix in ("-Bold", "-Italic", "-Regular", "-Roman"):
            family = family.replace(suffix, "")
        return f'"{escape(family)}", Arial, Helvetica, sans-serif'


class HtmlStructureExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[dict] = []
        self.texts: list[dict] = []
        self._stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        self._stack.append(tag)
        if tag == "img":
            self.images.append({"src": attrs_dict.get("src", ""), "alt": attrs_dict.get("alt", "")})

    def handle_endtag(self, tag: str) -> None:
        if self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.texts.append({"text": text, "path": list(self._stack)})

    def to_document(self, source_name: str) -> dict:
        return {
            "source": source_name,
            "version": 1,
            "layout": "html-structure",
            "images": self.images,
            "texts": self.texts,
        }

    def to_reader_document(self, source_name: str) -> dict:
        texts = []
        y = 48
        for index, item in enumerate(self.texts, start=1):
            texts.append(
                {
                    "id": f"p1_t{index}",
                    "text": item["text"],
                    "x": 48,
                    "y": y,
                    "w": 700,
                    "h": 18,
                    "fontSize": 14,
                    "font": "Arial",
                    "fontWeight": "400",
                    "fontStyle": "normal",
                    "color": "#15171c",
                    "block": index,
                    "line": 1,
                    "span": 1,
                }
            )
            y += 26

        return {
            "source": source_name,
            "version": 1,
            "layout": "html-text-reader",
            "languages": [language.code for language in LANGUAGES],
            "pages": [
                {
                    "page": 1,
                    "width": 794,
                    "height": max(1123, y + 48),
                    "images": [],
                    "drawings": [],
                    "texts": texts,
                }
            ],
        }


class PackageBuilder:
    def __init__(self) -> None:
        self.extractor = PdfDocumentExtractor()
        self.translator = TranslationService()
        self.reader_template = ReaderTemplate()
        self.html_converter = PdfHtmlConverter()

    def build_reader_package(
        self,
        pdf_path: Path,
        output_dir: Path,
        source_name: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Path:
        images_dir, data_dir, _reader_dir = self._prepare_dirs(output_dir)
        document = self.extractor.extract(pdf_path, images_dir, source_name, progress_callback)
        self._write_json(data_dir / "document.json", document)
        self._write_json(data_dir / "translations.json", self.translator.create_shell(document))
        return self.create_zip(output_dir, output_dir / f"output{PACKAGE_EXTENSION}")

    def build_html_package(
        self,
        pdf_path: Path,
        output_dir: Path,
        source_name: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Path:
        images_dir, data_dir, _reader_dir = self._prepare_dirs(output_dir)
        document = self.extractor.extract(pdf_path, images_dir, source_name, progress_callback)
        self._write_json(data_dir / "document.json", document)
        self.html_converter.build_html(document, output_dir / "html" / "document.html")
        return self.create_zip(output_dir, output_dir / "pdf-html.zip")

    def build_html_package_from_html(self, html_path: Path, output_dir: Path, source_name: str) -> Path:
        data_dir = output_dir / "data"
        images_dir = output_dir / "images"
        data_dir.mkdir(parents=True, exist_ok=True)
        images_dir.mkdir(parents=True, exist_ok=True)

        parser = HtmlStructureExtractor()
        parser.feed(html_path.read_text(encoding="utf-8", errors="ignore"))
        document = parser.to_reader_document(source_name)
        self._write_json(data_dir / "document.json", document)
        self._write_json(data_dir / "translations.json", self.translator.create_shell(document))
        return self.create_zip(output_dir, output_dir / f"html{PACKAGE_EXTENSION}")

    def translate_reader_package(
        self,
        output_dir: Path,
        source_language: str,
        target_language: str,
        provider: str = "indictrans2",
        progress_callback: Callable[[int, int], None] | None = None,
        cancel_callback: Callable[[], bool] | None = None,
    ) -> Path:
        translations_path = output_dir / "data" / "translations.json"
        document_path = output_dir / "data" / "document.json"
        log_path = output_dir / "data" / "translation-log.json"
        cache_path = output_dir / "data" / "translation-cache.json"
        if not translations_path.exists():
            raise FileNotFoundError("translations.json not found for this job")
        self.translator.translate_file(
            translations_path,
            source_language,
            target_language,
            provider,
            progress_callback,
            cancel_callback,
            document_path,
            log_path,
            cache_path,
        )
        return self.create_zip(output_dir, output_dir / f"output{PACKAGE_EXTENSION}")

    def extract_reader_package(self, package_path: Path, output_dir: Path) -> None:
        with zipfile.ZipFile(package_path) as archive:
            names = set(archive.namelist())
            required = {"data/document.json", "data/translations.json"}
            missing = required - names
            if missing:
                raise ValueError(f"Invalid .phjz package. Missing: {', '.join(sorted(missing))}")
            archive.extractall(output_dir)

    def translate_extracted_package(
        self,
        output_dir: Path,
        source_language: str,
        target_language: str,
        provider: str = "indictrans2",
        progress_callback: Callable[[int, int], None] | None = None,
        cancel_callback: Callable[[], bool] | None = None,
    ) -> Path:
        translations_path = output_dir / "data" / "translations.json"
        document_path = output_dir / "data" / "document.json"
        log_path = output_dir / "data" / "translation-log.json"
        cache_path = output_dir / "data" / "translation-cache.json"
        if not translations_path.exists():
            raise FileNotFoundError("translations.json not found in .phjz package")
        self.translator.translate_file(
            translations_path,
            source_language,
            target_language,
            provider,
            progress_callback,
            cancel_callback,
            document_path,
            log_path,
            cache_path,
        )
        return self.create_zip(output_dir, output_dir / f"translated{PACKAGE_EXTENSION}")

    def estimate_translation(
        self,
        output_dir: Path,
        source_language: str,
        target_language: str,
        provider: str,
    ) -> dict:
        translations_path = output_dir / "data" / "translations.json"
        if not translations_path.exists():
            raise FileNotFoundError("translations.json not found")
        return self.translator.estimate_package_translation(
            translations_path,
            source_language,
            target_language,
            provider,
        )

    def provider_config(self) -> dict:
        return {
            "presets": [
                {"id": "fast-online", "label": "Fast Online", "provider": "gemini", "target": "hi_modern"},
                {"id": "accurate-online", "label": "Accurate Online", "provider": "google", "target": "hi_modern"},
                {"id": "private-offline", "label": "Private Offline", "provider": "indictrans2", "target": "hi_pure"},
                {"id": "modern-offline", "label": "Modern Hindi Offline", "provider": "indictrans2", "target": "hi_modern"},
            ],
            "gemini": {
                "rpm": self.translator.env_int("GEMINI_RPM", 15),
                "tpm": self.translator.env_int("GEMINI_TPM", 1_000_000),
                "rpd": self.translator.env_int("GEMINI_RPD", 1500),
                "batchSize": self.translator.gemini_batch_size(),
                "batchMaxTokens": self.translator.env_int("GEMINI_BATCH_MAX_TOKENS", 6000),
                "quota": self.translator.gemini_quota_status(),
            },
        }

    def retranslate_item(
        self,
        output_dir: Path,
        text_id: str,
        source_language: str,
        target_language: str,
        provider: str = "indictrans2",
    ) -> str:
        translations_path = output_dir / "data" / "translations.json"
        translations = json.loads(translations_path.read_text(encoding="utf-8"))
        item = translations["items"].get(text_id)
        if not item:
            raise KeyError(f"Text id not found: {text_id}")
        source = item.get(source_language) or item.get("en") or ""
        return self.translator.translate_text(source, source_language, target_language, provider)

    def save_translation(
        self,
        output_dir: Path,
        text_id: str,
        target_language: str,
        value: str,
    ) -> Path:
        translations_path = output_dir / "data" / "translations.json"
        translations = json.loads(translations_path.read_text(encoding="utf-8"))
        item = translations["items"].get(text_id)
        if not item:
            raise KeyError(f"Text id not found: {text_id}")
        item[target_language] = value
        self.translator._ensure_language(translations, target_language)
        translations_path.write_text(
            json.dumps(translations, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.create_zip(output_dir, output_dir / f"output{PACKAGE_EXTENSION}")

    def create_zip(self, output_dir: Path, zip_path: Path) -> Path:
        if zip_path.exists():
            zip_path.unlink()

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in output_dir.rglob("*"):
                if (
                    path == zip_path
                    or path.is_dir()
                    or path.name == "job.json"
                    or path.suffix in {".zip", PACKAGE_EXTENSION}
                ):
                    continue
                archive.write(path, path.relative_to(output_dir))

        return zip_path

    def _prepare_dirs(self, output_dir: Path) -> tuple[Path, Path, Path]:
        images_dir = output_dir / "images"
        data_dir = output_dir / "data"
        reader_dir = output_dir / "reader"
        images_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        reader_dir.mkdir(parents=True, exist_ok=True)
        return images_dir, data_dir, reader_dir

    def _write_json(self, path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
