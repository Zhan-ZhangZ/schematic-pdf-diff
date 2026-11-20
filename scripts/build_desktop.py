#!/usr/bin/env python3
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"
DIST = ROOT / "dist"
WORK = ROOT / "build" / "desktop"


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def architecture() -> str:
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64"}:
        return "x64"
    return machine


def archive_windows() -> Path:
    source = DIST / "ElectronicSchematicPdfDiff.exe"
    if not source.exists():
        raise FileNotFoundError(source)
    output = RELEASE / f"electronic-schematic-pdf-diff-windows-{architecture()}.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(source, source.name)
        bundle.write(ROOT / "README.md", "README.md")
    return output


def archive_macos() -> list[Path]:
    app = DIST / "Electronic Schematic PDF Diff.app"
    if not app.exists():
        raise FileNotFoundError(app)
    run("codesign", "--force", "--deep", "--sign", "-", str(app))
    output = RELEASE / f"electronic-schematic-pdf-diff-macos-{architecture()}.zip"
    run(
        "ditto",
        "-c",
        "-k",
        "--sequesterRsrc",
        "--keepParent",
        str(app),
        str(output),
    )
    staging = WORK / "dmg"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    run("ditto", str(app), str(staging / app.name))
    (staging / "Applications").symlink_to("/Applications", target_is_directory=True)
    shutil.copy2(ROOT / "packaging" / "MACOS-README.txt", staging / "README.txt")
    dmg = RELEASE / f"electronic-schematic-pdf-diff-macos-{architecture()}.dmg"
    run(
        "hdiutil",
        "create",
        "-volname",
        "Electronic Schematic PDF Diff",
        "-srcfolder",
        str(staging),
        "-ov",
        "-format",
        "UDZO",
        str(dmg),
    )
    shutil.rmtree(staging)
    return [output, dmg]


def smoke_test() -> None:
    if sys.platform == "darwin":
        executable = (
            DIST
            / "Electronic Schematic PDF Diff.app"
            / "Contents"
            / "MacOS"
            / "ElectronicSchematicPdfDiff"
        )
    else:
        executable = DIST / "ElectronicSchematicPdfDiff.exe"
    if not executable.exists():
        raise FileNotFoundError(executable)
    data_dir = WORK / "smoke-data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    subprocess.run(
        [str(executable), "--smoke-test", "--data-dir", str(data_dir)],
        cwd=ROOT,
        check=True,
        timeout=60,
    )


def main() -> int:
    RELEASE.mkdir(parents=True, exist_ok=True)
    if DIST.exists():
        shutil.rmtree(DIST)
    run(sys.executable, str(ROOT / "scripts" / "make_desktop_icons.py"))
    run(
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST),
        "--workpath",
        str(WORK),
        str(ROOT / "packaging" / "schemadiff-desktop.spec"),
    )
    smoke_test()
    if sys.platform == "win32":
        outputs = [archive_windows()]
    elif sys.platform == "darwin":
        outputs = archive_macos()
    else:
        raise SystemExit("Desktop packaging is currently supported on Windows and macOS.")
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
