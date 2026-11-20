from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader

from .config import CompareConfig
from .logging_setup import log_event
from .models import Box, PageInfo, TextLine, TextToken

logger = logging.getLogger(__name__)


class PdfReadError(RuntimeError):
    pass


@dataclass(frozen=True)
class RenderResult:
    image: np.ndarray
    scale_x: float
    scale_y: float
    dpi_x: float
    dpi_y: float


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text).strip()


def _dedupe_tokens(tokens: Iterable[TextToken]) -> list[TextToken]:
    seen: set[tuple[str, int, int, int, int]] = set()
    output: list[TextToken] = []
    for token in tokens:
        text = token.text.strip()
        if not text:
            continue
        key = (
            normalize_text(text),
            round(token.box.x0),
            round(token.box.y0),
            round(token.box.x1),
            round(token.box.y1),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(
            TextToken(
                text=text,
                box=token.box,
                source=token.source,
                confidence=token.confidence,
            )
        )
    return output


class PdfSource:
    """One local PDF and its raw extraction adapters."""

    def __init__(self, path: Path):
        self.path = path.resolve()
        self._reader: PdfReader | None = None
        self._plumber = None
        self._pdfium: pdfium.PdfDocument | None = None
        self._token_cache: dict[int, list[TextToken]] = {}
        self._line_cache: dict[int, list[TextLine]] = {}
        self._page_info: list[PageInfo] | None = None

    def __enter__(self) -> "PdfSource":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        with self.path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise PdfReadError(f"Not a PDF file: {self.path}")
        try:
            self._reader = PdfReader(str(self.path), strict=False)
            self._plumber = pdfplumber.open(str(self.path))
            self._pdfium = pdfium.PdfDocument(str(self.path))
        except Exception as exc:
            raise PdfReadError(f"Could not open PDF: {self.path}") from exc
        if self._reader.is_encrypted:
            raise PdfReadError(f"Encrypted PDFs are unsupported: {self.path}")
        if len(self._reader.pages) == 0:
            raise PdfReadError(f"PDF contains no pages: {self.path}")
        log_event(
            logger,
            logging.INFO,
            "Opened PDF",
            path=str(self.path),
            pages=len(self),
            size_bytes=self.path.stat().st_size,
        )

    def close(self) -> None:
        if self._plumber is not None:
            self._plumber.close()
            self._plumber = None
        if self._pdfium is not None:
            self._pdfium.close()
            self._pdfium = None
        self._reader = None

    def __len__(self) -> int:
        if self._reader is None:
            raise RuntimeError("PDF is not open")
        return len(self._reader.pages)

    @property
    def metadata(self) -> dict:
        if self._reader is None:
            return {}
        return {
            key.lstrip("/"): str(value)
            for key, value in (self._reader.metadata or {}).items()
        }

    def page_infos(self, config: CompareConfig) -> list[PageInfo]:
        if self._reader is None or self._plumber is None:
            raise RuntimeError("PDF is not open")
        if self._page_info is not None:
            return self._page_info
        if len(self) > config.max_pages:
            raise PdfReadError(
                f"PDF has {len(self)} pages; configured maximum is {config.max_pages}"
            )
        infos: list[PageInfo] = []
        for index, page in enumerate(self._plumber.pages):
            text = page.extract_text() or ""
            rotation = int(page.rotation or 0)
            image_count = len(page.images)
            infos.append(
                PageInfo(
                    index=index,
                    width=float(page.width),
                    height=float(page.height),
                    rotation=rotation,
                    text_char_count=len(text.strip()),
                    image_count=image_count,
                    has_text_layer=len(re.sub(r"\s+", "", text)) >= config.text_min_chars,
                )
            )
        self._page_info = infos
        return infos

    def extract_tokens(self, page_index: int) -> list[TextToken]:
        if page_index in self._token_cache:
            return self._token_cache[page_index]
        if self._plumber is None:
            raise RuntimeError("PDF is not open")
        page = self._plumber.pages[page_index]
        words = page.extract_words(
            use_text_flow=False,
            keep_blank_chars=False,
            extra_attrs=["size", "fontname"],
        )
        tokens = [
            TextToken(
                text=word["text"],
                box=Box(
                    float(word["x0"]),
                    float(word["top"]),
                    float(word["x1"]),
                    float(word["bottom"]),
                ),
            )
            for word in words
        ]
        self._token_cache[page_index] = _dedupe_tokens(tokens)
        return self._token_cache[page_index]

    def extract_lines(self, page_index: int) -> list[TextLine]:
        if page_index in self._line_cache:
            return self._line_cache[page_index]
        if self._plumber is None:
            raise RuntimeError("PDF is not open")
        page = self._plumber.pages[page_index]
        lines = []
        for line in page.extract_text_lines():
            text = line.get("text", "")
            if not text.strip():
                continue
            lines.append(
                TextLine(
                    text=text,
                    box=Box(
                        float(line["x0"]),
                        float(line["top"]),
                        float(line["x1"]),
                        float(line["bottom"]),
                    ),
                    chars=line.get("chars", []),
                )
            )
        self._line_cache[page_index] = lines
        return lines

    def render(
        self,
        page_index: int,
        config: CompareConfig,
        *,
        dpi: int | None = None,
        draw_annots: bool = False,
    ) -> RenderResult:
        if self._pdfium is None:
            raise RuntimeError("PDF is not open")
        page = self._pdfium[page_index]
        page_width, page_height = page.get_size()
        requested_dpi = dpi or config.dpi
        scale = requested_dpi / 72.0
        estimated_pixels = page_width * scale * page_height * scale
        if estimated_pixels > config.max_render_pixels:
            scale *= (config.max_render_pixels / estimated_pixels) ** 0.5
        bitmap = page.render(
            scale=scale,
            grayscale=True,
            draw_annots=draw_annots,
        )
        pil_image = bitmap.to_pil()
        image = np.asarray(pil_image.convert("L"), dtype=np.uint8)
        scale_x = image.shape[1] / page_width if page_width else scale
        scale_y = image.shape[0] / page_height if page_height else scale
        return RenderResult(
            image=image,
            scale_x=scale_x,
            scale_y=scale_y,
            dpi_x=72.0 * scale_x,
            dpi_y=72.0 * scale_y,
        )

    def render_rgb(
        self,
        page_index: int,
        config: CompareConfig,
        *,
        dpi: int | None = None,
    ) -> np.ndarray:
        if self._pdfium is None:
            raise RuntimeError("PDF is not open")
        page = self._pdfium[page_index]
        page_width, page_height = page.get_size()
        scale = (dpi or config.display_dpi) / 72.0
        estimated_pixels = page_width * scale * page_height * scale
        if estimated_pixels > config.max_render_pixels:
            scale *= (config.max_render_pixels / estimated_pixels) ** 0.5
        bitmap = page.render(scale=scale, draw_annots=False)
        return np.asarray(bitmap.to_pil().convert("RGB"), dtype=np.uint8)

    def render_rgb_region(
        self,
        page_index: int,
        region: Box,
        config: CompareConfig,
        *,
        padding_pt: float = 0.0,
        dpi: int | None = None,
    ) -> np.ndarray:
        """Render a page region without allocating a full-page bitmap."""
        if self._pdfium is None:
            raise RuntimeError("PDF is not open")
        page = self._pdfium[page_index]
        page_width, page_height = page.get_size()
        expanded = region.expand(padding_pt).clamp(page_width, page_height)
        if expanded.width <= 0 or expanded.height <= 0:
            return np.full((1, 1, 3), 255, dtype=np.uint8)

        requested_dpi = dpi or config.display_dpi
        rotation = int(page.get_rotation()) % 360
        if rotation:
            image = self.render_rgb(page_index, config, dpi=requested_dpi)
            scale_x = image.shape[1] / max(page_width, 1.0)
            scale_y = image.shape[0] / max(page_height, 1.0)
            x0 = max(0, int(expanded.x0 * scale_x))
            y0 = max(0, int(expanded.y0 * scale_y))
            x1 = min(image.shape[1], int(np.ceil(expanded.x1 * scale_x)))
            y1 = min(image.shape[0], int(np.ceil(expanded.y1 * scale_y)))
            return image[y0:y1, x0:x1].copy()

        original_cropbox = page.get_cropbox()
        if original_cropbox is None:
            original_cropbox = (0.0, 0.0, page_width, page_height)
        left, bottom, right, top = (float(value) for value in original_cropbox)
        cropbox = (
            left + expanded.x0,
            top - expanded.y1,
            left + expanded.x1,
            top - expanded.y0,
        )
        page.set_cropbox(*cropbox)
        try:
            scale = requested_dpi / 72.0
            estimated_pixels = expanded.width * scale * expanded.height * scale
            if estimated_pixels > config.max_render_pixels:
                scale *= (config.max_render_pixels / estimated_pixels) ** 0.5
            bitmap = page.render(scale=scale, draw_annots=False)
            return np.asarray(bitmap.to_pil().convert("RGB"), dtype=np.uint8)
        finally:
            page.set_cropbox(*original_cropbox)

    def text_in_box(self, page_index: int, box: Box) -> str:
        tokens = [
            token
            for token in self.extract_tokens(page_index)
            if token.box.intersection(box) is not None
        ]
        if not tokens:
            return ""
        tokens.sort(key=lambda token: (round(token.box.y0 / 2.0), token.box.x0))
        rows: list[list[TextToken]] = []
        for token in tokens:
            if not rows:
                rows.append([token])
                continue
            previous = rows[-1][-1]
            tolerance = max(2.0, min(previous.box.height, token.box.height) * 0.75)
            if abs(token.box.center[1] - previous.box.center[1]) <= tolerance:
                rows[-1].append(token)
            else:
                rows.append([token])
        return "\n".join(" ".join(token.text for token in row) for row in rows)

    def ocr_tokens(
        self,
        page_index: int,
        config: CompareConfig,
    ) -> list[TextToken]:
        tesseract = shutil.which("tesseract")
        if not tesseract:
            log_event(
                logger,
                logging.WARNING,
                "OCR requested but tesseract is unavailable",
                page=page_index + 1,
            )
            return []
        render = self.render(
            page_index,
            config,
            dpi=max(config.ocr_dpi, config.dpi),
            draw_annots=False,
        )
        image = Image.fromarray(render.image)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        command = [
            tesseract,
            "stdin",
            "stdout",
            "-l",
            config.ocr_language,
            "--psm",
            str(config.ocr_page_segmentation),
            "tsv",
        ]
        try:
            result = subprocess.run(
                command,
                input=buffer.getvalue(),
                capture_output=True,
                check=False,
                timeout=config.ocr_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log_event(
                logger,
                logging.WARNING,
                "OCR failed",
                page=page_index + 1,
                error=str(exc),
            )
            return []
        if result.returncode != 0:
            log_event(
                logger,
                logging.WARNING,
                "Tesseract returned an error",
                page=page_index + 1,
                stderr=result.stderr.decode("utf-8", "replace")[-500:],
            )
            return []
        rows = csv.DictReader(io.StringIO(result.stdout.decode("utf-8", "replace")), delimiter="\t")
        tokens: list[TextToken] = []
        page_info = self.page_infos(config)[page_index]
        for row in rows:
            try:
                text = (row.get("text") or "").strip()
                confidence = float(row.get("conf") or -1)
                left = float(row.get("left") or 0)
                top = float(row.get("top") or 0)
                width = float(row.get("width") or 0)
                height = float(row.get("height") or 0)
            except ValueError:
                continue
            if (
                not text
                or confidence < config.ocr_min_confidence
                or width <= 0
                or height <= 0
            ):
                continue
            tokens.append(
                TextToken(
                    text=text,
                    box=Box(
                        left / render.scale_x,
                        top / render.scale_y,
                        (left + width) / render.scale_x,
                        (top + height) / render.scale_y,
                    ).clamp(page_info.width, page_info.height),
                    source="ocr",
                    confidence=confidence / 100.0,
                )
            )
        return _dedupe_tokens(tokens)

    def ensure_tokens(
        self,
        page_index: int,
        config: CompareConfig,
    ) -> list[TextToken]:
        tokens = self.extract_tokens(page_index)
        info = self.page_infos(config)[page_index]
        should_ocr = config.ocr_mode == "always" or (
            config.ocr_mode == "auto"
            and (not info.has_text_layer or len(tokens) < 4)
        )
        if should_ocr:
            ocr = self.ocr_tokens(page_index, config)
            if ocr:
                tokens = _dedupe_tokens([*tokens, *ocr])
                self._token_cache[page_index] = tokens
        return tokens


def thumbnail_ink(render: RenderResult, size: int = 48) -> np.ndarray:
    image = render.image
    height, width = image.shape
    crop_x = int(width * 0.045)
    crop_y = int(height * 0.06)
    cropped = image[crop_y : height - crop_y, crop_x : width - crop_x]
    if cropped.size == 0:
        cropped = image
    thumbnail = cv2.resize(cropped, (size, size), interpolation=cv2.INTER_AREA)
    ink = (255.0 - thumbnail.astype(np.float32)) / 255.0
    return ink
