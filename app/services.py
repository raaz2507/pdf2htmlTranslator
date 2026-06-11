from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parent.parent
READER_TEMPLATE_DIR = ROOT / "templates" / "reader"


@dataclass(frozen=True)
class Language:
    code: str
    label: str


LANGUAGES = [
    Language("en", "English"),
    Language("hi", "Hindi"),
    Language("es", "Spanish"),
    Language("fr", "French"),
    Language("de", "German"),
    Language("ar", "Arabic"),
]


class PdfDocumentExtractor:
    def extract(self, pdf_path: Path, images_dir: Path, source_name: str) -> dict:
        images_dir.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(pdf_path)
        pages = []

        try:
            for page_index, page in enumerate(doc, start=1):
                pages.append(self._extract_page(page, page_index, images_dir))
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

    def translate_file(self, translations_path: Path, source_language: str, target_language: str) -> None:
        translations = json.loads(translations_path.read_text(encoding="utf-8"))
        for item in translations["items"].values():
            source = item.get(source_language) or item.get("en") or ""
            item[target_language] = self.translate_text(source, source_language, target_language)

        if target_language not in [language["code"] for language in translations["availableLanguages"]]:
            translations["availableLanguages"].append({"code": target_language, "label": target_language})

        translations_path.write_text(
            json.dumps(translations, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def translate_text(self, text: str, source_language: str, target_language: str) -> str:
        if source_language == target_language:
            return text
        return f"[{target_language.upper()}] {text}"


class ReaderTemplate:
    def copy_to(self, reader_dir: Path) -> None:
        reader_dir.mkdir(parents=True, exist_ok=True)
        for source in READER_TEMPLATE_DIR.iterdir():
            if source.is_file():
                shutil.copy2(source, reader_dir / source.name)


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


class PackageBuilder:
    def __init__(self) -> None:
        self.extractor = PdfDocumentExtractor()
        self.translator = TranslationService()
        self.reader_template = ReaderTemplate()
        self.html_converter = PdfHtmlConverter()

    def build_reader_package(self, pdf_path: Path, output_dir: Path, source_name: str) -> Path:
        images_dir, data_dir, reader_dir = self._prepare_dirs(output_dir)
        document = self.extractor.extract(pdf_path, images_dir, source_name)
        self._write_json(data_dir / "document.json", document)
        self._write_json(data_dir / "translations.json", self.translator.create_shell(document))
        self.reader_template.copy_to(reader_dir)
        self.html_converter.build_html(document, output_dir / "html" / "document.html")
        return self.create_zip(output_dir, output_dir / "output.zip")

    def build_html_package(self, pdf_path: Path, output_dir: Path, source_name: str) -> Path:
        images_dir, data_dir, _reader_dir = self._prepare_dirs(output_dir)
        document = self.extractor.extract(pdf_path, images_dir, source_name)
        self._write_json(data_dir / "document.json", document)
        self.html_converter.build_html(document, output_dir / "html" / "document.html")
        return self.create_zip(output_dir, output_dir / "pdf-html.zip")

    def build_html_structure_package(self, html_path: Path, output_dir: Path, source_name: str) -> Path:
        data_dir = output_dir / "data"
        html_dir = output_dir / "html"
        images_dir = output_dir / "images"
        data_dir.mkdir(parents=True, exist_ok=True)
        html_dir.mkdir(parents=True, exist_ok=True)
        images_dir.mkdir(parents=True, exist_ok=True)

        parser = HtmlStructureExtractor()
        parser.feed(html_path.read_text(encoding="utf-8", errors="ignore"))
        self._write_json(data_dir / "html-structure.json", parser.to_document(source_name))
        shutil.copy2(html_path, html_dir / "source.html")
        return self.create_zip(output_dir, output_dir / "html-structure.zip")

    def translate_reader_package(self, output_dir: Path, source_language: str, target_language: str) -> Path:
        translations_path = output_dir / "data" / "translations.json"
        if not translations_path.exists():
            raise FileNotFoundError("translations.json not found for this job")
        self.translator.translate_file(translations_path, source_language, target_language)
        return self.create_zip(output_dir, output_dir / "output.zip")

    def create_zip(self, output_dir: Path, zip_path: Path) -> Path:
        if zip_path.exists():
            zip_path.unlink()

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in output_dir.rglob("*"):
                if path == zip_path or path.is_dir():
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
