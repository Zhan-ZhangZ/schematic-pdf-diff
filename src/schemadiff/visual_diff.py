from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from .config import CompareConfig
from .models import Box, VisualRegion
from .registration import AlignmentResult, map_box_affine

logger = logging.getLogger(__name__)


@dataclass
class VisualAnalysis:
    regions: list[VisualRegion]
    raw_mask: np.ndarray
    removed_mask: np.ndarray
    added_mask: np.ndarray
    changed_pixels: int
    changed_fraction: float


def _clean_mask(
    mask: np.ndarray,
    config: CompareConfig,
) -> tuple[np.ndarray, np.ndarray]:
    retained = np.zeros_like(mask)
    contours, _ = cv2.findContours(
        mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        pixels = int(cv2.countNonZero(mask[y : y + height, x : x + width]))
        long_feature = max(width, height) >= config.thin_line_min_length_px
        if pixels >= config.min_component_pixels or long_feature:
            cv2.drawContours(retained, [contour], -1, 255, thickness=cv2.FILLED)
    retained = cv2.bitwise_and(mask, retained)
    if config.region_dilate_px > 1:
        group_mask = cv2.dilate(
            retained,
            np.ones(
                (config.region_dilate_px, config.region_dilate_px),
                dtype=np.uint8,
            ),
            iterations=1,
        )
    else:
        group_mask = retained.copy()
    return retained, group_mask


def _map_region_box(
    box_pixels: tuple[int, int, int, int],
    alignment: AlignmentResult,
    old_render_scale: tuple[float, float],
    new_render_scale: tuple[float, float],
) -> tuple[Box, Box]:
    x, y, width, height = box_pixels
    aligned_box = Box(
        float(x),
        float(y),
        float(x + width),
        float(y + height),
    )
    old_box = Box(
        aligned_box.x0 / old_render_scale[0],
        aligned_box.y0 / old_render_scale[1],
        aligned_box.x1 / old_render_scale[0],
        aligned_box.y1 / old_render_scale[1],
    )
    try:
        reference_to_source = cv2.invertAffineTransform(
            alignment.source_to_reference
        )
        source_box = map_box_affine(aligned_box, reference_to_source)
    except cv2.error:
        source_box = aligned_box
    new_box = Box(
        source_box.x0 / new_render_scale[0],
        source_box.y0 / new_render_scale[1],
        source_box.x1 / new_render_scale[0],
        source_box.y1 / new_render_scale[1],
    )
    return old_box, new_box


def compare_rendered_pages(
    old: np.ndarray,
    alignment: AlignmentResult,
    *,
    page_index: int,
    old_page: int,
    new_page: int,
    old_render_scale: tuple[float, float],
    new_render_scale: tuple[float, float],
    config: CompareConfig,
) -> VisualAnalysis:
    new = alignment.aligned_new
    height = max(old.shape[0], new.shape[0])
    width = max(old.shape[1], new.shape[1])
    if old.shape != (height, width):
        padded = np.full((height, width), 255, dtype=np.uint8)
        padded[: old.shape[0], : old.shape[1]] = old
        old = padded
    if new.shape != (height, width):
        padded = np.full((height, width), 255, dtype=np.uint8)
        padded[: new.shape[0], : new.shape[1]] = new
        new = padded

    raw = cv2.absdiff(old, new)
    metric_mask = (raw >= config.visual_threshold).astype(np.uint8) * 255
    retained, group_mask = _clean_mask(metric_mask, config)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        group_mask,
        connectivity=8,
    )
    regions: list[VisualRegion] = []
    old_ink = (255 - old).astype(np.int16)
    new_ink = (255 - new).astype(np.int16)
    for label in range(1, count):
        x, y, width, height, grouped_pixels = stats[label]
        if grouped_pixels <= 0:
            continue
        component = labels[y : y + height, x : x + width] == label
        changed = (retained[y : y + height, x : x + width] > 0) & component
        pixel_count = int(changed.sum())
        if pixel_count < config.min_component_pixels and max(width, height) < config.thin_line_min_length_px:
            continue
        removed = int(
            np.count_nonzero(
                changed & (old_ink[y : y + height, x : x + width] > new_ink[y : y + height, x : x + width])
            )
        )
        added = int(
            np.count_nonzero(
                changed & (new_ink[y : y + height, x : x + width] > old_ink[y : y + height, x : x + width])
            )
        )
        old_box, new_box = _map_region_box(
            (int(x), int(y), int(width), int(height)),
            alignment,
            old_render_scale,
            new_render_scale,
        )
        density = pixel_count / max(1, int(width) * int(height))
        confidence = min(0.99, 0.55 + min(0.3, pixel_count / 120.0) + min(0.12, density))
        regions.append(
            VisualRegion(
                page_index=page_index,
                old_page=old_page,
                new_page=new_page,
                old_box=old_box,
                new_box=new_box,
                pixel_count=pixel_count,
                removed_pixels=removed,
                added_pixels=added,
                confidence=confidence,
                reason="Rasterized stroke difference after conservative alignment",
            )
        )
    regions.sort(key=lambda region: (region.box.y0, region.box.x0))
    changed_pixels = int(cv2.countNonZero(retained))
    return VisualAnalysis(
        regions=regions,
        raw_mask=metric_mask,
        removed_mask=retained.copy(),
        added_mask=retained.copy(),
        changed_pixels=changed_pixels,
        changed_fraction=changed_pixels / max(1, retained.size),
    )
