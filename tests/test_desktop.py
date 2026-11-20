from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from schemadiff.desktop import DesktopServer, application_data_dir


def test_desktop_server_starts_ephemeral_loopback_server(tmp_path: Path) -> None:
    with DesktopServer(tmp_path / "desktop-data") as server:
        assert server.url.startswith("http://127.0.0.1:")
        with urllib.request.urlopen(f"{server.url}api/health", timeout=3) as response:
            payload = json.load(response)
        assert payload["status"] == "ok"
        assert payload["offline"] is True


def test_application_data_dir_can_be_overridden(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SCHEMADIFF_DESKTOP_DATA", str(tmp_path / "custom"))
    assert application_data_dir() == (tmp_path / "custom").resolve()
