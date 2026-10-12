"""``Capturer`` interface, a small frame cache, and image file helpers.

Frames are ``numpy.ndarray`` of shape (h, w, 3), dtype uint8, BGR (OpenCV order).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

import cv2
import numpy as np

from ngoalong.core.errors import CaptureError


@runtime_checkable
class Capturer(Protocol):
    def grab(self) -> np.ndarray:
        """Return a fresh BGR frame of the target window's client area (or page)."""
        ...


class CachedCapturer:
    """Wraps a ``Capturer`` and reuses the last frame while it is younger than ``max_age_s``.

    Several searches in the same step then share one capture instead of grabbing the window each
    time. ``invalidate()`` forces the next ``grab`` to capture again (call it after a click).
    """

    def __init__(
        self, inner: Capturer, max_age_s: float = 0.1, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._inner = inner
        self._max_age_s = max_age_s
        self._clock = clock
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._taken_at = 0.0

    def grab(self) -> np.ndarray:
        with self._lock:
            now = self._clock()
            if self._frame is None or now - self._taken_at > self._max_age_s:
                self._frame = self._inner.grab()
                self._taken_at = now
            return self._frame

    def invalidate(self) -> None:
        with self._lock:
            self._frame = None

    def client_size(self) -> tuple[int, int] | None:
        """The inner capturer's current (width, height) without capturing; None if unknown."""
        fn = getattr(self._inner, "client_size", None)
        return fn() if callable(fn) else None

    def visible_fraction(self) -> float | None:
        """The inner capturer's share of the window shown on screen; None if it cannot tell."""
        fn = getattr(self._inner, "visible_fraction", None)
        return fn() if callable(fn) else None

    @property
    def last_frame(self) -> np.ndarray | None:
        return self._frame


def read_image(path: str | Path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray:
    """Read an image file; works with non-ASCII paths (cv2.imread does not on Windows)."""
    p = Path(path)
    try:
        data = np.fromfile(p, dtype=np.uint8)
    except OSError as exc:
        raise CaptureError(f"cannot read image {p}: {exc}") from exc
    img = cv2.imdecode(data, flags)
    if img is None:
        raise CaptureError(f"cannot decode image {p}")
    return img


def to_bgr(img: np.ndarray) -> np.ndarray:
    """Normalise a grey / BGRA / BGR image to 3-channel BGR uint8."""
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img


def save_png(path: str | Path, frame: np.ndarray) -> Path:
    """Write ``frame`` as PNG (non-ASCII paths supported), creating parent folders."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        raise CaptureError(f"cannot encode PNG for {p}")
    buf.tofile(p)
    return p
