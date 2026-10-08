"""Shared fixtures: golden screenshot expectations, cached frames, profile-backed detectors."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from ngoalong.core.capture import read_image

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "profiles" / "ngoalongviet"
EXPECTED_FILE = ROOT / "tests" / "fixtures" / "screens" / "expected.yaml"


def load_expected() -> dict[str, list[dict[str, Any]]]:
    return yaml.safe_load(EXPECTED_FILE.read_text(encoding="utf-8"))


def golden_cases(*groups: str) -> list[Any]:
    data = load_expected()
    return [
        pytest.param(case, id=Path(case["file"]).stem)
        for g in groups
        for case in data[g]
    ]


@lru_cache(maxsize=None)
def _frame(rel: str) -> np.ndarray:
    return read_image(ROOT / rel)


def frame(rel: str) -> np.ndarray:
    """A screenshot (cached across tests; copy before modifying)."""
    return _frame(rel)


@pytest.fixture(scope="session")
def scene_detector():
    from ngoalong.core.scenes import SceneDetector

    return SceneDetector.load(PROFILE / "scenes", templates_dir=PROFILE / "templates")


@pytest.fixture
def locator():
    from ngoalong.core.locator import CanvasLocator

    return CanvasLocator.from_profile(PROFILE)


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the session, offscreen (no window is ever shown on the desktop)."""
    import os

    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
