from __future__ import annotations

import importlib.metadata
import json
import logging
import platform
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .compatibility import (
    DocumentMismatchError,
    assess_document_compatibility,
)
from .config import CompareConfig
from .fusion import fuse_page_changes
from .logging_setup import log_event
from .models import Box, Change, PagePair, PageResult
from .offline_guard import offline_network_guard
from .page_match import (
    PageFingerprint,
    align_pages,
    matching_tokens,
    similarity_matrix,
)
from .pdf_document import PdfSource, RenderResult, sha256_file, thumbnail_ink
from .registration import align_page_images
from .relocation import (
    detect_relocations,
    relocation_changes,
    suppress_relocated_content,
)
from .report import build_report
from .text_diff import PairTextContext, analyze_text_pair
from .visual_diff import compare_rendered_pages

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str, int, int], None]


def _package_versions() -> dict[str, str]:
    versions = {}
    for name in (
        "numpy",
        "opencv-python-headless",
        "pdfplumber",
        "Pillow",
        "pypdf",
        "pypdfium2",
    ):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "unavailable"
    return versions


def _progress(callback: ProgressCallback | None, phase: str, current: int, total: int) -> None:
    if callback:
        callback(phase, current, total)


def _fingerprint(
    source: PdfSource,
    page_index: int,
    config: CompareConfig,
) -> PageFingerprint:
    tokens = source.ensure_tokens(page_index, config)
    info = source.page_infos(config)[page_index]
    render = source.render(page_index, config, dpi=72)
    return PageFingerprint(
        info=info,
        tokens=set(matching_tokens(tokens)),
        token_counts=matching_tokens(tokens),
        visual=thumbnail_ink(render),
    )


def _unmatched_change(
    pair: PagePair,
    page_index: int,
    old_info,
    new_info,
) -> Change:
    if pair.old_page is not None:
        box = Box(0.0, 0.0, old_info.width, old_info.height)
        old_box = box
        new_box = Box(0.0, 0.0, 1.0, 1.0)
        change_type = "page_removed"
        title = "Page removed"
    else:
        box = Box(0.0, 0.0, new_info.width, new_info.height)
        old_box = Box(0.0, 0.0, 1.0, 1.0)
        new_box = box
        change_type = "page_added"
        title = "Page added"
    return Change(
        change_id=f"P{page_index + 1:02d}-C001",
        page_index=page_index,
        old_page=pair.old_page,
        new_page=pair.new_page,
        change_type=change_type,
        title=title,
        before="",
        after="",
        old_box=old_box,
        new_box=new_box,
        confidence=1.0,
        reason=pair.reason,
        evidence=["page-pairing"],
    )


def compare_documents(
    old_path: str | Path,
    new_path: str | Path,
    output_dir: str | Path,
    *,
    config: CompareConfig | None = None,
    progress: ProgressCallback | None = None,
) -> dict:
    config = config or CompareConfig()
    old_path = Path(old_path).resolve()
    new_path = Path(new_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    start_wall = datetime.now(timezone.utc)
    start_time = time.perf_counter()
    phase_times: dict[str, float] = {}
    manifest = {
        "schema_version": "1",
        "status": "running",
        "started_at_utc": start_wall.isoformat(),
        "completed_at_utc": None,
        "offline_runtime": True,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": _package_versions(),
        "tesseract": shutil.which("tesseract"),
        "config": config.to_dict(),
        "inputs": {
            "old": {
                "path": str(old_path),
                "size_bytes": old_path.stat().st_size,
                "sha256": sha256_file(old_path),
            },
            "new": {
                "path": str(new_path),
                "size_bytes": new_path.stat().st_size,
                "sha256": sha256_file(new_path),
            },
        },
        "phases": phase_times,
        "outputs": {},
    }
    (output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log_event(
        logger,
        logging.INFO,
        "Comparison started",
        old=str(old_path),
        new=str(new_path),
        output=str(output_dir),
    )

    with offline_network_guard(), PdfSource(old_path) as old_source, PdfSource(
        new_path
    ) as new_source:
        old_infos = old_source.page_infos(config)
        new_infos = new_source.page_infos(config)
        manifest["inputs"]["old"].update(
            pages=len(old_infos),
            metadata=old_source.metadata,
            page_info=[info.to_dict() for info in old_infos],
        )
        manifest["inputs"]["new"].update(
            pages=len(new_infos),
            metadata=new_source.metadata,
            page_info=[info.to_dict() for info in new_infos],
        )

        phase_start = time.perf_counter()
        old_features = []
        new_features = []
        for index in range(len(old_infos)):
            old_features.append(_fingerprint(old_source, index, config))
            _progress(progress, "indexing", index + 1, len(old_infos) + len(new_infos))
        for index in range(len(new_infos)):
            new_features.append(_fingerprint(new_source, index, config))
            _progress(
                progress,
                "indexing",
                len(old_infos) + index + 1,
                len(old_infos) + len(new_infos),
            )
        scores = similarity_matrix(old_features, new_features)
        pairs = align_pages(scores, len(old_infos), len(new_infos), config)
        phase_times["page_matching_seconds"] = time.perf_counter() - phase_start
        _progress(progress, "matching", len(pairs), len(pairs))
        compatibility = assess_document_compatibility(
            pairs,
            len(old_infos),
            len(new_infos),
            config,
        )
        manifest["document_compatibility"] = compatibility.to_dict()
        if compatibility.rejected:
            manifest["status"] = "rejected"
            manifest["network_blocked"] = True
            manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
            manifest["total_seconds"] = time.perf_counter() - start_time
            manifest["phases"] = phase_times
            (output_dir / "run_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log_event(
                logger,
                logging.WARNING,
                "Document compatibility rejected",
                compatibility=compatibility.to_dict(),
            )
            raise DocumentMismatchError(compatibility)

        page_results: list[PageResult] = []
        old_tokens_by_page: dict[int, list] = {}
        new_tokens_by_page: dict[int, list] = {}
        for page_index, pair in enumerate(pairs, start=1):
            result = PageResult(pair=pair)
            if pair.old_page is None or pair.new_page is None:
                result.changes = [
                    _unmatched_change(
                        pair,
                        len(page_results),
                        old_infos[pair.old_page] if pair.old_page is not None else old_infos[0],
                        new_infos[pair.new_page] if pair.new_page is not None else new_infos[0],
                    )
                ]
                page_results.append(result)
                _progress(progress, "comparing", page_index, len(pairs))
                continue

            old_tokens = old_source.ensure_tokens(pair.old_page, config)
            new_tokens = new_source.ensure_tokens(pair.new_page, config)
            old_tokens_by_page[pair.old_page] = old_tokens
            new_tokens_by_page[pair.new_page] = new_tokens
            old_lines = old_source.extract_lines(pair.old_page)
            new_lines = new_source.extract_lines(pair.new_page)
            result.text_deltas = analyze_text_pair(
                PairTextContext(
                    page_index=len(page_results),
                    old_page=pair.old_page,
                    new_page=pair.new_page,
                    old_tokens=old_tokens,
                    new_tokens=new_tokens,
                    old_lines=old_lines,
                    new_lines=new_lines,
                )
            )

            old_render: RenderResult = old_source.render(
                pair.old_page,
                config,
                dpi=config.dpi,
            )
            new_render: RenderResult = new_source.render(
                pair.new_page,
                config,
                dpi=config.dpi,
            )
            alignment = align_page_images(
                old_render.image,
                new_render.image,
                config,
            )
            visual = compare_rendered_pages(
                old_render.image,
                alignment,
                page_index=len(page_results),
                old_page=pair.old_page,
                new_page=pair.new_page,
                old_render_scale=(old_render.scale_x, old_render.scale_y),
                new_render_scale=(new_render.scale_x, new_render.scale_y),
                config=config,
            )
            result.visual_regions = visual.regions
            result.alignment = alignment.to_dict()
            result.metrics = {
                "visual_changed_pixels": visual.changed_pixels,
                "visual_changed_fraction": visual.changed_fraction,
                "old_dpi": [old_render.dpi_x, old_render.dpi_y],
                "new_dpi": [new_render.dpi_x, new_render.dpi_y],
            }
            page_results.append(result)
            _progress(progress, "comparing", page_index, len(pairs))

        relocations = detect_relocations(
            page_results,
            old_tokens_by_page,
            new_tokens_by_page,
            old_infos,
            new_infos,
            config,
        )
        suppress_relocated_content(page_results, relocations)
        for result_index, result in enumerate(page_results):
            if result.pair.old_page is None or result.pair.new_page is None:
                continue
            result.changes = fuse_page_changes(
                result_index,
                result.pair.old_page,
                result.pair.new_page,
                result.text_deltas,
                result.visual_regions,
                old_tokens_by_page[result.pair.old_page],
                new_tokens_by_page[result.pair.new_page],
                config,
            )
        for change in relocation_changes(page_results, relocations):
            page_results[change.page_index].changes.append(change)

        matched_pages = sum(
            1
            for pair in pairs
            if pair.old_page is not None and pair.new_page is not None
        )
        changed_page_indexes = {
            index
            for index, result in enumerate(page_results)
            if result.changed
        }
        for relocation in relocations:
            changed_page_indexes.add(relocation.old_page)
            changed_page_indexes.add(relocation.new_page)
        changed_pages = len(changed_page_indexes)
        total_changes = sum(len(result.changes) for result in page_results)
        summary = {
            "old_pages": len(old_infos),
            "new_pages": len(new_infos),
            "matched_pages": matched_pages,
            "changed_pages": changed_pages,
            "change_count": total_changes,
            "visual_regions": sum(
                len(result.visual_regions) for result in page_results
            ),
            "text_deltas": sum(len(result.text_deltas) for result in page_results),
            "review_pairs": sum(1 for pair in pairs if pair.review),
            "relocations": len(relocations),
            "document_compatibility": compatibility.to_dict(),
        }

        report_start = time.perf_counter()
        outputs = build_report(
            old_source,
            new_source,
            page_results,
            output_dir,
            config,
            summary=summary,
        )
        phase_times["report_seconds"] = time.perf_counter() - report_start
        _progress(progress, "reporting", 1, 1)

    manifest["status"] = "complete"
    manifest["network_blocked"] = True
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["total_seconds"] = time.perf_counter() - start_time
    manifest["summary"] = summary
    manifest["outputs"] = outputs
    manifest["phases"] = phase_times
    (output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log_event(
        logger,
        logging.INFO,
        "Comparison completed",
        summary=summary,
        outputs=outputs,
        seconds=manifest["total_seconds"],
    )
    return {
        "summary": summary,
        "outputs": outputs,
        "manifest": str(output_dir / "run_manifest.json"),
        "pages": [result.to_dict() for result in page_results],
    }
