# Module tracker

Status values: `planned`, `prototype`, `verified`, `hardened`.

| Module | Status | Notes |
| --- | --- | --- |
| Research | verified | Five representative projects reviewed |
| Architecture | verified | Initial pipeline and stack selected |
| Config and logging | verified | Structured JSONL, bounded parser logging, manifest |
| PDF document adapter | verified | Render, words, page probes, local OCR |
| Page matching | verified | Text and visual fingerprints, DP alignment |
| Document compatibility gate | verified | Reject unrelated PDFs; review weak identity evidence |
| Registration | verified | Translation plus bounded affine support |
| Text diff | verified | Reading-order spans and spatial evidence |
| Visual diff | verified | Thin-line-safe masks and grouped regions |
| Schematic association | prototype | Refdes, value, net, and context heuristics |
| Fusion | verified | Grouped semantic and visual evidence on local fixtures |
| Relocation normalization | verified | Same-page and cross-page moves, delete/add suppression |
| HTML/PDF report | verified | Offline static report and side-by-side PDF |
| Benchmark evaluation | verified | Optional local mapping and reference-conflict notes |
| Scanned PDF fixture | verified | Translation/rotation plus local OCR test |
| Chinese documentation | verified | Sanitized public architecture and implementation notes |
| Local web interface | verified | Upload, progress, persistent history, embedded report |
| Comparison output page | verified | Standalone HTML, mouse zoom/pan, 900 DPI evidence, lazy detail layer |

## Next hardening work

- Add local tile registration for warped scans.
- Add vector primitive extraction and net-attachment graph matching.
- Improve crop-level OCR for part numbers and dense pin tables.
- Add more benchmark pairs from different EDA exporters.
- Add page insertion, deletion, and reorder fixtures.
