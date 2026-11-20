# Architecture

## Product boundary

The tool accepts two PDF paths and writes one self-contained result directory.
It performs no network requests during a comparison. It may invoke a locally
installed Tesseract binary for scanned pages, but the PDF never leaves the
machine.

## Processing pipeline

```text
PDF A + PDF B
    |
    v
[1] Probe and safety checks
    - encryption, page count, rotation, page boxes, text availability
    - active-content awareness
    |
    v
[2] Page identity and pairing
    - normalized text fingerprint
    - ink thumbnail fingerprint
    - order-preserving dynamic programming
    |
    v
[3] Page extraction
    - text words with PDF-point bounding boxes
    - reading-order lines
    - local OCR fallback when no usable text layer exists
    - raster render at bounded DPI
    |
    v
[4] Registration
    - translation first
    - bounded affine only when it measurably improves alignment
    - record transform and safety decision
    |
    v
[5] Parallel evidence extraction
    A. text changes: exact spans, replacements, additions, deletions
    B. visual changes: thin-line-safe pixel components and grouped regions
    C. future vector changes: line, curve, and symbol primitives
    |
    v
[6] Schematic association and fusion
    - classify reference designator, value, net, pin, annotation, title block
    - associate local evidence with nearest component or net context
    - merge overlapping or functionally adjacent evidence
    |
    v
[7] Reporting
    - results.json with PDF-point coordinates
    - crop before/after evidence
    - side-by-side highlighted pages
    - offline report.html and annotated report PDF
    - run manifest and JSONL logs
```

## Modules

| Module | Responsibility |
| --- | --- |
| `config.py` | Typed run configuration and safety limits |
| `models.py` | Shared dataclasses and serializable result schema |
| `logging_setup.py` | Console and JSONL structured logs |
| `pdf_document.py` | PDF probing, word extraction, rendering, OCR adapter |
| `page_match.py` | Fingerprints and order-preserving page alignment |
| `registration.py` | Conservative translation or affine registration |
| `text_diff.py` | Reading-order and spatial text evidence |
| `visual_diff.py` | Raster evidence, component cleanup, region grouping |
| `schematic.py` | Refdes, value, net, pin, and annotation heuristics |
| `fusion.py` | Evidence association, deduplication, and change descriptions |
| `report.py` | Static HTML, JSON, crops, and report PDF |
| `pipeline.py` | Orchestration, progress, timings, and manifest |
| `cli.py` | Offline command line interface |

Each module logs through its own module logger. The pipeline records phase
start, phase end, duration, item counts, and recoverable warnings.

## Evidence model

The fundamental record is not a raw pixel difference. Each record is a piece of
evidence with:

- page pairing and page numbers
- old and new PDF-point boxes
- evidence source: text, OCR, visual, or later vector
- old and new text where available
- confidence and reason
- association candidates such as `R1`, `C2`, `VCC`, or `J1`

Fusion produces grouped `Change` records while retaining the underlying
evidence. This lets the report explain why a change was reported and lets a
reviewer inspect unavoidable false positives.

## Why a Python CLI plus static HTML

- Python provides mature offline PDF parsing, image processing, testing, and
  packaging tools.
- A static HTML report opens from the local filesystem and needs no server,
  CDN, cloud API, or runtime fetch.
- HTML is suitable for page navigation, crop evidence, zooming, side-by-side
  views, and later slider or blink modes.
- A CLI can also run in batch and CI environments without a desktop GUI.

## Selected initial stack

- Python 3.12.
- `pypdfium2` for bounded local raster rendering.
- `pdfplumber` and `pdfminer.six` for words, characters, and vector primitives.
- `pypdf` for PDF structure and safety probes.
- `OpenCV` and NumPy for registration and image analysis.
- Pillow for crops, annotated pages, and raster PDF output.
- Tesseract CLI for optional offline OCR.
- Pytest for regression tests.

No browser automation, online OCR, telemetry, update client, or remote font
service is used at runtime.

## Change strategy and optimization order

### Phase 1: validated prototype

- Pair pages by order and content.
- Detect text replacements and visual regions on the benchmark PDFs.
- Associate nearby component references and values.
- Emit JSON, crops, and a static HTML report.
- Measure results against a separately supplied local acceptance mapping.

### Phase 2: robustness

- Scan and rotation profiles.
- OCR confidence handling and crop-level OCR refinement.
- Page insertion, removal, size, scale, and rotation regression fixtures.
- Thin-line and bus/connection-specific tolerance.
- Better functional grouping of adjacent symbol and wire changes.

### Phase 3: schematic semantics

- Vector primitive extraction and local stroke matching.
- Pin and net-attachment graph around symbols.
- Net-name relocation and swap detection.
- Component addition, deletion, replacement, and value-change templates.
- Title-block and sheet-identity awareness.

### Phase 4: usability and deployment

- Optional local viewer with overlay, slider, and blink modes.
- Batch and folder comparison.
- Annotated source PDF export without copying active content.
- Offline wheelhouse and reproducible install verification.

## Safety and non-goals

- The tool does not execute PDF JavaScript or launch embedded files.
- It does not certify electrical correctness.
- It does not compare the source EDA design unless source files are provided.
- It does not treat one visual region as exactly one engineering change.
- It reports uncertainty and preserves evidence for manual review.
