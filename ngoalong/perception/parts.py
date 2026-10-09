"""UI parts at run time: each part of ``parts.yaml`` searched in its region, lazily, once per frame
(docs/08 "Concepts" 1, "UI parts").

A part is an icon or label template plus a search region. ``PartScan.find(name)`` returns where it
is: one ``Hit`` for a plain part, every copy for a ``multi`` part (✕ of stacked dialogs, magic
circles, shop chests). The scan caches each answer, so screen rules, readers and the debug view can
ask for the same part many times at the cost of one search.

An ``ink`` part (``ink: 170``) is matched on the white-text mask (every channel above that level) of
the region and of its template, not on colours: for white map labels with a dark outline over map
art that changes with the season and the animations (the building labels "lvN").
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import yaml

from ngoalong.core.geometry import Point, Rect
from ngoalong.core.templates import Template, TemplateStore
from ngoalong.core.vision import score_map

CANVAS = Rect(0, 0, 1000, 600)
DEFAULT_THRESHOLD = 0.90
MAX_HITS = 24  # a multi part never has more copies than this on screen


@dataclass(frozen=True, slots=True)
class PartSpec:
    name: str
    template: str
    search: Rect
    threshold: float = DEFAULT_THRESHOLD
    multi: bool = False
    ink: int = 0  # > 0: match the white-text masks (all channels above this level), not colours
    max_hits: int = MAX_HITS


@dataclass(frozen=True, slots=True)
class Hit:
    """A part found on the canvas: its rectangle (canvas pixels) and match score."""

    part: str
    rect: Rect
    score: float

    @property
    def center(self) -> Point:
        return self.rect.center

    def near(self, p: Point | tuple[int, int], tol: int) -> bool:
        x, y = (p.x, p.y) if isinstance(p, Point) else p
        c = self.center
        return abs(c.x - x) <= tol and abs(c.y - y) <= tol

    def __str__(self) -> str:
        c = self.center
        return f"{self.part}@{c.x},{c.y}({self.score:.2f})"


def _rect(v: object, name: str) -> Rect:
    if v is None or v == "canvas":
        return CANVAS
    if isinstance(v, (list, tuple)) and len(v) == 4:
        return Rect(*(int(n) for n in v)).intersect(CANVAS)
    raise ValueError(f"part {name}: search must be [x, y, w, h] or 'canvas', not {v!r}")


class PartLibrary:
    """Part specs plus their templates (loaded once, shared by every frame)."""

    def __init__(self, specs: dict[str, PartSpec], store: TemplateStore) -> None:
        self.specs = specs
        self.store = store
        self._ink: dict[str, np.ndarray] = {}

    @classmethod
    def load(cls, profile_dir: str | Path) -> PartLibrary:
        d = Path(profile_dir)
        raw = yaml.safe_load((d / "parts.yaml").read_text(encoding="utf-8")) or {}
        specs = {
            name: PartSpec(name, v["template"], _rect(v.get("search"), name),
                           float(v.get("threshold", DEFAULT_THRESHOLD)), bool(v.get("multi", False)),
                           int(v.get("ink", 0)), int(v.get("max_hits", MAX_HITS)))
            for name, v in raw.items()
        }
        lib = cls(specs, TemplateStore(d / "templates"))
        for s in specs.values():  # fail early on a missing template
            lib.template(s.name)
        return lib

    def __contains__(self, name: str) -> bool:
        return name in self.specs

    def template(self, name: str) -> Template:
        return self.store.get(self.specs[name].template)

    def ink_template(self, name: str) -> np.ndarray:
        """The white-text mask of an ``ink`` part's template (uint8 0/255), computed once."""
        m = self._ink.get(name)
        if m is None:
            m = self._ink[name] = white_ink(self.template(name).image, self.specs[name].ink)
        return m


def white_ink(bgr: np.ndarray, level: int) -> np.ndarray:
    """uint8 0/255 mask of the pixels whose every channel is above ``level`` (white text)."""
    return np.where(bgr[:, :, :3].min(axis=2) > level, 255, 0).astype(np.uint8)


def _peaks(res: np.ndarray, thr: float, size: tuple[int, int], limit: int = MAX_HITS) -> list[tuple[float, int, int]]:
    """Local maxima >= thr, best first, at least one template size apart."""
    out: list[tuple[float, int, int]] = []
    res = res.copy()
    tw, th = size
    while len(out) < limit:
        _mn, mx, _mnl, (x, y) = cv2.minMaxLoc(res)
        if mx < thr:
            break
        out.append((float(min(mx, 1.0)), x, y))
        res[max(0, y - th // 2):y + th // 2 + 1, max(0, x - tw // 2):x + tw // 2 + 1] = -1.0
    return out


class PartScan:
    """The parts of one canvas image, searched on demand and cached."""

    def __init__(self, library: PartLibrary, canvas: np.ndarray) -> None:
        self.lib = library
        self.canvas = canvas
        self._hits: dict[str, tuple[Hit, ...]] = {}
        self._best: dict[str, float] = {}
        self.ms = 0.0  # time spent searching
        self.searches = 0

    def find(self, name: str) -> tuple[Hit, ...]:
        """Every hit of ``name`` (at most one unless the part is ``multi``), best first."""
        cached = self._hits.get(name)
        if cached is not None:
            return cached
        spec = self.lib.specs[name]
        t0 = time.perf_counter()
        t = self.lib.template(name)
        a = spec.search
        region = self.canvas[a.y:a.bottom, a.x:a.right]
        if spec.ink:
            res = score_map(white_ink(region, spec.ink), self.lib.ink_template(name))
        else:
            res = score_map(region, t.image, t.mask)
        hits: tuple[Hit, ...] = ()
        best = -1.0
        if res is not None:
            tw, th = t.size
            peaks = _peaks(res, spec.threshold, (tw, th), spec.max_hits) if spec.multi else []
            if not spec.multi:
                _mn, mx, _mnl, (x, y) = cv2.minMaxLoc(res)
                if mx >= spec.threshold:
                    peaks = [(float(min(mx, 1.0)), x, y)]
                best = float(mx)
            else:
                best = peaks[0][0] if peaks else float(res.max())
            hits = tuple(Hit(name, Rect(a.x + x, a.y + y, tw, th), s) for s, x, y in peaks)
        self.ms += (time.perf_counter() - t0) * 1000
        self.searches += 1
        self._hits[name] = hits
        self._best[name] = best
        return hits

    def first(self, name: str) -> Hit | None:
        hits = self.find(name)
        return hits[0] if hits else None

    def present(self, name: str) -> bool:
        return bool(self.find(name))

    def score(self, name: str) -> float:
        """Best score of ``name`` in its region (searched if needed), found or not."""
        self.find(name)
        return self._best[name]

    def searched(self) -> dict[str, tuple[Hit, ...]]:
        """Every part searched so far on this frame, with its hits."""
        return dict(self._hits)
