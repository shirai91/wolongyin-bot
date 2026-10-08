"""Setup-phase sanity tests: package imports, dependencies, profile coords split."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COORDS_DIR = ROOT / "profiles" / "ngoalongviet" / "coords"


def test_package_imports() -> None:
    import ngoalong

    assert ngoalong.__version__


@pytest.mark.parametrize(
    "module",
    [
        "ngoalong.config",
        "ngoalong.core",
        "ngoalong.core.drivers",
        "ngoalong.engine",
        "ngoalong.features",
        "ngoalong.debug",
        "ngoalong.tools",
        "ngoalong.ui",
        "ngoalong.ui.tabs",
        "ngoalong.ui.widgets",
    ],
)
def test_subpackages_import(module: str) -> None:
    importlib.import_module(module)


@pytest.mark.parametrize(
    "module", ["win32gui", "numpy", "cv2", "PySide6.QtWidgets", "pydantic", "yaml", "tomli_w", "requests"]
)
def test_dependencies_import(module: str) -> None:
    importlib.import_module(module)


def test_coords_one_key_per_file() -> None:
    """Each profile coords file holds exactly the top-level key named after it."""
    paths = sorted(COORDS_DIR.glob("*.yaml"))
    assert paths
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert list(data) == [path.stem], f"{path.name} must hold exactly the key '{path.stem}'"
