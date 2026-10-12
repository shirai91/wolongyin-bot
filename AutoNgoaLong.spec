# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: one-folder build of AutoNgoaLong v2 (docs/06 Stack: Packaging).

Output: dist/AutoNgoaLong/
    AutoNgoaLong.exe        windowed app (``--selftest`` runs the offline checks)
    _internal/              Python, Qt, OpenCV, the ``ngoalong`` package, selftest/ (corpus frames + labels)
    profiles/               copied next to the exe: parts, screens, navigation and templates stay editable
    HUONG-DAN.md            the Vietnamese user guide (README.md)
    LICENSE.txt             MIT licence

Build with ``.\\build.ps1`` (or ``pyinstaller --noconfirm --clean AutoNgoaLong.spec``).
"""

import shutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve()  # noqa: F821 - defined by PyInstaller
sys.path.insert(0, str(ROOT))

from ngoalong import __version__  # noqa: E402
from ngoalong.selftest import write_bundle  # noqa: E402

APP_NAME = "AutoNgoaLong"

# The corpus for ``--selftest``: the frames of ngoalong.selftest.BUNDLE_FRAMES with their labels, read
# from corpus/live/ on this machine (its PNGs are not in git). Without the corpus the build still
# goes on and the self-test reports that no corpus is bundled.
selftest_dir, selftest_missing = write_bundle(Path(workpath) / "selftest-bundle")  # noqa: F821 - PyInstaller global
selftest_count = len(list(selftest_dir.glob("*.webp")))
if selftest_missing:
    print(f"[spec] WARNING: {len(selftest_missing)} self-test frames are not in corpus/live: {', '.join(selftest_missing)}")
print(f"[spec] self-test corpus: {selftest_count} frames bundled")
selftest_datas = [(str(selftest_dir), "selftest")] if selftest_count else []

# Features, UI tabs and widgets are found with pkgutil at run time: collect every submodule.
hiddenimports = collect_submodules("ngoalong")

a = Analysis(  # noqa: F821
    [str(ROOT / "ngoalong" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=selftest_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "_pytest", "PyInstaller", "IPython", "matplotlib", "pandas", "rapidocr",
              "onnxruntime"],
    noarchive=False,
    optimize=0,
)

# Large binaries the app never loads (~90 MB): OpenCV's FFmpeg video I/O (only imread/matchTemplate
# are used), Qt's software OpenGL fallback, and Qt Quick/QML/PDF/virtual-keyboard pulled in by
# optional plugins (the UI is plain Qt Widgets). Removing a plugin's DLL removes the plugin too.
EXCLUDED_BINARIES = (
    "opencv_videoio_ffmpeg", "opengl32sw.dll", "qt6quick", "qt6qml", "qt6pdf", "qt6virtualkeyboard",
    "qtvirtualkeyboardplugin", "qpdf.dll",
)


def _keep(entry) -> bool:
    name = str(entry[0]).replace("\\", "/").rsplit("/", 1)[-1].lower()
    return not any(name.startswith(x) or name == x for x in EXCLUDED_BINARIES)


a.binaries = [b for b in a.binaries if _keep(b)]
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
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
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

# --- editable files next to the exe -----------------------------------------------------------------
dist_dir = Path(DISTPATH) / APP_NAME  # noqa: F821 - PyInstaller global
profiles_dst = dist_dir / "profiles"
if profiles_dst.exists():
    shutil.rmtree(profiles_dst)
shutil.copytree(ROOT / "profiles", profiles_dst, ignore=shutil.ignore_patterns(".gitkeep", "__pycache__"))
guide = ROOT / "README.md"
if guide.is_file():
    shutil.copy2(guide, dist_dir / "HUONG-DAN.md")
shutil.copy2(ROOT / "LICENSE", dist_dir / "LICENSE.txt")
(dist_dir / "VERSION.txt").write_text(f"{APP_NAME} {__version__}\n", encoding="utf-8")
print(f"[spec] {APP_NAME} {__version__}: profiles copied to {profiles_dst}")
