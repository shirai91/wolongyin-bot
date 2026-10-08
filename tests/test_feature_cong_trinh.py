"""Nâng cấp công trình (``cong_trinh``): Thành chính by OCR (city level + "lvN" labels) on a scripted fake
game over the live screenshots, Hải cảng v1.8.0 click/delay parity, params validation and legacy import."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest

from conftest import PROFILE, ROOT
from ngoalong.core.capture import read_image
from ngoalong.core.coords import CoordMap
from ngoalong.core.drivers.fake import InputEvent, RecordingInputDriver
from ngoalong.core.drivers.file import FileCapturer
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.ocr import OcrLine, OcrWord
from ngoalong.core.scenes import SceneDetector
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher
from ngoalong.debug.sink import PngSink
from ngoalong.engine import registry
from ngoalong.engine.context import FixedCanvas, RunContext
from ngoalong.engine.control import RunControl
from ngoalong.engine.runner import RunStatus, execute
from ngoalong.features.cong_trinh import (
    CongTrinh, CongTrinhParams, Mode, format_building_list, parse_building_list, parse_city_level,
    parse_labels,
)

LIVE = ROOT / "docs" / "game screenshots" / "live"
STORE = TemplateStore(PROFILE / "templates")
COORDS = CoordMap.load(PROFILE / "coords")
SCENES = SceneDetector.load(PROFILE / "scenes", templates_dir=PROFILE / "templates")
ORIGIN = Point(180, 110)  # canvas origin in every live capture (docs/07)

# v1.8.0 NCCT_HAICANG offsets, copied from v1.8.0 app_logic_only.au3.
V180_HARBOUR = [(775, 140), (570, 125), (865, 250), (850, 400), (570, 345), (440, 272)]
V180_UPGRADE = (375, 325)
V180_CLOSE = (690, 204)
ICONS_TOGGLE = (831, 79)  # centre of the ">>" template (field.hud.icons_toggle at 822, 70)
HUD_TOGGLE = (830, 77)  # city.hud_toggle, clicked to open the icon rows again
NOTICE_TOGGLE = (10, 137)  # centre of the "<<" template (hud.notice_collapse at 2, 129)


def at(offset: tuple[int, int]) -> Point:
    return Point(ORIGIN.x + offset[0], ORIGIN.y + offset[1])


def canvas_of(p: Point) -> tuple[int, int]:
    return p.x - ORIGIN.x, p.y - ORIGIN.y


def blank() -> np.ndarray:
    return np.zeros((800, 1400, 3), np.uint8)


def near(p: tuple[int, int], q: tuple[int, int], tol: int = 6) -> bool:
    return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol


def make_ctx(capturer, driver: RecordingInputDriver, control: RunControl | None = None,
             png_dir: Path | None = None, ocr=None, gold: bool = False) -> RunContext:
    return RunContext(
        capturer=capturer, input_driver=driver, coords=COORDS, matcher=Matcher(STORE, history=64),
        canvas_source=FixedCanvas(ORIGIN), scenes=SCENES, control=control,
        png_sink=PngSink(png_dir) if png_dir is not None else None, poll_interval_s=0.02, ocr=ocr,
        policy=lambda name: gold and name == "spend_gold_skip_wait",
    )


def line(text: str, x: int, y: int, w: int = 30, h: int = 16) -> OcrLine:
    r = Rect(x, y, w, h)
    return OcrLine(text, r, (OcrWord(text, r),))


_FRAMES = {
    "city": LIVE / "01-city-main.png",
    "toast": LIVE / "00-start-city-with-baovat-popup.png",
    "panel": LIVE / "14-building-panel-dan-cu.png",
    "field": LIVE / "04-pho-ban.png",
}


@lru_cache(maxsize=None)
def frame(state: str) -> np.ndarray:
    return read_image(_FRAMES[state])


# --- a scripted fake city: capturer + OCR + click reactions --------------------------------------------
class CityGame:
    """Buildings are labels (centre -> level). Clicking ``centre + hit`` opens the building's panel;
    "Tăng cấp" raises its level by one when ``instant`` (gold skip), never above the city level."""

    def __init__(self, labels: dict[tuple[int, int], int], city_level: int = 80, state: str = "city",
                 hit: tuple[int, int] = (0, -14), instant: bool = True,
                 dead: Iterable[tuple[int, int]] = (), city_text: str = "80") -> None:
        self.levels = dict(labels)
        self.city_level = city_level
        self.state = state
        self.hit = hit
        self.instant = instant
        self.dead = set(dead)
        self.city_text = city_text
        self.icons_hidden = False
        self.notice_hidden = False
        self.open: tuple[int, int] | None = None
        self.visits: list[tuple[int, int]] = []
        self.upgrade_clicks: list[int] = []
        self._lock = threading.Lock()

    # capturer
    def grab(self) -> np.ndarray:
        with self._lock:
            img = frame(self.state).copy()
            if self.state != "field":
                if self.icons_hidden:
                    x, y = ORIGIN.x + 822, ORIGIN.y + 70
                    img[y:y + 18, x:x + 19] = 0
                if self.notice_hidden:
                    x, y = ORIGIN.x + 2, ORIGIN.y + 129
                    img[y:y + 17, x:x + 17] = 0
            return img

    def client_size(self) -> tuple[int, int]:
        img = frame(self.state)
        return int(img.shape[1]), int(img.shape[0])

    # OCR engine: the crop of RunContext.read_text starts at the canvas region's top-left
    def read(self, image: np.ndarray) -> list[OcrLine]:
        if image.shape[0] < 100:  # the city-level badge
            return [line(self.city_text, 8, 4, 19, 15)] if self.city_text else []
        with self._lock:
            return [line(f"lv{lv}", cx - 15, cy - 8) for (cx, cy), lv in self.levels.items()]

    # input
    def on_event(self, ev: InputEvent) -> None:
        if ev.kind != "click" or ev.point is None:
            return
        c = canvas_of(ev.point)
        with self._lock:
            if self.state == "city":
                if near(c, ICONS_TOGGLE, 3) or near(c, HUD_TOGGLE, 3):
                    self.icons_hidden = not self.icons_hidden
                elif near(c, NOTICE_TOGGLE, 3):
                    self.notice_hidden = True
                else:
                    for centre in self.levels:
                        if centre not in self.dead and near(c, (centre[0] + self.hit[0], centre[1] + self.hit[1]), 2):
                            self.state, self.open = "panel", centre
                            self.visits.append(centre)
                            self.upgrade_clicks.append(0)
            elif self.state == "panel":
                if near(c, V180_UPGRADE, 2):
                    self.upgrade_clicks[-1] += 1
                    if self.instant and self.open is not None:
                        self.levels[self.open] = min(self.city_level, self.levels[self.open] + 1)
                elif near(c, V180_CLOSE, 2):
                    self.state, self.open = "city", None


def run_city(game: CityGame, gold: bool = False, png_dir: Path | None = None, **params):
    drv = RecordingInputDriver(on_event=game.on_event)
    ctx = make_ctx(game, drv, png_dir=png_dir, ocr=game, gold=gold)
    params.setdefault("delay_ms", 0)
    params.setdefault("scene_timeout_s", 0.5)
    return execute(CongTrinh(CongTrinhParams(**params)), ctx), drv


# --- coordinates / registry ----------------------------------------------------------------------------
def test_coords_file() -> None:
    assert COORDS.offsets("nang_cap_cong_trinh.hai_cang_buildings") == V180_HARBOUR
    assert COORDS.offset("nang_cap_cong_trinh.upgrade") == V180_UPGRADE
    assert COORDS.offset("nang_cap_cong_trinh.close") == V180_CLOSE
    assert COORDS.raw("nang_cap_cong_trinh.city_level_region") == [4, 96, 36, 34]
    assert COORDS.offsets("nang_cap_cong_trinh.label_click_offsets") == [(0, -14), (0, 0)]
    assert COORDS.source_of("nang_cap_cong_trinh").name == "nang_cap_cong_trinh.yaml"


def test_registered() -> None:
    assert registry.get_feature("cong_trinh") is CongTrinh
    for name in CongTrinh.required_templates():
        assert STORE.get(name) is not None


# --- Thành chính: reading the screen -------------------------------------------------------------------
def test_parse_labels_and_city_level() -> None:
    lines = [line("lv80", 411, 97), line("'Iv60", 552, 422, 31), line("IV1", 110, 376, 18, 11),
             line("Lệ1000/100", 90, 105, 80), line("Chúa công:21", 40, 77, 80), line("Tổng LC:56741", 220, 100),
             line("lv80 lv60", 100, 200, 90)]
    got = [(b.level, b.rect.x) for b in parse_labels(lines)]
    assert got == [(80, 411), (60, 558), (1, 110), (80, 100), (60, 150)]
    assert parse_city_level([line("80", 0, 0)]) == 80
    assert parse_city_level([line("110", 0, 0)]) == 110
    assert parse_city_level([line("", 0, 0)]) is None
    assert parse_city_level([]) is None


@pytest.mark.parametrize("name", ["01-city-main.png", "34-city-panel-collapsed.png"])
def test_ocr_reads_live_city(name: str) -> None:
    """RapidOCR on the live captures: the city level badge and all 17 building labels."""
    ocr = pytest.importorskip("ngoalong.core.ocr").default_ocr()
    if ocr is None:
        pytest.skip("RapidOCR not installed")
    canvas = read_image(LIVE / name)[ORIGIN.y:ORIGIN.y + 600, ORIGIN.x:ORIGIN.x + 1000]
    r = Rect(*COORDS.raw("nang_cap_cong_trinh.city_level_region"))
    assert parse_city_level(ocr.read(canvas[r.y:r.bottom, r.x:r.right])) == 80
    levels = sorted(b.level for b in parse_labels(ocr.read(canvas)))
    assert levels == [1] * 5 + [60] * 3 + [80] * 9


def test_city_upgrades_buildings_below_the_city_level_then_stops() -> None:
    game = CityGame({(100, 300): 78, (300, 300): 80, (500, 200): 79})
    r, drv = run_city(game)
    assert r.status is RunStatus.DONE, r.message
    assert set(game.levels.values()) == {80}
    # lowest first; one "Tăng cấp" per visit without gold skip
    assert game.visits == [(100, 300), (500, 200), (100, 300)]
    assert game.upgrade_clicks == [1, 1, 1]
    assert (300, 300) not in game.visits  # already at the city level
    assert "lv78→lv80" in r.message and "lv79→lv80" in r.message
    # panels: icon rows and notice list collapsed first, icon rows opened again at the end
    assert near(canvas_of(drv.clicks[0]), ICONS_TOGGLE, 3) and near(canvas_of(drv.clicks[1]), NOTICE_TOGGLE, 3)
    assert near(canvas_of(drv.clicks[-1]), HUD_TOGGLE, 3)
    assert game.notice_hidden and not game.icons_hidden
    assert game.state == "city"


def test_city_nothing_to_upgrade() -> None:
    game = CityGame({(100, 300): 80, (300, 300): 80})
    r, drv = run_city(game)
    assert r.status is RunStatus.DONE, r.message
    assert game.visits == [] and at(V180_UPGRADE) not in drv.clicks
    assert "không còn công trình nào dưới cấp 80" in r.message


def test_city_building_that_does_not_rise_is_left_alone() -> None:
    """Without instant upgrades (timer running, no silver) every building is visited once."""
    game = CityGame({(100, 300): 50, (300, 300): 60, (500, 200): 80}, instant=False)
    r, drv = run_city(game)
    assert r.status is RunStatus.DONE, r.message
    assert game.visits == [(100, 300), (300, 300)]
    assert "2 công trình chưa lên cấp" in r.message


def test_city_with_gold_skip_clicks_upgrade_several_times() -> None:
    game = CityGame({(100, 300): 70}, city_level=75, city_text="75")
    r, _ = run_city(game, gold=True, upgrades=3)
    assert r.status is RunStatus.DONE, r.message
    assert game.levels[(100, 300)] == 75
    assert game.upgrade_clicks == [3, 3]


def test_city_tries_the_next_click_offset() -> None:
    game = CityGame({(300, 300): 79}, hit=(0, 0))
    r, drv = run_city(game)
    assert r.status is RunStatus.DONE, r.message
    assert game.levels[(300, 300)] == 80
    assert at((300, 286)) in drv.clicks and at((300, 300)) in drv.clicks


def test_city_building_whose_panel_never_opens_is_skipped() -> None:
    game = CityGame({(100, 300): 60, (300, 300): 79}, dead=[(100, 300)])
    r, _ = run_city(game)
    assert r.status is RunStatus.DONE, r.message
    assert game.visits == [(300, 300)] and game.levels[(300, 300)] == 80
    assert "1 công trình chưa lên cấp" in r.message and "lv60" in r.message


def test_city_toast_is_dismissed_first() -> None:
    game = CityGame({(300, 300): 79}, state="toast")
    game.on_event_orig = game.on_event

    def on_event(ev: InputEvent) -> None:
        if game.state == "toast" and ev.point is not None and near(canvas_of(ev.point), (987, 314), 10):
            game.state = "city"
            return
        game.on_event_orig(ev)

    game.on_event = on_event  # type: ignore[method-assign]
    r, drv = run_city(game)
    assert r.status is RunStatus.DONE, r.message
    assert near(canvas_of(drv.clicks[0]), (987, 314), 10)
    assert game.levels[(300, 300)] == 80


def test_city_level_unreadable_fails_clearly(tmp_path: Path) -> None:
    game = CityGame({(300, 300): 79}, city_text="")
    r, _ = run_city(game, png_dir=tmp_path)
    assert r.status is RunStatus.FAILED
    assert "không đọc được cấp Thành chính" in r.message
    assert game.visits == []


def test_refuses_to_start_outside_the_city(tmp_path: Path) -> None:
    game = CityGame({}, state="field")
    r, drv = run_city(game, png_dir=tmp_path, scene_timeout_s=0.5)
    assert r.status is RunStatus.FAILED
    assert "Thành chính" in r.message and "field" in r.message
    assert drv.clicks == []
    assert r.screenshot is not None and r.screenshot.is_file()


# --- Hải cảng: v1.8.0 parity ---------------------------------------------------------------------------
def v180_sequence(buildings: Iterable[tuple[int, int]], upgrades: int) -> list[Point]:
    seq: list[Point] = []
    for b in buildings:
        seq += [at(b)] + [at(V180_UPGRADE)] * upgrades + [at(V180_CLOSE)]
    return seq


class HarbourGame:
    """Capturer + click reactions on the city captures: listed points open the panel, ✕ closes it."""

    def __init__(self, opens: Iterable[tuple[int, int]] = ()) -> None:
        self.state = "city"
        self.opens = list(opens)
        self.upgrades: list[int] = []

    def grab(self) -> np.ndarray:
        return frame(self.state).copy()

    def client_size(self) -> tuple[int, int]:
        img = frame(self.state)
        return int(img.shape[1]), int(img.shape[0])

    def on_event(self, ev: InputEvent) -> None:
        if ev.kind != "click" or ev.point is None:
            return
        c = canvas_of(ev.point)
        if self.state == "city" and any(near(c, b, 2) for b in self.opens):
            self.state = "panel"
            self.upgrades.append(0)
        elif self.state == "panel":
            if near(c, V180_UPGRADE, 2):
                self.upgrades[-1] += 1
            elif near(c, V180_CLOSE, 2):
                self.state = "city"


def test_harbour_blind_click_sequence_matches_v180() -> None:
    drv = RecordingInputDriver()
    params = CongTrinhParams(mode=Mode.HAI_CANG, upgrades=3, delay_ms=0, verify_screens=False)
    progress: list[tuple[int, int | None]] = []
    ctx = make_ctx(FileCapturer([blank()], loop=True), drv)
    ctx._on_progress = lambda d, t: progress.append((d, t))
    r = execute(CongTrinh(params), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert drv.clicks == v180_sequence(V180_HARBOUR, 3)
    assert all(e.button == "left" and not e.double for e in drv.events)
    assert progress == [(i, 6) for i in range(1, 7)]
    assert "6/6" in r.message


def test_harbour_blind_delays_match_v180() -> None:
    drv = RecordingInputDriver()
    ctx = make_ctx(FileCapturer([blank()], loop=True), drv)
    sleeps: list[float] = []
    ctx.sleep = sleeps.append  # type: ignore[method-assign]
    execute(CongTrinh(CongTrinhParams(mode=Mode.HAI_CANG, upgrades=2, delay_ms=500, buildings="1",
                                      verify_screens=False)), ctx)
    # SLEEP, click building, SLEEP, (SLEEP/100, upgrade, SLEEP/10) x2, SLEEP, close, SLEEP
    assert sleeps == [500, 500, 5, 50, 5, 50, 500, 500]


def test_harbour_subset() -> None:
    drv = RecordingInputDriver()
    execute(CongTrinh(CongTrinhParams(mode=Mode.HAI_CANG, buildings="2, 5-6", upgrades=1, delay_ms=0,
                                      verify_screens=False)),
            make_ctx(FileCapturer([blank()], loop=True), drv))
    assert drv.clicks == v180_sequence([V180_HARBOUR[1], V180_HARBOUR[4], V180_HARBOUR[5]], 1)


def test_harbour_mode_with_checks() -> None:
    game = HarbourGame(opens=[V180_HARBOUR[0]])
    drv = RecordingInputDriver(on_event=game.on_event)
    r = execute(CongTrinh(CongTrinhParams(mode=Mode.HAI_CANG, buildings="1-2", upgrades=1, delay_ms=0,
                                          scene_timeout_s=0.5)), make_ctx(game, drv))
    assert r.status is RunStatus.DONE, r.message
    assert drv.clicks == v180_sequence([V180_HARBOUR[0]], 1) + [at(V180_HARBOUR[1])]
    assert "1/2 công trình Hải cảng" in r.message


def test_stop_within_one_second() -> None:
    control = RunControl()
    drv = RecordingInputDriver()
    ctx = make_ctx(FileCapturer([blank()], loop=True), drv, control=control)
    task = CongTrinh(CongTrinhParams(mode=Mode.HAI_CANG, delay_ms=60_000, verify_screens=False))
    out: list = []
    t = threading.Thread(target=lambda: out.append(execute(task, ctx)), daemon=True)
    t.start()
    time.sleep(0.2)  # inside the first 60 s delay
    t0 = time.monotonic()
    control.stop()
    t.join(2.0)
    assert not t.is_alive() and time.monotonic() - t0 < 1.0
    assert out[0].status is RunStatus.STOPPED
    assert drv.clicks == []


# --- params -----------------------------------------------------------------------------------------
def test_building_list_parsing() -> None:
    assert parse_building_list("") == []
    assert parse_building_list(" 5, 1-3 ,2;9 ") == [1, 2, 3, 5, 9]
    assert format_building_list([1, 2, 3, 5, 9, 10]) == "1-3,5,9-10"
    for bad in ("abc", "0", "3-1", "1-", "-2"):
        with pytest.raises(ValueError):
            parse_building_list(bad)


def test_params_validation() -> None:
    p = CongTrinhParams(mode=Mode.HAI_CANG, buildings="5,1-3, 2")
    assert p.buildings == "1-3,5" and p.targets() == [1, 2, 3, 5]
    assert CongTrinhParams(mode=Mode.HAI_CANG).targets() == list(range(1, 7))
    assert CongTrinhParams(buildings="1-18").mode is Mode.THANH_CHINH  # old city lists still load
    with pytest.raises(ValueError):
        CongTrinhParams(upgrades=0)  # v1.8.0 #15: empty / zero upgrades ran nothing silently
    with pytest.raises(ValueError):
        CongTrinhParams(delay_ms=-1)
    with pytest.raises(ValueError):
        CongTrinhParams(mode=Mode.HAI_CANG, buildings="7")
    with pytest.raises(ValueError):
        CongTrinhParams(buildings="x")
    p = CongTrinhParams(buildings="1-18")
    with pytest.raises(ValueError):
        p.mode = Mode.HAI_CANG  # validated on assignment as well
    assert CongTrinhParams.model_validate({"mode": "hai_cang", "skip_hud_buildings": True}).mode is Mode.HAI_CANG
    assert Mode.THANH_CHINH.label == "Thành chính" and Mode.HAI_CANG.label == "Hải cảng"


def test_legacy_import() -> None:
    assert CongTrinh.import_legacy({"CAPDO": "10", "NCCT_SLEEP": "500"}) == {"upgrades": 10, "delay_ms": 500}
    assert CongTrinh.import_legacy({"CAPDO": "", "NCCT_SLEEP": "abc"}) == {}
    assert CongTrinh.import_legacy({"CAPDO": "0", "NCCT_SLEEP": "-5"}) == {}
    out = CongTrinh.import_legacy({"CAPDO": "3", "NCCT_SLEEP": "0"})
    assert CongTrinhParams.model_validate(out).upgrades == 3


def test_form_is_generated(qapp) -> None:
    from ngoalong.ui.forms import ChoiceEditor, ParamsForm

    form = ParamsForm(CongTrinhParams)
    assert isinstance(form.editors["mode"], ChoiceEditor)
    form.editors["mode"].set("hai_cang")
    form.editors["buildings"].set("1-3")
    v = form.value()
    assert v.mode is Mode.HAI_CANG and v.buildings == "1-3"
