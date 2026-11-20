from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from .compatibility import DocumentMismatchError
from .config import CompareConfig
from .logging_setup import configure_logging
from .pipeline import compare_documents


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schemadiff",
        description="Compare two schematic PDF revisions entirely offline.",
    )
    parser.add_argument("old", type=Path, help="Original PDF")
    parser.add_argument("new", type=Path, help="Revised PDF")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output directory",
    )
    parser.add_argument("--dpi", type=int, default=180, help="Comparison render DPI")
    parser.add_argument(
        "--display-dpi",
        type=int,
        default=110,
        help="Report page render DPI",
    )
    parser.add_argument(
        "--alignment",
        choices=["auto", "translation", "affine", "off"],
        default="auto",
        help="Registration mode",
    )
    parser.add_argument(
        "--ocr",
        choices=["auto", "always", "never"],
        default="auto",
        help="Local Tesseract OCR mode",
    )
    parser.add_argument(
        "--ocr-language",
        default="eng",
        help="Tesseract language list, for example eng+chi_sim",
    )
    parser.add_argument(
        "--visual-threshold",
        type=int,
        default=20,
        help="Grayscale difference threshold from 1 to 255",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.output is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        args.output = Path("output") / f"compare-{stamp}"
    configure_logging(args.output, verbose=args.verbose)
    if not 1 <= args.visual_threshold <= 255:
        print("--visual-threshold must be between 1 and 255", file=sys.stderr)
        return 2
    config = CompareConfig(
        dpi=max(72, args.dpi),
        display_dpi=max(48, args.display_dpi),
        alignment_mode=args.alignment,
        ocr_mode=args.ocr,
        ocr_language=args.ocr_language,
        visual_threshold=args.visual_threshold,
    )
    try:
        result = compare_documents(
            args.old,
            args.new,
            args.output,
            config=config,
            progress=lambda phase, current, total: logging.getLogger(
                "schemadiff.progress"
            ).debug("%s %s/%s", phase, current, total),
        )
    except DocumentMismatchError as exc:
        print(str(exc), file=sys.stderr)
        logging.getLogger(__name__).warning("Input documents rejected: %s", exc)
        return 3
    except Exception:
        logging.getLogger(__name__).exception("Comparison failed")
        return 1
    print(
        f"Changed pages: {result['summary']['changed_pages']}  "
        f"Review items: {result['summary']['change_count']}"
    )
    print(f"Report: {result['outputs']['report_html']}")
    print(f"JSON:   {result['outputs']['results_json']}")
    print(f"PDF:    {result['outputs']['changes_pdf']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
