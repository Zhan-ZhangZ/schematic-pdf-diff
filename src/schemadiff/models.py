from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import hypot
from pathlib import Path
from typing import Any


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2.0, (self.y0 + self.y1) / 2.0)

    def expand(self, amount: float) -> "Box":
        return Box(
            self.x0 - amount,
            self.y0 - amount,
            self.x1 + amount,
            self.y1 + amount,
        )

    def clamp(self, width: float, height: float) -> "Box":
        return Box(
            max(0.0, min(width, self.x0)),
            max(0.0, min(height, self.y0)),
            max(0.0, min(width, self.x1)),
            max(0.0, min(height, self.y1)),
        )

    def union(self, other: "Box") -> "Box":
        return Box(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def intersection(self, other: "Box") -> "Box | None":
        box = Box(
            max(self.x0, other.x0),
            max(self.y0, other.y0),
            min(self.x1, other.x1),
            min(self.y1, other.y1),
        )
        return box if box.width > 0 and box.height > 0 else None

    def iou(self, other: "Box") -> float:
        intersection = self.intersection(other)
        if intersection is None:
            return 0.0
        union = self.area + other.area - intersection.area
        return intersection.area / union if union > 0 else 0.0

    def gap(self, other: "Box") -> float:
        horizontal = max(0.0, self.x0 - other.x1, other.x0 - self.x1)
        vertical = max(0.0, self.y0 - other.y1, other.y0 - self.y1)
        return hypot(horizontal, vertical)

    def contains_center(self, other: "Box", margin: float = 0.0) -> bool:
        x, y = other.center
        return (
            self.x0 - margin <= x <= self.x1 + margin
            and self.y0 - margin <= y <= self.y1 + margin
        )

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass
class PageInfo:
    index: int
    width: float
    height: float
    rotation: int = 0
    text_char_count: int = 0
    image_count: int = 0
    has_text_layer: bool = False

    def to_dict(self) -> dict:
        return _jsonable(asdict(self))


@dataclass
class TextToken:
    text: str
    box: Box
    source: str = "text"
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "box": self.box.to_dict(),
            "source": self.source,
            "confidence": self.confidence,
        }


@dataclass
class TextLine:
    text: str
    box: Box
    chars: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "box": self.box.to_dict(),
            "char_count": len(self.chars),
        }


@dataclass
class PagePair:
    old_page: int | None
    new_page: int | None
    score: float
    review: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return _jsonable(asdict(self))


@dataclass
class TextDelta:
    page_index: int
    old_page: int | None
    new_page: int | None
    kind: str
    old_text: str
    new_text: str
    old_box: Box | None
    new_box: Box | None
    confidence: float
    source: str
    reason: str

    @property
    def box(self) -> Box:
        boxes = [box for box in (self.old_box, self.new_box) if box is not None]
        merged = boxes[0]
        for box in boxes[1:]:
            merged = merged.union(box)
        return merged

    def to_dict(self) -> dict:
        return {
            "page_index": self.page_index,
            "old_page": self.old_page,
            "new_page": self.new_page,
            "kind": self.kind,
            "old_text": self.old_text,
            "new_text": self.new_text,
            "old_box": self.old_box.to_dict() if self.old_box else None,
            "new_box": self.new_box.to_dict() if self.new_box else None,
            "confidence": self.confidence,
            "source": self.source,
            "reason": self.reason,
        }


@dataclass
class VisualRegion:
    page_index: int
    old_page: int | None
    new_page: int | None
    old_box: Box
    new_box: Box
    pixel_count: int
    removed_pixels: int
    added_pixels: int
    confidence: float
    reason: str

    @property
    def box(self) -> Box:
        return self.old_box.union(self.new_box)

    def to_dict(self) -> dict:
        return {
            "page_index": self.page_index,
            "old_page": self.old_page,
            "new_page": self.new_page,
            "old_box": self.old_box.to_dict(),
            "new_box": self.new_box.to_dict(),
            "pixel_count": self.pixel_count,
            "removed_pixels": self.removed_pixels,
            "added_pixels": self.added_pixels,
            "confidence": self.confidence,
            "reason": self.reason,
        }


@dataclass
class Change:
    change_id: str
    page_index: int
    old_page: int | None
    new_page: int | None
    change_type: str
    title: str
    before: str
    after: str
    old_box: Box
    new_box: Box
    confidence: float
    reason: str
    refdes: list[str] = field(default_factory=list)
    nets: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    text_delta_ids: list[int] = field(default_factory=list)
    visual_region_ids: list[int] = field(default_factory=list)

    @property
    def box(self) -> Box:
        return self.old_box.union(self.new_box)

    def to_dict(self) -> dict:
        return {
            "change_id": self.change_id,
            "page_index": self.page_index,
            "old_page": self.old_page,
            "new_page": self.new_page,
            "change_type": self.change_type,
            "title": self.title,
            "before": self.before,
            "after": self.after,
            "old_box": self.old_box.to_dict(),
            "new_box": self.new_box.to_dict(),
            "confidence": self.confidence,
            "reason": self.reason,
            "refdes": self.refdes,
            "nets": self.nets,
            "evidence": self.evidence,
            "text_delta_ids": self.text_delta_ids,
            "visual_region_ids": self.visual_region_ids,
        }


@dataclass
class PageResult:
    pair: PagePair
    text_deltas: list[TextDelta] = field(default_factory=list)
    visual_regions: list[VisualRegion] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    alignment: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.changes or self.text_deltas or self.visual_regions)

    def to_dict(self) -> dict:
        return {
            "pair": self.pair.to_dict(),
            "text_deltas": [item.to_dict() for item in self.text_deltas],
            "visual_regions": [item.to_dict() for item in self.visual_regions],
            "changes": [item.to_dict() for item in self.changes],
            "alignment": _jsonable(self.alignment),
            "metrics": _jsonable(self.metrics),
        }
