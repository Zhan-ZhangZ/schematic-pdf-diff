from __future__ import annotations

from schemadiff.config import CompareConfig
from schemadiff.models import (
    Box,
    PageInfo,
    PagePair,
    PageResult,
    TextDelta,
    TextToken,
)
from schemadiff.relocation import (
    detect_relocations,
    relocation_changes,
    suppress_relocated_content,
)


def token(text: str, x: float, y: float) -> TextToken:
    return TextToken(
        text=text,
        box=Box(x, y, x + 12, y + 6),
        source="text",
        confidence=1.0,
    )


def info(index: int) -> PageInfo:
    return PageInfo(
        index=index,
        width=842,
        height=595,
        rotation=0,
        text_char_count=100,
        image_count=0,
        has_text_layer=True,
    )


def test_cross_page_relocation_uses_labels_not_page_number() -> None:
    results = [
        PageResult(pair=PagePair(old_page=0, new_page=0, score=0.9)),
        PageResult(pair=PagePair(old_page=1, new_page=1, score=0.9)),
    ]
    old_tokens = {
        0: [token("R1", 100, 100), token("C2", 130, 100)],
        1: [token("U9", 300, 200)],
    }
    new_tokens = {
        0: [token("U9", 300, 200)],
        1: [token("R1", 500, 350), token("C2", 530, 350)],
    }

    relocations = detect_relocations(
        results,
        old_tokens,
        new_tokens,
        [info(0), info(1)],
        [info(0), info(1)],
        CompareConfig(),
    )

    assert len(relocations) == 1
    relocation = relocations[0]
    assert relocation.old_page == 0
    assert relocation.new_page == 1
    assert relocation.scope == "cross-page"
    assert {"R1", "C2"} <= set(relocation.refdes)


def test_same_page_large_move_is_detected() -> None:
    results = [PageResult(pair=PagePair(old_page=0, new_page=0, score=0.9))]
    old_tokens = {0: [token("R1", 100, 100), token("C2", 130, 100)]}
    new_tokens = {0: [token("R1", 500, 350), token("C2", 540, 355)]}

    relocations = detect_relocations(
        results,
        old_tokens,
        new_tokens,
        [info(0)],
        [info(0)],
        CompareConfig(),
    )

    assert len(relocations) == 1
    assert relocations[0].scope == "same-page"


def test_unmatched_deletion_is_not_called_a_move() -> None:
    results = [PageResult(pair=PagePair(old_page=0, new_page=0, score=0.9))]
    relocations = detect_relocations(
        results,
        {0: [token("R1", 100, 100), token("C2", 130, 100)]},
        {0: [token("U9", 400, 300)]},
        [info(0)],
        [info(0)],
        CompareConfig(),
    )

    assert relocations == []


def test_relocated_noise_is_suppressed_and_one_move_row_is_kept() -> None:
    results = [
        PageResult(
            pair=PagePair(old_page=0, new_page=0, score=0.9),
            text_deltas=[
                TextDelta(
                    page_index=0,
                    old_page=0,
                    new_page=0,
                    kind="word-delete",
                    old_text="R1 C2",
                    new_text="",
                    old_box=Box(90, 90, 150, 115),
                    new_box=None,
                    confidence=0.9,
                    source="text",
                    reason="moved away",
                )
            ],
        ),
        PageResult(
            pair=PagePair(old_page=1, new_page=1, score=0.9),
            text_deltas=[
                TextDelta(
                    page_index=1,
                    old_page=1,
                    new_page=1,
                    kind="word-insert",
                    old_text="",
                    new_text="R1 C2",
                    old_box=None,
                    new_box=Box(490, 340, 550, 365),
                    confidence=0.9,
                    source="text",
                    reason="moved here",
                )
            ],
        ),
    ]
    old_tokens = {
        0: [token("R1", 100, 100), token("C2", 130, 100)],
        1: [token("U9", 300, 200)],
    }
    new_tokens = {
        0: [token("U9", 300, 200)],
        1: [token("R1", 500, 350), token("C2", 530, 350)],
    }
    config = CompareConfig()
    relocations = detect_relocations(
        results,
        old_tokens,
        new_tokens,
        [info(0), info(1)],
        [info(0), info(1)],
        config,
    )

    suppress_relocated_content(results, relocations)
    changes = relocation_changes(results, relocations)

    assert all(not page.text_deltas for page in results)
    assert len(changes) == 1
    assert changes[0].change_type == "block_move"
    assert (changes[0].old_page, changes[0].new_page) == (0, 1)
