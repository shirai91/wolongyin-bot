"""``Actor``: the decision loop's hands and eyes on one game window (docs/08 "Code layout").

It captures frames, keeps the canvas fix fresh, turns canvas points (1000x600) into window points
for clicks, keys and typing, and records what the bot saw and did for the debug views (overlay,
debug panel, PNGs) and the run log. Every input first passes the run's Stop / Pause switches.

During the transition (R2-R3) it is built from the old ``RunContext`` (``Actor.from_context``);
R4 builds it directly in ``app.py``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import numpy as np

from ngoalong.core.detection import Detection
from ngoalong.core.geometry import Point
from ngoalong.core.input import VK_BACK, VK_DELETE, VK_RETURN
from ngoalong.core.locator import CanvasFix
from ngoalong.debug.model import DebugFrame
from ngoalong.engine.control import RunControl
from ngoalong.perception import Perceiver, Snapshot
from ngoalong.perception.screens import CLOSE, UNKNOWN

if TYPE_CHECKING:
    from ngoalong.engine.context import RunContext

log = logging.getLogger(__name__)

RELOCATE_S = 5.0  # the canvas is located again at least this often (22 ms)
GESTURE_GAP_S = 0.12  # between the parts of a typing gesture (double-click, Backspace, text, Enter)
NEUTRAL = Point(600, 2)  # the mouse rests here after a click: top edge, no button on any screen (a move
# outside the canvas never reaches the game, so the last button would keep its hover glow)


def snapshot_frame(frame: np.ndarray, fix: CanvasFix, snap: Snapshot, step: str = "",
                   clicks: tuple[Point, ...] = (), window: str = "", task: str = "",
                   note: str = "", failed: bool = False) -> DebugFrame:
    """A ``DebugFrame`` that draws a Snapshot: every part found (green), the ✕ / buttons no screen
    claimed (red), the screen stack as the status line's scene, planned clicks (canvas points)."""
    dets: list[Detection] = []
    unknown = {(h.part, h.rect.x, h.rect.y) for h in snap.unknown}
    for name, hits in snap.senses.scan.searched().items():
        for h in hits:
            bad = (h.part, h.rect.x, h.rect.y) in unknown
            label = f"{UNKNOWN} {name}" if bad else name
            dets.append(Detection(label, fix.rect_to_frame(h.rect), h.score, 1.0 if bad else 0.0, not bad))
    return DebugFrame(
        image=frame, canvas=fix, scene=snap.describe(read=False), detections=dets,
        clicks=[fix.to_frame(p) for p in clicks], window=window, task=task, step=step,
        failed=failed, note=note,
    )


class Actor:
    def __init__(
        self,
        capturer: Any,
        input_driver: Any,
        canvas_source: Any,
        control: RunControl,
        on_log: Callable[[str, str], None] | None = None,
        on_progress: Callable[[int, int | None], None] | None = None,
        on_count: Callable[[str, int], int] | None = None,
        on_status: Callable[[Any, Any], None] | None = None,
        publish: Callable[[DebugFrame], None] | None = None,
        debug_enabled: Callable[[], bool] = lambda: False,
        window: str = "",
        task: str = "",
    ) -> None:
        self.capturer = capturer
        self.input = input_driver
        self.canvas_source = canvas_source
        self.control = control
        self._on_log = on_log
        self._on_progress = on_progress
        self._on_count = on_count
        self._on_status = on_status
        self._publish = publish
        self.debug_enabled = debug_enabled
        self.window = window
        self.task = task
        self.fix: CanvasFix | None = None
        self.frame: np.ndarray | None = None
        self._fix_at = float("-inf")
        self._fix_size: tuple[int, int] | None = None
        self.inputs = 0  # clicks, keys and texts sent (the frozen-client watchdog counts them)
        self.counters: dict[str, int] = {}
        self._ctx: RunContext | None = None

    @classmethod
    def from_context(cls, ctx: RunContext) -> Actor:
        """An actor on the window, switches, sinks and log of an old-style ``RunContext``."""

        def publish(frame: DebugFrame) -> None:
            if ctx.png_sink is not None and frame.image is not None:
                ctx.png_sink.enabled = ctx.debug_enabled()
                try:
                    ctx.png_sink.save(frame)
                except Exception:
                    log.exception("could not save a debug frame")
            if ctx.debug_sink is not None and (frame.failed or ctx.debug_enabled() or ctx.overlay_enabled()):
                try:
                    ctx.debug_sink.publish(frame)
                except Exception:
                    log.exception("debug sink failed")

        a = cls(ctx.capturer, ctx.input, ctx.canvas_source, ctx.control, ctx._on_log, ctx.progress,
                ctx.count, ctx._on_status, publish,
                lambda: ctx.debug_enabled() or ctx.overlay_enabled(), ctx.window_label, ctx.task_name)
        a._ctx = ctx
        return a

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

    @staticmethod
    def canvas(frame: np.ndarray, fix: CanvasFix) -> np.ndarray:
        return Perceiver.canvas(frame, fix)

    # --- acting ---------------------------------------------------------------------------------
    def _point(self, p: Point) -> Point:
        if self.fix is None or not self.fix.reliable:
            raise RuntimeError("no reliable canvas to click on")
        return self.fix.to_frame(p)

    def _invalidate(self) -> None:
        inv = getattr(self.capturer, "invalidate", None)
        if callable(inv):
            inv()

    def click(self, p: Point, double: bool = False) -> None:
        self.control.checkpoint()
        self.input.click(self._point(p), "left", double)
        self.inputs += 1
        if self._ctx is not None:
            self._ctx.click_count += 1
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
        """Double-click a text box, clear it (Backspace and Delete: the caret may land before the
        old text), type, Enter (page box, quantity boxes)."""
        if at is not None:
            self.control.checkpoint()
            self.input.click(self._point(at), "left", True)
            self.control.sleep(GESTURE_GAP_S)
        for _ in range(clear):  # the caret may sit before or after the old text
            self.input.key(VK_BACK)
            self.input.key(VK_DELETE)
        self.control.sleep(GESTURE_GAP_S)
        self.input.text(text)
        if enter:
            self.control.sleep(GESTURE_GAP_S)
            self.input.key(VK_RETURN)
        self.inputs += 1
        self.rest_mouse()
        self._invalidate()

    def sleep(self, seconds: float) -> None:
        self.control.sleep(seconds)

    # --- reporting ------------------------------------------------------------------------------
    def log(self, message: str, level: str = "info") -> None:
        log.log(logging.getLevelNamesMapping().get(level.upper(), logging.INFO), "%s", message)
        if self._on_log is not None:
            self._on_log(level, message)

    def progress(self, done: int, total: int | None = None) -> None:
        if self._on_progress is not None:
            self._on_progress(done, total)

    def count(self, name: str, n: int = 1) -> int:
        """Add to a run-history counter (floors, chests, ...)."""
        if self._on_count is not None:
            return self._on_count(name, n)
        self.counters[name] = self.counters.get(name, 0) + n
        return self.counters[name]

    def status(self, text: str) -> None:
        """The app's status line ("Màn hình: ...")."""
        if self._on_status is not None:
            self._on_status(self.fix, text)

    def show(self, snap: Snapshot, step: str, clicks: tuple[Point, ...] = ()) -> None:
        """Draw this tick on the debug views (overlay / panel; PNGs in debug mode)."""
        if self._publish is None or self.frame is None or self.fix is None or not self.debug_enabled():
            return
        self._publish(snapshot_frame(self.frame, self.fix, snap, step, clicks, self.window, self.task))

    def hand_over_failure(self, snap: Snapshot | None, note: str) -> None:
        """Leave the last frame, drawn with its Snapshot, where the runner's failure screenshot
        (``RunContext.save_failure``) picks it up."""
        ctx = self._ctx
        if ctx is None or self.frame is None or self.fix is None:
            return
        ctx._frame = self.frame
        ctx._canvas = self.fix
        ctx._clicks = []
        ctx._pending = snapshot_frame(self.frame, self.fix, snap).detections if snap is not None else []
        ctx.step(note if snap is None else snap.describe(read=False))


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
