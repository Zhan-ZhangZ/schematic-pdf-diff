from __future__ import annotations

from schemadiff.models import Box, TextLine, TextToken
from schemadiff.text_diff import PairTextContext, analyze_text_pair


def token(text: str, x: float, y: float) -> TextToken:
    return TextToken(text, Box(x, y, x + 20, y + 5))


def test_word_value_change_is_detected() -> None:
    old_context = PairTextContext(
        page_index=0,
        old_page=0,
        new_page=0,
        old_tokens=[token("R1", 10, 10), token("100K", 40, 10)],
        new_tokens=[token("R1", 10, 10), token("10K", 40, 10)],
        old_lines=[],
        new_lines=[],
    )
    deltas = analyze_text_pair(old_context)
    assert any(
        delta.old_text == "100K" and delta.new_text == "10K"
        for delta in deltas
    )


def test_line_change_catches_split_value() -> None:
    old_line = TextLine(
        "C2 100nF",
        Box(0, 20, 100, 28),
        [
            {"text": "C2 100nF", "x0": 0, "x1": 100, "top": 20, "bottom": 28}
        ],
    )
    new_line = TextLine(
        "C2 10nF",
        Box(0, 20, 100, 28),
        [
            {"text": "C2 10nF", "x0": 0, "x1": 100, "top": 20, "bottom": 28}
        ],
    )
    context = PairTextContext(
        page_index=0,
        old_page=0,
        new_page=0,
        old_tokens=[token("C2", 0, 20), token("100nF", 30, 20)],
        new_tokens=[token("C2", 0, 20), token("10nF", 30, 20)],
        old_lines=[old_line],
        new_lines=[new_line],
    )
    deltas = analyze_text_pair(context)
    assert any("100nF" in delta.old_text and "10nF" in delta.new_text for delta in deltas)
