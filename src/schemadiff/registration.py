from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import cv2
import numpy as np

from .config import CompareConfig
from .models import Box

logger = logging.getLogger(__name__)


@dataclass
class AlignmentResult:
    aligned_new: np.ndarray
    source_to_reference: np.ndarray
    method: str
    accepted: bool
    reason: str
    score: float | None
    baseline_disagreement: int
    aligned_disagreement: int

    def to_dict(self) -> dict:
        matrix = self.source_to_reference
        return {
            "method": self.method,
            "accepted": self.accepted,
            "reason": self.reason,
            "score": self.score,
            "baseline_disagreement": self.baseline_disagreement,
            "aligned_disagreement": self.aligned_disagreement,
            "source_to_reference": [
                [float(value) for value in row] for row in matrix.tolist()
            ],
        }


def pad_to_common_canvas(
    old: np.ndarray,
    new: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    height = max(old.shape[0], new.shape[0])
    width = max(old.shape[1], new.shape[1])

    def pad(image: np.ndarray) -> np.ndarray:
        result = np.full((height, width), 255, dtype=np.uint8)
        result[: image.shape[0], : image.shape[1]] = image
        return result

    return pad(old), pad(new)


def _disagreement(left: np.ndarray, right: np.ndarray, threshold: int) -> int:
    return int(cv2.countNonZero(cv2.absdiff(left, right) >= threshold))


def _foreground_mask(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    foreground = ((left < 248) | (right < 248)).astype(np.uint8) * 255
    return cv2.dilate(foreground, np.ones((5, 5), np.uint8), iterations=1)


def _transform_metrics(
    matrix: np.ndarray,
    canvas_width: int,
    canvas_height: int,
    config: CompareConfig,
) -> tuple[bool, str]:
    a, b, tx = (float(value) for value in matrix[0])
    c, d, ty = (float(value) for value in matrix[1])
    scale_x = math.hypot(a, c)
    scale_y = math.hypot(b, d)
    rotation = abs(math.degrees(math.atan2(c, a)))
    shear = abs(a * b + c * d) / max(scale_x * scale_y, 1e-12)
    max_translation = (
        max(canvas_width, canvas_height)
        * config.alignment_max_translation_fraction
    )
    issues = []
    if abs(scale_x - 1.0) > config.alignment_max_scale_delta:
        issues.append(f"x scale {scale_x:.6f}")
    if abs(scale_y - 1.0) > config.alignment_max_scale_delta:
        issues.append(f"y scale {scale_y:.6f}")
    if rotation > config.alignment_max_rotation_deg:
        issues.append(f"rotation {rotation:.4f} deg")
    if shear > config.alignment_max_shear:
        issues.append(f"shear {shear:.6f}")
    if abs(tx) > max_translation or abs(ty) > max_translation:
        issues.append(f"translation ({tx:.2f}, {ty:.2f})")
    return not issues, ", ".join(issues)


def _phase_translation(
    old: np.ndarray,
    new: np.ndarray,
) -> tuple[np.ndarray, float, str]:
    old_ink = (255.0 - old.astype(np.float32)) / 255.0
    new_ink = (255.0 - new.astype(np.float32)) / 255.0
    window = cv2.createHanningWindow(
        (old.shape[1], old.shape[0]),
        cv2.CV_32F,
    )
    shift, response = cv2.phaseCorrelate(old_ink, new_ink, window)
    if not all(math.isfinite(float(value)) for value in shift):
        raise ValueError("phase correlation returned non-finite values")
    matrix = np.array(
        [[1.0, 0.0, float(shift[0])], [0.0, 1.0, float(shift[1])]],
        dtype=np.float32,
    )
    return matrix, float(response), "phase correlation"


def _try_affine(
    old: np.ndarray,
    new: np.ndarray,
    initial: np.ndarray,
) -> tuple[np.ndarray, float, str]:
    template = cv2.GaussianBlur(old, (3, 3), 0).astype(np.float32) / 255.0
    moving = cv2.GaussianBlur(new, (3, 3), 0).astype(np.float32) / 255.0
    mask = _foreground_mask(old, new)
    if cv2.countNonZero(mask) < 100:
        raise ValueError("insufficient foreground for affine registration")
    criteria = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
        120,
        1e-6,
    )
    score, matrix = cv2.findTransformECC(
        template,
        moving,
        initial.astype(np.float32).copy(),
        cv2.MOTION_AFFINE,
        criteria,
        inputMask=mask,
        gaussFiltSize=3,
    )
    return matrix.astype(np.float32), float(score), "ECC affine"


def _warp_for_inverse_matrix(image: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return cv2.warpAffine(
        image,
        matrix,
        (image.shape[1], image.shape[0]),
        flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )


def align_page_images(
    old: np.ndarray,
    new: np.ndarray,
    config: CompareConfig,
) -> AlignmentResult:
    """Align the revised raster to the original raster conservatively."""
    old, new = pad_to_common_canvas(old, new)
    baseline = _disagreement(old, new, config.visual_threshold)
    identity = np.eye(2, 3, dtype=np.float32)
    if config.alignment_mode == "off":
        return AlignmentResult(
            new,
            identity,
            "disabled",
            False,
            "alignment disabled",
            None,
            baseline,
            baseline,
        )
    foreground = cv2.countNonZero(_foreground_mask(old, new))
    if foreground < 100:
        return AlignmentResult(
            new,
            identity,
            "none",
            False,
            "insufficient foreground",
            None,
            baseline,
            baseline,
        )

    candidates: list[tuple[str, np.ndarray, float]] = []
    try:
        translation, response, translation_method = _phase_translation(old, new)
        candidates.append((translation_method, translation, response))
    except (cv2.error, ValueError) as exc:
        logger.debug("Translation registration failed: %s", exc)
        translation = identity.copy()
        response = 0.0

    if config.alignment_mode == "affine":
        try:
            affine, response, method = _try_affine(old, new, translation)
            candidates.append((method, affine, response))
        except (cv2.error, ValueError) as exc:
            logger.debug("Affine registration failed: %s", exc)

    evaluated: list[tuple[int, str, np.ndarray, float, np.ndarray, str]] = []
    for method, matrix, score in candidates:
        safe, reason = _transform_metrics(
            matrix,
            old.shape[1],
            old.shape[0],
            config,
        )
        if not safe:
            logger.debug("Rejected registration %s: %s", method, reason)
            continue
        aligned = _warp_for_inverse_matrix(new, matrix)
        disagreement = _disagreement(aligned, old, config.visual_threshold)
        evaluated.append(
            (disagreement, method, matrix, score, aligned, reason)
        )

    if not evaluated:
        return AlignmentResult(
            new,
            identity,
            "none",
            False,
            "no safe transform",
            None,
            baseline,
            baseline,
        )

    disagreement, method, matrix, score, aligned, _ = min(
        evaluated,
        key=lambda item: item[0],
    )
    required = max(2, int(round(baseline * (1.0 - config.alignment_min_improvement))))
    if baseline == 0 or disagreement > required:
        return AlignmentResult(
            new,
            identity,
            method,
            False,
            f"rejected: disagreement {baseline} -> {disagreement}",
            score,
            baseline,
            disagreement,
        )

    # Regression matrices are destination-to-source because warpAffine uses
    # WARP_INVERSE_MAP. Invert it for mapping source boxes into the canvas.
    source_to_reference = cv2.invertAffineTransform(matrix)
    return AlignmentResult(
        aligned,
        source_to_reference.astype(np.float32),
        method,
        True,
        f"applied: disagreement {baseline} -> {disagreement}",
        score,
        baseline,
        disagreement,
    )


def map_box_affine(box: Box, matrix: np.ndarray) -> Box:
    points = np.array(
        [
            [box.x0, box.y0, 1.0],
            [box.x1, box.y0, 1.0],
            [box.x1, box.y1, 1.0],
            [box.x0, box.y1, 1.0],
        ],
        dtype=np.float64,
    )
    mapped = points @ matrix.T
    return Box(
        float(mapped[:, 0].min()),
        float(mapped[:, 1].min()),
        float(mapped[:, 0].max()),
        float(mapped[:, 1].max()),
    )
