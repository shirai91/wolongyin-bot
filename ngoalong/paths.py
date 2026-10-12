"""Where the app's files live, from source and in the PyInstaller build (docs/06 Packaging).

* ``APP_ROOT``: the folder holding the user-editable files: ``profiles/``, ``settings.toml``,
  ``logs/`` and ``data/``. From source it is the project root; in the
  one-folder build it is the folder of ``AutoNgoaLong.exe`` (``profiles/`` is copied next to the exe,
  so templates and offsets stay editable without rebuilding).
* ``BUNDLE_ROOT``: read-only data bundled inside the build (``_internal/`` = ``sys._MEIPASS``),
  for example the self-test screenshots. From source it equals ``APP_ROOT``.

This module imports nothing from the package, so every module can use it.
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """True inside the PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundle_root() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if is_frozen() and meipass:
        return Path(meipass)
    return app_root()


APP_ROOT = app_root()
BUNDLE_ROOT = bundle_root()
PROFILES_DIR = APP_ROOT / "profiles"
