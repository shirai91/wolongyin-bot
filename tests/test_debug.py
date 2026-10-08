"""Debug model, renderer and sinks."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ngoalong.core.capture import read_image
from ngoalong.core.detection import Detection
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.locator import CanvasFix, Confidence
from ngoalong.debug.model import DebugFrame, Marker
from ngoalong.debug.render import CYAN, GREEN, RED, STATUS_BAND, YELLOW, ascii_label, detection_label, render
from ngoalong.debug.sink import CallbackSink, FanoutSink, OverlaySink, PanelSink, PngSink, new_run_dir, slug


def _frame(**kw) -> DebugFrame:
    img = np.full((800, 1200, 3), 60, np.uint8)
    canvas = CanvasFix(Rect(100, 50, 1000, 600), 1.0, Confidence.HIGH, ("anchor:a", "border"))
    found = Detection("ok_button", Rect(300, 300, 60, 20), 0.97, 0.85, True)
    missed = Detection("close", Rect(700, 100, 50, 40), 0.41, 0.85, False)
    defaults = dict(
        image=img, canvas=canvas, scene="tower.confirm", scene_score=0.98,
        detections=[found, missed], clicks=[Point(500, 400)], window="Coowon", task="thap", step="confirm",
    )
    defaults.update(kw)
    return DebugFrame(**defaults)


def px(img: np.ndarray, x: int, y: int) -> tuple[int, int, int]:
    return tuple(int(v) for v in img[y, x])


def test_render_colours() -> None:
    f = _frame()
    out = render(f)
    assert out.shape == f.image.shape  # room under the canvas: no extra band
    assert px(out, 600, 50) == CYAN  # canvas top edge (away from the label)
    assert px(out, 300, 310) == GREEN  # found: left edge of the match
    assert px(out, 700, 130) == RED  # not found: left edge of the search region
    assert px(out, 492, 400) == YELLOW  # crosshair arm
    assert not np.shares_memory(out, f.image)
    assert px(f.image, 600, 50) == (60, 60, 60)  # source untouched


def test_low_confidence_canvas_is_red() -> None:
    canvas = CanvasFix(Rect(100, 50, 1000, 600), 1.0, Confidence.LOW, stale=True)
    out = render(_frame(canvas=canvas))
    assert px(out, 600, 50) == RED


def test_status_band_added_when_no_room() -> None:
    img = np.zeros((600, 1000, 3), np.uint8)
    canvas = CanvasFix(Rect(0, 0, 1000, 600), 1.0, Confidence.HIGH)
    out = render(_frame(image=img, canvas=canvas))
    assert out.shape == (600 + STATUS_BAND, 1000, 3)
    assert out[600:].any()  # text drawn in the band


def test_render_without_canvas_or_image() -> None:
    out = render(_frame(canvas=None))
    assert out.shape[0] == 800 + STATUS_BAND
    with pytest.raises(ValueError):
        render(DebugFrame())


def test_labels_and_status_line() -> None:
    f = _frame(markers=[Marker("Tháp", Point(10, 10))])
    assert detection_label(f.detections[0]) == "ok_button 0.97"
    assert detection_label(f.detections[1]) == "close 0.41 < 0.85"
    s = f.status_line()
    assert "scene: tower.confirm 0.98" in s and "window: Coowon" in s and "task: thap" in s
    assert "step: confirm" in s and "next click: (500, 400)" in s
    assert "next click: -" in DebugFrame().status_line()
    assert ascii_label("Tháp Thí Luyện Đổi") == "Thap Thi Luyen Doi"
    render(f)  # non-ASCII marker label must not raise


def test_png_sink_failures_only_when_debug_off(tmp_path: Path) -> None:
    sink = PngSink(tmp_path, enabled=False)
    sink.publish(_frame())
    assert sink.saved == []
    sink.publish(_frame(failed=True, step="Vượt tháp"))
    assert [p.name for p in sink.saved] == ["001-vuot-thap.png"]
    img = read_image(sink.saved[0])
    assert img.shape == (600 + STATUS_BAND, 1000, 3)  # cropped to the canvas, status band below


def test_png_sink_keeps_the_whole_window_when_the_canvas_is_uncertain(tmp_path: Path) -> None:
    sink = PngSink(tmp_path, enabled=True)
    canvas = CanvasFix(Rect(100, 50, 1000, 600), 1.0, Confidence.LOW, stale=True)
    sink.publish(_frame(canvas=canvas))
    assert read_image(sink.saved[0]).shape == (800, 1200, 3)


def test_cropped_to_canvas_moves_annotations() -> None:
    f = _frame(markers=[Marker("m", Point(150, 60))])
    c = f.cropped_to_canvas()
    assert c.image.shape == (600, 1000, 3) and np.shares_memory(c.image, f.image)
    assert c.canvas.rect == Rect(0, 0, 1000, 600) and c.canvas.reliable
    assert [d.rect for d in c.detections] == [Rect(200, 250, 60, 20), Rect(600, 50, 50, 40)]
    assert c.clicks == [Point(400, 350)] and c.markers[0].point == Point(50, 10)
    assert f.canvas.rect == Rect(100, 50, 1000, 600) and f.clicks == [Point(500, 400)]  # source untouched
    out = render(c)
    assert px(out, 200, 260) == GREEN and px(out, 600, 80) == RED and px(out, 392, 350) == YELLOW
    for kw in ({"canvas": None}, {"canvas": CanvasFix(Rect(300, 300, 1000, 600), 1.0, Confidence.HIGH)}):
        g = _frame(**kw)  # no canvas, or a canvas partly outside the image: unchanged
        assert g.cropped_to_canvas() is g


def test_png_sink_saves_every_frame_in_debug_mode(tmp_path: Path) -> None:
    sink = PngSink(tmp_path / "run", enabled=True)
    for i in range(3):
        sink.publish(_frame(step=f"s{i}"))
    sink.publish(DebugFrame(step="no image"))  # nothing to draw: skipped
    assert [p.name for p in sink.saved] == ["001-s0.png", "002-s1.png", "003-s2.png"]


def test_panel_sink_latest_and_listeners() -> None:
    panel = PanelSink()
    got: list[DebugFrame] = []
    unsubscribe = panel.subscribe(got.append)
    panel.subscribe(lambda f: (_ for _ in ()).throw(RuntimeError("broken listener")))
    assert panel.latest_image() is None
    f = _frame()
    panel.publish(f)
    assert panel.latest is f and got == [f]
    img = panel.latest_image()
    assert img is not None and img is panel.latest_image()  # rendered once, cached
    unsubscribe()
    panel.publish(_frame())
    assert len(got) == 1


class _Presenter:
    def __init__(self) -> None:
        self.shown: list[DebugFrame] = []
        self.hidden = 0

    def show_frame(self, frame: DebugFrame) -> None:
        self.shown.append(frame)

    def hide(self) -> None:
        self.hidden += 1


def test_overlay_sink_respects_enabled() -> None:
    p = _Presenter()
    sink = OverlaySink(p)
    sink.publish(_frame())
    assert p.shown == []
    sink.set_enabled(True)
    sink.publish(_frame())
    assert len(p.shown) == 1
    sink.set_enabled(False)
    assert p.hidden == 1


def test_fanout_isolates_failures(tmp_path: Path) -> None:
    calls: list[str] = []

    def boom(_f: DebugFrame) -> None:
        raise RuntimeError("sink down")

    fan = FanoutSink([CallbackSink(boom), CallbackSink(lambda f: calls.append(f.step))])
    png = PngSink(tmp_path, enabled=True)
    fan.add(png)
    fan.publish(_frame(step="a"))
    assert calls == ["a"] and len(png.saved) == 1
    fan.remove(png)
    fan.publish(_frame(step="b"))
    assert calls == ["a", "b"] and len(png.saved) == 1


def test_helpers() -> None:
    assert slug("Tầng 5 / Vượt Tháp!") == "tang-5-vuot-thap"
    assert slug("***") == "step"
    assert new_run_dir("logs", "42") == Path("logs") / "run-42"
    assert new_run_dir().name.startswith("run-")
