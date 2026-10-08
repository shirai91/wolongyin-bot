"""``CanvasLocator``: finds the 1000x600 game canvas inside a capture of the Coowon window.

Replaces v1.8.0's root coordinate (*Tọa độ gốc*) and F9 (docs/06 "Finding the game screen
automatically"). Three detectors run on every frame:

* **Anchor templates** (primary): HUD pieces with fixed canvas positions (``locator.yaml``).
  ``origin = match - anchor offset * scale``; two anchors far apart also give the scale.
* **Black-border box**: the page background is pure black, so the bounding box of the non-black
  rows and columns below the browser toolbar is the canvas on bright scenes. Accepted only when it
  is ``1000x600 * scale`` within ``tolerance_px``.
* **Layout prior**: the canvas is centred horizontally and top-aligned under the toolbar. Used only
  to place the anchor search windows, never as a result on its own.

A rectangle is accepted (``Confidence.HIGH``) when two detectors agree within ``agree_px`` (each
anchor counts as one detector). Otherwise the last good rectangle is returned with
``Confidence.LOW`` and ``stale=True``; without one, the best single detector (``LOW``) or the
layout guess (``NONE``). Callers must not click on anything but a ``HIGH`` fix.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from statistics import median

import numpy as np
import yaml
from pydantic import BaseModel, Field

from ngoalong.core.detection import Detection
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher, frame_rect
from ngoalong.paths import APP_ROOT

PROJECT_ROOT = APP_ROOT  # the exe folder in the PyInstaller build (ngoalong/paths.py)
DEFAULT_PROFILE_DIR = PROJECT_ROOT / "profiles" / "ngoalongviet"

BORDER = "border"
ANCHOR_PREFIX = "anchor:"


# --- configuration ------------------------------------------------------------------------------
class CanvasSize(BaseModel):
    width: int = 1000
    height: int = 600


class BlackBorderConfig(BaseModel):
    black_level: int = Field(40, ge=0, le=255)
    tolerance_px: int = Field(2, ge=0)
    min_count: int = Field(10, ge=1)
    min_scale: float = Field(0.5, gt=0)
    max_scale: float = Field(2.0, gt=0)


class LayoutConfig(BaseModel):
    center_offset_x: int = 5
    search_margin_px: int = Field(60, ge=0)


class AnchorConfig(BaseModel):
    name: str
    template: str
    at: tuple[int, int]
    threshold: float = Field(0.9, gt=0, le=1)


class LocatorConfig(BaseModel):
    canvas: CanvasSize = CanvasSize()
    black_border: BlackBorderConfig = BlackBorderConfig()
    agree_px: int = Field(3, ge=0)
    layout: LayoutConfig = LayoutConfig()
    anchors: list[AnchorConfig] = []

    @classmethod
    def load(cls, path: str | Path) -> LocatorConfig:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)


# --- results ------------------------------------------------------------------------------------
class Confidence(StrEnum):
    HIGH = "high"  # two detectors agree: safe to click
    LOW = "low"  # last good rectangle re-used, or a single detector
    NONE = "none"  # nothing found; rect is only the layout guess


@dataclass(frozen=True, slots=True)
class Candidate:
    """One detector's opinion of the canvas origin."""

    source: str  # "border" or "anchor:<name>"
    origin: Point
    scale: float
    score: float = 1.0
    match: Rect | None = None  # matched template rectangle (anchors)


@dataclass(frozen=True, slots=True)
class CanvasFix:
    rect: Rect  # canvas rectangle in frame (client) pixels
    scale: float  # canvas width / 1000
    confidence: Confidence
    detectors: tuple[str, ...] = ()  # detectors that agreed (or the single one used)
    stale: bool = False  # True when the last good fix was re-used
    candidates: tuple[Candidate, ...] = ()
    detections: tuple[Detection, ...] = field(default=(), compare=False)

    @property
    def origin(self) -> Point:
        return self.rect.top_left

    @property
    def reliable(self) -> bool:
        return self.confidence is Confidence.HIGH

    def to_frame(self, p: Point | tuple[float, float]) -> Point:
        """Canvas point (at 1000x600) -> frame point."""
        x, y = (p.x, p.y) if isinstance(p, Point) else p
        return Point(self.rect.x + round(x * self.scale), self.rect.y + round(y * self.scale))

    def rect_to_frame(self, r: Rect) -> Rect:
        """Canvas rectangle (at 1000x600) -> frame rectangle."""
        tl = self.to_frame((r.x, r.y))
        return Rect(tl.x, tl.y, max(1, round(r.w * self.scale)), max(1, round(r.h * self.scale)))

    def to_canvas(self, p: Point) -> tuple[float, float]:
        return ((p.x - self.rect.x) / self.scale, (p.y - self.rect.y) / self.scale)


def canvas_crop(image: np.ndarray, fix: CanvasFix | None) -> Rect | None:
    """The canvas rectangle to crop ``image`` to for screenshots (logs, notifications, captures);
    ``None`` keeps the whole window: the canvas is not certain or not entirely in the image, which
    is exactly when the whole window helps to see what went wrong."""
    if fix is None or not fix.reliable or fix.rect.is_empty():
        return None
    return fix.rect if fix.rect.intersect(frame_rect(image)) == fix.rect else None


@dataclass(frozen=True, slots=True)
class BorderBox:
    rect: Rect  # raw bounding box of the non-black pixels
    page_top: int
    scale: float
    valid: bool  # size matches 1000x600 * scale within the tolerance


# --- pure detectors -----------------------------------------------------------------------------
def _runs(mask: np.ndarray, merge_gap: int = 2) -> list[tuple[int, int]]:
    """Half-open runs of True values; runs separated by <= merge_gap False values are merged."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    out: list[tuple[int, int]] = []
    start = prev = int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if i - prev > merge_gap + 1:
            out.append((start, prev + 1))
            start = i
        prev = i
    out.append((start, prev + 1))
    return out


def non_black(frame: np.ndarray, black_level: int) -> np.ndarray:
    img = frame if frame.ndim == 2 else frame[:, :, :3].max(axis=2)
    return img > black_level


def find_page_top(nb: np.ndarray, rows_needed: int = 5) -> int:
    """First row below the browser toolbar: both side margins are black from there on.

    Returns 0 when the frame has no black side margins (e.g. the frame is the canvas itself).
    """
    h, w = nb.shape
    l0, l1 = int(w * 0.02), max(int(w * 0.10), int(w * 0.02) + 1)
    r0, r1 = int(w * 0.90), max(int(w * 0.98), int(w * 0.90) + 1)
    probe = (nb[:, l0:l1].mean(axis=1) < 0.02) & (nb[:, r0:r1].mean(axis=1) < 0.02)
    if h < rows_needed:
        return 0
    window = np.convolve(probe.astype(np.int32), np.ones(rows_needed, np.int32), mode="valid")
    hits = np.flatnonzero(window == rows_needed)
    return int(hits[0]) if hits.size else 0


def black_border_box(
    frame: np.ndarray, cfg: BlackBorderConfig, canvas: CanvasSize = CanvasSize()
) -> BorderBox | None:
    """Bounding box of the canvas from the black page around it; ``None`` when nothing is found."""
    nb = non_black(frame, cfg.black_level)
    top = find_page_top(nb)
    page = nb[top:]
    if page.size == 0:
        return None
    col_runs = _runs(page.sum(axis=0) >= cfg.min_count)
    if not col_runs:
        return None
    c0, c1 = max(col_runs, key=lambda r: r[1] - r[0])
    row_runs = _runs(page[:, c0:c1].sum(axis=1) >= cfg.min_count)
    if not row_runs:
        return None
    r0, r1 = max(row_runs, key=lambda r: r[1] - r[0])
    rect = Rect(c0, top + r0, c1 - c0, r1 - r0)
    scale = (rect.w / canvas.width + rect.h / canvas.height) / 2.0
    if abs(scale - 1.0) * canvas.width <= cfg.tolerance_px:
        scale = 1.0
    tol = cfg.tolerance_px
    valid = (
        cfg.min_scale <= scale <= cfg.max_scale
        and abs(rect.w - canvas.width * scale) <= tol
        and abs(rect.h - canvas.height * scale) <= tol
    )
    return BorderBox(rect, top, scale, valid)


def layout_prior(frame_shape: Sequence[int], page_top: int, scale: float, cfg: LocatorConfig) -> Rect:
    """Where the canvas should be: centred horizontally, top-aligned under the toolbar."""
    w = round(cfg.canvas.width * scale)
    h = round(cfg.canvas.height * scale)
    fw = int(frame_shape[1])
    x = max(0, (fw - w) // 2 + (cfg.layout.center_offset_x if fw > w else 0))
    return Rect(x, page_top, w, h)


def snap_scale(scale: float, eps: float = 0.005) -> float:
    return 1.0 if abs(scale - 1.0) < eps else round(scale, 4)


# --- the locator --------------------------------------------------------------------------------
class CanvasLocator:
    def __init__(self, config: LocatorConfig, matcher: Matcher) -> None:
        self.config = config
        self.matcher = matcher
        self._lock = threading.Lock()
        self._last_good: CanvasFix | None = None

    @classmethod
    def from_profile(
        cls, profile_dir: str | Path = DEFAULT_PROFILE_DIR, matcher: Matcher | None = None
    ) -> CanvasLocator:
        profile = Path(profile_dir)
        config = LocatorConfig.load(profile / "locator.yaml")
        if matcher is None:
            matcher = Matcher(TemplateStore(profile / "templates"), history=64)
        return cls(config, matcher)

    @property
    def last_good(self) -> CanvasFix | None:
        return self._last_good

    def reset(self) -> None:
        """Forget the last good fix (e.g. after re-binding to another window)."""
        with self._lock:
            self._last_good = None

    # --- detectors ------------------------------------------------------------------------------
    def _anchor_candidates(
        self, frame: np.ndarray, scale: float, prior: Rect, trusted_prior: bool
    ) -> tuple[list[Candidate], list[Detection]]:
        cands: list[Candidate] = []
        dets: list[Detection] = []
        margin = round(self.config.layout.search_margin_px * scale)
        for a in self.config.anchors:
            t = self.matcher.templates.get(a.template, scale)
            tw, th = t.size
            ex = prior.x + round(a.at[0] * scale)
            ey = prior.y + round(a.at[1] * scale)
            roi = Rect(ex - margin, ey - margin, tw + 2 * margin, th + 2 * margin)
            det = self.matcher.search(a.template, roi, a.threshold, frame=frame, scale=scale)
            if not det.found and not trusted_prior:
                det = self.matcher.search(a.template, None, a.threshold, frame=frame, scale=scale)
            dets.append(replace(det, name=f"{ANCHOR_PREFIX}{a.name}"))
            if det.found:
                origin = Point(det.rect.x - round(a.at[0] * scale), det.rect.y - round(a.at[1] * scale))
                cands.append(Candidate(f"{ANCHOR_PREFIX}{a.name}", origin, scale, det.score, det.rect))
                if not trusted_prior:  # later anchors search around this one, not the whole frame
                    prior, trusted_prior = Rect(origin.x, origin.y, 0, 0), True
        return cands, dets

    def _cluster(self, cands: Sequence[Candidate]) -> list[Candidate]:
        """Largest group of candidates whose origins agree within ``agree_px`` (ties: best score)."""
        tol = self.config.agree_px
        best: list[Candidate] = []
        best_score = -1.0
        for c in cands:
            group = [
                o
                for o in cands
                if abs(o.origin.x - c.origin.x) <= tol
                and abs(o.origin.y - c.origin.y) <= tol
                and abs(o.scale - c.scale) <= 0.02 * c.scale
            ]
            score = sum(o.score for o in group)
            if len(group) > len(best) or (len(group) == len(best) and score > best_score):
                best, best_score = group, score
        return best

    def _scale_from_anchors(self, group: Sequence[Candidate], fallback: float) -> float:
        anchors = [c for c in group if c.match is not None]
        by_name = {a.name: a for a in self.config.anchors}
        best_span = 0
        scale = fallback
        for i, a in enumerate(anchors):
            for b in anchors[i + 1 :]:
                ca = by_name[a.source.removeprefix(ANCHOR_PREFIX)].at
                cb = by_name[b.source.removeprefix(ANCHOR_PREFIX)].at
                span = max(abs(ca[0] - cb[0]), abs(ca[1] - cb[1]))
                if span < 100 or span <= best_span:
                    continue
                assert a.match is not None and b.match is not None
                if abs(ca[0] - cb[0]) >= abs(ca[1] - cb[1]):
                    scale = abs(a.match.x - b.match.x) / abs(ca[0] - cb[0])
                else:
                    scale = abs(a.match.y - b.match.y) / abs(ca[1] - cb[1])
                best_span = span
        return snap_scale(scale)

    def _fix(
        self, group: Sequence[Candidate], scale: float, confidence: Confidence,
        cands: Sequence[Candidate], dets: Sequence[Detection],
    ) -> CanvasFix:
        border = next((c for c in group if c.source == BORDER), None)
        if border is not None:
            origin = border.origin
        else:
            origin = Point(round(median(c.origin.x for c in group)), round(median(c.origin.y for c in group)))
        w = round(self.config.canvas.width * scale)
        h = round(self.config.canvas.height * scale)
        return CanvasFix(
            Rect(origin.x, origin.y, w, h), scale, confidence,
            tuple(c.source for c in group), False, tuple(cands), tuple(dets),
        )

    # --- public API -----------------------------------------------------------------------------
    def locate(self, frame: np.ndarray) -> CanvasFix:
        cfg = self.config
        border = black_border_box(frame, cfg.black_border, cfg.canvas)
        border_cand: Candidate | None = None
        if border is not None and border.valid:
            border_cand = Candidate(BORDER, border.rect.top_left, border.scale)
        last = self._last_good
        page_top = border.page_top if border is not None else 0

        scales: list[float] = []
        for s in (last.scale if last else None, border_cand.scale if border_cand else None, 1.0):
            if s is not None and all(abs(s - o) > 0.005 for o in scales):
                scales.append(s)

        all_cands: list[Candidate] = []
        first_dets: list[Detection] | None = None
        for s in scales:
            attempts: list[tuple[Rect, bool]]
            if border_cand is not None:
                attempts = [(Rect(border_cand.origin.x, border_cand.origin.y, 0, 0), True)]
            elif last is not None:
                # Around the last good fix first; if the canvas moved further than the search
                # margin (window resized, page re-centred), search again from the layout prior
                # with the full-frame fallback enabled.
                attempts = [(last.rect, True), (layout_prior(frame.shape, page_top, s, cfg), False)]
            else:
                attempts = [(layout_prior(frame.shape, page_top, s, cfg), False)]
            for prior, trusted in attempts:
                anchor_cands, dets = self._anchor_candidates(frame, s, prior, trusted)
                if first_dets is None:
                    first_dets = dets
                cands = anchor_cands + ([border_cand] if border_cand is not None else [])
                all_cands.extend(c for c in anchor_cands if c not in all_cands)
                group = self._cluster(cands)
                if len(group) >= 2:
                    scale = self._scale_from_anchors(group, border_cand.scale if border_cand in group else s)
                    fix = self._fix(group, scale, Confidence.HIGH, cands, dets)
                    with self._lock:
                        self._last_good = fix
                    return fix

        if border_cand is not None:
            all_cands.append(border_cand)
        dets_out = tuple(first_dets or ())
        if last is not None:
            return replace(last, confidence=Confidence.LOW, stale=True,
                           candidates=tuple(all_cands), detections=dets_out)
        if all_cands:
            best = max(all_cands, key=lambda c: (c.source == BORDER, c.score))
            return self._fix([best], best.scale, Confidence.LOW, all_cands, dets_out)
        prior = layout_prior(frame.shape, page_top, 1.0, cfg)
        return CanvasFix(prior, 1.0, Confidence.NONE, (), False, (), dets_out)
