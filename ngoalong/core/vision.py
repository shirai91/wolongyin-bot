"""``Matcher``: OpenCV template search with a score threshold, ROI, mask and centre point.

Matching tolerates small pixel differences, clicks go to the centre of a match, and templates come
from the in-memory ``TemplateStore``. Every search records a ``Detection`` for the debug overlay, found or not.

Coordinates: ``roi`` and returned rectangles are in FRAME pixels (the captured client area). Use
``scale`` (canvas width / 1000) when the canvas is zoomed; templates are resized to match.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ngoalong.core.cancel import CancelFlag, check_cancel, sleep_cancellable
from ngoalong.core.capture import Capturer
from ngoalong.core.detection import Detection
from ngoalong.core.errors import WaitTimeout
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.templates import Template, TemplateStore, normalize_mask

DEFAULT_THRESHOLD = 0.85


@dataclass(frozen=True, slots=True)
class Match:
    name: str
    rect: Rect
    score: float

    @property
    def center(self) -> Point:
        return self.rect.center


def frame_rect(frame: np.ndarray) -> Rect:
    return Rect(0, 0, int(frame.shape[1]), int(frame.shape[0]))


def score_map(region: np.ndarray, image: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray | None:
    """Score of ``image`` at every position inside ``region`` (float32); ``None`` when it does not fit.

    Uses ``TM_CCOEFF_NORMED`` (tolerant to brightness drift). A flat template (one colour) has no
    variance for that method, so it is scored by mean squared difference instead:
    ``1 - rms / 255``. Scores are in [-1, 1]; non-finite values become -1 (no match).
    """
    rh, rw = region.shape[:2]
    th, tw = image.shape[:2]
    if th > rh or tw > rw or th == 0 or tw == 0:
        return None
    if region.ndim != image.ndim:
        raise ValueError("frame and template must have the same channel count")
    flat = float(np.std(image if mask is None else image[mask > 0])) < 1e-6
    if flat:
        res = cv2.matchTemplate(region, image, cv2.TM_SQDIFF, mask=mask)
        n = float(np.count_nonzero(mask)) if mask is not None else float(image.size)
        res = 1.0 - np.sqrt(np.maximum(res, 0) / (max(n, 1.0) * 255.0 * 255.0))
    else:
        res = cv2.matchTemplate(region, image, cv2.TM_CCOEFF_NORMED, mask=mask)
    return np.where(np.isfinite(res), res, -1.0).astype(np.float32)


def match_template(
    region: np.ndarray, image: np.ndarray, mask: np.ndarray | None = None
) -> tuple[float, tuple[int, int]] | None:
    """Best (score, (x, y)) of ``image`` inside ``region``; ``None`` when it does not fit
    (scores as in ``score_map``)."""
    res = score_map(region, image, mask)
    if res is None:
        return None
    _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(res)
    return float(min(max_val, 1.0)), (int(max_loc[0]), int(max_loc[1]))


class Matcher:
    def __init__(
        self,
        templates: TemplateStore | str | Path | Iterable[str | Path],
        capturer: Capturer | None = None,
        default_threshold: float = DEFAULT_THRESHOLD,
        poll_interval_s: float = 0.25,
        scale: float = 1.0,
        on_detection: Callable[[Detection], None] | None = None,
        history: int = 512,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.templates = templates if isinstance(templates, TemplateStore) else TemplateStore(templates)
        self.capturer = capturer
        self.default_threshold = default_threshold
        self.poll_interval_s = poll_interval_s
        self.scale = scale
        self.on_detection = on_detection
        self._detections: deque[Detection] = deque(maxlen=history)
        self._lock = threading.Lock()
        self._clock = clock

    # --- detection log --------------------------------------------------------------------------
    @property
    def detections(self) -> list[Detection]:
        with self._lock:
            return list(self._detections)

    def drain_detections(self) -> list[Detection]:
        """Return and clear the recorded detections (one DebugFrame's worth)."""
        with self._lock:
            out = list(self._detections)
            self._detections.clear()
            return out

    def _record(self, det: Detection) -> None:
        with self._lock:
            self._detections.append(det)
        if self.on_detection is not None:
            self.on_detection(det)

    # --- searching ------------------------------------------------------------------------------
    def _frame(self, frame: np.ndarray | None) -> np.ndarray:
        if frame is not None:
            return frame
        if self.capturer is None:
            raise ValueError("Matcher has no capturer; pass frame=")
        return self.capturer.grab()

    def _threshold(self, t: Template, threshold: float | None) -> float:
        if threshold is not None:
            return threshold
        return t.threshold if t.threshold is not None else self.default_threshold

    def search(
        self,
        name: str,
        roi: Rect | None = None,
        threshold: float | None = None,
        frame: np.ndarray | None = None,
        mask: np.ndarray | None = None,
        scale: float | None = None,
    ) -> Detection:
        """Search once and return the recorded ``Detection`` (found or not)."""
        img = self._frame(frame)
        t = self.templates.get(name, self.scale if scale is None else scale)
        thr = self._threshold(t, threshold)
        full = frame_rect(img)
        area = full if roi is None else roi.intersect(full)
        tmask = t.mask
        if mask is not None:
            tmask = normalize_mask(mask, t.image.shape)
        result = None
        if not area.is_empty():
            region = img[area.y : area.bottom, area.x : area.right]
            if region.ndim == 2:
                region = cv2.cvtColor(region, cv2.COLOR_GRAY2BGR)
            elif region.shape[2] == 4:
                region = cv2.cvtColor(region, cv2.COLOR_BGRA2BGR)
            result = match_template(region, t.image, tmask)
        search_rect = roi if roi is not None else full
        if result is None:
            det = Detection(name, search_rect, 0.0, thr, False, None, roi)
        else:
            score, (x, y) = result
            tw, th = t.size
            best = Rect(area.x + x, area.y + y, tw, th)
            found = score >= thr
            det = Detection(name, best if found else search_rect, score, thr, found, best, roi)
        self._record(det)
        return det

    def find(
        self,
        name: str,
        roi: Rect | None = None,
        threshold: float | None = None,
        frame: np.ndarray | None = None,
        mask: np.ndarray | None = None,
        scale: float | None = None,
    ) -> Match | None:
        det = self.search(name, roi, threshold, frame, mask, scale)
        return Match(name, det.rect, det.score) if det.found else None

    def find_any(
        self,
        names: Sequence[str],
        roi: Rect | None = None,
        threshold: float | None = None,
        frame: np.ndarray | None = None,
    ) -> Match | None:
        """Search every name on ONE frame; return the highest-scoring match, if any."""
        img = self._frame(frame)
        best: Match | None = None
        for name in names:
            m = self.find(name, roi, threshold, frame=img)
            if m is not None and (best is None or m.score > best.score):
                best = m
        return best

    # --- waiting --------------------------------------------------------------------------------
    def wait_for(
        self,
        name: str | Sequence[str],
        timeout_s: float,
        roi: Rect | None = None,
        threshold: float | None = None,
        cancel: CancelFlag | None = None,
        interval_s: float | None = None,
    ) -> Match:
        """Poll until ``name`` (or any of several names) appears.

        Raises ``WaitTimeout`` after ``timeout_s`` and ``Cancelled`` as soon as ``cancel`` is set.
        """
        names = [name] if isinstance(name, str) else list(name)
        if not names:
            raise ValueError("wait_for needs at least one template name")
        interval = self.poll_interval_s if interval_s is None else interval_s
        deadline = self._clock() + max(0.0, timeout_s)
        best_score = float("-inf")
        while True:
            check_cancel(cancel)
            frame = self._frame(None)
            dets = [self.search(n, roi, threshold, frame=frame) for n in names]
            best_score = max([best_score, *(d.score for d in dets)])
            found = [d for d in dets if d.found]
            if found:
                d = max(found, key=lambda d: d.score)
                return Match(d.name, d.rect, d.score)
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise WaitTimeout(
                    f"{'/'.join(names)} not found within {timeout_s:.1f}s (best score {best_score:.3f})"
                )
            sleep_cancellable(min(interval, remaining), cancel)

    def wait_gone(
        self,
        name: str,
        timeout_s: float,
        roi: Rect | None = None,
        threshold: float | None = None,
        cancel: CancelFlag | None = None,
        interval_s: float | None = None,
    ) -> None:
        """Poll until ``name`` is no longer visible (e.g. a dialog closed)."""
        interval = self.poll_interval_s if interval_s is None else interval_s
        deadline = self._clock() + max(0.0, timeout_s)
        while True:
            check_cancel(cancel)
            if self.find(name, roi, threshold) is None:
                return
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise WaitTimeout(f"{name} still visible after {timeout_s:.1f}s")
            sleep_cancellable(min(interval, remaining), cancel)
