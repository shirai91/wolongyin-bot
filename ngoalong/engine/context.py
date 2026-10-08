"""``RunContext``: everything a feature uses to see and act on the game (docs/06 Core interfaces).

Coordinates a feature deals with:

* ``str`` click targets are ``CoordMap`` keys (``"quay_ruong.per_spin.0"``), canvas-relative at
  1000x600, converted with the current canvas origin and scale.
* ``Point`` targets are canvas-relative offsets at 1000x600 too (AutoClick's recorded points).
* ``Match`` / ``ObjectResult`` targets are already in frame (client) pixels: they come from a search.
* ``region`` arguments of ``find``/``wait_for`` are canvas rectangles at 1000x600.

Every wait has a time limit and honours Stop and Pause (``RunControl``). Every search is recorded as
a ``Detection``; in debug mode each step is published as a ``DebugFrame`` (live overlay, debug panel,
PNG files), and failure frames are always saved.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import numpy as np

from ngoalong.core.capture import Capturer
from ngoalong.core.coords import CoordMap
from ngoalong.core.detection import Detection
from ngoalong.core.errors import NgoaLongError, TaskStopped, WaitTimeout
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.input import InputDriver, MouseButton
from ngoalong.core.locator import CanvasFix, Confidence
from ngoalong.core.ocr import OcrEngine, OcrLine, OcrUnavailable
from ngoalong.core.scenes import ObjectResult, SceneDetector, SceneMatch
from ngoalong.core.vision import Match, Matcher
from ngoalong.debug.model import DebugFrame
from ngoalong.debug.sink import DebugSink, PngSink
from ngoalong.engine.control import RunControl
from ngoalong.engine.panels import PanelTidier
from ngoalong.engine.popups import PopupGuard

log = logging.getLogger(__name__)

ClickTarget = str | Point | Match | ObjectResult


class CanvasNotFound(NgoaLongError):
    """The game canvas could not be located reliably, so clicking would be blind."""


class SceneTimeout(WaitTimeout):
    """``expect_scene`` reached its time limit; ``actual`` is what the screen showed instead."""

    def __init__(self, message: str, expected: str, actual: SceneMatch | None) -> None:
        super().__init__(message)
        self.expected = expected
        self.actual = actual


class ClickType(StrEnum):
    """The six click types of v1.8.0's AutoClick combo (#10: now honoured)."""

    LEFT = "left"
    LEFT_DOUBLE = "left_double"
    MIDDLE = "middle"
    MIDDLE_DOUBLE = "middle_double"
    RIGHT = "right"
    RIGHT_DOUBLE = "right_double"

    @property
    def button(self) -> MouseButton:
        return self.value.split("_")[0]  # type: ignore[return-value]

    @property
    def double(self) -> bool:
        return self.value.endswith("_double")

    @property
    def label(self) -> str:
        return _CLICK_LABELS[self]


_CLICK_LABELS: dict[ClickType, str] = {
    ClickType.LEFT: "Chuột trái",
    ClickType.LEFT_DOUBLE: "Chuột trái (nhấp đúp)",
    ClickType.MIDDLE: "Chuột giữa",
    ClickType.MIDDLE_DOUBLE: "Chuột giữa (nhấp đúp)",
    ClickType.RIGHT: "Chuột phải",
    ClickType.RIGHT_DOUBLE: "Chuột phải (nhấp đúp)",
}


class CanvasSource(Protocol):
    """Anything that finds the canvas in a frame (``CanvasLocator``, ``FixedCanvas``)."""

    def locate(self, frame: np.ndarray) -> CanvasFix: ...


class FixedCanvas:
    """A canvas at a known place (tests, or a frame that IS the canvas)."""

    def __init__(self, origin: Point = Point(0, 0), scale: float = 1.0, size: tuple[int, int] = (1000, 600)) -> None:
        w, h = round(size[0] * scale), round(size[1] * scale)
        self.fix = CanvasFix(Rect(origin.x, origin.y, w, h), scale, Confidence.HIGH, ("fixed",))

    def locate(self, frame: np.ndarray) -> CanvasFix:
        return self.fix


LogFn = Callable[[str, str], None]  # (level, message)
ProgressFn = Callable[[int, "int | None"], None]
StatusFn = Callable[["CanvasFix | None", "SceneMatch | None"], None]


class RunContext:
    def __init__(
        self,
        capturer: Capturer,
        input_driver: InputDriver,
        coords: CoordMap,
        matcher: Matcher,
        canvas_source: CanvasSource,
        scenes: SceneDetector | None = None,
        control: RunControl | None = None,
        debug_sink: DebugSink | None = None,
        png_sink: PngSink | None = None,
        debug_enabled: Callable[[], bool] = lambda: False,
        overlay_enabled: Callable[[], bool] = lambda: False,
        on_log: LogFn | None = None,
        on_progress: ProgressFn | None = None,
        on_status: StatusFn | None = None,
        task_name: str = "",
        window_label: str = "",
        canvas_max_age_s: float = 10.0,
        canvas_timeout_s: float = 3.0,
        poll_interval_s: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
        popups: PopupGuard | None = None,
        policy: Callable[[str], bool] | None = None,
        ocr: OcrEngine | None = None,
        panels: PanelTidier | None = None,
    ) -> None:
        self.capturer = capturer
        self.ocr = ocr
        self.panels = panels
        self.popups = popups
        # answers the game's cost questions (popups.yaml ``policy``): True = accept; default: decline
        self.policy: Callable[[str], bool] = policy or (lambda name: False)
        self._dismissing = False
        self.input = input_driver
        self.coords = coords
        self.matcher = matcher
        self.canvas_source = canvas_source
        self.scenes = scenes
        self.control = control or RunControl()
        self.debug_sink = debug_sink
        self.png_sink = png_sink
        self.debug_enabled = debug_enabled
        self.overlay_enabled = overlay_enabled
        self._on_log = on_log
        self._on_progress = on_progress
        self._on_status = on_status
        self.task_name = task_name
        self.window_label = window_label
        self.canvas_max_age_s = canvas_max_age_s
        self.canvas_timeout_s = canvas_timeout_s
        self.poll_interval_s = poll_interval_s
        self._clock = clock

        self._frame: np.ndarray | None = None
        self._canvas: CanvasFix | None = None
        self._canvas_at = float("-inf")
        self._canvas_dirty = False
        self._canvas_frame_size: tuple[int, int] | None = None  # (w, h) of the frame located on
        self._scene: SceneMatch | None = None
        self._pending: list[Detection] = []
        self._clicks: list[Point] = []
        self._step = ""
        self.click_count = 0
        # Run-history counters (spins, floors, bosses...): ``count()`` from features, plus the
        # click count and the last ``progress()`` reported (docs/06 "Run history").
        self.counters: dict[str, int] = {}
        self.last_progress: tuple[int, int | None] | None = None

    # --- state ----------------------------------------------------------------------------------
    @property
    def last_frame(self) -> np.ndarray | None:
        return self._frame

    @property
    def last_canvas(self) -> CanvasFix | None:
        return self._canvas

    @property
    def last_scene(self) -> SceneMatch | None:
        return self._scene

    @property
    def cancelled(self) -> bool:
        return self.control.stopped

    def _should_publish(self) -> bool:
        return self.debug_enabled() or self.overlay_enabled()

    # --- reporting ------------------------------------------------------------------------------
    def log(self, message: str, level: str = "info") -> None:
        log.log(logging.getLevelNamesMapping().get(level.upper(), logging.INFO), "%s", message)
        if self._on_log is not None:
            self._on_log(level, message)

    def info(self, message: str) -> None:
        self.log(message, "info")

    def warn(self, message: str) -> None:
        self.log(message, "warning")

    def progress(self, done: int, total: int | None = None) -> None:
        self.last_progress = (int(done), None if total is None else int(total))
        if self._on_progress is not None:
            self._on_progress(done, total)

    def count(self, name: str, n: int = 1) -> int:
        """Add ``n`` to the run-history counter ``name`` (e.g. ``"spins"``); returns the new value."""
        value = self.counters.get(name, 0) + int(n)
        self.counters[name] = value
        return value

    def counter_snapshot(self) -> dict[str, int]:
        """The counters saved in the run history: feature counters, clicks and the last progress."""
        snap = {"clicks": int(self.click_count), **self.counters}
        if self.last_progress is not None:
            done, total = self.last_progress
            snap.setdefault("progress", done)
            if total is not None:
                snap.setdefault("progress_total", total)
        return snap

    def step(self, name: str) -> None:
        """Name the current step (shown in the debug status line and PNG file names)."""
        self._step = name

    def _status(self) -> None:
        if self._on_status is not None:
            self._on_status(self._canvas, self._scene)

    # --- waiting --------------------------------------------------------------------------------
    def checkpoint(self) -> None:
        """Raise ``Cancelled`` when stopped; block while paused."""
        self.control.checkpoint()

    def sleep(self, ms: float) -> None:
        """Cancellable, pause-aware sleep in milliseconds."""
        self.control.sleep(max(0.0, ms) / 1000.0, self._clock)

    def sleep_s(self, seconds: float) -> None:
        self.control.sleep(max(0.0, seconds), self._clock)

    # --- capture and canvas ---------------------------------------------------------------------
    def grab(self) -> np.ndarray:
        """A fresh frame; starts a new debug frame (detections and clicks refer to it)."""
        self.checkpoint()
        frame = self.capturer.grab()
        self._frame = frame
        self._pending = []
        self._clicks = []
        return frame

    def _invalidate_capture(self) -> None:
        inv = getattr(self.capturer, "invalidate", None)
        if callable(inv):
            inv()

    def _locate(self, frame: np.ndarray) -> CanvasFix:
        fix = self.canvas_source.locate(frame)
        self._canvas = fix
        self._canvas_at = self._clock()
        self._canvas_dirty = False
        self._canvas_frame_size = (int(frame.shape[1]), int(frame.shape[0]))
        self._pending.extend(fix.detections)
        self._status()
        return fix

    def _window_resized(self, frame: np.ndarray | None = None) -> bool:
        """True when the client size differs from the frame the canvas was located on.

        Uses ``frame`` when given, else the capturer's optional cheap ``client_size()``. A window
        move/resize or a re-laid-out page then forces a re-locate before the next click.
        """
        if self._canvas_frame_size is None:
            return False
        if frame is not None:
            size: tuple[int, int] | None = (int(frame.shape[1]), int(frame.shape[0]))
        else:
            fn = getattr(self.capturer, "client_size", None)
            if not callable(fn):
                return False
            try:
                size = fn()
            except Exception:  # window gone etc.: the next grab reports it properly
                return False
        if size is None:
            return False
        return tuple(size) != self._canvas_frame_size

    def observe(self) -> tuple[np.ndarray, CanvasFix]:
        """Capture a frame and return it with the canvas (re-located when stale or after a miss).

        Known harmless pop-ups (``popups.yaml``) are closed first, so every feature sees the screen
        underneath them.
        """
        frame = self.grab()
        stale = self._clock() - self._canvas_at > self.canvas_max_age_s
        if (self._canvas is None or stale or self._canvas_dirty or not self._canvas.reliable
                or self._window_resized(frame)):
            fix = self._locate(frame)
        else:
            fix = self._canvas
        if self.popups and not self._dismissing:
            frame, fix = self._close_popups(frame, fix)
        return frame, fix

    def _close_popups(self, frame: np.ndarray, fix: CanvasFix) -> tuple[np.ndarray, CanvasFix]:
        """Close listed pop-ups shown on ``frame``; returns a fresh frame once the screen is clear."""
        assert self.popups is not None
        self._dismissing = True
        try:
            for _ in range(self.popups.max_rounds):
                hit = self.popups.find(frame, fix, self.matcher)
                if hit is None:
                    return frame, fix
                popup = hit.popup
                decline = popup.policy is not None and not self.policy(popup.policy)
                point = hit.decline_point if decline and hit.decline_point is not None else hit.point
                self._pending.extend(hit.detections)
                self._clicks.append(point)
                if popup.policy is None:
                    self.info(f"Tự đóng popup: {popup.description}")
                    self.count("popups")
                elif decline:
                    self.warn(f"{popup.description}: cài đặt đang tắt, bấm Huỷ")
                else:
                    self.info(f"{popup.description}: đồng ý theo cài đặt")
                    self.count(popup.name)
                if self._should_publish():
                    self.publish_debug(step=f"popup {popup.name}")
                self.checkpoint()
                self.input.click(point, "left", False)
                self.click_count += 1
                self._invalidate_capture()
                self.sleep_s(self.popups.settle_s)
                if decline:
                    raise TaskStopped(popup.stop_message or f"{popup.description}: đã từ chối")
                frame = self.grab()
                if self._window_resized(frame):
                    fix = self._locate(frame)
            if self.popups.find(frame, fix, self.matcher) is not None:
                self.warn(f"Popup vẫn còn sau {self.popups.max_rounds} lần đóng; để tính năng xử lý tiếp")
            return frame, fix
        finally:
            self._dismissing = False

    def canvas(self, refresh: bool = False) -> CanvasFix:
        """The canvas on a fresh frame; ``refresh`` forces the locator to run again."""
        if refresh:
            return self._locate(self.grab())
        return self.observe()[1]

    def reliable_canvas(self, timeout_s: float | None = None) -> CanvasFix:
        """A HIGH-confidence canvas, re-locating until ``timeout_s``; raises ``CanvasNotFound``."""
        fix = self._canvas
        stale = self._clock() - self._canvas_at > self.canvas_max_age_s
        if (fix is not None and fix.reliable and not stale and not self._canvas_dirty
                and not self._window_resized()):
            return fix
        limit = self.canvas_timeout_s if timeout_s is None else timeout_s
        deadline = self._clock() + limit
        while True:
            fix = self._locate(self.grab())
            if fix.reliable:
                return fix
            remaining = deadline - self._clock()
            if remaining <= 0:
                self.publish_debug(note="canvas not found", failed=False)
                raise CanvasNotFound(
                    f"không tìm thấy khung game (độ tin cậy {fix.confidence.value}) sau {limit:.1f}s"
                )
            self.sleep_s(min(self.poll_interval_s, remaining))

    # --- clicking -------------------------------------------------------------------------------
    def resolve(self, target: ClickTarget, fix: CanvasFix | None = None) -> Point:
        """Client point for a target (see the module docstring for the coordinate rules)."""
        if isinstance(target, Match):
            return target.center
        if isinstance(target, ObjectResult):
            if target.point is None:
                raise ValueError(f"object {target.name!r} was not found; nothing to click")
            return target.point
        fix = fix or self.reliable_canvas()
        if isinstance(target, str):
            return self.coords.point(target, origin=fix.origin, scale=fix.scale)
        if isinstance(target, Point):
            return fix.to_frame(target)
        raise TypeError(f"cannot click {target!r}")

    def click(
        self,
        target: ClickTarget,
        button: MouseButton | ClickType = "left",
        double: bool = False,
        label: str | None = None,
    ) -> Point:
        """Click a target; returns the client point clicked."""
        self.checkpoint()
        if isinstance(button, ClickType):
            button, double = button.button, button.double
        p = self.resolve(target)
        name = label or (target if isinstance(target, str) else getattr(target, "name", "point"))
        self._clicks.append(p)
        if self._should_publish():
            self.publish_debug(step=f"click {name}")
        self.checkpoint()
        self.input.click(p, button, double)
        self.click_count += 1
        self._invalidate_capture()
        return p

    # --- searching ------------------------------------------------------------------------------
    def _search(self, frame: np.ndarray, fix: CanvasFix, name: str, region: Rect | None,
                threshold: float | None) -> Detection:
        roi = fix.rect_to_frame(region) if region is not None else fix.rect
        det = self.matcher.search(name, roi, threshold, frame=frame, scale=fix.scale)
        self._pending.append(det)
        return det

    def find(self, name: str, region: Rect | None = None, threshold: float | None = None) -> Match | None:
        """Search once inside the canvas (or a canvas ``region``)."""
        frame, fix = self.observe()
        det = self._search(frame, fix, name, region, threshold)
        if not det.found:
            self._canvas_dirty = True
        if self._should_publish():
            self.publish_debug(step=f"find {name}")
        return Match(det.name, det.rect, det.score) if det.found else None

    def wait_for(
        self,
        names: str | Sequence[str],
        timeout_s: float,
        region: Rect | None = None,
        threshold: float | None = None,
        interval_s: float | None = None,
    ) -> Match:
        """Poll until one of ``names`` appears; raises ``WaitTimeout`` / ``Cancelled``."""
        wanted = [names] if isinstance(names, str) else list(names)
        if not wanted:
            raise ValueError("wait_for needs at least one template name")
        interval = self.poll_interval_s if interval_s is None else interval_s
        deadline = self._clock() + max(0.0, timeout_s)
        best = float("-inf")
        while True:
            frame, fix = self.observe()
            dets = [self._search(frame, fix, n, region, threshold) for n in wanted]
            if self._should_publish():
                self.publish_debug(step=f"wait {'/'.join(wanted)}")
            found = [d for d in dets if d.found]
            if found:
                d = max(found, key=lambda d: d.score)
                return Match(d.name, d.rect, d.score)
            self._canvas_dirty = True
            best = max([best, *(d.score for d in dets)])
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise WaitTimeout(
                    f"không thấy {'/'.join(wanted)} sau {timeout_s:.1f}s (điểm cao nhất {best:.2f})"
                )
            self.sleep_s(min(interval, remaining))

    def wait_gone(
        self, name: str, timeout_s: float, region: Rect | None = None,
        threshold: float | None = None, interval_s: float | None = None,
    ) -> None:
        """Poll until ``name`` is no longer visible."""
        interval = self.poll_interval_s if interval_s is None else interval_s
        deadline = self._clock() + max(0.0, timeout_s)
        while True:
            frame, fix = self.observe()
            det = self._search(frame, fix, name, region, threshold)
            if self._should_publish():
                self.publish_debug(step=f"wait gone {name}")
            if not det.found:
                return
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise WaitTimeout(f"{name} vẫn còn sau {timeout_s:.1f}s")
            self.sleep_s(min(interval, remaining))

    # --- panels ---------------------------------------------------------------------------------
    def tidy_panels(self) -> list[str]:
        """Collapse the HUD panels that cover the game (``panels.yaml``); done before every run."""
        if self.panels is None or not self.panels.panels:
            return []
        self.step("thu gọn các bảng")
        closed = self.panels.tidy(self)
        if closed:
            self.info("Đã thu gọn: " + ", ".join(closed))
        return closed

    # --- text -----------------------------------------------------------------------------------
    def crop(self, region: Rect) -> np.ndarray:
        """A fresh frame's pixels inside a canvas ``region`` (frame scale, BGR)."""
        frame, fix = self.observe()
        r = fix.rect_to_frame(region)
        return frame[max(0, r.y):max(0, r.bottom), max(0, r.x):max(0, r.right)]

    def read_text(self, region: Rect) -> list[OcrLine]:
        """OCR of a canvas ``region``; line and word rectangles in canvas coordinates (1000x600)."""
        if self.ocr is None:
            raise OcrUnavailable("chưa có OCR (nhận dạng chữ) cho lần chạy này")
        frame, fix = self.observe()
        r = fix.rect_to_frame(region)
        x0, y0 = max(0, r.x), max(0, r.y)
        lines = self.ocr.read(frame[y0:max(y0, r.bottom), x0:max(x0, r.right)])
        # frame pixels of the crop -> canvas: (x0 + p) mapped back by the canvas origin and scale
        s = fix.scale
        out = [ln.moved((x0 - fix.rect.x) / s, (y0 - fix.rect.y) / s, 1 / s) for ln in lines]
        if self._should_publish():
            self._pending.extend(
                Detection(f"ocr '{ln.text}'", fix.rect_to_frame(ln.rect), 1.0, 0.0, True) for ln in out)
            self.publish_debug(step=f"ocr {region.x},{region.y}")
        return out

    # --- scenes ---------------------------------------------------------------------------------
    def _require_scenes(self) -> SceneDetector:
        if self.scenes is None:
            raise NgoaLongError("this run has no scene detector")
        return self.scenes

    def detect_scene(self) -> SceneMatch:
        scenes = self._require_scenes()
        frame, fix = self.observe()
        m = scenes.detect(frame, fix)
        self._pending.extend(m.detections)
        self._scene = m
        self._status()
        if self._should_publish():
            self.publish_debug(step="detect scene")
        return m

    def expect_scene(self, scene: str, timeout_s: float, interval_s: float | None = None) -> SceneMatch:
        """Poll until ``scene`` is shown; raises ``SceneTimeout`` naming what was shown instead."""
        scenes = self._require_scenes()
        scenes.get(scene)  # unknown scene names fail fast
        interval = self.poll_interval_s if interval_s is None else interval_s
        deadline = self._clock() + max(0.0, timeout_s)
        while True:
            frame, fix = self.observe()
            m = scenes.check(frame, fix, scene)
            self._pending.extend(m.detections)
            if self._should_publish():
                self.publish_debug(step=f"expect {scene}")
            if m.found:
                self._scene = m
                self._status()
                return m
            self._canvas_dirty = True
            remaining = deadline - self._clock()
            if remaining <= 0:
                actual = scenes.detect(frame, fix)
                self._scene = actual
                self._status()
                shown = actual.name if actual.found else f"không rõ (gần nhất {actual.candidate})"
                raise SceneTimeout(
                    f"không thấy màn hình {scene} sau {timeout_s:.1f}s (đang hiển thị: {shown})",
                    scene, actual,
                )
            self.sleep_s(min(interval, remaining))

    def expect_scene_in(
        self, scenes: Sequence[str], timeout_s: float, interval_s: float | None = None
    ) -> SceneMatch:
        """Poll until one of ``scenes`` is shown; raises ``SceneTimeout`` naming what was shown instead."""
        detector = self._require_scenes()
        for name in scenes:
            detector.get(name)  # unknown scene names fail fast
        interval = self.poll_interval_s if interval_s is None else interval_s
        deadline = self._clock() + max(0.0, timeout_s)
        while True:
            frame, fix = self.observe()
            for name in scenes:
                m = detector.check(frame, fix, name)
                self._pending.extend(m.detections)
                if m.found:
                    self._scene = m
                    self._status()
                    if self._should_publish():
                        self.publish_debug(step=f"expect {name}")
                    return m
            self._canvas_dirty = True
            remaining = deadline - self._clock()
            if remaining <= 0:
                actual = detector.detect(frame, fix)
                self._scene = actual
                self._status()
                shown = actual.name if actual.found else f"không rõ (gần nhất {actual.candidate})"
                joined = " / ".join(scenes)
                raise SceneTimeout(
                    f"không thấy màn hình trong [{joined}] sau {timeout_s:.1f}s (đang hiển thị: {shown})",
                    scenes[0] if scenes else "", actual,
                )
            self.sleep_s(min(interval, remaining))

    def find_object(self, scene: str, obj: str) -> ObjectResult:
        scenes = self._require_scenes()
        frame, fix = self.observe()
        r = scenes.find_object(frame, fix, scene, obj)
        if r.detection is not None:
            self._pending.append(r.detection)
        return r

    def click_object(self, scene: str, obj: str, button: MouseButton | ClickType = "left",
                     double: bool = False) -> Point:
        r = self.find_object(scene, obj)
        if not r.found:
            raise WaitTimeout(f"không thấy {scene}.{obj}")
        self.reliable_canvas()
        return self.click(r, button, double, label=f"{scene}.{obj}")

    # --- debug ----------------------------------------------------------------------------------
    def debug_frame(self, step: str | None = None, note: str = "", failed: bool = False) -> DebugFrame:
        sc = self._scene
        return DebugFrame(
            image=self._frame, canvas=self._canvas,
            scene=sc.name if sc is not None else None,
            scene_score=sc.score if sc is not None else None,
            detections=list(self._pending), clicks=list(self._clicks),
            window=self.window_label, task=self.task_name,
            step=step if step is not None else self._step, failed=failed, note=note,
        )

    def publish_debug(self, step: str | None = None, note: str = "", failed: bool = False) -> DebugFrame:
        """Publish the current frame to the live sinks and the PNG sink (debug mode or failure)."""
        frame = self.debug_frame(step, note, failed)
        if self.png_sink is not None and frame.image is not None:
            self.png_sink.enabled = self.debug_enabled()
            try:
                self.png_sink.save(frame)
            except Exception:  # saving a debug image must never break the run
                log.exception("could not save debug frame")
        if self.debug_sink is not None and (failed or self._should_publish()):
            try:
                self.debug_sink.publish(frame)
            except Exception:
                log.exception("debug sink failed")
        return frame

    def save_failure(self, note: str) -> Path | None:
        """Always save the last frame (capturing one if needed) as a failure PNG."""
        if self._frame is None:
            try:
                self._frame = self.capturer.grab()
            except Exception as exc:  # the window may be gone: nothing to save
                log.warning("no frame for the failure screenshot: %s", exc)
                return None
        before = len(self.png_sink.saved) if self.png_sink is not None else 0
        self.publish_debug(note=note, failed=True)
        if self.png_sink is not None and len(self.png_sink.saved) > before:
            return self.png_sink.saved[-1]
        return None
