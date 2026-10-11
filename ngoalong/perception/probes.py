"""Colour probes: a few pixels or a small histogram instead of a template (docs/08 "UI parts",
cheapest detector kind).

Each probe in ``probes.yaml`` has a ``kind`` and an optional ``rect`` (readers that check a row of
tabs or a grid of slots pass their own rects). ``use: other`` copies another probe's settings, so
one calibrated rule (say, a checkbox tick) serves several checkboxes.

Kinds:

* ``mean``   mean of ``expr`` (``r-b``, ``b-r``, ``g``, ``sat``, ``v``) over the rect; ``skip_light``
             ignores label pixels (max channel >= that level). On when ``min <= value <= max``.
             Selected tab: mean(R - B) without the white label is +22..+33 gold, -11..-20 teal.
* ``count``  pixels matching ``colour`` (per-channel ranges, also ``r-b``, ``g-r``...); ``ring: N``
             counts only an N px band along the rect's border (a selection frame). On from ``min``
             (default 1) up to ``max``, so ``max`` alone checks that something is NOT drawn.
* ``blobs``  connected regions of ``colour`` with a minimum size and height/width ``aspect``; the
             value is their rectangles (canvas pixels), largest first. On when there is one.
             ``core: {colour: ..., min: N}`` keeps only regions holding at least N pixels of a
             stricter colour (a pale glow counts only around a saturated centre).
* ``fill``   fraction of the rect's columns where at least ``col_frac`` of the pixels match
             ``colour`` (a progress or HP bar). On at ``min``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from ngoalong.core.geometry import Rect

KINDS = ("mean", "count", "blobs", "fill")
CHANNELS = ("r", "g", "b", "r-b", "b-r", "g-r", "r-g", "g-b")


@dataclass(frozen=True, slots=True)
class ProbeSpec:
    name: str
    kind: str
    rect: Rect | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    name: str
    value: float | int | tuple[Rect, ...]
    on: bool

    def __bool__(self) -> bool:
        return self.on

    def __str__(self) -> str:
        v = self.value
        shown = f"{len(v)} blob(s)" if isinstance(v, tuple) else (f"{v:.2f}" if isinstance(v, float) else str(v))
        return f"{self.name}={shown}{'' if self.on else ' (off)'}"


def load_probes(path: str | Path) -> dict[str, ProbeSpec]:
    raw: dict[str, dict[str, Any]] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: dict[str, ProbeSpec] = {}

    def resolve(name: str, seen: tuple[str, ...] = ()) -> dict[str, Any]:
        if name in seen:
            raise ValueError(f"probe {name}: 'use' loop {seen}")
        v = dict(raw[name])
        base = v.pop("use", None)
        return {**resolve(base, (*seen, name)), **v} if base else v

    for name in raw:
        v = resolve(name)
        kind = v.pop("kind", None)
        if kind not in KINDS:
            raise ValueError(f"probe {name}: kind must be one of {KINDS}, not {kind!r}")
        rect = v.pop("rect", None)
        for ch in (*v.get("colour", {}), *v.get("core", {}).get("colour", {})):
            if ch not in CHANNELS:
                raise ValueError(f"probe {name}: colour channel {ch!r} not in {CHANNELS}")
        out[name] = ProbeSpec(name, kind, Rect(*rect) if rect else None, v)
    return out


def _channels(img: np.ndarray) -> dict[str, np.ndarray]:
    b, g, r = (img[..., i].astype(np.int16) for i in range(3))
    return {"r": r, "g": g, "b": b, "r-b": r - b, "b-r": b - r, "g-r": g - r, "r-g": r - g, "g-b": g - b}


def colour_mask(img: np.ndarray, colour: dict[str, list[int]]) -> np.ndarray:
    """Pixels whose channels all fall in the given [lo, hi] ranges."""
    ch = _channels(img)
    mask = np.ones(img.shape[:2], dtype=bool)
    for key, (lo, hi) in colour.items():
        mask &= (ch[key] >= lo) & (ch[key] <= hi)
    return mask


def _expr(img: np.ndarray, expr: str) -> np.ndarray:
    if expr in ("sat", "v"):
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        return hsv[..., 1 if expr == "sat" else 2].astype(np.int16)
    return _channels(img)[expr]


def evaluate(spec: ProbeSpec, canvas: np.ndarray, rect: Rect | None = None) -> ProbeResult:
    """Run one probe on the canvas (``rect`` overrides the probe's own)."""
    r = rect or spec.rect
    if r is None:
        raise ValueError(f"probe {spec.name} has no rect; the reader must pass one")
    img = canvas[r.y:r.bottom, r.x:r.right]
    p = spec.params
    if img.size == 0:
        return ProbeResult(spec.name, 0, False)
    if spec.kind == "mean":
        vals = _expr(img, p.get("expr", "v"))
        if p.get("skip_light") is not None:
            keep = img.max(axis=2) < int(p["skip_light"])
            vals = vals[keep] if keep.any() else vals
        v = float(vals.mean())
        on = float(p.get("min", -1e9)) <= v <= float(p.get("max", 1e9))
        return ProbeResult(spec.name, v, on)
    mask = colour_mask(img, p["colour"])
    if spec.kind == "count":
        ring = p.get("ring")
        if ring:
            inner = np.ones_like(mask)
            inner[ring:-ring, ring:-ring] = False
            mask &= inner
        n = int(mask.sum())
        return ProbeResult(spec.name, n, int(p.get("min", 1)) <= n <= int(p.get("max", 10**9)))
    if spec.kind == "fill":
        cols = mask.mean(axis=0) >= float(p.get("col_frac", 0.3))
        v = float(cols.mean())
        return ProbeResult(spec.name, v, v >= float(p.get("min", 0.5)))
    # blobs
    n, lab, st, _c = cv2.connectedComponentsWithStats(mask.astype(np.uint8))
    core = p.get("core")
    if core:  # core pixels per region
        in_core = np.bincount(lab[colour_mask(img, core["colour"]) & mask], minlength=n)
    blobs = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in st[i])
        if (w >= p.get("min_w", 1) and h >= p.get("min_h", 1) and area >= p.get("min_area", 1)
                and h >= p.get("aspect", 0) * w and (not core or in_core[i] >= int(core.get("min", 1)))):
            blobs.append((area, Rect(r.x + x, r.y + y, w, h)))
    rects = tuple(b for _a, b in sorted(blobs, key=lambda t: -t[0]))
    return ProbeResult(spec.name, rects, bool(rects))
