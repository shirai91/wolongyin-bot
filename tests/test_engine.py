"""Engine: RunControl, RunContext (clicks, waits, scenes, debug publishing), execute(), registry."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import pytest

from conftest import PROFILE, ROOT
from ngoalong.core.coords import CoordMap
from ngoalong.core.drivers.fake import RecordingInputDriver
from ngoalong.core.drivers.file import FileCapturer
from ngoalong.core.errors import Cancelled, WaitTimeout
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.locator import CanvasLocator
from ngoalong.core.scenes import SceneDetector
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Match, Matcher
from ngoalong.debug.model import DebugFrame
from ngoalong.debug.sink import CallbackSink, PngSink
from ngoalong.engine import registry
from ngoalong.engine.context import ClickType, FixedCanvas, RunContext, SceneTimeout
from ngoalong.engine.control import RunControl
from ngoalong.engine.runner import RunStatus, execute
from ngoalong.engine.task import Task, TaskParams

CHEST = ROOT / "docs" / "game screenshots" / "chest-rolling-ui.png"
DUNGEON = ROOT / "docs" / "game screenshots" / "dungeon.png"
STORE = TemplateStore(PROFILE / "templates")


def blank(h: int = 800, w: int = 1400) -> np.ndarray:
    return np.zeros((h, w, 3), np.uint8)


def make_ctx(frames=None, canvas=None, real_scenes: bool = False, **kw) -> tuple[RunContext, RecordingInputDriver]:
    driver = RecordingInputDriver()
    matcher = Matcher(STORE, history=64)
    ctx = RunContext(
        capturer=FileCapturer(frames if frames is not None else [blank()], loop=True),
        input_driver=driver,
        coords=CoordMap.load(PROFILE / "coords"),
        matcher=matcher,
        canvas_source=canvas or FixedCanvas(Point(180, 110)),
        scenes=SceneDetector.load(PROFILE / "scenes", templates_dir=PROFILE / "templates") if real_scenes else None,
        poll_interval_s=0.05,
        **kw,
    )
    return ctx, driver


# --- RunControl ---------------------------------------------------------------------------------
def test_control_stop_interrupts_long_sleep() -> None:
    c = RunControl()
    threading.Timer(0.2, c.stop).start()
    t0 = time.monotonic()
    with pytest.raises(Cancelled):
        c.sleep(30)
    assert time.monotonic() - t0 < 1.0


def test_control_pause_blocks_until_resume() -> None:
    c = RunControl()
    c.pause()
    assert c.paused
    done = threading.Event()

    def worker() -> None:
        c.sleep(0.01)
        done.set()

    threading.Thread(target=worker, daemon=True).start()
    assert not done.wait(0.3)  # paused: the sleep cannot finish
    c.resume()
    assert done.wait(1.0)


def test_control_stop_wins_over_pause() -> None:
    c = RunControl()
    c.pause()
    threading.Timer(0.1, c.stop).start()
    t0 = time.monotonic()
    with pytest.raises(Cancelled):
        c.checkpoint()
    assert time.monotonic() - t0 < 1.0
    assert not c.paused  # stopped, not paused


# --- clicks -------------------------------------------------------------------------------------
def test_click_resolves_coords_points_and_matches() -> None:
    ctx, drv = make_ctx()
    assert ctx.click("quay_ruong.per_spin.1") == Point(180 + 440, 110 + 360)
    assert ctx.click(Point(10, 20), ClickType.RIGHT_DOUBLE) == Point(190, 130)
    assert ctx.click(Match("x", Rect(500, 500, 10, 20), 0.9)) == Point(505, 510)
    ev = drv.events
    assert [(e.button, e.double) for e in ev] == [("left", False), ("right", True), ("left", False)]
    assert ctx.click_count == 3


def test_click_scales_with_zoom() -> None:
    ctx, drv = make_ctx(canvas=FixedCanvas(Point(100, 50), scale=1.25))
    ctx.click("quay_ruong.per_spin.0")  # (200, 420) * 1.25
    assert drv.clicks == [Point(100 + 250, 50 + 525)]


def test_click_types() -> None:
    assert ClickType.MIDDLE_DOUBLE.button == "middle" and ClickType.MIDDLE_DOUBLE.double
    assert ClickType.LEFT.button == "left" and not ClickType.LEFT.double
    assert ClickType.RIGHT.label == "Chuột phải"
    assert len(ClickType) == 6


def test_click_needs_a_reliable_canvas() -> None:
    from ngoalong.engine.context import CanvasNotFound

    ctx, drv = make_ctx(canvas=CanvasLocator.from_profile(PROFILE), canvas_timeout_s=0.2)
    with pytest.raises(CanvasNotFound):
        ctx.click("quay_ruong.per_spin.0")  # all-black frame: no canvas
    assert drv.clicks == []


def test_click_on_real_screenshot_uses_located_canvas() -> None:
    ctx, drv = make_ctx(frames=[CHEST], canvas=CanvasLocator.from_profile(PROFILE))
    p = ctx.click("quay_ruong.per_spin.0")
    o = ctx.last_canvas.origin
    assert abs(o.x - 271) <= 2 and abs(o.y - 112) <= 2
    assert p == Point(o.x + 200, o.y + 420)


# --- waits --------------------------------------------------------------------------------------
def test_wait_for_times_out() -> None:
    ctx, _ = make_ctx()
    t0 = time.monotonic()
    with pytest.raises(WaitTimeout):
        ctx.wait_for("chest.wheel.title", timeout_s=0.3)
    assert time.monotonic() - t0 < 2.0


def test_wait_for_is_cancellable_within_one_second() -> None:
    ctx, _ = make_ctx()
    threading.Timer(0.2, ctx.control.stop).start()
    t0 = time.monotonic()
    with pytest.raises(Cancelled):
        ctx.wait_for("chest.wheel.title", timeout_s=60)
    assert time.monotonic() - t0 < 1.0


def test_find_and_wait_on_screenshot() -> None:
    ctx, _ = make_ctx(frames=[CHEST], canvas=CanvasLocator.from_profile(PROFILE))
    m = ctx.find("chest.wheel.title")
    assert m is not None and m.score > 0.9
    assert ctx.wait_for(["tower.dialog.title", "chest.wheel.title"], 1.0).name == "chest.wheel.title"
    assert ctx.find("tower.dialog.title") is None
    with pytest.raises(WaitTimeout):
        ctx.wait_gone("chest.wheel.title", 0.2)


def test_expect_scene_success_and_timeout() -> None:
    ctx, _ = make_ctx(frames=[CHEST], canvas=CanvasLocator.from_profile(PROFILE), real_scenes=True)
    assert ctx.expect_scene("chest.wheel", 2.0).name == "chest.wheel"
    assert ctx.detect_scene().name == "chest.wheel"

    ctx2, _ = make_ctx(frames=[DUNGEON], canvas=CanvasLocator.from_profile(PROFILE), real_scenes=True)
    with pytest.raises(SceneTimeout) as ei:
        ctx2.expect_scene("chest.wheel", 0.3)
    assert ei.value.expected == "chest.wheel"
    assert ei.value.actual is not None and ei.value.actual.name == "field"
    assert "field" in str(ei.value)


def test_status_callback_reports_canvas_and_scene() -> None:
    seen: list[tuple] = []
    ctx, _ = make_ctx(frames=[CHEST], canvas=CanvasLocator.from_profile(PROFILE), real_scenes=True,
                      on_status=lambda c, s: seen.append((c, s)))
    ctx.expect_scene("chest.wheel", 2.0)
    assert seen and seen[-1][0].reliable and seen[-1][1].name == "chest.wheel"


# --- debug publishing ---------------------------------------------------------------------------
def test_debug_mode_publishes_and_saves_every_step(tmp_path: Path) -> None:
    frames: list[DebugFrame] = []
    png = PngSink(tmp_path, enabled=False)
    ctx, _ = make_ctx(frames=[CHEST], canvas=CanvasLocator.from_profile(PROFILE), debug_sink=CallbackSink(frames.append),
                      png_sink=png, debug_enabled=lambda: True, task_name="Quay rương")
    ctx.find("chest.wheel.title")
    ctx.click("quay_ruong.per_spin.0")
    assert len(frames) == 2 and len(png.saved) == 2
    assert any(d.name == "chest.wheel.title" and d.found for d in frames[0].detections)
    assert frames[1].clicks and frames[1].task == "Quay rương"
    assert all(p.is_file() for p in png.saved)


def test_debug_off_saves_nothing_but_failures(tmp_path: Path) -> None:
    frames: list[DebugFrame] = []
    png = PngSink(tmp_path, enabled=True)  # the context syncs it with debug_enabled()
    ctx, _ = make_ctx(debug_sink=CallbackSink(frames.append), png_sink=png)
    ctx.find("chest.wheel.title")
    ctx.click(Point(1, 1))
    assert frames == [] and png.saved == []
    path = ctx.save_failure("boom")
    assert path is not None and path.is_file() and len(frames) == 1 and frames[0].failed


# --- execute ------------------------------------------------------------------------------------
class _Boom(Task):
    id = "boom"
    title = "Boom"

    def run(self, ctx: RunContext) -> str:
        ctx.grab()
        raise RuntimeError("hỏng")


class _Stopper(Task):
    id = "stopper"
    title = "Stopper"

    def run(self, ctx: RunContext) -> str:
        ctx.control.stop()
        ctx.sleep(10)
        return "never"


def test_execute_failure_always_saves_screenshot(tmp_path: Path) -> None:
    ctx, _ = make_ctx(png_sink=PngSink(tmp_path / "run-x"))
    logs: list[tuple[str, str]] = []
    ctx._on_log = lambda lvl, msg: logs.append((lvl, msg))
    r = execute(_Boom(), ctx, tmp_path / "run-x")
    assert r.status is RunStatus.FAILED and "hỏng" in r.message
    assert r.screenshot is not None and r.screenshot.is_file()
    assert r.screenshot.parent == tmp_path / "run-x"
    assert any(lvl == "error" for lvl, _ in logs)


def test_execute_stop_is_not_a_failure(tmp_path: Path) -> None:
    ctx, _ = make_ctx(png_sink=PngSink(tmp_path))
    r = execute(_Stopper(), ctx)
    assert r.status is RunStatus.STOPPED and r.screenshot is None
    assert not list(tmp_path.glob("*.png"))


# --- registry -----------------------------------------------------------------------------------
def test_registry_discovers_p0_features() -> None:
    ids = [f.id for f in registry.discover()]
    assert "autoclick" in ids and "quay_ruong" in ids
    assert registry.get_feature("quay_ruong").title == "Quay rương"


def test_registry_rejects_bad_features() -> None:
    class NoTitle(Task):
        id = "no_title"
        title = ""

        def run(self, ctx: RunContext) -> None:
            return None

    with pytest.raises(registry.RegistryError):
        registry.register(NoTitle)

    class BadParams(Task):
        id = "bad_params"
        title = "x"
        Params = dict  # type: ignore[assignment]

        def run(self, ctx: RunContext) -> None:
            return None

    with pytest.raises(registry.RegistryError):
        registry.register(BadParams)


def test_task_params_coercion() -> None:
    class P(TaskParams):
        n: int = 3

    class T(Task):
        id = "t"
        title = "T"
        Params = P

        def run(self, ctx: RunContext) -> None:
            return None

    assert T().params.n == 3
    assert T({"n": 5}).params.n == 5
    assert isinstance(T({"n": 5}).params, P)
