from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median

from .config import CompareConfig
from .models import PagePair


@dataclass(frozen=True)
class DocumentCompatibility:
    status: str
    matched_pages: int
    required_matches: int
    coverage: float
    median_similarity: float
    minimum_similarity: float
    strong_match_ratio: float
    review_pair_ratio: float
    page_count_ratio: float
    reasons: tuple[str, ...]

    @property
    def rejected(self) -> bool:
        return self.status == "reject"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "matched_pages": self.matched_pages,
            "required_matches": self.required_matches,
            "coverage": self.coverage,
            "median_similarity": self.median_similarity,
            "minimum_similarity": self.minimum_similarity,
            "strong_match_ratio": self.strong_match_ratio,
            "review_pair_ratio": self.review_pair_ratio,
            "page_count_ratio": self.page_count_ratio,
            "reasons": list(self.reasons),
        }


class DocumentMismatchError(ValueError):
    def __init__(self, compatibility: DocumentCompatibility):
        self.compatibility = compatibility
        super().__init__(mismatch_message(compatibility))


def mismatch_message(compatibility: DocumentCompatibility) -> str:
    if compatibility.matched_pages == 0:
        return (
            "两份 PDF 未找到可靠对应的页面，疑似不是同一套原理图，"
            "已停止比对。请确认旧版和新版文件没有传反或传错项目。"
        )
    detail = (
        f"可靠配对 {compatibility.matched_pages} 页，"
        f"匹配覆盖率 {compatibility.coverage:.0%}，"
        f"页面相似度中位数 {compatibility.median_similarity:.0%}"
    )
    return (
        f"两份文件疑似不是同一套原理图，已停止比对（{detail}）。"
        "请检查是否混入其他项目、页面是否完整，或重新导出 PDF。"
    )


def assess_document_compatibility(
    pairs: list[PagePair],
    old_page_count: int,
    new_page_count: int,
    config: CompareConfig,
) -> DocumentCompatibility:
    minimum_page_count = min(old_page_count, new_page_count)
    required_matches = max(
        1,
        math.ceil(minimum_page_count * config.document_min_match_coverage),
    )
    matched = [
        pair
        for pair in pairs
        if pair.old_page is not None and pair.new_page is not None
    ]
    similarities = [pair.score for pair in matched]
    matched_count = len(matched)
    coverage = matched_count / max(1, minimum_page_count)
    median_similarity = median(similarities) if similarities else 0.0
    minimum_similarity = min(similarities, default=0.0)
    strong_count = sum(
        score >= config.document_strong_match_similarity
        for score in similarities
    )
    strong_match_ratio = strong_count / max(1, matched_count)
    review_count = sum(pair.review for pair in matched)
    review_pair_ratio = review_count / max(1, matched_count)
    page_count_ratio = minimum_page_count / max(old_page_count, new_page_count)

    reasons: list[str] = []
    if matched_count == 0:
        reasons.append("没有任何页面达到可靠匹配阈值")
    elif matched_count < required_matches:
        reasons.append(
            f"可靠匹配页面仅 {matched_count} 页，至少需要 {required_matches} 页"
        )
    if coverage < config.document_min_match_coverage:
        reasons.append("可靠匹配页面覆盖率不足")
    if matched_count and median_similarity < config.document_min_median_similarity:
        reasons.append("匹配页面相似度中位数过低")
    if (
        matched_count
        and review_pair_ratio >= 0.7
        and strong_match_ratio < 0.3
    ):
        reasons.append("大多数匹配页存在低质量或多候选配对")

    rejected = (
        matched_count == 0
        or coverage < config.document_min_match_coverage
        or median_similarity < config.document_min_median_similarity
    )
    review = not rejected and (
        coverage < config.document_review_match_coverage
        or median_similarity < config.document_review_median_similarity
        or review_pair_ratio > 0.35
        or strong_match_ratio < 0.5
    )
    if not rejected and review:
        reasons.append("页面身份证据偏弱，结果需要人工复核")
    status = "reject" if rejected else ("review" if review else "pass")
    return DocumentCompatibility(
        status=status,
        matched_pages=matched_count,
        required_matches=required_matches,
        coverage=round(coverage, 6),
        median_similarity=round(median_similarity, 6),
        minimum_similarity=round(minimum_similarity, 6),
        strong_match_ratio=round(strong_match_ratio, 6),
        review_pair_ratio=round(review_pair_ratio, 6),
        page_count_ratio=round(page_count_ratio, 6),
        reasons=tuple(reasons),
    )
