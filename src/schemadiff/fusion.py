from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .config import CompareConfig
from .models import Box, Change, TextDelta, VisualRegion
from .schematic import (
    SemanticItem,
    SemanticContext,
    canonical_net,
    canonical_refdes,
    canonical_value,
    nearby_semantics,
)

logger = logging.getLogger(__name__)


@dataclass
class _Candidate:
    page_index: int
    old_page: int
    new_page: int
    old_box: Box
    new_box: Box
    change_type: str
    title: str
    before: str
    after: str
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

    @property
    def key(self) -> tuple[str, ...]:
        if self.refdes:
            return ("ref", self.refdes[0])
        if self.nets:
            return ("net", *self.nets[:2])
        return ("box", str(self.page_index), str(round(self.box.x0 / 40)), str(round(self.box.y0 / 40)))


def _dedupe(values: list[str]) -> list[str]:
    output = []
    for value in values:
        if value and value not in output:
            output.append(value)
    return output


def _combine_boxes(
    old_boxes: list[Box],
    new_boxes: list[Box],
) -> tuple[Box, Box]:
    if not old_boxes:
        old_boxes = new_boxes
    if not new_boxes:
        new_boxes = old_boxes
    old = old_boxes[0]
    new = new_boxes[0]
    for box in old_boxes[1:]:
        old = old.union(box)
    for box in new_boxes[1:]:
        new = new.union(box)
    return old, new


def _compact_text(text: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _is_noise_text(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text)
    if not normalized:
        return False
    if re.fullmatch(r"(?:P+|I+|C+|O+|N+|L+|R+|\d+)+", normalized, re.IGNORECASE):
        return True
    if re.search(r"(?:PPII|CO[A-Z]){2,}", normalized, re.IGNORECASE) and len(normalized) > 16:
        return True
    return False


def _delta_is_semantic(
    delta: TextDelta,
    old_context: SemanticContext,
    new_context: SemanticContext,
) -> bool:
    if _is_noise_text(delta.old_text) and _is_noise_text(delta.new_text):
        return False
    pieces = [delta.old_text, delta.new_text]
    for piece in pieces:
        for part in re.findall(r"[A-Za-z0-9_.%/+Ω-]+", piece):
            if canonical_refdes(part) or canonical_value(part) or canonical_net(part):
                return True
    return bool(old_context.refs or new_context.refs or old_context.nets or new_context.nets)


def _direct_semantics(text: str) -> list[str]:
    output = []
    for part in re.findall(r"[A-Za-z0-9_.%/+Ω-]+", text):
        if canonical_refdes(part) or canonical_value(part) or canonical_net(part):
            output.append(part)
    return output


def _matching_items(
    context: SemanticContext,
    kind: str,
    hints: list[str],
    box: Box,
) -> list[SemanticItem]:
    items = [item for item in context.items if item.kind == kind]
    normalized_hints = {
        re.sub(r"\s+", "", hint).casefold()
        for hint in hints
        if hint
    }
    matching = [
        item
        for item in items
        if re.sub(r"\s+", "", item.text).casefold() in normalized_hints
        or any(
            re.sub(r"\s+", "", hint).casefold()
            in re.sub(r"\s+", "", item.text).casefold()
            for hint in hints
            if hint
        )
    ]
    if normalized_hints and not matching:
        return []
    if matching:
        items = matching
    return sorted(items, key=lambda item: item.box.gap(box))


def _distance(left: Box, right: Box) -> float:
    left_x, left_y = left.center
    right_x, right_y = right.center
    return ((left_x - right_x) ** 2 + (left_y - right_y) ** 2) ** 0.5


def _title_for(
    *,
    refdes: list[str],
    nets: list[str],
    before: str,
    after: str,
    change_type: str,
) -> str:
    ref = refdes[0] if refdes else None
    if change_type == "component_value" and ref:
        return f"{ref}: {before or 'value unavailable'} -> {after or 'value unavailable'}"
    if change_type == "component_added" and ref:
        return f"{ref} added"
    if change_type == "component_removed" and ref:
        return f"{ref} removed"
    if change_type == "net_change":
        if ref and before and after:
            return f"{ref}: {before} -> {after}"
        if before and after:
            return f"Net label: {before} -> {after}"
        if nets:
            return "Network labels reordered: " + ", ".join(nets[:4])
        return "Network topology or label change"
    if change_type == "component_change" and ref:
        detail = f" ({before} -> {after})" if before or after else ""
        return f"{ref} component change{detail}"
    if change_type == "annotation":
        return f"Annotation or symbol change near {ref}" if ref else "Annotation or symbol change"
    if change_type == "wire_symbol":
        return (
            f"Wire or symbol change near {ref}"
            if ref
            else "Wire or symbol change"
        )
    if before or after:
        return f"{before or '(none)'} -> {after or '(none)'}"
    return "Visual change"


def _context_for_region(
    region: VisualRegion,
    old_tokens,
    new_tokens,
    config: CompareConfig,
) -> tuple[SemanticContext, SemanticContext]:
    old_box = region.old_box.expand(config.evidence_radius_pt)
    new_box = region.new_box.expand(config.evidence_radius_pt)
    return (
        nearby_semantics(old_tokens, old_box, config.evidence_radius_pt),
        nearby_semantics(new_tokens, new_box, config.evidence_radius_pt),
    )


def _value_pair(
    old_context: SemanticContext,
    new_context: SemanticContext,
    preferred_refs: list[str],
    target_box: Box,
    old_hints: list[str] | None = None,
    new_hints: list[str] | None = None,
    max_ref_value_gap: float = 24.0,
) -> tuple[str, str, str | None]:
    old_hints = old_hints or []
    new_hints = new_hints or []
    old_values = _matching_items(old_context, "value", old_hints, target_box)
    new_values = _matching_items(new_context, "value", new_hints, target_box)
    old_refs = [item for item in old_context.items if item.kind == "refdes"]
    new_refs = [item for item in new_context.items if item.kind == "refdes"]
    common_refs = {
        old_item.text
        for old_item in old_refs
        if any(old_item.text == new_item.text for new_item in new_refs)
    }
    ranked = []
    for ref in common_refs:
        old_ref = min(
            (item.box for item in old_refs if item.text == ref),
            key=lambda ref_box: _distance(ref_box, target_box),
        )
        new_ref = min(
            (item.box for item in new_refs if item.text == ref),
            key=lambda ref_box: _distance(ref_box, target_box),
        )
        old_near = sorted(old_values, key=lambda item: item.box.gap(old_ref))
        new_near = sorted(new_values, key=lambda item: item.box.gap(new_ref))
        if not old_near or not new_near:
            continue
        if (
            old_near[0].box.gap(old_ref) > max_ref_value_gap
            or new_near[0].box.gap(new_ref) > max_ref_value_gap
        ):
            continue
        if old_near[0].text == new_near[0].text:
            continue
        score = (
            old_near[0].box.gap(old_ref)
            + new_near[0].box.gap(new_ref)
            + 0.08 * (_distance(old_ref, target_box) + _distance(new_ref, target_box))
        )
        ranked.append((score, ref, old_near[0].text, new_near[0].text))
    if ranked:
        _, ref, old_value, new_value = min(ranked, key=lambda item: item[0])
        return old_value, new_value, ref

    if old_values and new_values and old_values[0].text != new_values[0].text:
        ref_boxes = []
        for context in (old_context, new_context):
            ref_boxes.extend(
                (item.text, item.box)
                for item in context.items
                if item.kind == "refdes"
            )
        ref = preferred_refs[0] if preferred_refs else None
        if ref_boxes:
            old_value = old_values[0]
            new_value = new_values[0]
            ref, _ = min(
                ref_boxes,
                key=lambda item: (
                    item[1].gap(old_value.box)
                    + item[1].gap(new_value.box),
                    item[0],
                ),
            )
        return old_values[0].text, new_values[0].text, ref

    if preferred_refs:
        for ref in preferred_refs:
            old_values_for_ref = old_context.ref_values.get(ref, [])
            new_values_for_ref = new_context.ref_values.get(ref, [])
            if (
                old_values_for_ref
                and new_values_for_ref
                and old_values_for_ref != new_values_for_ref
            ):
                return (
                    old_values_for_ref[0],
                    new_values_for_ref[0],
                    ref,
                )
    old_only = [
        value for value in old_context.values if value not in new_context.values
    ] if preferred_refs else []
    new_only = [
        value for value in new_context.values if value not in old_context.values
    ] if preferred_refs else []
    if preferred_refs and (old_only or new_only):
        return (
            old_only[0] if old_only else "",
            new_only[0] if new_only else "",
            preferred_refs[0] if preferred_refs else None,
        )
    return "", "", preferred_refs[0] if preferred_refs else None


def _anchor_refs(
    old_context: SemanticContext,
    new_context: SemanticContext,
    box: Box,
    *,
    max_gap: float = 8.0,
) -> list[str]:
    candidates = []
    for context in (old_context, new_context):
        for item in context.items:
            if item.kind != "refdes":
                continue
            gap = item.box.gap(box)
            if gap <= max_gap:
                candidates.append((gap, item.text))
    return _dedupe(
        [ref for _, ref in sorted(candidates, key=lambda item: (item[0], item[1]))]
    )


def _ranked_refs(
    old_context: SemanticContext,
    new_context: SemanticContext,
    box: Box,
    limit: int = 5,
) -> list[str]:
    values = []
    for context in (old_context, new_context):
        for item in context.items:
            if item.kind == "refdes":
                values.append((item.box.gap(box), item.text))
    return _dedupe(
        [
            text
            for _, text in sorted(
                values,
                key=lambda value: (value[0], value[1]),
            )
        ]
    )[:limit]


def _candidate_from_text_delta(
    delta_index: int,
    delta: TextDelta,
    old_tokens,
    new_tokens,
    config: CompareConfig,
) -> _Candidate | None:
    query = delta.box.expand(8.0)
    old_context = nearby_semantics(old_tokens, query, config.evidence_radius_pt)
    new_context = nearby_semantics(new_tokens, query, config.evidence_radius_pt)
    old_direct = _direct_semantics(delta.old_text)
    new_direct = _direct_semantics(delta.new_text)
    if not old_direct and not new_direct:
        return None
    if (
        delta.source.startswith("text-line")
        and bool(old_direct) != bool(new_direct)
    ):
        return None
    refs = _anchor_refs(old_context, new_context, query)
    nets = _dedupe(
        [
            item
            for item in [*old_direct, *new_direct]
            if canonical_net(item)
        ]
    )
    direct_values_old = [item for item in old_direct if canonical_value(item)]
    direct_values_new = [item for item in new_direct if canonical_value(item)]
    value_before, value_after, value_ref = _value_pair(
        old_context,
        new_context,
        refs,
        query,
        direct_values_old or old_direct,
        direct_values_new or new_direct,
        max_ref_value_gap=12.0,
    )
    if value_ref and value_ref not in refs:
        refs.insert(0, value_ref)

    before = value_before or (old_direct[0] if old_direct else _compact_text(delta.old_text))
    after = value_after or (new_direct[0] if new_direct else _compact_text(delta.new_text))

    if value_before or value_after:
        change_type = "component_value"
    elif nets:
        change_type = "net_change"
    elif refs:
        change_type = "component_change"
    else:
        change_type = "annotation"

    old_box = delta.old_box or delta.new_box or query
    new_box = delta.new_box or delta.old_box or query
    title = _title_for(
        refdes=refs,
        nets=nets,
        before=before,
        after=after,
        change_type=change_type,
    )
    return _Candidate(
        page_index=delta.page_index,
        old_page=delta.old_page or 0,
        new_page=delta.new_page or 0,
        old_box=old_box,
        new_box=new_box,
        change_type=change_type,
        title=title,
        before=before,
        after=after,
        confidence=delta.confidence,
        reason=delta.reason,
        refdes=_dedupe([*( [value_ref] if value_ref else []), *refs])[:4],
        nets=nets[:6],
        evidence=[f"text:{delta.source}"],
        text_delta_ids=[delta_index],
    )


def _candidate_from_visual_region(
    delta_index_offset: int,
    region_index: int,
    region: VisualRegion,
    old_tokens,
    new_tokens,
    config: CompareConfig,
    *,
    overlaps_text_change: bool = False,
) -> _Candidate:
    local_config = CompareConfig(
        **{
            **config.to_dict(),
            "evidence_radius_pt": min(config.evidence_radius_pt, 24.0),
        }
    )
    old_context, new_context = _context_for_region(
        region,
        old_tokens,
        new_tokens,
        local_config,
    )
    anchor_refs = _anchor_refs(old_context, new_context, region.box, max_gap=18.0)
    before = ""
    after = ""
    value_ref = None
    refs = _dedupe(anchor_refs)
    if not refs:
        refs = _ranked_refs(old_context, new_context, region.box, limit=5)
    nets = _dedupe([*old_context.nets[:6], *new_context.nets[:6]])
    if value_ref and value_ref not in refs:
        refs.insert(0, value_ref)
    old_refs_only = [ref for ref in old_context.refs if ref not in new_context.refs]
    new_refs_only = [ref for ref in new_context.refs if ref not in old_context.refs]
    old_nets_only = [net for net in old_context.nets if net not in new_context.nets]
    new_nets_only = [net for net in new_context.nets if net not in old_context.nets]

    if old_refs_only and new_refs_only:
        change_type = "component_change"
    elif new_refs_only and not old_refs_only:
        refs = _dedupe([*new_refs_only, *refs])
        change_type = "component_added"
    elif old_refs_only and not new_refs_only:
        refs = _dedupe([*old_refs_only, *refs])
        change_type = "component_removed"
    elif overlaps_text_change and refs:
        change_type = "component_change"
    elif old_nets_only or new_nets_only or nets:
        change_type = "net_change"
        if old_nets_only or new_nets_only:
            before = before or (old_nets_only[0] if old_nets_only else "")
            after = after or (new_nets_only[0] if new_nets_only else "")
    else:
        change_type = "wire_symbol"

    title = _title_for(
        refdes=refs,
        nets=nets,
        before=before,
        after=after,
        change_type=change_type,
    )
    confidence = region.confidence
    if refs or nets or value_ref:
        confidence = min(0.98, confidence + 0.08)
    return _Candidate(
        page_index=region.page_index,
        old_page=region.old_page or 0,
        new_page=region.new_page or 0,
        old_box=region.old_box,
        new_box=region.new_box,
        change_type=change_type,
        title=title,
        before=before,
        after=after,
        confidence=confidence,
        reason=region.reason,
        refdes=refs[:6],
        nets=nets[:8],
        evidence=[
            f"visual:{region.pixel_count}px",
            f"removed:{region.removed_pixels}",
            f"added:{region.added_pixels}",
        ],
        visual_region_ids=[region_index],
    )


def _compatible(left: _Candidate, right: _Candidate, config: CompareConfig) -> bool:
    if left.page_index != right.page_index:
        return False
    gap = left.box.gap(right.box)
    same_primary_ref = bool(
        set(left.refdes[:1]) & set(right.refdes[:1])
    )
    same_ref = bool(set(left.refdes[:2]) & set(right.refdes[:2]))
    shared_nets = set(left.nets) & set(right.nets)
    if same_primary_ref and gap <= config.semantic_merge_gap_pt:
        return True
    if same_ref and gap <= 22.0:
        return True
    if (
        left.change_type == "net_change"
        and right.change_type == "net_change"
        and shared_nets
        and gap <= 18.0
    ):
        return True
    return gap <= 6.0


def _merge(left: _Candidate, right: _Candidate) -> _Candidate:
    old_box, new_box = _combine_boxes(
        [left.old_box, right.old_box],
        [left.new_box, right.new_box],
    )
    left.refdes = _dedupe([*left.refdes, *right.refdes])
    left.nets = _dedupe([*left.nets, *right.nets])
    left.evidence = _dedupe([*left.evidence, *right.evidence])
    left.text_delta_ids = sorted(set([*left.text_delta_ids, *right.text_delta_ids]))
    left.visual_region_ids = sorted(
        set([*left.visual_region_ids, *right.visual_region_ids])
    )
    left.old_box = old_box
    left.new_box = new_box
    left.confidence = max(left.confidence, right.confidence)
    if right.before and not left.before:
        left.before = right.before
    if right.after and not left.after:
        left.after = right.after
    if left.change_type in {"wire_symbol", "annotation"} and right.change_type not in {
        "wire_symbol",
        "annotation",
    }:
        left.change_type = right.change_type
        left.title = right.title
    elif not left.refdes:
        left.refdes = list(right.refdes)
    return left


def fuse_page_changes(
    page_index: int,
    old_page: int,
    new_page: int,
    text_deltas: list[TextDelta],
    visual_regions: list[VisualRegion],
    old_tokens,
    new_tokens,
    config: CompareConfig,
) -> list[Change]:
    candidates: list[_Candidate] = []
    for index, delta in enumerate(text_deltas):
        candidate = _candidate_from_text_delta(
            index,
            delta,
            old_tokens,
            new_tokens,
            config,
        )
        if candidate is not None:
            candidates.append(candidate)

    unassigned = []
    for index, region in enumerate(visual_regions):
        best = None
        best_distance = float("inf")
        for candidate in candidates:
            distance = candidate.box.gap(region.box)
            common_ref = bool(set(candidate.refdes) & set(
                nearby_semantics(old_tokens, region.old_box.expand(20), 20).refs
                + nearby_semantics(new_tokens, region.new_box.expand(20), 20).refs
            ))
            if distance <= 14.0 or (common_ref and distance <= 40.0):
                if distance < best_distance:
                    best = candidate
                    best_distance = distance
        region_candidate = _candidate_from_visual_region(
            len(text_deltas),
            index,
            region,
            old_tokens,
            new_tokens,
            config,
            overlaps_text_change=any(
                delta.box.gap(region.box) <= 8.0
                for delta in text_deltas
            ),
        )
        if best is not None:
            best.visual_region_ids = sorted(
                set([*best.visual_region_ids, index])
            )
            best.evidence = _dedupe([*best.evidence, *region_candidate.evidence])
            best.confidence = max(best.confidence, region.confidence)
            if best.change_type in {"wire_symbol", "annotation"}:
                best.change_type = region_candidate.change_type
                best.title = region_candidate.title
                best.refdes = _dedupe([*best.refdes, *region_candidate.refdes])
                best.nets = _dedupe([*best.nets, *region_candidate.nets])
                best.before = best.before or region_candidate.before
                best.after = best.after or region_candidate.after
        else:
            unassigned.append(region_candidate)
    candidates.extend(unassigned)

    merged: list[_Candidate] = []
    for candidate in sorted(candidates, key=lambda item: item.confidence, reverse=True):
        target = next(
            (
                existing
                for existing in merged
                if _compatible(existing, candidate, config)
            ),
            None,
        )
        if target is None:
            merged.append(candidate)
        else:
            _merge(target, candidate)

    merged.sort(key=lambda item: (item.box.y0, item.box.x0, item.title))
    changes = []
    for local_id, candidate in enumerate(merged, start=1):
        normalized_before = re.sub(r"\s+", "", candidate.before).casefold()
        normalized_after = re.sub(r"\s+", "", candidate.after).casefold()
        if (
            candidate.change_type == "component_value"
            and normalized_before
            and normalized_before == normalized_after
        ):
            candidate.change_type = "annotation"
            candidate.before = ""
            candidate.after = ""
        title = _title_for(
            refdes=candidate.refdes,
            nets=candidate.nets,
            before=candidate.before,
            after=candidate.after,
            change_type=candidate.change_type,
        )
        if candidate.change_type == "component_value" and candidate.refdes and (
            candidate.before or candidate.after
        ):
            title = f"{candidate.refdes[0]}: {candidate.before or '(unknown)'} -> {candidate.after or '(unknown)'}"
        elif candidate.change_type == "net_change" and candidate.nets:
            if candidate.before or candidate.after:
                title = _title_for(
                    refdes=candidate.refdes,
                    nets=candidate.nets,
                    before=candidate.before,
                    after=candidate.after,
                    change_type="net_change",
                )
            elif len(candidate.nets) > 1:
                title = "Network labels or topology changed: " + ", ".join(
                    candidate.nets[:4]
                )
        changes.append(
            Change(
                change_id=f"P{page_index + 1:02d}-C{local_id:03d}",
                page_index=page_index,
                old_page=old_page,
                new_page=new_page,
                change_type=candidate.change_type,
                title=title,
                before=_compact_text(candidate.before),
                after=_compact_text(candidate.after),
                old_box=candidate.old_box,
                new_box=candidate.new_box,
                confidence=candidate.confidence,
                reason=candidate.reason,
                refdes=candidate.refdes,
                nets=candidate.nets,
                evidence=candidate.evidence,
                text_delta_ids=candidate.text_delta_ids,
                visual_region_ids=candidate.visual_region_ids,
            )
        )
    logger.info(
        "Fused page %s: %s text deltas, %s visual regions, %s changes",
        page_index + 1,
        len(text_deltas),
        len(visual_regions),
        len(changes),
    )
    return changes
