from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

from .config import CompareConfig
from .models import Box, Change, PageInfo, PageResult, TextDelta, TextToken, VisualRegion
from .schematic import semantic_items

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Relocation:
    old_page: int
    new_page: int
    old_box: Box
    new_box: Box
    old_content_box: Box
    new_content_box: Box
    refdes: tuple[str, ...]
    nets: tuple[str, ...]
    translation: tuple[float, float]
    residual: float
    confidence: float
    scope: str = "cross-page"


def _semantic_refs(tokens: list[TextToken]) -> dict[str, Box]:
    refs: dict[str, Box] = {}
    for item in semantic_items(tokens):
        if item.kind != "refdes":
            continue
        refs[item.text] = item.box if item.text not in refs else refs[item.text].union(item.box)
    return refs


def _semantic_nets(tokens: list[TextToken], region: Box) -> list[str]:
    nets = []
    for item in semantic_items(tokens):
        if item.kind != "net" or item.box.intersection(region) is None:
            continue
        if item.text not in nets:
            nets.append(item.text)
    return nets


def _union(boxes: list[Box]) -> Box:
    result = boxes[0]
    for box in boxes[1:]:
        result = result.union(box)
    return result


def _translation_groups(
    shared_refs: set[str],
    old_refs: dict[str, Box],
    new_refs: dict[str, Box],
    tolerance: float,
) -> list[tuple[set[str], tuple[float, float], float]]:
    motions = {
        ref: (
            new_refs[ref].center[0] - old_refs[ref].center[0],
            new_refs[ref].center[1] - old_refs[ref].center[1],
        )
        for ref in shared_refs
    }
    groups: list[tuple[set[str], tuple[float, float], float]] = []
    for anchor in sorted(shared_refs):
        anchor_dx, anchor_dy = motions[anchor]
        members = set()
        residuals = []
        for ref in sorted(shared_refs):
            dx, dy = motions[ref]
            residual = math.hypot(dx - anchor_dx, dy - anchor_dy)
            if residual <= tolerance:
                members.add(ref)
                residuals.append(residual)
        if len(members) < 2:
            continue
        residual = max(residuals, default=0.0)
        groups.append((members, (anchor_dx, anchor_dy), residual))

    unique: dict[frozenset[str], tuple[set[str], tuple[float, float], float]] = {}
    for group in groups:
        key = frozenset(group[0])
        current = unique.get(key)
        if current is None or group[2] < current[2]:
            unique[key] = group
    return sorted(
        unique.values(),
        key=lambda item: (-len(item[0]), item[2], sorted(item[0])),
    )


def _make_relocation(
    *,
    old_page: int,
    new_page: int,
    refs: set[str],
    old_refs: dict[str, Box],
    new_refs: dict[str, Box],
    translation: tuple[float, float],
    residual: float,
    old_tokens_by_page: dict[int, list[TextToken]],
    new_tokens_by_page: dict[int, list[TextToken]],
    old_infos: list[PageInfo],
    new_infos: list[PageInfo],
    config: CompareConfig,
    scope: str,
) -> Relocation | None:
    if len(refs) < config.relocation_min_refs:
        return None
    old_ref_box = _union([old_refs[ref] for ref in refs])
    new_ref_box = _union([new_refs[ref] for ref in refs])
    if old_ref_box.width * old_ref_box.height < 24.0:
        return None
    old_box = old_ref_box.expand(config.relocation_padding_pt).clamp(
        old_infos[old_page].width,
        old_infos[old_page].height,
    )
    new_box = new_ref_box.expand(config.relocation_padding_pt).clamp(
        new_infos[new_page].width,
        new_infos[new_page].height,
    )
    old_nets = set(
        _semantic_nets(old_tokens_by_page.get(old_page, []), old_box)
    )
    new_nets = set(
        _semantic_nets(new_tokens_by_page.get(new_page, []), new_box)
    )
    nets = sorted(
        net
        for net in old_nets & new_nets
        if not re.fullmatch(r"\d+(?:\.\d+)?V", net, re.IGNORECASE)
        and re.match(
            r"^(?:VCC|VDD|GND|AVCC|AGND|DVCC|DGND|NET|NL|USB|"
            r"AUDIO|TV|HDMI|JTAG|WIFI|CPU|EPHY|SDC|IR|TX|RX|RTC|"
            r"VBUS|SCK|AV|OUT|IN)",
            net,
            re.IGNORECASE,
        )
    )
    base_confidence = 0.72 if scope == "cross-page" else 0.7
    confidence = min(
        0.99,
        base_confidence
        + min(0.14, 0.035 * (len(refs) - config.relocation_min_refs))
        + max(0.0, 0.09 - residual / 100.0),
    )
    return Relocation(
        old_page=old_page,
        new_page=new_page,
        old_box=old_box,
        new_box=new_box,
        old_content_box=old_ref_box,
        new_content_box=new_ref_box,
        refdes=tuple(sorted(refs)),
        nets=tuple(nets[:8]),
        translation=translation,
        residual=residual,
        confidence=confidence,
        scope=scope,
    )


def _overlaps_either(box: Box, left: Box, right: Box, padding: float = 6.0) -> bool:
    return (
        box.intersection(left.expand(padding)) is not None
        or box.intersection(right.expand(padding)) is not None
    )


def detect_relocations(
    page_results: list[PageResult],
    old_tokens_by_page: dict[int, list[TextToken]],
    new_tokens_by_page: dict[int, list[TextToken]],
    old_infos: list[PageInfo],
    new_infos: list[PageInfo],
    config: CompareConfig,
) -> list[Relocation]:
    refs_old = {
        page: _semantic_refs(tokens)
        for page, tokens in old_tokens_by_page.items()
    }
    refs_new = {
        page: _semantic_refs(tokens)
        for page, tokens in new_tokens_by_page.items()
    }
    candidates: list[Relocation] = []
    seen_groups: set[tuple[int, int, frozenset[str], str]] = set()

    # Same-page relocation: the same labelled parts survive, but their local
    # coordinate system has moved far enough that local diff cannot pair them.
    for result in page_results:
        old_page = result.pair.old_page
        new_page = result.pair.new_page
        if (
            old_page is None
            or new_page is None
            or old_page != new_page
        ):
            continue
        old_refs = refs_old.get(old_page, {})
        new_refs = refs_new.get(new_page, {})
        shared = set(old_refs) & set(new_refs)
        moving = {
            ref
            for ref in shared
            if math.dist(old_refs[ref].center, new_refs[ref].center)
            >= config.relocation_min_displacement_pt
        }
        for refs, translation, residual in _translation_groups(
            moving,
            old_refs,
            new_refs,
            config.relocation_translation_tolerance_pt,
        ):
            average_motion = sum(
                math.hypot(
                    translation[0],
                    translation[1],
                )
                for _ in refs
            ) / max(1, len(refs))
            if average_motion < config.relocation_min_displacement_pt:
                continue
            key = (old_page, new_page, frozenset(refs), "same-page")
            if key in seen_groups:
                continue
            seen_groups.add(key)
            relocation = _make_relocation(
                old_page=old_page,
                new_page=new_page,
                refs=refs,
                old_refs=old_refs,
                new_refs=new_refs,
                translation=translation,
                residual=residual,
                old_tokens_by_page=old_tokens_by_page,
                new_tokens_by_page=new_tokens_by_page,
                old_infos=old_infos,
                new_infos=new_infos,
                config=config,
                scope="same-page",
            )
            if relocation is not None:
                candidates.append(relocation)

    # Cross-page relocation: labels disappear from the paired destination page
    # and appear together on a different page with a compatible displacement.
    for source in page_results:
        old_page = source.pair.old_page
        source_new_page = source.pair.new_page
        if old_page is None or source_new_page is None:
            continue
        old_refs = refs_old.get(old_page, {})
        if not old_refs:
            continue
        refs_in_source_counterpart = set(refs_new.get(source_new_page, {}))
        old_only = set(old_refs) - refs_in_source_counterpart
        if len(old_only) < config.relocation_min_refs:
            continue

        for destination in page_results:
            new_page = destination.pair.new_page
            destination_old_page = destination.pair.old_page
            if new_page is None or destination_old_page is None or new_page == old_page:
                continue
            new_refs = refs_new.get(new_page, {})
            refs_in_destination_counterpart = set(
                refs_old.get(destination_old_page, {})
            )
            new_only = set(new_refs) - refs_in_destination_counterpart
            shared = old_only & new_only
            if len(shared) < config.relocation_min_refs:
                continue

            for refs, translation, residual in _translation_groups(
                shared,
                old_refs,
                new_refs,
                config.relocation_translation_tolerance_pt,
            ):
                if len(refs) < config.relocation_min_refs:
                    continue
                key = (old_page, new_page, frozenset(refs), "cross-page")
                if key in seen_groups:
                    continue
                seen_groups.add(key)
                relocation = _make_relocation(
                    old_page=old_page,
                    new_page=new_page,
                    refs=refs,
                    old_refs=old_refs,
                    new_refs=new_refs,
                    translation=translation,
                    residual=residual,
                    old_tokens_by_page=old_tokens_by_page,
                    new_tokens_by_page=new_tokens_by_page,
                    old_infos=old_infos,
                    new_infos=new_infos,
                    config=config,
                    scope="cross-page",
                )
                if relocation is not None:
                    candidates.append(relocation)

    # Collapse groups that describe the same moved block through overlapping refs.
    merged: list[Relocation] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (-len(item.refdes), item.old_page, item.new_page),
    ):
        target = next(
            (
                relocation
                for relocation in merged
                if relocation.old_page == candidate.old_page
                and relocation.new_page == candidate.new_page
                and relocation.scope == candidate.scope
                and (
                    set(relocation.refdes) & set(candidate.refdes)
                    or relocation.old_box.iou(candidate.old_box) >= 0.25
                    or relocation.new_box.iou(candidate.new_box) >= 0.25
                )
            ),
            None,
        )
        if target is None:
            merged.append(candidate)
    logger.info("Detected %s relocation candidate(s)", len(merged))
    return merged


def suppress_relocated_content(
    page_results: list[PageResult],
    relocations: list[Relocation],
) -> None:
    if not relocations:
        return
    for result in page_results:
        old_page = result.pair.old_page
        new_page = result.pair.new_page
        kept_text: list[TextDelta] = []
        for delta in result.text_deltas:
            suppress = False
            for relocation in relocations:
                if (
                    old_page == relocation.old_page
                    and new_page != relocation.new_page
                    and delta.old_box is not None
                    and _overlaps_either(
                        delta.old_box,
                        relocation.old_box,
                        relocation.old_box,
                    )
                ):
                    suppress = True
                    break
                if (
                    new_page == relocation.new_page
                    and old_page != relocation.old_page
                    and delta.new_box is not None
                    and _overlaps_either(
                        delta.new_box,
                        relocation.new_box,
                        relocation.new_box,
                    )
                ):
                    suppress = True
                    break
                if (
                    old_page == relocation.old_page
                    and new_page == relocation.new_page
                    and (
                        (
                            delta.old_box is not None
                            and _overlaps_either(
                                delta.old_box,
                                relocation.old_box,
                                relocation.old_box,
                            )
                        )
                        or (
                            delta.new_box is not None
                            and _overlaps_either(
                                delta.new_box,
                                relocation.new_box,
                                relocation.new_box,
                            )
                        )
                    )
                ):
                    suppress = True
                    break
            if not suppress:
                kept_text.append(delta)
        result.text_deltas = kept_text

        kept_visual: list[VisualRegion] = []
        for region in result.visual_regions:
            suppress = any(
                (
                    old_page == relocation.old_page
                    and _overlaps_either(
                        region.old_box,
                        relocation.old_box,
                        relocation.old_box,
                    )
                )
                or (
                    new_page == relocation.new_page
                    and _overlaps_either(
                        region.new_box,
                        relocation.new_box,
                        relocation.new_box,
                    )
                )
                for relocation in relocations
            )
            if not suppress:
                kept_visual.append(region)
        result.visual_regions = kept_visual


def relocation_changes(
    page_results: list[PageResult],
    relocations: list[Relocation],
) -> list[Change]:
    changes = []
    for index, relocation in enumerate(relocations, start=1):
        destination_index = next(
            (
                result_index
                for result_index, result in enumerate(page_results)
                if result.pair.new_page == relocation.new_page
            ),
            relocation.new_page,
        )
        visible_refs = [
            ref
            for ref in relocation.refdes
            if not re.fullmatch(r"[CR]\d{4}", ref)
        ]
        refs = visible_refs or list(relocation.refdes)
        changes.append(
            Change(
                change_id=f"P{relocation.new_page + 1:02d}-M{index:03d}",
                page_index=destination_index,
                old_page=relocation.old_page,
                new_page=relocation.new_page,
                change_type="block_move",
                title=(
                    "局部电路移动"
                    + (f"：{', '.join(refs[:6])}" if refs else "")
                ),
                before=f"第 {relocation.old_page + 1} 页",
                after=f"第 {relocation.new_page + 1} 页",
                old_box=relocation.old_content_box,
                new_box=relocation.new_content_box,
                confidence=relocation.confidence,
                reason=(
                    (
                        "Same-page component set and relative layout match; "
                        if relocation.scope == "same-page"
                        else "Cross-page component set and relative layout match; "
                    )
                    +
                    f"translation=({relocation.translation[0]:.1f}, "
                    f"{relocation.translation[1]:.1f}) pt, "
                    f"residual={relocation.residual:.1f} pt"
                ),
                refdes=list(refs[:8]),
                nets=list(relocation.nets),
                evidence=[
                    f"relocation:{relocation.scope}",
                    f"refs:{len(relocation.refdes)}",
                    f"residual:{relocation.residual:.2f}",
                ],
            )
        )
    return changes
