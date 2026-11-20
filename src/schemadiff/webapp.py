from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, redirect, request, send_from_directory
from werkzeug.serving import make_server

from . import __version__
from .compatibility import DocumentMismatchError
from .config import CompareConfig
from .pipeline import compare_documents

logger = logging.getLogger("schemadiff.webapp")

MAX_UPLOAD_BYTES = 250 * 1024 * 1024
JOB_ID_RE = re.compile(r"^[a-f0-9]{12}$")
ALLOWED_DPI = {150, 180, 220, 300}
ALLOWED_ALIGNMENT = {"auto", "translation", "affine", "off"}
ALLOWED_OCR = {"auto", "always", "never"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_original_name(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    return Path(value.replace("\\", "/")).name or fallback


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


class WebJobManager:
    def __init__(self, root: Path, max_workers: int = 1, prefix: str = ""):
        self.root = root.resolve()
        self.prefix = prefix
        self.jobs_dir = self.root / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, max_workers),
            thread_name_prefix="schemadiff-job",
        )
        self._load_existing()

    def _load_existing(self) -> None:
        for metadata_path in self.jobs_dir.glob("*/job.json"):
            try:
                job = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if job.get("status") in {"queued", "running"}:
                job["status"] = "error"
                job["phase"] = "error"
                job["phase_label"] = "服务重启导致任务中断"
                job["error"] = "处理服务在任务完成前退出，请重新提交。"
                _write_json(metadata_path, job)
            self._jobs[job["job_id"]] = job

    def _job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def _job_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "job.json"

    def _update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            job.update(fields)
            job["updated_at"] = _utc_now()
            _write_json(self._job_path(job_id), job)
            return dict(job)

    def _append_event(self, job_id: str, event: dict[str, Any]) -> None:
        path = self._job_dir(job_id) / "events.jsonl"
        payload = {"timestamp": _utc_now(), **event}
        with self._lock:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job is not None else None

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda item: item.get("updated_at") or item.get("created_at") or "",
                reverse=True,
            )
            return [
                {
                    key: job.get(key)
                    for key in (
                        "job_id",
                        "status",
                        "phase_label",
                        "progress",
                        "created_at",
                        "updated_at",
                        "completed_at",
                        "old_name",
                        "new_name",
                        "summary",
                        "error",
                        "links",
                    )
                }
                for job in jobs[: max(1, min(limit, 200))]
            ]

    def create_job(
        self,
        old_file,
        new_file,
        *,
        dpi: int,
        alignment: str,
        ocr: str,
        ocr_language: str,
    ) -> dict[str, Any]:
        old_name = _safe_original_name(old_file.filename, "old.pdf")
        new_name = _safe_original_name(new_file.filename, "new.pdf")
        if not old_name.lower().endswith(".pdf") or not new_name.lower().endswith(".pdf"):
            raise ValueError("只支持 PDF 文件。")

        job_id = uuid.uuid4().hex[:12]
        job_dir = self._job_dir(job_id)
        while job_dir.exists():
            job_id = uuid.uuid4().hex[:12]
            job_dir = self._job_dir(job_id)
        input_dir = job_dir / "inputs"
        old_input_dir = input_dir / "old"
        new_input_dir = input_dir / "new"
        result_dir = job_dir / "result"
        old_input_dir.mkdir(parents=True)
        new_input_dir.mkdir(parents=True)
        result_dir.mkdir(parents=True)
        old_path = old_input_dir / old_name
        new_path = new_input_dir / new_name
        old_file.save(old_path)
        new_file.save(new_path)

        try:
            for path in (old_path, new_path):
                size = path.stat().st_size
                if size <= 0 or size > MAX_UPLOAD_BYTES:
                    raise ValueError("每个 PDF 必须大于 0 且不超过 250 MB。")
                with path.open("rb") as stream:
                    if stream.read(5) != b"%PDF-":
                        raise ValueError("上传文件不是有效的 PDF。")
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise

        config = CompareConfig(
            dpi=dpi,
            alignment_mode=alignment,
            ocr_mode=ocr,
            ocr_language=ocr_language,
        )
        job = {
            "job_id": job_id,
            "status": "queued",
            "phase": "queued",
            "phase_label": "等待处理",
            "progress": 44,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "old_name": old_name,
            "new_name": new_name,
            "config": config.to_dict(),
            "summary": None,
            "changes": [],
            "error": None,
            "links": self.links(job_id),
        }
        with self._lock:
            self._jobs[job_id] = job
            _write_json(self._job_path(job_id), job)
        self._append_event(job_id, {"event": "queued", "old": old_name, "new": new_name})
        self._executor.submit(
            self._run_job,
            job_id,
            old_path,
            new_path,
            result_dir,
            config,
        )
        return dict(job)

    def links(self, job_id: str) -> dict[str, str]:
        run_base = f"{self.prefix}/runs/{job_id}"
        return {
            "report": f"{run_base}/report.html",
            "pdf": f"{run_base}/changes.pdf",
            "results": f"{run_base}/results.json",
            "status": f"{self.prefix}/api/jobs/{job_id}",
        }

    def _phase_progress(self, phase: str, current: int, total: int) -> tuple[str, float]:
        safe_total = max(1, total)
        fraction = min(1.0, max(0.0, current / safe_total))
        if phase == "indexing":
            return "正在建立页面指纹", 5 + 35 * fraction
        if phase == "matching":
            return "正在配对页面", 42
        if phase == "comparing":
            return "正在比对页面内容", 46 + 44 * fraction
        if phase == "reporting":
            return "正在生成报告", 94
        return "正在处理", 50

    def _run_job(
        self,
        job_id: str,
        old_path: Path,
        new_path: Path,
        result_dir: Path,
        config: CompareConfig,
    ) -> None:
        self._update(
            job_id,
            status="running",
            phase="starting",
            phase_label="正在启动比对",
            progress=46,
        )
        self._append_event(job_id, {"event": "started", "config": config.to_dict()})

        def progress(phase: str, current: int, total: int) -> None:
            label, percent = self._phase_progress(phase, current, total)
            self._update(
                job_id,
                status="running",
                phase=phase,
                phase_label=label,
                progress=percent,
            )
            self._append_event(
                job_id,
                {
                    "event": "progress",
                    "phase": phase,
                    "current": current,
                    "total": total,
                    "progress": percent,
                },
            )

        try:
            result = compare_documents(
                old_path,
                new_path,
                result_dir,
                config=config,
                progress=progress,
            )
            changes = []
            for page in result["pages"]:
                page_number = (
                    page["pair"]["old_page"]
                    if page["pair"]["old_page"] is not None
                    else page["pair"]["new_page"]
                )
                for change in page["changes"]:
                    changes.append(
                        {
                            "id": change["change_id"],
                            "page": None if page_number is None else page_number + 1,
                            "type": change["change_type"],
                            "title": change["title"],
                            "confidence": change["confidence"],
                        }
                    )
            self._update(
                job_id,
                status="complete",
                phase="complete",
                phase_label="比对完成",
                progress=100,
                summary=result["summary"],
                changes=changes,
                completed_at=_utc_now(),
            )
            self._append_event(
                job_id,
                {
                    "event": "complete",
                    "summary": result["summary"],
                    "change_count": len(changes),
                },
            )
        except DocumentMismatchError as exc:
            self._update(
                job_id,
                status="rejected",
                phase="rejected",
                phase_label="文件不匹配",
                progress=100,
                error=str(exc),
                completed_at=_utc_now(),
            )
            self._append_event(
                job_id,
                {
                    "event": "rejected",
                    "error": str(exc),
                    "compatibility": exc.compatibility.to_dict(),
                },
            )
        except Exception as exc:
            logger.exception("Web comparison job %s failed", job_id)
            self._update(
                job_id,
                status="error",
                phase="error",
                phase_label="处理失败",
                progress=100,
                error=str(exc) or exc.__class__.__name__,
                completed_at=_utc_now(),
            )
            self._append_event(
                job_id,
                {
                    "event": "error",
                    "error": str(exc) or exc.__class__.__name__,
                },
            )

    def result_dir(self, job_id: str) -> Path | None:
        if not JOB_ID_RE.fullmatch(job_id):
            return None
        job = self.get(job_id)
        if job is None:
            return None
        return self._job_dir(job_id) / "result"

def _configure_web_logging(output_root: Path) -> None:
    app_logger = logging.getLogger("schemadiff")
    app_logger.setLevel(logging.INFO)
    if any(getattr(handler, "_schemadiff_web", False) for handler in app_logger.handlers):
        return
    log_dir = output_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_dir / "web.log", mode="a", encoding="utf-8")
    handler._schemadiff_web = True  # type: ignore[attr-defined]
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    handler.setLevel(logging.INFO)
    app_logger.addHandler(handler)
    for noisy in ("pdfminer", "pdfplumber", "PIL", "pypdf"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def create_app(
    output_root: str | Path | None = None,
    *,
    max_workers: int = 1,
    prefix: str = "",
) -> Flask:
    normalized_prefix = ""
    if prefix.strip("/"):
        normalized_prefix = "/" + prefix.strip("/")
    root = Path(output_root) if output_root else Path("output") / "web"
    root.mkdir(parents=True, exist_ok=True)
    _configure_web_logging(root)
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 2 * MAX_UPLOAD_BYTES + 4 * 1024 * 1024
    app.json.ensure_ascii = False
    manager = WebJobManager(
        root,
        max_workers=max_workers,
        prefix=normalized_prefix,
    )
    app.extensions["schemadiff_manager"] = manager

    def route(path: str) -> str:
        return f"{normalized_prefix}{path}"

    @app.after_request
    def security_headers(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get(route("/"))
    def index():
        content = (files("schemadiff.web") / "index.html").read_text(
            encoding="utf-8"
        ).replace("__BASE_PATH__", normalized_prefix)
        return Response(content, mimetype="text/html")

    if normalized_prefix:
        @app.get("/")
        def root_redirect():
            return redirect(normalized_prefix + "/")

        @app.get(normalized_prefix)
        def prefix_redirect():
            return redirect(normalized_prefix + "/")

    @app.get(route("/api/health"))
    def health():
        return jsonify(
            {
                "status": "ok",
                "version": __version__,
                "offline": True,
                "tesseract": shutil.which("tesseract"),
                "jobs_dir": str(manager.jobs_dir),
            }
        )

    @app.post(route("/api/jobs"))
    def create_job():
        old_file = request.files.get("old")
        new_file = request.files.get("new")
        if old_file is None or new_file is None:
            return jsonify({"error": "请同时上传旧版和新版 PDF。"}), 400
        try:
            dpi = int(request.form.get("dpi", "180"))
            alignment = request.form.get("alignment", "auto")
            ocr = request.form.get("ocr", "auto")
            ocr_language = request.form.get("ocr_language", "eng")
            if dpi not in ALLOWED_DPI:
                raise ValueError("比较分辨率不受支持。")
            if alignment not in ALLOWED_ALIGNMENT:
                raise ValueError("配准模式不受支持。")
            if ocr not in ALLOWED_OCR:
                raise ValueError("OCR 模式不受支持。")
            job = manager.create_job(
                old_file,
                new_file,
                dpi=dpi,
                alignment=alignment,
                ocr=ocr,
                ocr_language=ocr_language,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            logger.exception("Failed to create comparison job")
            return jsonify({"error": "无法创建处理任务，请检查文件后重试。"}), 500
        return jsonify(job), 202

    @app.get(route("/api/jobs"))
    def list_jobs():
        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            return jsonify({"error": "记录数量参数无效。"}), 400
        return jsonify({"jobs": manager.list_jobs(limit=limit)})

    @app.get(route("/api/jobs/<job_id>"))
    def get_job(job_id: str):
        if not JOB_ID_RE.fullmatch(job_id):
            return jsonify({"error": "任务编号无效。"}), 404
        job = manager.get(job_id)
        if job is None:
            return jsonify({"error": "任务不存在。"}), 404
        return jsonify(job)

    @app.get(route("/runs/<job_id>/<path:filename>"))
    def run_file(job_id: str, filename: str):
        result_dir = manager.result_dir(job_id)
        if result_dir is None or not result_dir.exists():
            return jsonify({"error": "任务结果不存在。"}), 404
        response = send_from_directory(result_dir, filename, conditional=True)
        if filename.endswith("report.html"):
            csp = response.headers.get(
                "Content-Security-Policy",
                "default-src 'none'; img-src 'self' data: file:; "
                "style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "connect-src 'none'",
            )
            response.headers["Content-Security-Policy"] = (
                csp + "; frame-ancestors 'self'"
            )
        return response

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify({"error": "上传文件过大，单个 PDF 限制为 250 MB。"}), 413

    app.extensions["schemadiff_output_root"] = root
    return app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local web UI for schematic PDF diff")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=8899, help="Bind port")
    parser.add_argument(
        "--prefix",
        default="",
        help="URL prefix such as /schemadiff-abc123",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("output") / "web",
        help="Web upload and result directory",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    app = create_app(args.output_root, prefix=args.prefix)
    server = make_server(args.host, args.port, app, threaded=True)
    address = f"http://{args.host}:{args.port}{args.prefix.rstrip('/')}/"
    print(f"Schematic PDF Diff web UI: {address}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
