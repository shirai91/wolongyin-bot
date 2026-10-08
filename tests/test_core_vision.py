"""Matcher / TemplateStore / FileCapturer on synthetic images (no live game)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from ngoalong.core.capture import CachedCapturer, read_image, save_png
from ngoalong.core.detection import Detection
from ngoalong.core.drivers.file import FileCapturer
from ngoalong.core.errors import Cancelled, CaptureError, TemplateError, WaitTimeout
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher, match_template

ROOT = Path(__file__).resolve().parents[1]


def noise(h: int, w: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)


def make_template(seed: int = 1, size: tuple[int, int] = (40, 30)) -> np.ndarray:
    w, h = size
    t = noise(h, w, seed)
    cv2.rectangle(t, (5, 5), (w - 6, h - 6), (0, 255, 255), 2)
    return t


def paste(frame: np.ndarray, t: np.ndarray, x: int, y: int) -> np.ndarray:
    out = frame.copy()
    out[y : y + t.shape[0], x : x + t.shape[1]] = t
    return out


@pytest.fixture()
def tpl() -> np.ndarray:
    return make_template()


@pytest.fixture()
def store(tpl: np.ndarray) -> TemplateStore:
    s = TemplateStore()
    s.register("btn.ok", tpl)
    return s


# --- find -----------------------------------------------------------------------------------------
def test_find_returns_rect_score_and_centre(store: TemplateStore, tpl: np.ndarray) -> None:
    frame = paste(noise(600, 1000), tpl, 300, 200)
    m = Matcher(store).find("btn.ok", frame=frame)
    assert m is not None
    assert m.rect == Rect(300, 200, 40, 30)
    assert m.score > 0.99
    assert m.center == Point(320, 215)  # centre, not the top-left corner (#3)


def test_tolerates_brightness_drift(store: TemplateStore, tpl: np.ndarray) -> None:
    frame = paste(noise(300, 400), tpl, 50, 60)
    drifted = cv2.convertScaleAbs(frame, alpha=0.9, beta=12)
    m = Matcher(store).find("btn.ok", frame=drifted)
    assert m is not None and m.rect.top_left == Point(50, 60)


def test_miss_records_red_detection_over_search_region(store: TemplateStore) -> None:
    matcher = Matcher(store)
    roi = Rect(10, 10, 200, 100)
    assert matcher.find("btn.ok", roi=roi, frame=noise(300, 400)) is None
    (det,) = matcher.drain_detections()
    assert isinstance(det, Detection)
    assert not det.found and det.rect == roi and det.roi == roi
    assert det.score < det.threshold == 0.85
    assert det.best_rect is not None
    assert matcher.detections == []  # drained


def test_every_search_is_recorded_and_forwarded(store: TemplateStore, tpl: np.ndarray) -> None:
    seen: list[Detection] = []
    matcher = Matcher(store, on_detection=seen.append)
    frame = paste(noise(300, 400), tpl, 0, 0)
    matcher.find("btn.ok", frame=frame)
    matcher.find("btn.ok", frame=noise(300, 400, seed=9))
    assert [d.found for d in seen] == [True, False]
    assert [d.found for d in matcher.detections] == [True, False]
    assert seen[0].rect == Rect(0, 0, 40, 30)


def test_roi_limits_search_and_offsets_result(store: TemplateStore, tpl: np.ndarray) -> None:
    frame = paste(paste(noise(400, 600), tpl, 20, 20), tpl, 400, 300)
    matcher = Matcher(store)
    m = matcher.find("btn.ok", roi=Rect(300, 200, 300, 200), frame=frame)
    assert m is not None and m.rect.top_left == Point(400, 300)
    # ROI partly outside the frame is clipped, not an error
    m2 = matcher.find("btn.ok", roi=Rect(350, 250, 1000, 1000), frame=frame)
    assert m2 is not None and m2.rect.top_left == Point(400, 300)
    # ROI smaller than the template: no match, score 0
    assert matcher.find("btn.ok", roi=Rect(0, 0, 10, 10), frame=frame) is None
    assert matcher.detections[-1].score == 0.0
    # ROI entirely outside the frame
    assert matcher.find("btn.ok", roi=Rect(5000, 5000, 50, 50), frame=frame) is None


def test_threshold_precedence(tpl: np.ndarray) -> None:
    s = TemplateStore()
    s.register("strict", tpl, threshold=0.999)
    frame = paste(noise(200, 200), cv2.GaussianBlur(tpl, (3, 3), 0), 10, 10)
    matcher = Matcher(s, default_threshold=0.5)
    assert matcher.find("strict", frame=frame) is None  # template's own threshold
    assert matcher.find("strict", frame=frame, threshold=0.6) is not None  # explicit wins
    s.register("loose", tpl)
    assert matcher.find("loose", frame=frame) is not None  # matcher default


def test_mask_ignores_changing_pixels(tpl: np.ndarray) -> None:
    # The centre of the button changes (e.g. a counter); mask it out.
    mask = np.full(tpl.shape[:2], 255, np.uint8)
    mask[10:20, 10:30] = 0
    variant = tpl.copy()
    variant[10:20, 10:30] = noise(10, 20, seed=42)
    frame = paste(noise(300, 300, seed=3), variant, 120, 80)
    s = TemplateStore()
    s.register("plain", tpl)
    s.register("masked", tpl, mask=mask)
    matcher = Matcher(s, default_threshold=0.95)
    assert matcher.find("plain", frame=frame) is None
    m = matcher.find("masked", frame=frame)
    assert m is not None and m.rect.top_left == Point(120, 80)
    # a mask passed per call works too
    assert matcher.find("plain", frame=frame, mask=mask) is not None


def test_alpha_channel_becomes_mask(tmp_path: Path, tpl: np.ndarray) -> None:
    bgra = cv2.cvtColor(tpl, cv2.COLOR_BGR2BGRA)
    bgra[10:20, 10:30, 3] = 0
    save_png(tmp_path / "icon.png", bgra)
    variant = tpl.copy()
    variant[10:20, 10:30] = 0
    frame = paste(noise(200, 200, seed=5), variant, 30, 40)
    m = Matcher(tmp_path, default_threshold=0.95).find("icon", frame=frame)
    assert m is not None and m.rect.top_left == Point(30, 40)


def test_flat_template_scores_by_difference() -> None:
    s = TemplateStore()
    s.register("black", np.zeros((10, 10, 3), np.uint8))
    frame = np.full((100, 100, 3), 200, np.uint8)
    frame[50:60, 70:80] = 0
    m = Matcher(s).find("black", frame=frame)
    assert m is not None and m.rect.top_left == Point(70, 50) and m.score == pytest.approx(1.0)
    assert Matcher(s).find("black", frame=np.full((100, 100, 3), 200, np.uint8)) is None


def test_flat_frame_region_never_matches(store: TemplateStore) -> None:
    assert Matcher(store).find("btn.ok", frame=np.zeros((100, 100, 3), np.uint8)) is None


def test_scale_resizes_template(store: TemplateStore, tpl: np.ndarray) -> None:
    frame = noise(600, 800, seed=11)
    big = cv2.resize(tpl, (60, 45), interpolation=cv2.INTER_LINEAR)
    frame = paste(frame, big, 500, 400)
    matcher = Matcher(store, scale=1.5, default_threshold=0.8)
    m = matcher.find("btn.ok", frame=frame)
    assert m is not None and m.rect == Rect(500, 400, 60, 45)


def test_find_any_uses_one_frame_and_returns_best(tpl: np.ndarray) -> None:
    other = make_template(seed=7)
    s = TemplateStore()
    s.register("a", tpl)
    s.register("b", other)
    s.register("c", make_template(seed=8))
    cap = FileCapturer(paste(paste(noise(300, 300), tpl, 10, 10), other, 200, 200))
    matcher = Matcher(s, capturer=cap)
    m = matcher.find_any(["c", "b", "a"])
    assert m is not None and m.name in {"a", "b"} and m.score > 0.99
    assert cap.grab_count == 1
    assert len(matcher.detections) == 3


def test_match_template_shape_guards() -> None:
    assert match_template(np.zeros((5, 5, 3), np.uint8), np.zeros((6, 6, 3), np.uint8)) is None
    with pytest.raises(ValueError):
        match_template(np.zeros((100, 100), np.uint8), make_template())


# --- template store -------------------------------------------------------------------------------
def test_store_resolves_dotted_names_and_caches(tmp_path: Path, tpl: np.ndarray) -> None:
    save_png(tmp_path / "tower" / "open.png", tpl)
    save_png(tmp_path / "Thám Bảo" / "x2.png", tpl)  # non-ASCII folder
    s = TemplateStore([tmp_path])
    assert s.resolve("tower.open") == tmp_path / "tower" / "open.png"
    t1 = s.get("tower.open")
    t2 = s.get("tower.open")
    assert t1 is t2 and s.load_count == 1
    s.get("tower.open", scale=0.5)
    s.get("tower.open", scale=0.5)
    assert s.load_count == 1  # resized from the cached base, not re-read
    assert s.get("Thám Bảo/x2").size == (40, 30)
    assert "tower.open" in s and "tower.nope" not in s
    with pytest.raises(TemplateError):
        s.get("tower.nope")


def test_store_reads_sibling_mask(tmp_path: Path, tpl: np.ndarray) -> None:
    save_png(tmp_path / "btn.png", tpl)
    mask = np.full(tpl.shape[:2], 255, np.uint8)
    mask[:5] = 0
    save_png(tmp_path / "btn.mask.png", mask)
    t = TemplateStore(tmp_path).get("btn")
    assert t.mask is not None and t.mask.shape == tpl.shape and t.mask[0, 0, 0] == 0


def test_register_replaces_and_invalidates_scaled(tpl: np.ndarray) -> None:
    s = TemplateStore()
    s.register("x", tpl)
    s.get("x", 2.0)
    s.register("x", make_template(seed=99))
    assert not np.array_equal(s.get("x").image, tpl)
    assert s.get("x", 2.0).size == (80, 60)


# --- wait_for -------------------------------------------------------------------------------------
def test_wait_for_polls_until_found(store: TemplateStore, tpl: np.ndarray) -> None:
    blank = noise(200, 200, seed=4)
    cap = FileCapturer([blank, blank, paste(blank, tpl, 50, 50)])
    m = Matcher(store, capturer=cap).wait_for("btn.ok", timeout_s=2, interval_s=0.001)
    assert m.rect.top_left == Point(50, 50)
    assert cap.grab_count == 3


def test_wait_for_any_of_several_names(store: TemplateStore, tpl: np.ndarray) -> None:
    store.register("other", make_template(seed=5))
    cap = FileCapturer(paste(noise(200, 200), tpl, 5, 5))
    m = Matcher(store, capturer=cap).wait_for(["other", "btn.ok"], timeout_s=1, interval_s=0.001)
    assert m.name == "btn.ok"


def test_wait_for_times_out(store: TemplateStore) -> None:
    cap = FileCapturer(noise(100, 100))
    matcher = Matcher(store, capturer=cap)
    t0 = time.monotonic()
    with pytest.raises(WaitTimeout, match="btn.ok"):
        matcher.wait_for("btn.ok", timeout_s=0.2, interval_s=0.02)
    assert 0.15 <= time.monotonic() - t0 < 1.0
    assert isinstance(WaitTimeout("x"), TimeoutError)


def test_wait_for_honours_cancel_quickly(store: TemplateStore) -> None:
    cap = FileCapturer(noise(100, 100))
    matcher = Matcher(store, capturer=cap)
    cancel = threading.Event()
    threading.Timer(0.1, cancel.set).start()
    t0 = time.monotonic()
    with pytest.raises(Cancelled):
        matcher.wait_for("btn.ok", timeout_s=30, interval_s=5, cancel=cancel)
    assert time.monotonic() - t0 < 1.0


def test_wait_for_already_cancelled_does_not_capture(store: TemplateStore) -> None:
    cap = FileCapturer(noise(100, 100))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        Matcher(store, capturer=cap).wait_for("btn.ok", timeout_s=5, cancel=cancel)
    assert cap.grab_count == 0


def test_wait_gone(store: TemplateStore, tpl: np.ndarray) -> None:
    blank = noise(200, 200, seed=4)
    with_btn = paste(blank, tpl, 50, 50)
    cap = FileCapturer([with_btn, with_btn, blank])
    Matcher(store, capturer=cap).wait_gone("btn.ok", timeout_s=2, interval_s=0.001)
    assert cap.grab_count == 3
    with pytest.raises(WaitTimeout):
        Matcher(store, capturer=FileCapturer(with_btn)).wait_gone("btn.ok", 0.05, interval_s=0.01)


def test_matcher_without_capturer_needs_frame(store: TemplateStore) -> None:
    with pytest.raises(ValueError):
        Matcher(store).find("btn.ok")


# --- capturers ------------------------------------------------------------------------------------
def test_file_capturer_sequence_and_loop(tmp_path: Path) -> None:
    a, b = noise(10, 10, 1), noise(10, 10, 2)
    save_png(tmp_path / "1.png", a)
    save_png(tmp_path / "2.png", b)
    cap = FileCapturer(tmp_path)
    assert len(cap) == 2
    assert np.array_equal(cap.grab(), a)
    assert np.array_equal(cap.grab(), b)
    assert np.array_equal(cap.grab(), b)  # holds the last frame
    looping = FileCapturer(str(tmp_path / "*.png"), loop=True)
    frames = [looping.grab() for _ in range(3)]
    assert np.array_equal(frames[2], a)
    # grabbed frames are copies: mutating one does not affect the replay
    f = looping.grab()
    f[:] = 0
    looping.seek(1)
    assert np.array_equal(looping.grab(), b)


def test_file_capturer_reads_reference_screenshots() -> None:
    shots = ROOT / "docs" / "game screenshots"
    if not any(shots.glob("*.png")):
        pytest.skip("reference screenshots not present")
    cap = FileCapturer(shots)
    frame = cap.grab()
    assert frame.ndim == 3 and frame.shape[2] == 3 and frame.shape[1] >= 1000


def test_file_capturer_errors(tmp_path: Path) -> None:
    with pytest.raises(CaptureError):
        FileCapturer(str(tmp_path / "*.png"))
    with pytest.raises(CaptureError):
        FileCapturer(tmp_path / "missing.png").grab()
    with pytest.raises(CaptureError):
        read_image(tmp_path / "missing.png")


def test_cached_capturer_reuses_recent_frame() -> None:
    now = [0.0]
    inner = FileCapturer([noise(5, 5, 1), noise(5, 5, 2), noise(5, 5, 3)])
    cache = CachedCapturer(inner, max_age_s=0.1, clock=lambda: now[0])
    f1 = cache.grab()
    assert cache.grab() is f1 and inner.grab_count == 1
    now[0] = 0.2
    assert cache.grab() is not f1 and inner.grab_count == 2
    cache.invalidate()
    cache.grab()
    assert inner.grab_count == 3
