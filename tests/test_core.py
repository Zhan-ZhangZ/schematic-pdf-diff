from __future__ import annotations

import cv2
import numpy as np
import socket

import pytest

from schemadiff.config import CompareConfig
from schemadiff.models import Box
from schemadiff.offline_guard import OfflineNetworkError, offline_network_guard
from schemadiff.page_match import align_pages
from schemadiff.registration import align_page_images


def test_box_operations() -> None:
    left = Box(0, 0, 10, 10)
    right = Box(5, 5, 15, 15)
    assert left.iou(right) == 25 / 175
    assert left.union(right) == Box(0, 0, 15, 15)
    assert left.intersection(right) == Box(5, 5, 10, 10)
    assert Box(0, 0, 2, 2).gap(Box(5, 0, 7, 2)) == 3


def test_translation_registration_reduces_disagreement() -> None:
    old = np.full((220, 260), 255, dtype=np.uint8)
    new = np.full_like(old, 255)
    cv2.rectangle(old, (50, 60), (150, 65), 0, -1)
    cv2.rectangle(old, (80, 40), (85, 130), 0, -1)
    cv2.rectangle(new, (54, 63), (154, 68), 0, -1)
    cv2.rectangle(new, (84, 43), (89, 133), 0, -1)
    result = align_page_images(
        old,
        new,
        CompareConfig(alignment_mode="translation", visual_threshold=20),
    )
    assert result.accepted
    assert result.aligned_disagreement < result.baseline_disagreement
    assert result.source_to_reference.shape == (2, 3)


def test_page_alignment_allows_inserted_page() -> None:
    scores = [
        [0.99, 0.02, 0.02, 0.02],
        [0.02, 0.02, 0.99, 0.02],
        [0.02, 0.02, 0.02, 0.99],
    ]
    pairs = align_pages(scores, 3, 4, CompareConfig())
    assert [(pair.old_page, pair.new_page) for pair in pairs] == [
        (0, 0),
        (None, 1),
        (1, 2),
        (2, 3),
    ]


def test_offline_guard_blocks_connections() -> None:
    with offline_network_guard(), pytest.raises(OfflineNetworkError):
        socket.create_connection(("127.0.0.1", 9), timeout=0.01)
