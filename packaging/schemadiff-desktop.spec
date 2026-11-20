import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path(SPECPATH).resolve().parent
ASSETS = ROOT / "packaging" / "assets"
datas = collect_data_files("schemadiff.web")
datas += [
    (str(ASSETS / "icon.png"), "packaging/assets"),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
]
if sys.platform == "darwin":
    hiddenimports = collect_submodules("webview.platforms.cocoa")
elif sys.platform == "win32":
    hiddenimports = [
        *collect_submodules("webview.platforms.winforms"),
        *collect_submodules("webview.platforms.edgechromium"),
    ]
else:
    hiddenimports = []

a = Analysis(
    [str(ROOT / "packaging" / "desktop_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

icon_name = "icon.icns" if sys.platform == "darwin" else "icon.ico"
if sys.platform == "darwin":
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="ElectronicSchematicPdfDiff",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(ASSETS / icon_name),
    )
    collected = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="ElectronicSchematicPdfDiff",
    )
    app = BUNDLE(
        collected,
        name="Electronic Schematic PDF Diff.app",
        icon=str(ASSETS / "icon.icns"),
        bundle_identifier="dev.schematicpdfdiff.desktop",
        info_plist={
            "CFBundleDisplayName": "Electronic Schematic PDF Diff",
            "CFBundleShortVersionString": "0.1.2",
            "CFBundleVersion": "1",
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="ElectronicSchematicPdfDiff",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(ASSETS / icon_name),
    )
