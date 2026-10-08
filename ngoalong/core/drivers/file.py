"""``FileCapturer``: replays stored screenshots (or in-memory frames) instead of a live window.

Used by offline tests and the annotate tool. Each ``grab()`` returns the next frame; after the last
one it either loops or keeps returning the last frame.
"""

from __future__ import annotations

import glob
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np

from ngoalong.core.capture import read_image, to_bgr
from ngoalong.core.errors import CaptureError

FrameSource = str | Path | np.ndarray

_IMAGE_SUFFIXES = {".png", ".bmp", ".jpg", ".jpeg"}


def expand_sources(sources: FrameSource | Iterable[FrameSource]) -> list[FrameSource]:
    """Expand directories and glob patterns into a sorted list of image files."""
    if isinstance(sources, (str, Path, np.ndarray)):
        sources = [sources]
    out: list[FrameSource] = []
    for src in sources:
        if isinstance(src, np.ndarray):
            out.append(src)
            continue
        p = Path(src)
        if p.is_dir():
            out.extend(sorted(f for f in p.iterdir() if f.suffix.lower() in _IMAGE_SUFFIXES))
        elif any(ch in str(src) for ch in "*?["):
            out.extend(Path(m) for m in sorted(glob.glob(str(src))))
        else:
            out.append(p)
    return out


class FileCapturer:
    def __init__(self, sources: FrameSource | Iterable[FrameSource], loop: bool = False) -> None:
        self._sources: Sequence[FrameSource] = expand_sources(sources)
        if not self._sources:
            raise CaptureError("FileCapturer has no frames")
        self.loop = loop
        self._cache: dict[int, np.ndarray] = {}
        self._index = 0
        self._lock = threading.Lock()
        self.grab_count = 0

    def __len__(self) -> int:
        return len(self._sources)

    @property
    def index(self) -> int:
        """Index of the frame the next ``grab()`` returns."""
        return self._index

    def current_source(self) -> FrameSource:
        return self._sources[min(self._index, len(self._sources) - 1)]

    def seek(self, index: int) -> None:
        with self._lock:
            if not 0 <= index < len(self._sources):
                raise IndexError(index)
            self._index = index

    def _load(self, i: int) -> np.ndarray:
        frame = self._cache.get(i)
        if frame is None:
            src = self._sources[i]
            frame = to_bgr(src if isinstance(src, np.ndarray) else read_image(src))
            frame = np.ascontiguousarray(frame)
            self._cache[i] = frame
        return frame

    def client_size(self) -> tuple[int, int]:
        """(width, height) of the frame the next ``grab()`` returns (the "window" right now)."""
        with self._lock:
            frame = self._load(min(self._index, len(self._sources) - 1))
            return (int(frame.shape[1]), int(frame.shape[0]))

    def grab(self) -> np.ndarray:
        with self._lock:
            i = self._index
            n = len(self._sources)
            if i + 1 < n:
                self._index = i + 1
            elif self.loop:
                self._index = 0
            self.grab_count += 1
            return self._load(i).copy()
