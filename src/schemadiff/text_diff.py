from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass

from .models import Box, TextDelta, TextLine, TextToken

logger = logging.getLogger(__name__)


@dataclass
class PairTextContext:
    page_index: int
    old_page: int
    new_page: int
    old_tokens: list[TextToken]
    new_tokens: list[TextToken]
    old_lines: list[TextLine]
    new_lines: list[TextLine]


def _normalized_token(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _ordered_tokens(tokens: list[TextToken]) -> list[TextToken]:
    if not tokens:
        return []
    median_height = sorted(max(0.5, token.box.height) for token in tokens)[
        len(tokens) // 2
    ]
    band = max(1.0, median_height * 1.25)
    return sorted(
        tokens,
        key=lambda token: (
            round(token.box.center[1] / band),
            token.box.x0,
        ),
    )


def _union_boxes(boxes: list[Box]) -> Box | None:
    if not boxes:
        return None
    result = boxes[0]
    for box in boxes[1:]:
        result = result.union(box)
    return result


def _delta_from_word_spans(
    context: PairTextContext,
    old_tokens: list[TextToken],
    new_tokens: list[TextToken],
) -> TextDelta | None:
    old_text = " ".join(token.text for token in old_tokens)
    new_text = " ".join(token.text for token in new_tokens)
    if _normalized_token(old_text) == _normalized_token(new_text):
        return None
    confidence = min(
        [token.confidence for token in [*old_tokens, *new_tokens]] or [1.0]
    )
    sources = {token.source for token in [*old_tokens, *new_tokens]}
    return TextDelta(
        page_index=context.page_index,
        old_page=context.old_page,
        new_page=context.new_page,
        kind="word-replace" if old_tokens and new_tokens else (
            "word-delete" if old_tokens else "word-insert"
        ),
        old_text=old_text,
        new_text=new_text,
        old_box=_union_boxes([token.box for token in old_tokens]),
        new_box=_union_boxes([token.box for token in new_tokens]),
        confidence=0.9 * confidence,
        source="+".join(sorted(sources)),
        reason="Reading-order word difference",
    )


def compare_words(context: PairTextContext) -> list[TextDelta]:
    old = _ordered_tokens(context.old_tokens)
    new = _ordered_tokens(context.new_tokens)
    old_text = [_normalized_token(token.text) for token in old]
    new_text = [_normalized_token(token.text) for token in new]
    matcher = difflib.SequenceMatcher(
        None,
        old_text,
        new_text,
        autojunk=False,
    )
    deltas = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        delta = _delta_from_word_spans(
            context,
            old[i1:i2],
            new[j1:j2],
        )
        if delta is not None:
            deltas.append(delta)
    return deltas


def _char_index_to_box(
    line: TextLine,
    text_start: int,
    text_end: int,
) -> Box:
    chars = line.chars
    if not chars:
        width = max(line.box.width, 1.0)
        x0 = line.box.x0 + width * text_start / max(1, len(line.text))
        x1 = line.box.x0 + width * text_end / max(1, len(line.text))
        return Box(x0, line.box.y0, x1, line.box.y1)
    visible_chars = [char for char in chars if char.get("text", "").strip()]
    if not visible_chars:
        return line.box
    selected = visible_chars[
        min(text_start, len(visible_chars) - 1) : min(text_end, len(visible_chars))
    ]
    if not selected:
        char = visible_chars[min(text_start, len(visible_chars) - 1)]
        return Box(
            float(char["x0"]),
            float(char["top"]),
            float(char["x1"]),
            float(char["bottom"]),
        )
    return Box(
        min(float(char["x0"]) for char in selected),
        min(float(char["top"]) for char in selected),
        max(float(char["x1"]) for char in selected),
        max(float(char["bottom"]) for char in selected),
    )


def _line_char_deltas(
    context: PairTextContext,
    old_line: TextLine,
    new_line: TextLine,
) -> list[TextDelta]:
    matcher = difflib.SequenceMatcher(
        None,
        old_line.text,
        new_line.text,
        autojunk=False,
    )
    output = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_fragment = old_line.text[i1:i2]
        new_fragment = new_line.text[j1:j2]
        if not old_fragment.strip() and not new_fragment.strip():
            continue
        if len(old_fragment.strip() + new_fragment.strip()) > 200:
            continue
        old_box = (
            _char_index_to_box(old_line, i1, i2) if old_fragment.strip() else None
        )
        new_box = (
            _char_index_to_box(new_line, j1, j2) if new_fragment.strip() else None
        )
        output.append(
            TextDelta(
                page_index=context.page_index,
                old_page=context.old_page,
                new_page=context.new_page,
                kind="line-replace" if old_box and new_box else (
                    "line-delete" if old_box else "line-insert"
                ),
                old_text=old_fragment.strip(),
                new_text=new_fragment.strip(),
                old_box=old_box,
                new_box=new_box,
                confidence=0.68
                * min(
                    [char.get("size", 10.0) / 10.0 for char in new_line.chars[:1]]
                    or [1.0]
                ),
                source="text-line",
                reason="Character difference inside an extracted text line",
            )
        )
    return output


def compare_lines(context: PairTextContext) -> list[TextDelta]:
    old = context.old_lines
    new = context.new_lines
    old_text = [re.sub(r"\s+", " ", line.text).strip() for line in old]
    new_text = [re.sub(r"\s+", " ", line.text).strip() for line in new]
    matcher = difflib.SequenceMatcher(
        None,
        old_text,
        new_text,
        autojunk=False,
    )
    output = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        paired = min(i2 - i1, j2 - j1)
        for offset in range(paired):
            output.extend(
                _line_char_deltas(
                    context,
                    old[i1 + offset],
                    new[j1 + offset],
                )
            )
        for line in old[i1 + paired : i2]:
            output.append(
                TextDelta(
                    page_index=context.page_index,
                    old_page=context.old_page,
                    new_page=context.new_page,
                    kind="line-delete",
                    old_text=line.text.strip(),
                    new_text="",
                    old_box=line.box,
                    new_box=None,
                    confidence=0.6,
                    source="text-line",
                    reason="Extracted line removed",
                )
            )
        for line in new[j1 + paired : j2]:
            output.append(
                TextDelta(
                    page_index=context.page_index,
                    old_page=context.old_page,
                    new_page=context.new_page,
                    kind="line-insert",
                    old_text="",
                    new_text=line.text.strip(),
                    old_box=None,
                    new_box=line.box,
                    confidence=0.6,
                    source="text-line",
                    reason="Extracted line added",
                )
            )
    return output


def _similar_delta(left: TextDelta, right: TextDelta) -> bool:
    if (left.old_text, left.new_text) == (right.old_text, right.new_text):
        return left.box.iou(right.box) > 0.35 or left.box.gap(right.box) < 3.0
    if left.source.startswith("word") and right.source.startswith("word"):
        return False
    return left.box.iou(right.box) > 0.75


def dedupe_deltas(deltas: list[TextDelta]) -> list[TextDelta]:
    ordered = sorted(
        deltas,
        key=lambda item: (
            0 if item.source.startswith("word") else 1,
            -item.confidence,
            item.box.y0,
            item.box.x0,
        ),
    )
    output = []
    for delta in ordered:
        if any(_similar_delta(delta, existing) for existing in output):
            continue
        output.append(delta)
    return output


def analyze_text_pair(context: PairTextContext) -> list[TextDelta]:
    word_deltas = compare_words(context)
    line_deltas = compare_lines(context)
    deltas = dedupe_deltas([*word_deltas, *line_deltas])
    logger.debug(
        "Text evidence page %s: %s word, %s line, %s deduped",
        context.page_index + 1,
        len(word_deltas),
        len(line_deltas),
        len(deltas),
    )
    return deltas
