from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CompareConfig:
    """Configuration for one deterministic comparison run."""

    dpi: int = 180
    display_dpi: int = 110
    max_render_pixels: int = 45_000_000
    max_pages: int = 300
    min_page_similarity: float = 0.46
    page_review_similarity: float = 0.72
    text_min_chars: int = 24
    visual_threshold: int = 20
    antialias_radius: int = 1
    min_component_pixels: int = 8
    thin_line_min_length_px: int = 18
    region_dilate_px: int = 7
    region_merge_gap_px: int = 28
    evidence_radius_pt: float = 48.0
    semantic_merge_gap_pt: float = 80.0
    alignment_mode: str = "auto"
    alignment_max_rotation_deg: float = 3.0
    alignment_max_scale_delta: float = 0.025
    alignment_max_shear: float = 0.025
    alignment_max_translation_fraction: float = 0.04
    alignment_min_improvement: float = 0.002
    ocr_mode: str = "auto"
    ocr_language: str = "eng"
    ocr_page_segmentation: int = 11
    ocr_dpi: int = 300
    ocr_timeout_seconds: int = 180
    ocr_min_confidence: float = 35.0
    max_text_evidence: int = 12_000
    crop_padding_pt: float = 24.0
    report_asset_quality: int = 88
    report_detail_dpi: int = 320
    report_evidence_dpi: int = 900
    report_detail_quality: int = 91
    relocation_min_refs: int = 2
    relocation_padding_pt: float = 56.0
    relocation_translation_tolerance_pt: float = 24.0
    relocation_min_displacement_pt: float = 36.0
    document_min_match_coverage: float = 0.5
    document_review_match_coverage: float = 0.72
    document_min_median_similarity: float = 0.55
    document_review_median_similarity: float = 0.78
    document_strong_match_similarity: float = 0.75

    def to_dict(self) -> dict:
        return asdict(self)
