from __future__ import annotations

from schemadiff.compatibility import (
    DocumentMismatchError,
    assess_document_compatibility,
)
from schemadiff.config import CompareConfig
from schemadiff.models import PagePair


def test_same_document_with_small_revision_passes() -> None:
    pairs = [
        PagePair(0, 0, 0.98),
        PagePair(1, 1, 0.91),
        PagePair(2, 2, 0.89),
    ]

    result = assess_document_compatibility(pairs, 3, 3, CompareConfig())

    assert result.status == "pass"
    assert result.matched_pages == 3
    assert result.median_similarity > 0.88


def test_unrelated_documents_are_rejected() -> None:
    pairs = [
        PagePair(0, None, 0.0),
        PagePair(1, None, 0.0),
        PagePair(None, 0, 0.0),
    ]

    result = assess_document_compatibility(pairs, 2, 1, CompareConfig())

    assert result.status == "reject"
    assert result.rejected
    assert result.matched_pages == 0


def test_partial_page_match_is_marked_for_review() -> None:
    pairs = [
        PagePair(0, 0, 0.88),
        PagePair(1, 1, 0.79),
        PagePair(2, None, 0.0),
        PagePair(3, None, 0.0),
    ]

    result = assess_document_compatibility(pairs, 4, 4, CompareConfig())

    assert result.status == "review"
    assert result.matched_pages == 2
    assert "人工复核" in " ".join(result.reasons)


def test_mismatch_error_explains_that_comparison_stopped() -> None:
    result = assess_document_compatibility(
        [PagePair(0, None, 0.0)],
        1,
        1,
        CompareConfig(),
    )

    error = DocumentMismatchError(result)

    assert "疑似不是同一套原理图" in str(error)
    assert "已停止比对" in str(error)
