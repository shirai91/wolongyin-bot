"""``CoordMap``: canvas-relative click offsets -> client coordinates (``origin + offset * scale``).

Offsets live in ``profiles/ngoalongviet/coords/*.yaml``, one file per feature, each holding exactly
one top-level key (the feature). All offsets are canvas-relative at 1000x600; ``origin`` is the
canvas's top-left corner in client pixels and ``scale`` is canvas width / 1000 (page zoom).

Keys are dotted paths: ``"thap.open_tower"``; list items by index:
``"quay_ruong.per_spin.1"``.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import yaml

from ngoalong.core.geometry import Point
from ngoalong.paths import APP_ROOT

PROJECT_ROOT = APP_ROOT  # the exe folder in the PyInstaller build (ngoalong/paths.py)
DEFAULT_COORDS_DIR = PROJECT_ROOT / "profiles" / "ngoalongviet" / "coords"

Offset = tuple[int, int]


class CoordError(KeyError):
    """Unknown key, or a value of the wrong shape."""

    def __str__(self) -> str:  # KeyError quotes its message; keep it readable
        return str(self.args[0]) if self.args else ""


def _is_pair(v: Any) -> bool:
    return (
        isinstance(v, (list, tuple))
        and len(v) == 2
        and all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in v)
    )


class CoordMap:
    def __init__(
        self,
        data: Mapping[str, Any],
        origin: Point = Point(0, 0),
        scale: float = 1.0,
        sources: Mapping[str, Path] | None = None,
    ) -> None:
        self._data: dict[str, Any] = dict(data)
        self._sources: dict[str, Path] = dict(sources or {})
        self._lock = threading.Lock()
        self._origin = origin
        self._scale = scale
        self._check_scale(scale)

    # --- loading --------------------------------------------------------------------------------
    @classmethod
    def load(
        cls, directory: str | Path = DEFAULT_COORDS_DIR, origin: Point = Point(0, 0), scale: float = 1.0
    ) -> CoordMap:
        """Load and merge every ``*.yaml`` file of ``directory``.

        Each file holds one or more top-level feature keys; the same key in two files is an error,
        so features never silently overwrite each other's offsets.
        """
        d = Path(directory)
        if not d.is_dir():
            raise FileNotFoundError(f"coords directory not found: {d}")
        data: dict[str, Any] = {}
        sources: dict[str, Path] = {}
        for path in sorted(d.glob("*.yaml")):
            content = yaml.safe_load(path.read_text(encoding="utf-8"))
            if content is None:
                continue
            if not isinstance(content, dict):
                raise ValueError(f"{path.name}: top level must be a mapping of feature -> offsets")
            for key, value in content.items():
                if key in data:
                    raise ValueError(
                        f"coords key {key!r} defined in both {sources[key].name} and {path.name}"
                    )
                data[key] = value
                sources[key] = path
        return cls(data, origin, scale, sources)

    # --- canvas placement -----------------------------------------------------------------------
    @staticmethod
    def _check_scale(scale: float) -> None:
        if not scale > 0:
            raise ValueError(f"scale must be positive, got {scale}")

    @property
    def origin(self) -> Point:
        return self._origin

    @property
    def scale(self) -> float:
        return self._scale

    def set_canvas(self, origin: Point, scale: float = 1.0) -> None:
        """Update the canvas position (from the canvas locator) atomically."""
        self._check_scale(scale)
        with self._lock:
            self._origin = origin
            self._scale = scale

    # --- raw access -----------------------------------------------------------------------------
    @property
    def features(self) -> list[str]:
        return list(self._data)

    def source_of(self, feature: str) -> Path | None:
        return self._sources.get(feature)

    def raw(self, key: str) -> Any:
        node: Any = self._data
        walked: list[str] = []
        for part in key.split("."):
            walked.append(part)
            if isinstance(node, Mapping) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.lstrip("-").isdigit() and -len(node) <= int(part) < len(node):
                node = node[int(part)]
            else:
                raise CoordError(f"unknown coords key {'.'.join(walked)!r} (in {key!r})")
        return node

    def __contains__(self, key: str) -> bool:
        try:
            self.raw(key)
        except CoordError:
            return False
        return True

    def offset(self, key: str) -> Offset:
        v = self.raw(key)
        if not _is_pair(v):
            raise CoordError(f"coords key {key!r} is not an [x, y] offset: {v!r}")
        return (int(v[0]), int(v[1]))

    def offsets(self, key: str) -> list[Offset]:
        v = self.raw(key)
        if not isinstance(v, list) or not all(_is_pair(p) for p in v):
            raise CoordError(f"coords key {key!r} is not a list of [x, y] offsets")
        return [(int(p[0]), int(p[1])) for p in v]

    def value(self, key: str) -> int | float | str:
        v = self.raw(key)
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise CoordError(f"coords key {key!r} is not a scalar: {v!r}")
        return v

    def point_keys(self, feature: str | None = None) -> Iterator[str]:
        """Every dotted key whose value is an [x, y] offset (list items included)."""

        def walk(prefix: str, node: Any) -> Iterator[str]:
            if _is_pair(node):
                yield prefix
            elif isinstance(node, Mapping):
                for k, v in node.items():
                    yield from walk(f"{prefix}.{k}" if prefix else str(k), v)
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    yield from walk(f"{prefix}.{i}", v)

        roots = [feature] if feature is not None else self.features
        for f in roots:
            yield from walk(f, self.raw(f))

    # --- conversion -----------------------------------------------------------------------------
    def to_client(
        self, offset: Offset | Point, origin: Point | None = None, scale: float | None = None
    ) -> Point:
        """Canvas offset (at 1000x600) -> client point: ``origin + offset * scale``, rounded."""
        with self._lock:
            o = self._origin if origin is None else origin
            s = self._scale if scale is None else scale
        dx, dy = (offset.x, offset.y) if isinstance(offset, Point) else offset
        return Point(o.x + round(dx * s), o.y + round(dy * s))

    def point(self, key: str, origin: Point | None = None, scale: float | None = None) -> Point:
        return self.to_client(self.offset(key), origin, scale)

    def points(self, key: str, origin: Point | None = None, scale: float | None = None) -> list[Point]:
        return [self.to_client(o, origin, scale) for o in self.offsets(key)]

    def to_canvas(self, p: Point) -> tuple[float, float]:
        """Inverse of ``to_client``: client point -> canvas offset at 1000x600."""
        with self._lock:
            o, s = self._origin, self._scale
        return ((p.x - o.x) / s, (p.y - o.y) / s)
