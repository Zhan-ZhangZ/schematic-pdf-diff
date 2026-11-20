from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass

import numpy as np

from .config import CompareConfig
from .models import PageInfo, PagePair, TextToken

logger = logging.getLogger(__name__)


@dataclass
class PageFingerprint:
    info: PageInfo
    tokens: set[str]
    token_counts: Counter[str]
    visual: np.ndarray


def matching_tokens(tokens: list[TextToken]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for token in tokens:
        text = token.text.casefold()
        text = re.sub(r"\b(?:page|sheet|sht)\s*[:#-]?\s*\d+\b", " ", text)
        for value in re.findall(r"[\w./-]{2,}", text, flags=re.UNICODE):
            if value.isdigit():
                continue
            if value.startswith(("pi", "co", "nl")) and len(value) > 6:
                continue
            counts[value] += 1
    return counts


def _weighted_similarity(
    left: PageFingerprint,
    right: PageFingerprint,
    weights: dict[str, float],
) -> float:
    if not left.tokens and not right.tokens:
        text_score = 0.0
    else:
        intersection = sum(
            weights.get(token, 1.0) for token in left.tokens.intersection(right.tokens)
        )
        left_total = sum(weights.get(token, 1.0) for token in left.tokens)
        right_total = sum(weights.get(token, 1.0) for token in right.tokens)
        union = max(intersection, left_total + right_total - intersection)
        text_score = intersection / union if union else 0.0
    left_norm = float(np.linalg.norm(left.visual))
    right_norm = float(np.linalg.norm(right.visual))
    if left_norm < 1e-8 and right_norm < 1e-8:
        visual_score = 1.0
    elif left_norm < 1e-8 or right_norm < 1e-8:
        visual_score = 0.0
    else:
        visual_score = float(
            np.clip(np.sum(left.visual * right.visual) / (left_norm * right_norm), 0.0, 1.0)
        )
    left_aspect = left.info.width / max(left.info.height, 1.0)
    right_aspect = right.info.width / max(right.info.height, 1.0)
    aspect_score = min(left_aspect, right_aspect) / max(left_aspect, right_aspect)
    if len(left.tokens) >= 5 and len(right.tokens) >= 5:
        score = 0.68 * text_score + 0.32 * visual_score
    else:
        score = 0.15 * text_score + 0.85 * visual_score
    return max(0.0, min(1.0, score * aspect_score))


def similarity_matrix(
    old_pages: list[PageFingerprint],
    new_pages: list[PageFingerprint],
) -> list[list[float]]:
    combined = [*old_pages, *new_pages]
    document_frequency: Counter[str] = Counter()
    for page in combined:
        document_frequency.update(page.tokens)
    weights = {
        token: math.log(1.0 + len(combined) / max(1, count)) ** 2
        for token, count in document_frequency.items()
    }
    return [
        [_weighted_similarity(old, new, weights) for new in new_pages]
        for old in old_pages
    ]


def align_pages(
    scores: list[list[float]],
    old_count: int,
    new_count: int,
    config: CompareConfig,
) -> list[PagePair]:
    columns = new_count + 1
    values = np.zeros((old_count + 1, columns), dtype=np.float64)
    directions = np.zeros((old_count + 1, columns), dtype=np.uint8)
    for i in range(1, old_count + 1):
        directions[i, 0] = 1
    for j in range(1, new_count + 1):
        directions[0, j] = 2
    for i in range(1, old_count + 1):
        for j in range(1, new_count + 1):
            similarity = scores[i - 1][j - 1]
            positional = 1.0 - abs(i / old_count - j / new_count)
            pair_value = similarity - config.min_page_similarity + 0.015 * positional
            candidates = [
                values[i - 1, j],
                values[i, j - 1],
                values[i - 1, j - 1] + pair_value,
            ]
            direction = int(np.argmax(candidates))
            values[i, j] = candidates[direction]
            directions[i, j] = (1, 2, 3)[direction]

    output: list[PagePair] = []
    i, j = old_count, new_count
    while i or j:
        direction = int(directions[i, j])
        if direction == 3:
            similarity = scores[i - 1][j - 1]
            if similarity >= config.min_page_similarity:
                alternatives = [
                    values
                    for index, values in enumerate(scores[i - 1])
                    if index != j - 1
                ]
                alternative = max(alternatives, default=0.0)
                review = (
                    similarity < config.page_review_similarity
                    or alternative >= similarity - 0.045
                )
                reason = (
                    "Low page similarity"
                    if similarity < config.page_review_similarity
                    else "Similar alternative page exists"
                )
                output.append(
                    PagePair(i - 1, j - 1, similarity, review, reason)
                )
                i -= 1
                j -= 1
                continue
            direction = 1
        if direction == 1 or j == 0:
            output.append(PagePair(i - 1, None, 0.0, False, "Old page is unmatched"))
            i -= 1
        else:
            output.append(PagePair(None, j - 1, 0.0, False, "New page is unmatched"))
            j -= 1
    output.reverse()
    return output
