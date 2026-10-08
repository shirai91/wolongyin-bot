"""P0 features with a fake input driver: AutoClick and Quay rương click sequences, stop within 1 s."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from conftest import PROFILE, ROOT
from ngoalong.core.coords import CoordMap
from ngoalong.core.drivers.fake import RecordingInputDriver
from ngoalong.core.drivers.file import FileCapturer
from ngoalong.core.geometry import Point
from ngoalong.core.locator import CanvasLocator
from ngoalong.core.scenes import SceneDetector
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher
from ngoalong.debug.sink import PngSink
from ngoalong.engine.context import ClickType, FixedCanvas, RunContext
from ngoalong.engine.runner import RunHooks, RunStatus, TaskRunner, execute
from ngoalong.engine.task import Task
from ngoalong.features.autoclick import AutoClick, AutoClickParams
from ngoalong.features.quay_ruong import QuayRuong, QuayRuongParams

CHEST = ROOT / "docs" / "game screenshots" / "chest-rolling-ui.png"
DUNGEON = ROOT / "docs" / "game screenshots" / "dungeon.png"
STORE = TemplateStore(PROFILE / "templates")
COORDS = CoordMap.load(PROFILE / "coords")
ORIGIN = Point(180, 110)


def ctx_for(frames, driver: RecordingInputDriver, located: bool = False, control=None,
            png_dir: Path | None = None) -> RunContext:
    matcher = Matcher(STORE, history=64)
    return RunContext(
        capturer=FileCapturer(frames, loop=True),
        input_driver=driver,
        coords=COORDS,
        matcher=matcher,
        canvas_source=CanvasLocator.from_profile(PROFILE) if located else FixedCanvas(ORIGIN),
        scenes=SceneDetector.load(PROFILE / "scenes", templates_dir=PROFILE / "templates"),
        control=control,
        png_sink=PngSink(png_dir) if png_dir is not None else None,
        poll_interval_s=0.05,
    )


def blank() -> np.ndarray:
    return np.zeros((800, 1400, 3), np.uint8)


# --- AutoClick ----------------------------------------------------------------------------------
def test_autoclick_sequence_and_click_type() -> None:
    drv = RecordingInputDriver()
    params = AutoClickParams(points=[{"x": 10, "y": 20}, {"x": 500, "y": 300}],
                             click_type=ClickType.RIGHT_DOUBLE, interval_ms=10, rounds=2)
    r = execute(AutoClick(params), ctx_for([blank()], drv))
    assert r.status is RunStatus.DONE, r.message
    expected = [Point(190, 130), Point(680, 410)] * 2
    assert drv.clicks == expected
    assert all(e.button == "right" and e.double for e in drv.events)
    assert "4" in r.message


def test_autoclick_points_follow_the_canvas_and_zoom() -> None:
    drv = RecordingInputDriver()
    ctx = ctx_for([blank()], drv)
    ctx.canvas_source = FixedCanvas(Point(50, 40), scale=1.5)
    execute(AutoClick({"points": [{"x": 100, "y": 200}], "rounds": 1, "interval_ms": 10}), ctx)
    assert drv.clicks == [Point(50 + 150, 40 + 300)]
    assert drv.events[0].button == "left" and not drv.events[0].double


def test_autoclick_relocates_when_the_window_is_resized() -> None:
    """P0-R2: the client size changes between two clicks, so the canvas is located again."""
    main = ROOT / "docs" / "game screenshots" / "main-game.png"
    from ngoalong.core.capture import read_image

    canvas = read_image(main)[109:709, 273:1273]
    resized = np.zeros((1100, 1700, 3), np.uint8)
    resized[:70] = 235  # browser toolbar
    resized[72:672, 400:1400] = canvas
    drv = RecordingInputDriver()
    ctx = ctx_for([main, resized], drv, located=True)
    r = execute(AutoClick({"points": [{"x": 100, "y": 200}], "rounds": 2, "interval_ms": 10}), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert len(drv.clicks) == 2
    c1, c2 = drv.clicks
    assert abs(c1.x - 373) <= 2 and abs(c1.y - 309) <= 2, c1
    assert abs(c2.x - 500) <= 2 and abs(c2.y - 272) <= 2, c2


def test_autoclick_without_points_fails_clearly(tmp_path: Path) -> None:
    drv = RecordingInputDriver()
    r = execute(AutoClick(), ctx_for([blank()], drv, png_dir=tmp_path))
    assert r.status is RunStatus.FAILED and "tọa độ" in r.message
    assert drv.clicks == []


def test_autoclick_params_validation() -> None:
    with pytest.raises(ValueError):
        AutoClickParams(points=[{"x": 1000, "y": 0}])  # outside the 1000x600 canvas
    with pytest.raises(ValueError):
        AutoClickParams(interval_ms=0)


def test_autoclick_legacy_import() -> None:
    ini = {"TOADO_GOC": "17x82", "ACP_LIST_TOADO": "492x493|10x10|", "ACP_INPUT_S": "0", "ACP_INPUT_MS": "100",
           "ACP_LABEL_X": "504", "ACP_LABEL_Y": "416"}
    out = AutoClick.import_legacy(ini)
    assert out == {"points": [{"x": 475, "y": 411}], "interval_ms": 100}  # 10x10 lies outside the canvas
    single = AutoClick.import_legacy({"TOADO_GOC": "0x0", "ACP_LIST_TOADO": "", "ACP_LABEL_X": "504",
                                      "ACP_LABEL_Y": "416", "ACP_INPUT_S": "1", "ACP_INPUT_MS": "000"})
    assert single == {"points": [{"x": 504, "y": 416}], "interval_ms": 1000}
    assert AutoClickParams.model_validate(out).points[0].x == 475


# --- Quay rương ---------------------------------------------------------------------------------
def test_quay_ruong_click_sequence_on_chest_screen() -> None:
    drv = RecordingInputDriver()
    ctx = ctx_for([CHEST], drv, located=True)
    progress: list[tuple[int, int | None]] = []
    ctx._on_progress = lambda d, t: progress.append((d, t))
    r = execute(QuayRuong(QuayRuongParams(spins=3, delay_ms=0)), ctx)
    assert r.status is RunStatus.DONE, r.message
    o = ctx.last_canvas.origin
    assert abs(o.x - 271) <= 2 and abs(o.y - 112) <= 2  # docs/07 canvas of chest-rolling-ui.png
    per_spin = [Point(o.x + 200, o.y + 420), Point(o.x + 440, o.y + 360), Point(o.x + 200, o.y + 420)]
    assert drv.clicks == per_spin * 3
    assert all(e.button == "left" and not e.double for e in drv.events)
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert r.counters["spins"] == 3 and r.counters["clicks"] == 9  # run-history counters


def test_quay_ruong_uses_the_coords_file() -> None:
    assert COORDS.offsets("quay_ruong.per_spin") == [(200, 420), (440, 360), (200, 420)]


def test_quay_ruong_refuses_to_click_on_the_wrong_screen(tmp_path: Path) -> None:
    drv = RecordingInputDriver()
    ctx = ctx_for([DUNGEON], drv, located=True, png_dir=tmp_path)
    r = execute(QuayRuong(QuayRuongParams(spins=2, delay_ms=0, scene_timeout_s=0.5)), ctx)
    assert r.status is RunStatus.FAILED
    assert "chest.wheel" in r.message and "field" in r.message
    assert drv.clicks == []
    assert r.screenshot is not None and r.screenshot.is_file()


def test_quay_ruong_stops_when_the_wheel_disappears(tmp_path: Path) -> None:
    """Spin 1 on the chest screen, then the capture shows the field: the run stops, not clicks blind."""
    drv = RecordingInputDriver()
    cap = FileCapturer([CHEST], loop=True)
    ctx = ctx_for([CHEST], drv, located=True, png_dir=tmp_path)
    ctx.capturer = cap

    def swap(_ev) -> None:
        if len(drv.events) == 3:
            ctx.capturer = FileCapturer([DUNGEON], loop=True)

    drv._on_event = swap
    r = execute(QuayRuong(QuayRuongParams(spins=5, delay_ms=0, scene_timeout_s=0.5)), ctx)
    assert r.status is RunStatus.FAILED
    assert len(drv.clicks) == 3


def test_quay_ruong_legacy_import() -> None:
    assert QuayRuong.import_legacy({"QR_SOLUONG": "200", "QR_TOCDO": "5"}) == {"spins": 200, "delay_ms": 5}
    assert QuayRuong.import_legacy({"QR_SOLUONG": "", "QR_TOCDO": "abc"}) == {}
    assert QuayRuong.import_legacy({"QR_SOLUONG": "0"}) == {}


# --- stop within 1 s through the TaskRunner -----------------------------------------------------
def _run_and_stop(qapp, task: Task, frames, located: bool, tmp_path: Path) -> tuple[float, object, RecordingInputDriver]:
    drv = RecordingInputDriver()
    runner = TaskRunner(tmp_path / "logs")

    def factory(t: Task, hooks: RunHooks) -> RunContext:
        ctx = ctx_for(frames, drv, located=located, control=hooks.control)
        ctx._on_log = hooks.on_log
        return ctx

    runner.start(task, factory)
    deadline = time.monotonic() + 10
    while not drv.clicks and time.monotonic() < deadline:
        time.sleep(0.01)
    assert drv.clicks, "the task never clicked"
    time.sleep(0.1)  # now inside the long delay
    t0 = time.monotonic()
    runner.stop()
    assert runner.wait(2.0)
    elapsed = time.monotonic() - t0
    return elapsed, runner.last_result, drv


def test_stop_ends_autoclick_within_one_second(qapp, tmp_path: Path) -> None:
    task = AutoClick({"points": [{"x": 5, "y": 5}], "interval_ms": 60_000})
    elapsed, result, drv = _run_and_stop(qapp, task, [blank()], False, tmp_path)
    assert elapsed < 1.0
    assert result.status is RunStatus.STOPPED
    assert len(drv.clicks) == 1
    log_files = list((tmp_path / "logs").glob("run-*/run.log"))
    assert log_files and "Đã dừng" in log_files[0].read_text(encoding="utf-8")


def test_stop_ends_quay_ruong_within_one_second(qapp, tmp_path: Path) -> None:
    task = QuayRuong(QuayRuongParams(spins=100, delay_ms=60_000))
    elapsed, result, drv = _run_and_stop(qapp, task, [CHEST], True, tmp_path)
    assert elapsed < 1.0
    assert result.status is RunStatus.STOPPED
    assert len(drv.clicks) == 1


def test_pause_and_resume_through_the_runner(qapp, tmp_path: Path) -> None:
    drv = RecordingInputDriver()
    runner = TaskRunner(tmp_path / "logs")

    def factory(t: Task, hooks: RunHooks) -> RunContext:
        return ctx_for([blank()], drv, control=hooks.control)

    runner.start(AutoClick({"points": [{"x": 5, "y": 5}], "interval_ms": 20, "rounds": 1000}), factory)
    time.sleep(0.2)
    runner.pause()
    assert runner.state == "paused"
    time.sleep(0.15)
    n = len(drv.clicks)
    time.sleep(0.3)
    assert len(drv.clicks) == n  # nothing happens while paused
    runner.resume()
    time.sleep(0.3)
    assert len(drv.clicks) > n
    with pytest.raises(RuntimeError):  # one run at a time
        runner.start(AutoClick({"points": [{"x": 1, "y": 1}]}), factory)
    runner.stop()
    assert runner.wait(2.0)
    assert runner.last_result.status is RunStatus.STOPPED
