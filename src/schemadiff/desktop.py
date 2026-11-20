from __future__ import annotations

import argparse
import logging
import os
import platform
import socket
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path

from werkzeug.serving import BaseWSGIServer, make_server

from .logging_setup import configure_logging
from .webapp import create_app

APP_TITLE = "电子原理图PDF差异扫描"
APP_DIR_NAME = "ElectronicSchematicPdfDiff"
logger = logging.getLogger(__name__)


def application_data_dir() -> Path:
    override = os.environ.get("SCHEMADIFF_DESKTOP_DATA")
    if override:
        return Path(override).expanduser().resolve()
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_DIR_NAME
        return Path.home() / "AppData" / "Local" / APP_DIR_NAME
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME


@dataclass
class DesktopServer:
    output_dir: Path

    def start(self) -> "DesktopServer":
        self.output_dir.mkdir(parents=True, exist_ok=True)
        app = create_app(self.output_dir, max_workers=1, prefix="")
        self._server: BaseWSGIServer = make_server(
            "127.0.0.1",
            0,
            app,
            threaded=True,
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="schemadiff-desktop-server",
            daemon=True,
        )
        self._thread.start()
        logger.info("Desktop local server started on port %s", self.port)
        return self

    @property
    def port(self) -> int:
        server = getattr(self, "_server", None)
        if server is None:
            raise RuntimeError("Desktop server has not started")
        return int(server.server_port)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def stop(self) -> None:
        server = getattr(self, "_server", None)
        thread = getattr(self, "_thread", None)
        if server is not None:
            server.shutdown()
            server.server_close()
            self._server = None
        if thread is not None:
            thread.join(timeout=3)
            self._thread = None
        logger.info("Desktop local server stopped")

    def __enter__(self) -> "DesktopServer":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


def _wait_for_server(server: DesktopServer, timeout: float = 5.0) -> None:
    deadline = threading.Event()
    for _ in range(max(1, int(timeout * 20))):
        try:
            with socket.create_connection(
                ("127.0.0.1", server.port),
                timeout=0.1,
            ):
                return
        except OSError:
            deadline.wait(0.05)
    raise RuntimeError(f"本地服务未能在 {timeout:.0f} 秒内启动")


def _show_startup_error(message: str, log_path: Path | None) -> None:
    detail = message if log_path is None else f"{message}\n\n日志：{log_path}"
    try:
        if platform.system() == "Windows":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, detail, APP_TITLE, 0x10)
        elif platform.system() == "Darwin":
            import subprocess

            escaped = detail.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display alert "{APP_TITLE}" message "{escaped}" as critical',
                ],
                check=False,
            )
    except Exception:
        print(detail, file=sys.stderr)


def run_desktop(
    *,
    data_dir: Path | None = None,
    debug: bool = False,
) -> None:
    root = (data_dir or application_data_dir()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    log_path = configure_logging(root, verbose=debug, console=debug)
    server = DesktopServer(root)
    try:
        server.start()
        _wait_for_server(server)

        import webview

        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        storage_path = root / "webview"
        storage_path.mkdir(parents=True, exist_ok=True)
        webview.create_window(
            APP_TITLE,
            server.url,
            width=1280,
            height=860,
            min_size=(900, 640),
            background_color="#f3f6f8",
            text_select=True,
            zoomable=True,
        )
        webview.start(
            debug=debug,
            private_mode=False,
            storage_path=str(storage_path),
        )
    except Exception as exc:
        logger.exception("Desktop application failed")
        crash_path = root / "startup-error.log"
        crash_path.write_text(traceback.format_exc(), encoding="utf-8")
        _show_startup_error(str(exc), log_path)
    finally:
        server.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="schemadiff-desktop")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override the local application data directory.",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    run_desktop(data_dir=args.data_dir, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
