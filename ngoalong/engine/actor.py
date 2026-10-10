"""``Actor``: the bot's hands and eyes on one game window for one run (docs/08 "Code layout").

It captures frames, keeps the canvas fix fresh, turns canvas points (1000x600) into window points
for clicks, keys and typing, and records what the bot saw and did: the run log, progress and
counters (saved in the run history), the debug views (overlay, debug panel, PNGs) and the failure
screenshot. Every input first passes the run's Stop / Pause switches (``RunControl``).

The app builds one per run (``AppServices.build_actor``); the runner hands it to ``Task.run``. A
``Feature`` runs it on the decision loop (``engine/loop.py``); AutoClick just clicks with it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from ngoalong.core.capture import save_png
from ngoalong.core.detection import Detection
from ngoalong.core.errors import NgoaLongError
from ngoalong.core.geometry import Point
from ngoalong.core.input import VK_BACK, VK_DELETE, VK_END, VK_RETURN, MouseButton
from ngoalong.core.locator import CanvasFix
from ngoalong.debug.model import DebugFrame
from ngoalong.debug.sink import PngSink, slug
from ngoalong.engine.control import RunControl
from ngoalong.perception import Perceiver, Snapshot
from ngoalong.perception.screens import CLOSE, UNKNOWN

log = logging.getLogger(__name__)

RELOCATE_S = 5.0  # the canvas is located again at least this often (22 ms)
CANVAS_TIMEOUT_S = 3.0  # ``canvas_fix`` keeps looking this long for a reliable canvas
GESTURE_GAP_S = 0.12  # between the parts of a typing gesture (double-click, keys, text, Enter)
NEUTRAL = Point(600, 2)  # the mouse rests here after a click: top edge, no button on any screen (a move
# outside the canvas never reaches the game, so the last button would keep its hover glow)


class CanvasNotFound(NgoaLongError):
    """The game canvas could not be located reliably, so clicking would be blind."""


def snapshot_frame(frame: np.ndarray, fix: CanvasFix, snap: Snapshot | None, step: str = "",
                   clicks: tuple[Point, ...] = (), window: str = "", task: str = "",
                   note: str = "", failed: bool = False) -> DebugFrame:
    """A ``DebugFrame`` that draws a Snapshot: every part found (green), the ✕ / buttons no screen
    claimed (red), the screen stack as the status line's scene, planned clicks (canvas points)."""
    dets: list[Detection] = []
    if snap is not None:
        unknown = {(h.part, h.rect.x, h.rect.y) for h in snap.unknown}
        for name, hits in snap.senses.scan.searched().items():
            for h in hits:
                bad = (h.part, h.rect.x, h.rect.y) in unknown
                label = f"{UNKNOWN} {name}" if bad else name
                dets.append(Detection(label, fix.rect_to_frame(h.rect), h.score, 1.0 if bad else 0.0, not bad))
    return DebugFrame(
        image=frame, canvas=fix, scene=snap.describe(read=False) if snap is not None else None,
        detections=dets, clicks=[fix.to_frame(p) for p in clicks], window=window, task=task, step=step,
        failed=failed, note=note,
    )


class Actor:
    def __init__(
        self,
        capturer: Any,
        input_driver: Any,
        canvas_source: Any,
        control: RunControl | None = None,
        *,
        on_log: Callable[[str, str], None] | None = None,
        on_progress: Callable[[int, int | None], None] | None = None,
        on_status: Callable[[CanvasFix | None, str | None], None] | None = None,
        debug_sink: Any = None,
        png_sink: PngSink | None = None,
        debug_enabled: Callable[[], bool] = lambda: False,
        overlay_enabled: Callable[[], bool] = lambda: False,
        policy: Callable[[str], bool] | None = None,
        popups: Any = None,
        panels: Any = None,
        matcher: Any = None,
        window: str = "",
        task: str = "",
    ) -> None:
        self.capturer = capturer
        self.input = input_driver
        self.canvas_source = canvas_source
        self.control = control or RunControl()
        self._on_log = on_log
        self._on_progress = on_progress
        self._on_status = on_status
        self.debug_sink = debug_sink
        self.png_sink = png_sink
        self.debug_enabled = debug_enabled
        self.overlay_enabled = overlay_enabled
        # the user's answers to the game's cost questions (Settings.game.<name>, e.g.
        # spend_gold_skip_wait): the pop-up guard and features that answer such a box themselves
        self.policy: Callable[[str], bool] = policy or (lambda name: False)
        # the profile's interrupt data (popups.yaml, panels.yaml) and the matcher that searches it
        self.popups = popups
        self.panels = panels
        self.matcher = matcher
        self.window = window
        self.task = task
        self.fix: CanvasFix | None = None
        self.frame: np.ndarray | None = None
        self._fix_at = float("-inf")
        self._fix_size: tuple[int, int] | None = None
        self.inputs = 0  # clicks, keys and texts sent (the frozen-client watchdog counts them)
        self.clicks = 0
        # Run-history counters (chests, floors, rounds...): ``count()``, plus the clicks and the last
        # ``progress()`` (docs/06 "Run history").
        self.counters: dict[str, int] = {}
        self.last_progress: tuple[int, int | None] | None = None
        self._failure: DebugFrame | None = None
        self._kept = 0

    # --- seeing ---------------------------------------------------------------------------------
    def look(self, relocate: bool = False) -> tuple[np.ndarray, CanvasFix]:
        """A fresh frame and its canvas fix (located again when stale, unreliable or resized)."""
        self.control.checkpoint()
        frame = self.capturer.grab()
        size = (int(frame.shape[1]), int(frame.shape[0]))
        now = time.monotonic()
        if (relocate or self.fix is None or not self.fix.reliable or size != self._fix_size
                or now - self._fix_at > RELOCATE_S):
            self.fix = self.canvas_source.locate(frame)
            self._fix_at, self._fix_size = now, size
            if self._on_status is not None:
                self._on_status(self.fix, None)
        self.frame = frame
        return frame, self.fix

    def canvas_fix(self, timeout_s: float = CANVAS_TIMEOUT_S) -> CanvasFix:
        """A reliable canvas fix without capturing while the last one is fresh and the window kept its
        size; else look again until one is found (``CanvasNotFound`` after ``timeout_s``)."""
        fix = self.fix
        if fix is not None and fix.reliable and time.monotonic() - self._fix_at <= RELOCATE_S \
                and self._client_size() in (None, self._fix_size):
            return fix
        deadline = time.monotonic() + timeout_s
        while True:
            _frame, fix = self.look(relocate=True)
            if fix.reliable:
                return fix
            if time.monotonic() >= deadline:
                raise CanvasNotFound(f"không tìm thấy khung game (độ tin cậy {fix.confidence.value}) "
                                     f"sau {timeout_s:g}s")
            self.control.sleep(0.25)

    def _client_size(self) -> tuple[int, int] | None:
        fn = getattr(self.capturer, "client_size", None)
        if not callable(fn):
            return None
        try:
            return tuple(fn())  # type: ignore[return-value]
        except Exception:  # a window that just went away: the next capture says so
            return None

    def visible_fraction(self) -> float | None:
        """Share of the game window shown on screen (None when the capturer cannot tell)."""
        fn = getattr(self.capturer, "visible_fraction", None)
        if not callable(fn):
            return None
        try:
            return fn()
        except Exception:
            return None

    @staticmethod
    def canvas(frame: np.ndarray, fix: CanvasFix) -> np.ndarray:
        return Perceiver.canvas(frame, fix)

    # --- acting ---------------------------------------------------------------------------------
    def _point(self, p: Point) -> Point:
        if self.fix is None or not self.fix.reliable:
            raise CanvasNotFound("no reliable canvas to click on")
        return self.fix.to_frame(p)

    def _invalidate(self) -> None:
        inv = getattr(self.capturer, "invalidate", None)
        if callable(inv):
            inv()

    def click(self, p: Point, double: bool = False, button: MouseButton = "left") -> None:
        """Click canvas point ``p``, then rest the mouse."""
        self.control.checkpoint()
        self.input.click(self._point(p), button, double)
        self.inputs += 1
        self.clicks += 1
        self.rest_mouse()
        self._invalidate()

    def burst(self, points: tuple[Point, ...], times: int, interval: float) -> None:
        """Click ``points`` in order, ``times`` cycles over, ``interval`` seconds between clicks,
        without looking (``Burst``). Stop / Pause are checked before each cycle, never inside one,
        so a stop leaves no cycle half done (it comes within one cycle: well under a second)."""
        tap = getattr(self.input, "tap", None) or self.input.click
        frame_points = [self._point(p) for p in points]
        for i in range(times):
            self.control.checkpoint()
            for j, p in enumerate(frame_points):
                if i or j:
                    time.sleep(interval)  # a high-resolution sleep; the switches' sleep has 15 ms steps
                tap(p)
            self.inputs += len(frame_points)
            self.clicks += len(frame_points)
        self.rest_mouse()
        self._invalidate()

    def rest_mouse(self) -> None:
        """Move the mouse off the game, so the clicked button stops glowing (a hovered tower row
        reads as selected, a hovered button scores lower)."""
        move = getattr(self.input, "move", None)
        if callable(move):
            move(self._point(NEUTRAL))

    def key(self, vk: int) -> None:
        self.control.checkpoint()
        self.input.key(vk)
        self.inputs += 1
        self._invalidate()

    def type_into(self, text: str, at: Point | None, clear: int, enter: bool) -> None:
        """Replace the number in a text box (``Text``): double-click ``at`` (focus; it usually
        selects the number too, but not always), End (the caret goes after the old text either
        way), Backspace ``clear`` times (the old text goes; a quantity box that is emptied puts "1"
        back with the caret in front of it), type ``text``, Delete ``clear`` times (removes that
        "1", or nothing), Enter. So no old digit survives, whether or not the double-click
        selected (2026-10-10: a double-click that did not select turned "200" into "1200")."""
        if at is not None:
            self.control.checkpoint()
            self.input.click(self._point(at), "left", True)
            self.control.sleep(GESTURE_GAP_S)
        if clear:
            self.input.key(VK_END)
            for _ in range(clear):
                self.input.key(VK_BACK)
            self.control.sleep(GESTURE_GAP_S)
        self.input.text(text)
        if clear:
            self.control.sleep(GESTURE_GAP_S)
            for _ in range(clear):
                self.input.key(VK_DELETE)
        if enter:
            self.control.sleep(GESTURE_GAP_S)
            self.input.key(VK_RETURN)
        self.inputs += 1
        self.rest_mouse()
        self._invalidate()

    def sleep(self, seconds: float) -> None:
        """Pause-aware, stoppable sleep."""
        self.control.sleep(max(0.0, seconds))

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
        """Add ``n`` to the run-history counter ``name``; returns the new value."""
        self.counters[name] = self.counters.get(name, 0) + int(n)
        return self.counters[name]

    def counter_snapshot(self) -> dict[str, int]:
        """The counters saved in the run history: feature counters, clicks and the last progress."""
        snap = {"clicks": int(self.clicks), **self.counters}
        if self.last_progress is not None:
            done, total = self.last_progress
            snap.setdefault("progress", done)
            if total is not None:
                snap.setdefault("progress_total", total)
        return snap

    def status(self, text: str) -> None:
        """The app's status line ("Màn hình: ...")."""
        if self._on_status is not None:
            self._on_status(self.fix, text)

    def _live(self) -> bool:
        return self.debug_enabled() or self.overlay_enabled()

    def publish(self, frame: DebugFrame) -> None:
        """A debug frame to the PNGs (debug mode, or a failure) and the live views. Never fails."""
        if self.png_sink is not None and frame.image is not None:
            self.png_sink.enabled = self.debug_enabled()
            try:
                self.png_sink.save(frame)
            except Exception:
                log.exception("could not save a debug frame")
        if self.debug_sink is not None and (frame.failed or self._live()):
            try:
                self.debug_sink.publish(frame)
            except Exception:
                log.exception("debug sink failed")

    def show(self, snap: Snapshot, step: str, clicks: tuple[Point, ...] = ()) -> None:
        """Draw this tick on the debug views (overlay / panel; PNGs in debug mode)."""
        if self.frame is None or self.fix is None or not self._live():
            return
        self.publish(snapshot_frame(self.frame, self.fix, snap, step, clicks, self.window, self.task))

    def keep(self, snap: Snapshot, name: str) -> Path | None:
        """Save the plain canvas of ``snap`` in the run folder (``event-NN-<name>.png``): a screen
        worth labelling later (a box seen for the first time). Never fails the run."""
        if self.png_sink is None:
            return None
        self._kept += 1
        path = Path(self.png_sink.directory) / f"event-{self._kept:02d}-{slug(name)}.png"
        try:
            save_png(path, snap.senses.canvas)
        except Exception:
            log.exception("could not keep a frame")
            return None
        self.log(f"Đã lưu khung hình {path.name}: {snap.describe(read=False)}")
        return path

    def hand_over_failure(self, snap: Snapshot | None, note: str) -> None:
        """Remember the last frame, drawn with its Snapshot, for the failure screenshot."""
        if self.frame is None or self.fix is None:
            return
        step = note if snap is None else snap.describe(read=False)
        self._failure = snapshot_frame(self.frame, self.fix, snap, step, (), self.window, self.task)

    def save_failure(self, note: str) -> Path | None:
        """Always save a failure PNG: the frame handed over by the loop, else the last frame (one is
        captured when there is none). Returns its path, or None when nothing could be saved."""
        frame = self._failure
        if frame is None:
            image = self.frame
            if image is None:
                try:
                    image = self.capturer.grab()
                except Exception as exc:  # the window may be gone: nothing to save
                    log.warning("no frame for the failure screenshot: %s", exc)
                    return None
            frame = DebugFrame(image=image, canvas=self.fix, window=self.window, task=self.task)
        frame.failed, frame.note = True, note
        before = len(self.png_sink.saved) if self.png_sink is not None else 0
        self.publish(frame)
        if self.png_sink is not None and len(self.png_sink.saved) > before:
            return self.png_sink.saved[-1]
        return None


def close_point(snap: Snapshot) -> Point | None:
    """The ✕ of the top dialog: its declared close point when a ✕ is there, else an unclaimed ✕."""
    if not snap.dialogs:
        return None
    top = snap.top
    closes = snap.parts(CLOSE)
    if top != UNKNOWN:
        spec = snap.book.by_name[top]
        if spec.close is not None and any(h.near(spec.close, 15) for h in closes):
            return spec.close
        return None
    unclaimed = [h for h in snap.unknown if h.part == CLOSE]
    return unclaimed[0].center if unclaimed else None
