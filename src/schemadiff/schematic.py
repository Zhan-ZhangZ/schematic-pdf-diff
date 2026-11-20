from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .models import Box, TextToken

REFDES_RE = re.compile(
    r"^(?:(?:DR|TP|LED|RN|CN|FB|SW|TVS|VR|FUSE|CORE|BAT|[RCLQUDPJXYFTM])\d+[A-Z]?)$",
    re.IGNORECASE,
)
REFDES_SCAN_RE = re.compile(
    r"(?<![A-Z0-9])(?:DR|TP|LED|RN|CN|FB|SW|TVS|VR|FUSE|CORE|BAT|[RCLQUDPJXYFTM])\d+[A-Z]?(?![A-Z0-9])",
    re.IGNORECASE,
)
VALUE_RE = re.compile(
    r"^(?:"
    r"\d+(?:\.\d+)?(?:[pnuumkKMG]?)(?:R|Ω|F|H|V|A|W|HZ)"
    r"(?:/\d+(?:\.\d+)?V)?"
    r"(?:_\d+(?:\.\d+)?%|-\d+(?:\.\d+)?%)?"
    r"|\d+(?:\.\d+)?(?:M|K)?HZ_\d+(?:\.\d+)?(?:pF|nF|uF)"
    r"|\d+(?:\.\d+)?[KMG](?:[-_]\d+(?:\.\d+)?%)?"
    r"|NC|DNP|DNI|NP"
    r")$",
    re.IGNORECASE,
)
VALUE_SCAN_RE = re.compile(
    r"(?<![A-Z0-9])(?:"
    r"\d+(?:\.\d+)?(?:[pnuumkKMG]?)(?:R|Ω|F|H|V|A|W|HZ)"
    r"(?:/\d+(?:\.\d+)?V)?"
    r"(?:_\d+(?:\.\d+)?%|-\d+(?:\.\d+)?%)?"
    r"|\d+(?:\.\d+)?(?:M|K)?HZ_\d+(?:\.\d+)?(?:pF|nF|uF)"
    r"|\d+(?:\.\d+)?[KMG](?:[-_]\d+(?:\.\d+)?%)?"
    r"|NC|DNP|DNI|NP"
    r")(?![A-Z0-9])",
    re.IGNORECASE,
)
NET_PREFIX_RE = re.compile(
    r"^(?:VCC|VDD|GND|AVCC|AGND|DVCC|DGND|NET|NL|USB|AUDIO|TV|HDMI|JTAG|WIFI|"
    r"CPU|EPHY|SDC|IR|TX|RX|RTC|VBUS|SCK|AV|OUT|IN)\b",
    re.IGNORECASE,
)
VOLTAGE_RAIL_RE = re.compile(r"^(?:\d+(?:\.\d+)?V|VDD|VCC|GND|AVCC)$", re.IGNORECASE)
SERIALIZED_PREFIX_RE = re.compile(r"^(?:PP?I|CO|NL)", re.IGNORECASE)


@dataclass(frozen=True)
class SemanticItem:
    kind: str
    text: str
    box: Box


@dataclass
class SemanticContext:
    refs: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    nets: list[str] = field(default_factory=list)
    ref_values: dict[str, list[str]] = field(default_factory=dict)
    items: list[SemanticItem] = field(default_factory=list)


def _clean(value: str) -> str:
    return re.sub(r"\s+", "", value).strip()


def canonical_refdes(value: str) -> str | None:
    text = _clean(value)
    if SERIALIZED_PREFIX_RE.match(text) and not text.upper().startswith(("NL", "VCC", "VDD")):
        stripped = re.sub(r"^(?:PP?I|CO)", "", text, flags=re.IGNORECASE)
        if REFDES_RE.fullmatch(stripped):
            text = stripped
    if REFDES_RE.fullmatch(text):
        return text.upper()
    return None


def canonical_value(value: str) -> str | None:
    text = _clean(value).replace("µ", "u")
    if VOLTAGE_RAIL_RE.fullmatch(text):
        return None
    if VALUE_RE.fullmatch(text):
        return text
    return None


def canonical_net(value: str) -> str | None:
    text = _clean(value)
    upper = text.upper()
    if VOLTAGE_RAIL_RE.fullmatch(text):
        return upper
    if canonical_refdes(text) or canonical_value(text):
        return None
    if NET_PREFIX_RE.match(text) and re.search(r"[A-Z]", upper):
        return upper
    if re.fullmatch(r"(?=.*[A-Z])(?=.*(?:-|_))[A-Z0-9_.-]{3,}", upper):
        return upper
    return None


def _box_gap(left: Box, right: Box) -> float:
    return left.gap(right)


def _adjacent_compounds(tokens: list[TextToken]) -> list[SemanticItem]:
    """Recover semantic labels split by overprinted PDF text objects."""
    by_line: dict[int, list[TextToken]] = defaultdict(list)
    for token in tokens:
        if not token.text.strip():
            continue
        band = round(token.box.center[1] / 1.4)
        by_line[band].append(token)
    output: list[SemanticItem] = []
    for line_tokens in by_line.values():
        line_tokens.sort(key=lambda token: token.box.x0)
        for start in range(len(line_tokens)):
            text = ""
            boxes = []
            for end in range(start, min(len(line_tokens), start + 12)):
                token = line_tokens[end]
                if boxes:
                    gap = token.box.x0 - boxes[-1].x1
                    if gap > 2.2:
                        break
                text += _clean(token.text)
                boxes.append(token.box)
                if len(text) > 36:
                    break
                ref = canonical_refdes(text)
                value = canonical_value(text)
                net = canonical_net(text)
                if ref or value or net:
                    merged = boxes[0]
                    for box in boxes[1:]:
                        merged = merged.union(box)
                    output.append(
                        SemanticItem(
                            kind="refdes" if ref else ("value" if value else "net"),
                            text=ref or value or net or text,
                            box=merged,
                        )
                    )
    return output


def semantic_items(tokens: list[TextToken]) -> list[SemanticItem]:
    output: list[SemanticItem] = []
    seen: set[tuple[str, str, int, int]] = set()
    candidates = []
    clean_ref_boxes: list[tuple[str, Box]] = []
    for token in tokens:
        text = _clean(token.text)
        exact_ref = REFDES_RE.fullmatch(text)
        if exact_ref:
            clean_ref_boxes.append((text.upper(), token.box))
        candidates.append(token)
        for match in REFDES_SCAN_RE.findall(text):
            folded = token.text.casefold()
            if folded.startswith(("pi", "co", "pp", "nl")) and len(text) > 8:
                continue
            candidates.append(TextToken(match, token.box, token.source, token.confidence))
        for match in VALUE_SCAN_RE.findall(text):
            if len(text) <= 24:
                candidates.append(TextToken(match, token.box, token.source, token.confidence))
    candidates.extend(
        TextToken(item.text, item.box, "compound", 1.0)
        for item in _adjacent_compounds(tokens)
    )
    for token in candidates:
        text = _clean(token.text)
        ref = canonical_refdes(text)
        value = canonical_value(text)
        net = canonical_net(text)
        if ref:
            kind, normalized = "refdes", ref
        elif value:
            kind, normalized = "value", value
        elif net:
            kind, normalized = "net", net
        else:
            continue
        if kind == "refdes":
            synthetic = any(
                clean_ref != normalized
                and normalized.startswith(clean_ref)
                and (
                    0 < len(normalized) - len(clean_ref) <= 3
                    and normalized[len(clean_ref) :].startswith("0")
                )
                and cleaned_box.gap(token.box) <= 30.0
                for clean_ref, cleaned_box in clean_ref_boxes
            )
            if synthetic:
                continue
        key = (kind, normalized, round(token.box.x0), round(token.box.y0))
        if key in seen:
            continue
        seen.add(key)
        output.append(SemanticItem(kind, normalized, token.box))
    refs = [item for item in output if item.kind == "refdes"]
    filtered = []
    for item in output:
        if item.kind == "refdes" and any(
            other.text != item.text
            and item.text.startswith(other.text)
            and (
                (
                    0 < len(item.text) - len(other.text) <= 3
                    and item.text[len(other.text) :].startswith("0")
                )
                or other.box.iou(item.box) >= 0.3
            )
            and other.box.gap(item.box) <= 30.0
            for other in refs
        ):
            continue
        filtered.append(item)
    return filtered


def nearby_semantics(
    tokens: list[TextToken],
    box: Box,
    radius: float,
) -> SemanticContext:
    nearby_tokens = [
        token for token in tokens if _box_gap(token.box, box) <= radius
    ]
    items = semantic_items(nearby_tokens)
    context = SemanticContext(items=items)
    for item in items:
        if item.kind == "refdes" and item.text not in context.refs:
            context.refs.append(item.text)
        elif item.kind == "value" and item.text not in context.values:
            context.values.append(item.text)
        elif item.kind == "net" and item.text not in context.nets:
            context.nets.append(item.text)

    ref_items = [item for item in items if item.kind == "refdes"]
    value_items = [item for item in items if item.kind == "value"]
    for ref in ref_items:
        ranked = sorted(
            value_items,
            key=lambda value: _box_gap(ref.box, value.box),
        )
        values = [
            value.text
            for value in ranked
            if _box_gap(ref.box, value.box) <= 48.0
        ][:6]
        if values:
            context.ref_values[ref.text] = values
    return context


def nearest_context_items(
    context: SemanticContext,
    box: Box,
    kind: str,
    limit: int = 8,
) -> list[SemanticItem]:
    candidates = [item for item in context.items if item.kind == kind]
    return sorted(candidates, key=lambda item: _box_gap(item.box, box))[:limit]


def one_side_change(
    old_context: SemanticContext,
    new_context: SemanticContext,
) -> tuple[list[str], list[str], list[str]]:
    old_refs, new_refs = set(old_context.refs), set(new_context.refs)
    old_nets, new_nets = set(old_context.nets), set(new_context.nets)
    old_values, new_values = set(old_context.values), set(new_context.values)
    return (
        sorted(old_refs - new_refs),
        sorted(new_refs - old_refs),
        sorted((old_nets - new_nets) | (new_nets - old_nets) | (old_values - new_values) | (new_values - old_values)),
    )
