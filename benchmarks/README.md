# Benchmarks

Benchmark PDFs, acceptance workbooks, screenshots, and project-specific mapping
files are intentionally excluded from this repository.

For local testing, keep private benchmark inputs outside the repository or under
ignored paths. Build synthetic fixtures with generic identifiers such as `R1`,
`C2`, `U3`, `VCC`, and `GND`.

The generic evaluator accepts an explicit result file, mapping file, and
workbook path:

```bash
python scripts/evaluate_benchmark.py results.json \
  --mapping /path/to/local-mapping.json \
  --xlsx /path/to/local-acceptance.xlsx
```

Do not commit the referenced local files.
