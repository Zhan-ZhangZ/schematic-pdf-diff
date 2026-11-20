# Open-source research

Research date: 2026-11-20.

## Relevant projects

### Revision Lens

- Repository: https://github.com/akapoor308/revision-lens
- License: MIT.
- Useful ideas:
  - Pair pages by text tokens, a compact visual signature, and document order
    instead of assuming page N matches page N.
  - Use order-preserving dynamic programming so inserted or removed sheets do
    not desynchronize every following page.
  - Flag low-similarity and ambiguous pairs for review instead of silently
    forcing a pairing.
  - Group nearby changed pixels into navigation regions.
  - Keep packaging offline and make the runtime network boundary explicit.
- Limitation relevant here:
  - Translation-only correction and line-level text diff do not identify
    schematic semantics such as `R1 = 10K`.
  - OCR and electrical entity recognition are out of scope.

### kogo

- Repository: https://github.com/ta-061/kogo
- License: AGPL-3.0-only. It was inspected for architectural ideas; no source
  code is copied into this project.
- Useful ideas:
  - Separate word extraction, page alignment, text diff, visual diff,
    annotation diff, rendering, and orchestration.
  - Match exact text lines first, then diff the remaining reading order. This
    suppresses layout-order noise.
  - Compare figures after masking text areas so the same edit is not reported
    twice.
  - Bound rendered pixels and page counts to protect against pathological PDFs.
  - Bake markers into output rather than relying on annotations.
- Limitation relevant here:
  - Its page matching falls back to visual signatures for image-heavy pages but
    does not perform OCR.
  - Generic document text diff does not associate values with schematic
    reference designators.

### PDF Comparison Tool by darkrubiks

- Repository: https://github.com/darkrubiks/pdf-comparison-tool
- Useful ideas:
  - Perform registration on a downscaled proxy, but detect differences at full
    resolution.
  - Reject affine transforms that exceed strict translation, scale, rotation,
    and shear limits.
  - Accept a transform only when it reduces measured disagreement. This makes
    accidental alignment less likely to hide a real edit.
  - Preserve long one-pixel or thin-line components while removing isolated
    specks.
  - Merge nearby change boxes with a spatial grid and union-find.
- Limitation relevant here:
  - It remains a visual-only comparator and does not produce component or net
    semantics.

### HTML Apps PDF Compare

- Repository: https://github.com/ttomohisa/htmlapps-pdf-compare
- License: MIT.
- Useful ideas:
  - Five inspection modes are valuable: diff, side by side, overlay, slider,
    and blink.
  - Expose threshold, tiny-region suppression, and rendered DPI.
  - Block network access in the built artifact with both policy and runtime
    guards.
- Limitation relevant here:
  - No OCR and no domain model for engineering drawings.

### KiCad-aware diffs

- KiDiff: https://github.com/INTI-CMNB/KiDiff
- kicadiff: https://github.com/sksat/kicadiff
- KiRI: https://github.com/leoheck/kiri
- Useful ideas:
  - Component, net, and symbol changes are the right conceptual model.
  - Stable identifiers, such as reference designators, are more useful than
    line numbers or raw PDF objects.
- Constraint:
  - These tools generally require native EDA source files. This project must
    work from PDFs, including scans, so their semantic parsers are not directly
    reusable.

## Consolidated design lessons

1. Page pairing must precede page comparison.
2. Registration must be conservative and measured, not assumed.
3. Text, geometry, and page appearance are complementary evidence.
4. Text changes should be normalized by reading layout first, then associated
   spatially with components and nets.
5. Pixel regions are evidence and navigation aids, not automatically the final
   count of engineering changes.
6. Scanned PDFs require an explicit OCR path and confidence reporting.
7. The output must preserve page coordinates and show crops, because textual
   descriptions alone are not sufficient for review.
8. Offline behavior should be enforced and tested, not merely documented.

## Licensing decision

The implementation takes algorithms and product lessons from these projects but
does not copy AGPL code. The planned runtime libraries are recorded with their
licenses in `THIRD_PARTY_NOTICES.md` once the dependency set is frozen.
