from __future__ import annotations

import base64
import json
import logging
import mimetypes
from importlib.resources import files
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import CompareConfig
from .models import Change, PageResult
from .pdf_document import PdfSource

logger = logging.getLogger(__name__)


def _data_uri(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _save_report_image(image: np.ndarray, path: Path, quality: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB) if image.ndim == 2 else image
    pil = Image.fromarray(rgb)
    jpeg_path = path.with_suffix(".jpg")
    webp_path = path.with_suffix(".webp")
    pil.save(
        jpeg_path,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
        subsampling=0,
    )
    pil.save(
        webp_path,
        format="WEBP",
        lossless=True,
        method=6,
    )
    if webp_path.stat().st_size <= jpeg_path.stat().st_size:
        jpeg_path.unlink()
        return webp_path
    webp_path.unlink()
    return jpeg_path


def _draw_boxes(
    image: np.ndarray,
    changes: list[Change],
    *,
    side: str,
    page_width: float,
    page_height: float,
) -> np.ndarray:
    output = image.copy()
    scale_x = image.shape[1] / max(page_width, 1.0)
    scale_y = image.shape[0] / max(page_height, 1.0)
    color = (218, 72, 56) if side == "old" else (34, 120, 198)
    for index, change in enumerate(changes, start=1):
        box = change.old_box if side == "old" else change.new_box
        x0 = max(0, min(image.shape[1] - 1, int(round(box.x0 * scale_x))))
        y0 = max(0, min(image.shape[0] - 1, int(round(box.y0 * scale_y))))
        x1 = max(0, min(image.shape[1] - 1, int(round(box.x1 * scale_x))))
        y1 = max(0, min(image.shape[0] - 1, int(round(box.y1 * scale_y))))
        pad = max(4, int(round(min(image.shape[:2]) * 0.004)))
        cv2.rectangle(
            output,
            (max(0, x0 - pad), max(0, y0 - pad)),
            (min(image.shape[1] - 1, x1 + pad), min(image.shape[0] - 1, y1 + pad)),
            color,
            2,
        )
        label = f"{index}"
        font_scale = max(0.35, min(0.65, image.shape[1] / 2400.0))
        cv2.putText(
            output,
            label,
            (max(2, x0 - pad), max(15, y0 - pad - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            color,
            2,
            cv2.LINE_AA,
        )
    return output


def _decorate_crop(
    crop: np.ndarray,
    box,
    region_box,
) -> Image.Image:
    if crop.size == 0:
        return Image.new("RGB", (640, 360), "white")
    crop = crop.copy()
    cv2.rectangle(
        crop,
        (0, 0),
        (max(0, crop.shape[1] - 1), max(0, crop.shape[0] - 1)),
        (205, 210, 213),
        1,
    )
    scale_x = crop.shape[1] / max(region_box.width, 1.0)
    scale_y = crop.shape[0] / max(region_box.height, 1.0)
    pad = max(4, round(min(crop.shape[:2]) * 0.006))
    x0 = max(0, round((box.x0 - region_box.x0) * scale_x) - pad)
    y0 = max(0, round((box.y0 - region_box.y0) * scale_y) - pad)
    x1 = min(
        crop.shape[1] - 1,
        round((box.x1 - region_box.x0) * scale_x) + pad,
    )
    y1 = min(
        crop.shape[0] - 1,
        round((box.y1 - region_box.y0) * scale_y) + pad,
    )
    if x1 > x0 and y1 > y0:
        cv2.rectangle(
            crop,
            (x0, y0),
            (x1, y1),
            (220, 42, 34),
            2,
            cv2.LINE_AA,
        )
    pil = Image.fromarray(crop)
    width, height = pil.size
    if width < 220 or height < 120:
        factor = max(220 / max(width, 1), 120 / max(height, 1), 1.0)
        pil = pil.resize(
            (round(width * factor), round(height * factor)),
            Image.Resampling.LANCZOS,
        )
    return pil


def _evidence_image(
    old_source: PdfSource,
    new_source: PdfSource,
    change: Change,
    old_page_index: int,
    new_page_index: int,
    old_page,
    new_page,
    config: CompareConfig,
) -> Image.Image:
    evidence_dpi = max(config.report_evidence_dpi, config.report_detail_dpi)
    old_region_box = change.old_box.expand(config.crop_padding_pt).clamp(
        old_page.width,
        old_page.height,
    )
    new_region_box = change.new_box.expand(config.crop_padding_pt).clamp(
        new_page.width,
        new_page.height,
    )
    old_region = old_source.render_rgb_region(
        old_page_index,
        change.old_box,
        config,
        padding_pt=config.crop_padding_pt,
        dpi=evidence_dpi,
    )
    new_region = new_source.render_rgb_region(
        new_page_index,
        change.new_box,
        config,
        padding_pt=config.crop_padding_pt,
        dpi=evidence_dpi,
    )
    old_crop = _decorate_crop(
        old_region,
        change.old_box,
        old_region_box,
    )
    new_crop = _decorate_crop(
        new_region,
        change.new_box,
        new_region_box,
    )
    if old_crop.height != new_crop.height:
        target_height = max(old_crop.height, new_crop.height)
        old_crop = ImageOps.pad(
            old_crop,
            (old_crop.width, target_height),
            color="white",
            centering=(0.5, 0.0),
        )
        new_crop = ImageOps.pad(
            new_crop,
            (new_crop.width, target_height),
            color="white",
            centering=(0.5, 0.0),
        )
    max_height = max(old_crop.height, new_crop.height)
    label_height = max(40, min(64, round(max_height * 0.07)))
    gap = 18
    width = old_crop.width + new_crop.width + gap
    canvas = Image.new("RGB", (width, max_height + label_height), "white")
    canvas.paste(old_crop, (0, label_height))
    canvas.paste(new_crop, (old_crop.width + gap, label_height))

    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.load_default(size=max(16, round(label_height * 0.42)))
    except TypeError:
        font = ImageFont.load_default()
    text_y = max(6, (label_height - 18) // 2)
    draw.text((10, text_y), "BEFORE", fill=(160, 32, 24), font=font)
    draw.text(
        (old_crop.width + gap + 10, text_y),
        "AFTER",
        fill=(18, 92, 166),
        font=font,
    )
    return canvas


def _html_document(
    *,
    old_name: str,
    new_name: str,
    report_data: list[dict[str, Any]],
    summary: dict,
) -> str:
    data_json = json.dumps(report_data, ensure_ascii=False).replace("</", "<\\/")
    summary_json = json.dumps(summary, ensure_ascii=False).replace("</", "<\\/")
    old_json = json.dumps(old_name, ensure_ascii=False).replace("</", "<\\/")
    new_json = json.dumps(new_name, ensure_ascii=False).replace("</", "<\\/")
    template = (files("schemadiff.web") / "report.html").read_text(encoding="utf-8")
    return (
        template.replace("__REPORT_DATA__", data_json)
        .replace("__SUMMARY__", summary_json)
        .replace("__OLD_NAME__", old_json)
        .replace("__NEW_NAME__", new_json)
    )


def build_report(
    old_source: PdfSource,
    new_source: PdfSource,
    page_results: list[PageResult],
    output_dir: Path,
    config: CompareConfig,
    *,
    summary: dict,
) -> dict[str, str]:
    assets_dir = output_dir / "assets"
    page_dir = assets_dir / "pages"
    evidence_dir = assets_dir / "evidence"
    page_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    old_infos = old_source.page_infos(config)
    new_infos = new_source.page_infos(config)
    report_data: list[dict[str, Any]] = []
    pdf_pages: list[Image.Image] = []
    flat_index = 0
    detail_dpi = max(config.display_dpi, config.report_detail_dpi)
    sided_page_cache: dict[tuple[str, int, str, bool], str] = {}

    def sided_page_image(
        change: Change,
        side: str,
        *,
        detail: bool,
    ) -> str:
        page_index = change.old_page if side == "old" else change.new_page
        if page_index is None:
            return ""
        cache_key = (side, page_index, change.change_id, detail)
        if cache_key in sided_page_cache:
            return sided_page_cache[cache_key]
        source = old_source if side == "old" else new_source
        info = old_infos[page_index] if side == "old" else new_infos[page_index]
        image = source.render_rgb(
            page_index,
            config,
            dpi=detail_dpi if detail else config.display_dpi,
        )
        marked = _draw_boxes(
            image,
            [change],
            side=side,
            page_width=info.width,
            page_height=info.height,
        )
        suffix = "_detail" if detail else ""
        file_path = _save_report_image(
            marked,
            page_dir / f"p{page_index + 1:03d}_{side}_{change.change_id}{suffix}",
            config.report_detail_quality
            if detail
            else config.report_asset_quality,
        )
        value = _data_uri(file_path)
        sided_page_cache[cache_key] = value
        return value

    for result in page_results:
        pair = result.pair
        changes = result.changes
        local_changes = [
            change
            for change in changes
            if change.old_page == pair.old_page and change.new_page == pair.new_page
        ]
        before_path = ""
        after_path = ""
        before_detail_path = ""
        after_detail_path = ""
        if (
            local_changes
            and pair.old_page is not None
            and pair.new_page is not None
        ):
            old_preview = old_source.render_rgb(
                pair.old_page,
                config,
                dpi=config.display_dpi,
            )
            new_preview = new_source.render_rgb(
                pair.new_page,
                config,
                dpi=config.display_dpi,
            )
            old_detail = old_source.render_rgb(
                pair.old_page,
                config,
                dpi=detail_dpi,
            )
            new_detail = new_source.render_rgb(
                pair.new_page,
                config,
                dpi=detail_dpi,
            )
            old_info = old_infos[pair.old_page]
            new_info = new_infos[pair.new_page]
            old_marked = _draw_boxes(
                old_preview,
                local_changes,
                side="old",
                page_width=old_info.width,
                page_height=old_info.height,
            )
            new_marked = _draw_boxes(
                new_preview,
                local_changes,
                side="new",
                page_width=new_info.width,
                page_height=new_info.height,
            )
            old_detail_marked = _draw_boxes(
                old_detail,
                local_changes,
                side="old",
                page_width=old_info.width,
                page_height=old_info.height,
            )
            new_detail_marked = _draw_boxes(
                new_detail,
                local_changes,
                side="new",
                page_width=new_info.width,
                page_height=new_info.height,
            )
            before_file = page_dir / f"p{pair.old_page + 1:03d}_before"
            after_file = page_dir / f"p{pair.new_page + 1:03d}_after"
            before_detail_file = (
                page_dir / f"p{pair.old_page + 1:03d}_before_detail"
            )
            after_detail_file = (
                page_dir / f"p{pair.new_page + 1:03d}_after_detail"
            )
            before_file = _save_report_image(
                old_marked,
                before_file,
                config.report_asset_quality,
            )
            after_file = _save_report_image(
                new_marked,
                after_file,
                config.report_asset_quality,
            )
            before_detail_file = _save_report_image(
                old_detail_marked,
                before_detail_file,
                config.report_detail_quality,
            )
            after_detail_file = _save_report_image(
                new_detail_marked,
                after_detail_file,
                config.report_detail_quality,
            )
            before_path = _data_uri(before_file)
            after_path = _data_uri(after_file)
            before_detail_path = _data_uri(before_detail_file)
            after_detail_path = _data_uri(after_detail_file)
            left = Image.fromarray(old_marked)
            right = Image.fromarray(new_marked)
            height = min(1500, max(left.height, right.height))
            scale = min(1.0, height / max(left.height, right.height))
            left = left.resize(
                (round(left.width * scale), round(left.height * scale)),
                Image.Resampling.LANCZOS,
            )
            right = right.resize(
                (round(right.width * scale), round(right.height * scale)),
                Image.Resampling.LANCZOS,
            )
            sheet = Image.new(
                "RGB",
                (left.width + right.width + 24, max(left.height, right.height)),
                "white",
            )
            sheet.paste(left, (0, 0))
            sheet.paste(right, (left.width + 24, 0))
            pdf_pages.append(sheet)
        page_changes = []
        for local_index, change in enumerate(changes, start=1):
            flat_index += 1
            evidence_rel = ""
            old_page_image = ""
            new_page_image = ""
            old_page_detail_image = ""
            new_page_detail_image = ""
            if change.old_page is not None:
                old_page_image = (
                    before_path
                    if change.old_page == pair.old_page and before_path
                    else sided_page_image(change, "old", detail=False)
                )
                old_page_detail_image = (
                    before_detail_path
                    if change.old_page == pair.old_page and before_detail_path
                    else sided_page_image(change, "old", detail=True)
                )
            if change.new_page is not None:
                new_page_image = (
                    after_path
                    if change.new_page == pair.new_page and after_path
                    else sided_page_image(change, "new", detail=False)
                )
                new_page_detail_image = (
                    after_detail_path
                    if change.new_page == pair.new_page and after_detail_path
                    else sided_page_image(change, "new", detail=True)
                )
            if (
                change.old_page is not None
                and change.new_page is not None
            ):
                evidence_image = _evidence_image(
                    old_source,
                    new_source,
                    change,
                    change.old_page,
                    change.new_page,
                    old_infos[change.old_page],
                    new_infos[change.new_page],
                    config,
                )
                evidence_file = _save_report_image(
                    np.asarray(evidence_image.convert("RGB")),
                    evidence_dir / change.change_id,
                    config.report_detail_quality,
                )
                evidence_rel = _data_uri(evidence_file)
            payload = {
                "index": flat_index,
                "local_index": local_index,
                "change_id": change.change_id,
                "change_type": change.change_type,
                "title": change.title,
                "before": change.before,
                "after": change.after,
                "confidence": change.confidence,
                "reason": change.reason,
                "refdes": change.refdes,
                "nets": change.nets,
                "evidence": change.evidence,
                "old_box": change.old_box.to_dict(),
                "new_box": change.new_box.to_dict(),
                "old_page": None
                if change.old_page is None
                else change.old_page + 1,
                "new_page": None
                if change.new_page is None
                else change.new_page + 1,
                "old_page_image": old_page_image,
                "new_page_image": new_page_image,
                "old_page_detail_image": old_page_detail_image,
                "new_page_detail_image": new_page_detail_image,
                "evidence_image": evidence_rel,
                "evidence_dpi": max(
                    config.report_evidence_dpi,
                    config.report_detail_dpi,
                ),
            }
            page_changes.append(payload)
        report_data.append(
            {
                "display_page": len(report_data) + 1,
                "old_page": None if pair.old_page is None else pair.old_page + 1,
                "new_page": None if pair.new_page is None else pair.new_page + 1,
                "pair_score": pair.score,
                "pair_review": pair.review,
                "before_page_image": before_path,
                "after_page_image": after_path,
                "before_page_detail_image": before_detail_path,
                "after_page_detail_image": after_detail_path,
                "changes": page_changes,
            }
        )

    report_path = output_dir / "report.html"
    report_path.write_text(
        _html_document(
            old_name=old_source.path.name,
            new_name=new_source.path.name,
            report_data=report_data,
            summary=summary,
        ),
        encoding="utf-8",
    )
    result_json = output_dir / "results.json"
    result_json.write_text(
        json.dumps(
            {
                "summary": summary,
                "pages": [result.to_dict() for result in page_results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_dir / "README.txt").write_text(
        "report.html is fully standalone and can be opened without this folder.\n"
        "results.json contains page and PDF-point evidence.\n"
        "changes.pdf is a raster side-by-side review copy.\n"
        "No network connection is required.\n",
        encoding="utf-8",
    )

    pdf_path = output_dir / "changes.pdf"
    if pdf_pages:
        pdf_pages[0].save(
            pdf_path,
            "PDF",
            resolution=110.0,
            save_all=True,
            append_images=pdf_pages[1:],
        )
    else:
        from PIL import ImageDraw

        summary_page = Image.new("RGB", (1400, 900), "white")
        draw = ImageDraw.Draw(summary_page)
        draw.text((60, 60), "No content changes detected.", fill=(23, 33, 43))
        draw.text(
            (60, 96),
            f"{summary['matched_pages']} pages matched with configured thresholds.",
            fill=(102, 114, 127),
        )
        summary_page.save(pdf_path, "PDF", resolution=110.0)

    logger.info("Wrote offline report, JSON, and evidence assets")
    return {
        "report_html": str(report_path),
        "results_json": str(result_json),
        "changes_pdf": str(pdf_path),
    }
