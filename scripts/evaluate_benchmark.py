#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from openpyxl import load_workbook


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9.+-]", "", value.casefold())


def text_blob(change: dict) -> str:
    return normalize(
        " ".join(
            [
                change.get("title", ""),
                change.get("before", ""),
                change.get("after", ""),
                *change.get("refdes", []),
                *change.get("nets", []),
                *change.get("evidence", []),
            ]
        )
    )


def matches(change: dict, row: dict) -> bool:
    if change.get("page_index", -1) + 1 != row["page"]:
        return False
    blob = text_blob(change)
    refs = [*row.get("refs", []), *row.get("aliases", [])]
    ref_match = any(normalize(ref) in blob for ref in refs) if refs else False
    before_values = [normalize(value) for value in row.get("before", [])]
    after_values = [normalize(value) for value in row.get("after", [])]
    before_match = not before_values or any(value in blob for value in before_values)
    after_match = not after_values or any(value in blob for value in after_values)
    value_match = before_match and after_match
    alias_match = bool(row.get("match_aliases")) and any(
        normalize(alias) in blob for alias in row.get("aliases", [])
    )
    if row.get("allow_missing_values") and ref_match:
        value_match = True
    return (ref_match or alias_match) and value_match


def read_descriptions(xlsx_path: Path) -> dict[int, str]:
    workbook = load_workbook(xlsx_path, read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    output = {}
    for row in sheet.iter_rows(min_row=2, max_col=4, values_only=True):
        number, _, _, description = row
        if isinstance(number, int) and description:
            output[number] = str(description)
    workbook.close()
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument(
        "--mapping",
        type=Path,
        required=True,
        help="Private acceptance mapping JSON, kept outside the repository.",
    )
    parser.add_argument(
        "--xlsx",
        type=Path,
        required=True,
        help="Private acceptance workbook, kept outside the repository.",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    results = json.loads(args.results.read_text(encoding="utf-8"))
    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    descriptions = read_descriptions(args.xlsx)
    changes = [
        change
        for page in results["pages"]
        for change in page.get("changes", [])
    ]
    rows = []
    matched_changes = set()
    for row in mapping["rows"]:
        expected = descriptions.get(row["id"], "")
        candidates = [change for change in changes if matches(change, row)]
        matched_changes.update(change["change_id"] for change in candidates)
        rows.append(
            {
                "id": row["id"],
                "description": expected,
                "page": row["page"],
                "matched": bool(candidates),
                "changes": [change["change_id"] for change in candidates],
                "evidence_titles": [change["title"] for change in candidates],
                "reference_note": row.get("reference_note"),
            }
        )
    matched = sum(row["matched"] for row in rows)
    extra_changes = [
        {
            "change_id": change["change_id"],
            "title": change["title"],
            "page": change["page_index"] + 1,
        }
        for change in changes
        if change["change_id"] not in matched_changes
    ]
    summary = {
        "expected_rows": len(rows),
        "matched_expected_rows": matched,
        "recall": matched / max(1, len(rows)),
        "reported_changes": len(changes),
        "unmatched_expected_rows": [
            row["id"] for row in rows if not row["matched"]
        ],
        "extra_review_items": extra_changes,
        "reference_conflicts": [
            {
                "id": row["id"],
                "note": row["reference_note"],
            }
            for row in rows
            if row.get("reference_note")
        ],
    }
    payload = {"summary": summary, "rows": rows}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if matched == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
