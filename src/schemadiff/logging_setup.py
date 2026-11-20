from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonlFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event = getattr(record, "event", None)
        if event is not None:
            payload.update(event)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    output_dir: Path,
    *,
    verbose: bool = False,
    console: bool = True,
) -> Path:
    """Configure console and JSONL logging for one run."""
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = output_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "events.jsonl"

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)

    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JsonlFormatter())
    root.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        root.addHandler(console_handler)

    # Keep parser internals out of routine logs. They emit every PDF content
    # stream token at DEBUG and can produce gigabytes of noise on large sheets.
    logging.getLogger("schemadiff").setLevel(logging.DEBUG)
    for noisy in ("pdfminer", "pdfplumber", "PIL", "pypdf"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.captureWarnings(True)
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
    return log_path


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    **fields: Any,
) -> None:
    logger.log(level, message, extra={"event": fields})
