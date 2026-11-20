from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from schemadiff.config import CompareConfig
from schemadiff.pdf_document import PdfSource
from schemadiff.pipeline import compare_documents


def make_vector_pdf(path: Path, changed: bool) -> None:
    doc = canvas.Canvas(str(path), pagesize=(612, 400))
    doc.setLineWidth(1)
    doc.rect(30, 300, 552, 70)
    doc.line(50, 330, 300, 330)
    doc.line(300, 330, 330, 310)
    doc.line(330, 310, 560, 310)
    doc.rect(180, 290, 50, 30)
    doc.setFont("Helvetica", 12)
    doc.drawString(60, 335, "R1")
    doc.drawString(230, 300, "20K" if changed else "10K")
    doc.drawString(60, 250, "USB-DM1")
    doc.line(120, 252, 520, 252)
    doc.drawString(60, 210, "Schematic offline scan fixture")
    doc.save()


def rasterize(path: Path) -> np.ndarray:
    pdf = pdfium.PdfDocument(path)
    image = pdf[0].render(scale=2, grayscale=False).to_pil().convert("RGB")
    pdf.close()
    return np.asarray(image)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract unavailable")
def test_scanned_pdf_with_registration_and_ocr(tmp_path: Path) -> None:
    vector_old = tmp_path / "old_vector.pdf"
    vector_new = tmp_path / "new_vector.pdf"
    make_vector_pdf(vector_old, changed=False)
    make_vector_pdf(vector_new, changed=True)
    old_image = rasterize(vector_old)
    new_image = rasterize(vector_new)
    height, width = new_image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), 0.55, 1.003)
    matrix[0, 2] += 3.0
    matrix[1, 2] += 2.0
    new_image = cv2.warpAffine(
        new_image,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    old_pdf = tmp_path / "old_scan.pdf"
    new_pdf = tmp_path / "new_scan.pdf"
    Image.fromarray(old_image).save(old_pdf, "PDF", resolution=144.0)
    Image.fromarray(new_image).save(new_pdf, "PDF", resolution=144.0)

    config = CompareConfig(
        dpi=130,
        display_dpi=90,
        ocr_mode="auto",
        ocr_language="eng",
        ocr_dpi=200,
        alignment_mode="affine",
    )
    with PdfSource(old_pdf) as source:
        tokens = source.ensure_tokens(0, config)
        assert tokens
        assert any(token.source == "ocr" for token in tokens)
    result = compare_documents(
        old_pdf,
        new_pdf,
        tmp_path / "scan_result",
        config=config,
    )
    assert result["summary"]["changed_pages"] >= 1
    assert result["summary"]["visual_regions"] >= 1
    assert Path(result["outputs"]["report_html"]).exists()
