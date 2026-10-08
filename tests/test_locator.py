"""CanvasLocator: golden screenshots (docs/07), scale, agreement rule and last-good fallback."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from conftest import PROFILE, frame, golden_cases

from ngoalong.core.geometry import Point, Rect
from ngoalong.core.locator import (
    BORDER,
    BlackBorderConfig,
    CanvasFix,
    CanvasLocator,
    Confidence,
    LocatorConfig,
    black_border_box,
    find_page_top,
    layout_prior,
    non_black,
)

TOL = 2  # docs/06 exit gate: canvas found within 2 px


def assert_canvas(fix: CanvasFix, expected: tuple[int, int]) -> None:
    assert abs(fix.rect.x - expected[0]) <= TOL and abs(fix.rect.y - expected[1]) <= TOL, fix.rect
    assert abs(fix.rect.w - 1000) <= TOL and abs(fix.rect.h - 600) <= TOL, fix.rect
    assert fix.scale == pytest.approx(1.0, abs=0.003)


@pytest.mark.parametrize("case", golden_cases("reference", "live"))
def test_golden_canvas(locator: CanvasLocator, case: dict) -> None:
    fix = locator.locate(frame(case["file"]))
    assert fix.confidence is Confidence.HIGH, fix
    assert not fix.stale
    assert len(fix.detectors) >= 2
    assert_canvas(fix, tuple(case["canvas"]))
    if case.get("via_anchors"):
        assert BORDER not in fix.detectors
        assert sum(d.startswith("anchor:") for d in fix.detectors) >= 2


@pytest.mark.parametrize("case", golden_cases("reference"))
def test_black_border_matches_docs07(case: dict) -> None:
    box = black_border_box(frame(case["file"]), BlackBorderConfig())
    assert box is not None
    if case.get("via_anchors"):
        assert not box.valid  # the dark tower battle defeats the border method (docs/07)
    else:
        assert box.valid
        assert abs(box.rect.x - case["canvas"][0]) <= TOL and abs(box.rect.y - case["canvas"][1]) <= TOL


def test_page_top_and_layout_prior() -> None:
    img = frame("docs/game screenshots/live/01-city-main.png")
    top = find_page_top(non_black(img, 40))
    assert top == 110
    prior = layout_prior(img.shape, top, 1.0, LocatorConfig())
    assert abs(prior.x - 180) <= 10 and prior.y == 110 and prior.size == (1000, 600)


def test_page_top_without_margins_is_zero() -> None:
    canvas = frame("docs/game screenshots/main-game.png")[109:709, 273:1273]
    assert find_page_top(non_black(canvas, 40)) == 0


def test_frame_that_is_only_the_canvas(locator: CanvasLocator) -> None:
    canvas = np.ascontiguousarray(frame("docs/game screenshots/main-game.png")[109:709, 273:1273])
    fix = locator.locate(canvas)
    assert fix.confidence is Confidence.HIGH
    assert_canvas(fix, (0, 0))


def _page(canvas: np.ndarray, origin: tuple[int, int], size: tuple[int, int] = (1700, 1100)) -> np.ndarray:
    """Black page with a bright browser toolbar on top and ``canvas`` pasted at ``origin``."""
    w, h = size
    page = np.zeros((h, w, 3), np.uint8)
    page[:70] = 235
    x, y = origin
    page[y : y + canvas.shape[0], x : x + canvas.shape[1]] = canvas
    return page


def test_zoomed_canvas_scale(locator: CanvasLocator) -> None:
    canvas = frame("docs/game screenshots/main-game.png")[109:709, 273:1273]
    zoomed = cv2.resize(canvas, (1250, 750), interpolation=cv2.INTER_LINEAR)
    fix = locator.locate(_page(zoomed, (225, 72)))
    assert fix.confidence is Confidence.HIGH, fix
    assert fix.scale == pytest.approx(1.25, abs=0.01)
    assert abs(fix.rect.x - 225) <= TOL and abs(fix.rect.y - 72) <= TOL
    assert abs(fix.rect.w - 1250) <= 3 and abs(fix.rect.h - 750) <= 3
    # canvas -> frame conversion uses the scale
    assert fix.to_frame((400, 200)) == Point(fix.rect.x + 500, fix.rect.y + 250)


def test_last_good_fallback_is_low_confidence(locator: CanvasLocator) -> None:
    good = locator.locate(frame("docs/game screenshots/main-game.png"))
    assert good.reliable
    black = np.zeros((852, 1536, 3), np.uint8)
    fix = locator.locate(black)
    assert fix.confidence is Confidence.LOW
    assert fix.stale
    assert fix.rect == good.rect
    assert locator.last_good == good
    # the next good capture is accepted again
    again = locator.locate(frame("docs/game screenshots/dungeon.png"))
    assert again.reliable and not again.stale
    assert_canvas(again, (272, 110))


def test_canvas_moved_far_on_dark_scene_is_found_again(locator: CanvasLocator) -> None:
    """P0-R1: after a good fix, a canvas moved > search margin on a dark scene is still found."""
    good = locator.locate(frame("docs/game screenshots/main-game.png"))
    assert good.reliable
    dark = frame("docs/game screenshots/tower-in-dungeon.png")
    fresh = locator.locate(dark)
    assert fresh.reliable
    shifted = np.zeros_like(dark)
    shifted[:, 150:] = dark[:, :-150]
    fix = locator.locate(shifted)
    assert fix.confidence is Confidence.HIGH, fix
    assert not fix.stale
    assert abs(fix.rect.x - (fresh.rect.x + 150)) <= TOL and abs(fix.rect.y - fresh.rect.y) <= TOL
    assert locator.last_good == fix


def test_nothing_found_without_history(locator: CanvasLocator) -> None:
    fix = locator.locate(np.zeros((852, 1536, 3), np.uint8))
    assert fix.confidence is Confidence.NONE
    assert not fix.reliable
    assert fix.detectors == ()
    assert locator.last_good is None


def test_single_detector_is_not_enough(locator: CanvasLocator) -> None:
    """Only the black border is left (anchors painted over): low confidence, not remembered."""
    img = frame("docs/game screenshots/main-game.png").copy()
    ox, oy = 273, 109
    img[oy + 570 : oy + 600, ox : ox + 280] = (40, 90, 40)  # chat bar
    img[oy : oy + 32, ox + 745 : ox + 780] = (40, 90, 40)  # gear
    fix = locator.locate(img)
    assert fix.confidence is Confidence.LOW
    assert fix.detectors == (BORDER,)
    assert_canvas(fix, (273, 109))
    assert locator.last_good is None


def test_detectors_must_agree_within_3px(locator: CanvasLocator) -> None:
    """Valid border box + one anchor 6 px away from it: the two disagree, so no HIGH fix."""
    img = frame("docs/game screenshots/main-game.png").copy()
    ox, oy = 273, 109
    bar = img[oy + 574 : oy + 596, ox + 2 : ox + 42].copy()
    img[oy + 560 : oy + 600, ox : ox + 280] = (40, 90, 40)  # remove both chat anchors
    img[oy : oy + 32, ox + 745 : ox + 780] = (40, 90, 40)  # and the gear
    img[oy + 568 : oy + 590, ox + 8 : ox + 48] = bar  # kvuc label pasted 6 px up-right
    fix = locator.locate(img)
    assert fix.confidence is Confidence.LOW
    assert len(fix.detectors) == 1
    sources = {c.source for c in fix.candidates}
    assert sources == {BORDER, "anchor:kvuc_label"}


def test_anchor_detections_are_recorded(locator: CanvasLocator) -> None:
    fix = locator.locate(frame("docs/game screenshots/tower-in-dungeon.png"))
    names = {d.name: d for d in fix.detections}
    assert names["anchor:kvuc_label"].found and names["anchor:chat_send"].found
    assert not names["anchor:hud_gear"].found  # the tower battle hides the gear (docs/07)


def test_canvasfix_conversions() -> None:
    fix = CanvasFix(Rect(100, 50, 1500, 900), 1.5, Confidence.HIGH)
    assert fix.origin == Point(100, 50)
    assert fix.to_frame(Point(10, 20)) == Point(115, 80)
    assert fix.rect_to_frame(Rect(10, 20, 40, 10)) == Rect(115, 80, 60, 15)
    assert fix.to_canvas(Point(115, 80)) == (10.0, 20.0)


def test_config_loads_profile() -> None:
    cfg = LocatorConfig.load(Path(PROFILE) / "locator.yaml")
    assert {a.name for a in cfg.anchors} >= {"kvuc_label", "chat_send"}
    assert cfg.agree_px == 3 and cfg.black_border.tolerance_px == 2
