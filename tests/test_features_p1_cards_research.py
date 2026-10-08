"""P1 Mở thẻ bài + Nâng cấp khoa kỹ: parity with v1.8.0 MOTHEBAI / NANGCAPKHOAKY (fake driver).

Each test compares the clicks recorded by ``RecordingInputDriver`` with a transliteration of the
AutoIt function (v1.8.0 ``app_logic_only.au3``): ``X_GOC``/``Y_GOC`` is the canvas origin, so an
AutoIt click ``X_GOC + dx`` is the canvas offset ``dx`` placed at that origin.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from conftest import PROFILE, ROOT
from ngoalong.core.coords import CoordMap
from ngoalong.core.drivers.fake import InputEvent, RecordingInputDriver
from ngoalong.core.drivers.file import FileCapturer
from ngoalong.core.geometry import Point
from ngoalong.core.scenes import SceneDetector
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher
from ngoalong.debug.sink import PngSink
from ngoalong.engine import registry
from ngoalong.engine.context import FixedCanvas, RunContext
from ngoalong.engine.runner import RunHooks, RunStatus, TaskRunner, execute
from ngoalong.engine.task import Task
from ngoalong.features.khoa_ky import KhoaKy, KhoaKyParams
from ngoalong.features.mo_the_bai import CardType, MoTheBai, MoTheBaiParams

LIVE = ROOT / "docs" / "game screenshots" / "live"
CITY = LIVE / "01-city-main.png"
RESEARCH = LIVE / "16-khoa-ky.png"
TINH_TU = LIVE / "19-tinh-tu.png"
STORE = TemplateStore(PROFILE / "templates")
COORDS = CoordMap.load(PROFILE / "coords")
SCENES = SceneDetector.load(PROFILE / "scenes", templates_dir=PROFILE / "templates")
X_GOC, Y_GOC = 180, 110  # canvas origin of every live capture (docs/07)
ORIGIN = Point(X_GOC, Y_GOC)

Ev = tuple[Any, ...]  # ("click", x, y, double) | ("text", s)


def legacy_coords() -> CoordMap:
    """The v1.8.0 offsets, which the profile keeps unchanged for these two features."""
    keys = ("mo_the_bai", "nang_cap_khoa_ky")
    return CoordMap({k: yaml.safe_load((PROFILE / "coords" / f"{k}.yaml").read_text(encoding="utf-8"))[k] for k in keys})


def ctx_for(frames, driver: RecordingInputDriver, coords: CoordMap = COORDS, control=None,
            png_dir: Path | None = None) -> RunContext:
    return RunContext(
        capturer=FileCapturer(frames, loop=True),
        input_driver=driver,
        coords=coords,
        matcher=Matcher(STORE, history=64),
        canvas_source=FixedCanvas(ORIGIN),
        scenes=SCENES,
        control=control,
        png_sink=PngSink(png_dir) if png_dir is not None else None,
        poll_interval_s=0.05,
    )


def blank() -> np.ndarray:
    return np.zeros((1391, 1349, 3), np.uint8)


def recorded(drv: RecordingInputDriver) -> list[Ev]:
    out: list[Ev] = []
    for e in drv.events:
        if e.kind == "click":
            assert e.point is not None and e.button == "left"
            out.append(("click", e.point.x, e.point.y, e.double))
        else:
            out.append((e.kind, e.text))
    return out


# --- AutoIt oracles (transliterations of v1.8.0 app_logic_only.au3) ------------------------------
def autoit_mothebai(xg: int, yg: int, thebaiid: int, soluong: int, muathebai: bool) -> list[Ev]:
    """MOTHEBAI, lines 1114-1164: clicks in order, ``double`` for "Left Double Click"."""
    def c(dx: int, dy: int, double: bool = False) -> Ev:
        return ("click", xg + dx, yg + dy, double)

    ev = [c(230, 280)] * 3                       # TOADOLUI x3
    ev += [c(770, 280)] * (thebaiid - 1)         # For J = 1 To THEBAIID - 1: TOADOTIEN
    ev += [c(666, 170)]                          # TOADOCHON
    if muathebai:
        ev += [c(500, 415), c(435, 285, True), ("text", str(soluong)), c(440, 365, True), c(450, 330)]
    for _ in range(soluong):                     # While I <= SOLUONG
        ev += [c(625, 415), c(440, 365), c(625, 415)]  # TOADORUTSLL, TOADORUT, TOADORUTSLL
    return ev


def autoit_nangcapkhoaky(xg: int, yg: int, solannc: int) -> list[Ev]:
    """NANGCAPKHOAKY, lines 1074-1113."""
    ev: list[Ev] = [("click", xg + 536, yg + 170, False), ("click", xg + 520, yg + 320, False)]
    for row_y in (250, 320, 400):
        x = xg + 240
        for _ in range(6):
            ev.append(("click", x, yg + row_y, False))
            ev += [("click", xg + 615, yg + 470, False)] * solannc
            x += 75
    return ev


# --- Mở thẻ bài -----------------------------------------------------------------------------------
@pytest.mark.parametrize("card", list(CardType))
@pytest.mark.parametrize("buy", [False, True])
def test_mo_the_bai_matches_autoit(card: CardType, buy: bool) -> None:
    drv = RecordingInputDriver()
    params = MoTheBaiParams(card=card, quantity=4, buy_first=buy, delay_ms=0, draw_delay_ms=0)
    r = execute(MoTheBai(params), ctx_for([blank()], drv))
    assert r.status is RunStatus.DONE, r.message
    assert recorded(drv) == autoit_mothebai(X_GOC, Y_GOC, int(card), 4, buy)


def test_mo_the_bai_profile_coords_are_the_v180_offsets() -> None:
    legacy = legacy_coords()
    for key in legacy.point_keys("mo_the_bai"):
        assert COORDS.offset(key) == legacy.offset(key), key
    assert sorted(COORDS.point_keys("mo_the_bai")) == sorted(legacy.point_keys("mo_the_bai"))


def test_mo_the_bai_counts_and_progress() -> None:
    drv = RecordingInputDriver()
    ctx = ctx_for([blank()], drv)
    progress: list[tuple[int, int | None]] = []
    ctx._on_progress = lambda d, t: progress.append((d, t))
    params = MoTheBaiParams(card=CardType.BONG_LAI_TIEN_SON, quantity=25, delay_ms=0, draw_delay_ms=0)
    r = execute(MoTheBai(params), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert len(drv.clicks) == 3 + 2 + 1 + 25 * 3
    assert progress == [(i, 25) for i in range(1, 26)]
    assert "25" in r.message and "Bồng Lai" in r.message


def test_mo_the_bai_follows_canvas_and_zoom() -> None:
    drv = RecordingInputDriver()
    ctx = ctx_for([blank()], drv)
    ctx.canvas_source = FixedCanvas(Point(50, 40), scale=1.5)
    execute(MoTheBai(MoTheBaiParams(quantity=1, delay_ms=0, draw_delay_ms=0)), ctx)
    assert drv.clicks[0] == Point(50 + 345, 40 + 420)  # back (230, 280) * 1.5


def test_mo_the_bai_runs_over_the_city() -> None:
    drv = RecordingInputDriver()
    r = execute(MoTheBai(MoTheBaiParams(quantity=1, delay_ms=0, draw_delay_ms=0)), ctx_for([CITY], drv))
    assert r.status is RunStatus.DONE, r.message
    assert recorded(drv) == autoit_mothebai(X_GOC, Y_GOC, 1, 1, False)


def test_mo_the_bai_refuses_while_another_dialog_is_open(tmp_path: Path) -> None:
    drv = RecordingInputDriver()
    r = execute(MoTheBai(MoTheBaiParams(quantity=3, delay_ms=0)), ctx_for([RESEARCH], drv, png_dir=tmp_path))
    assert r.status is RunStatus.FAILED
    assert "research" in r.message
    assert drv.events == []
    assert r.screenshot is not None and r.screenshot.is_file()


def test_mo_the_bai_screen_check_can_be_turned_off() -> None:
    drv = RecordingInputDriver()
    params = MoTheBaiParams(quantity=1, delay_ms=0, draw_delay_ms=0, check_screen=False)
    r = execute(MoTheBai(params), ctx_for([RESEARCH], drv))
    assert r.status is RunStatus.DONE, r.message
    assert len(drv.clicks) == 3 + 1 + 3


@pytest.mark.parametrize("bad", [
    {"quantity": 0}, {"quantity": -1}, {"quantity": "Số lượng"}, {"quantity": ""},
    {"quantity": 100_001}, {"card": 0}, {"card": 4}, {"delay_ms": -1},
])
def test_mo_the_bai_params_validation(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        MoTheBaiParams(**bad)


def test_mo_the_bai_params_defaults_and_roundtrip() -> None:
    p = MoTheBaiParams()
    assert (p.card, p.quantity, p.buy_first, p.delay_ms, p.draw_delay_ms) == (CardType(1), 100, False, 500, 5)
    dumped = MoTheBaiParams(card=CardType.HOA_DIEM_SON).model_dump(mode="json")
    assert dumped["card"] == 2
    assert MoTheBaiParams.model_validate(dumped).card is CardType.HOA_DIEM_SON
    assert CardType.HOA_DIEM_SON.label == "2. Thẻ Bài Hỏa Diễm Sơn"


def test_mo_the_bai_legacy_import() -> None:
    assert MoTheBai.import_legacy({"MTB_SOLUONGTHE": "100"}) == {"quantity": 100}
    assert MoTheBai.import_legacy({"MTB_SOLUONGTHE": "Số lượng"}) == {}
    assert MoTheBai.import_legacy({"MTB_SOLUONGTHE": ""}) == {}
    assert MoTheBai.import_legacy({"MTB_SOLUONGTHE": "0"}) == {}
    assert MoTheBai.import_legacy({}) == {}
    assert MoTheBai.import_legacy({"MTB_SOLUONGTHE": "999999"}) == {"quantity": 100_000}


# --- Nâng cấp khoa kỹ -----------------------------------------------------------------------------
@pytest.mark.parametrize("upgrades", [1, 3])
def test_khoa_ky_matches_autoit(upgrades: int) -> None:
    """Started on the research dialog: the grid walk is click for click the v1.8.0 one."""
    drv = RecordingInputDriver()
    params = KhoaKyParams(upgrades_per_tech=upgrades, delay_ms=0, upgrade_delay_ms=0)
    r = execute(KhoaKy(params), ctx_for([RESEARCH], drv))
    assert r.status is RunStatus.DONE, r.message
    expected = autoit_nangcapkhoaky(X_GOC, Y_GOC, upgrades)
    assert recorded(drv) == expected[2:]  # the two v1.8.0 city clicks are replaced (docs/07)
    assert len(drv.clicks) == 18 * (1 + upgrades)


def test_khoa_ky_profile_coords_are_the_v180_offsets() -> None:
    legacy = legacy_coords()
    assert COORDS.offsets("nang_cap_khoa_ky.rows_first_item") == legacy.offsets("nang_cap_khoa_ky.rows_first_item")
    assert COORDS.value("nang_cap_khoa_ky.item_step_x") == legacy.value("nang_cap_khoa_ky.item_step_x") == 75
    assert COORDS.offset("nang_cap_khoa_ky.upgrade") == legacy.offset("nang_cap_khoa_ky.upgrade")


def test_khoa_ky_v180_clicks_land_inside_the_live_targets() -> None:
    """docs/07 measured icon centres x 238 + 72k, y 248 / 322 / 395 (icons ~60 px) and "Tăng cấp"
    (615, 472) on 16-khoa-ky.png: every v1.8.0 click falls inside its target."""
    drv = RecordingInputDriver()
    execute(KhoaKy(KhoaKyParams(delay_ms=0, upgrade_delay_ms=0)), ctx_for([RESEARCH], drv))
    techs = [(p.x - X_GOC, p.y - Y_GOC) for p in drv.clicks[0::2]]
    upgrades = [(p.x - X_GOC, p.y - Y_GOC) for p in drv.clicks[1::2]]
    measured = [(238 + 72 * col, y) for y in (248, 322, 395) for col in range(6)]
    assert len(techs) == len(measured) == 18
    for (x, y), (mx, my) in zip(techs, measured):
        assert abs(x - mx) <= 25 and abs(y - my) <= 25, ((x, y), (mx, my))
    assert all(abs(x - 615) <= 20 and abs(y - 472) <= 8 for x, y in upgrades)


def test_khoa_ky_grid_follows_the_coords_file() -> None:
    """Editing the yaml (e.g. to the live-measured step 72) moves the clicks; nothing is hard-coded."""
    tuned = CoordMap({"nang_cap_khoa_ky": {
        "rows_first_item": [[238, 248], [238, 322], [238, 395]], "item_step_x": 72, "upgrade": [615, 472],
    }})
    drv = RecordingInputDriver()
    r = execute(KhoaKy(KhoaKyParams(delay_ms=0, upgrade_delay_ms=0)), ctx_for([RESEARCH], drv, coords=tuned))
    assert r.status is RunStatus.DONE, r.message
    assert [(p.x - X_GOC, p.y - Y_GOC) for p in drv.clicks[0::2]] == [
        (238 + 72 * col, y) for y in (248, 322, 395) for col in range(6)
    ]
    assert set(drv.clicks[1::2]) == {Point(X_GOC + 615, Y_GOC + 472)}


def test_khoa_ky_opens_research_from_the_city() -> None:
    drv = RecordingInputDriver()
    ctx = ctx_for([CITY], drv)

    def on_click(ev: InputEvent) -> None:
        if len(drv.events) == 1:  # the bottom-bar "Khoa kỹ" opens the dialog
            ctx.capturer = FileCapturer([RESEARCH], loop=True)

    drv._on_event = on_click
    r = execute(KhoaKy(KhoaKyParams(delay_ms=0, upgrade_delay_ms=0)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert drv.clicks[0] == Point(X_GOC + 717, Y_GOC + 578)
    assert len(drv.clicks) == 1 + 18 * 2
    assert recorded(drv)[1:] == autoit_nangcapkhoaky(X_GOC, Y_GOC, 1)[2:]


def test_khoa_ky_fails_when_the_dialog_does_not_open(tmp_path: Path) -> None:
    drv = RecordingInputDriver()
    r = execute(KhoaKy(KhoaKyParams(delay_ms=0, scene_timeout_s=0.5)), ctx_for([CITY], drv, png_dir=tmp_path))
    assert r.status is RunStatus.FAILED
    assert "research" in r.message
    assert drv.clicks == [Point(X_GOC + 717, Y_GOC + 578)]  # only the open click, no blind grid
    assert r.screenshot is not None and r.screenshot.is_file()


def test_khoa_ky_refuses_to_start_on_another_screen(tmp_path: Path) -> None:
    drv = RecordingInputDriver()
    r = execute(KhoaKy(KhoaKyParams(delay_ms=0, scene_timeout_s=0.5)), ctx_for([TINH_TU], drv, png_dir=tmp_path))
    assert r.status is RunStatus.FAILED
    assert "chest.wheel" in r.message
    assert drv.events == []


def test_khoa_ky_stops_when_the_dialog_closes(tmp_path: Path) -> None:
    drv = RecordingInputDriver()
    ctx = ctx_for([RESEARCH], drv, png_dir=tmp_path)

    def on_click(ev: InputEvent) -> None:
        if len(drv.events) == 3:  # tech 1 + 2 upgrades, then the dialog is gone
            ctx.capturer = FileCapturer([CITY], loop=True)

    drv._on_event = on_click
    params = KhoaKyParams(upgrades_per_tech=2, delay_ms=0, upgrade_delay_ms=0, scene_timeout_s=0.5)
    r = execute(KhoaKy(params), ctx)
    assert r.status is RunStatus.FAILED
    assert "research" in r.message and "city" in r.message
    assert len(drv.clicks) == 3


def test_khoa_ky_progress_per_tech() -> None:
    drv = RecordingInputDriver()
    ctx = ctx_for([RESEARCH], drv)
    progress: list[tuple[int, int | None]] = []
    ctx._on_progress = lambda d, t: progress.append((d, t))
    r = execute(KhoaKy(KhoaKyParams(delay_ms=0, upgrade_delay_ms=0, verify_each_tech=False)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert progress == [(i, 18) for i in range(1, 19)]
    assert "18" in r.message


@pytest.mark.parametrize("bad", [
    {"upgrades_per_tech": 0}, {"upgrades_per_tech": ""}, {"upgrades_per_tech": "Số lần"},
    {"delay_ms": -5}, {"scene_timeout_s": 0},
])
def test_khoa_ky_params_validation(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        KhoaKyParams(**bad)


def test_khoa_ky_open_button_is_the_bottom_bar_khoa_ky() -> None:
    assert tuple(SCENES.get("city").objects["research"].offset) == (717, 578)  # docs/07


# --- registry and stop ----------------------------------------------------------------------------
def test_features_are_registered() -> None:
    ids = {f.id for f in registry.discover()}
    assert {"mo_the_bai", "khoa_ky"} <= ids
    assert registry.get_feature("mo_the_bai").title == "Mở thẻ bài"
    assert registry.get_feature("khoa_ky").title == "Nâng cấp khoa kỹ"


def _run_and_stop(task: Task, frames, tmp_path: Path) -> tuple[float, Any, RecordingInputDriver]:
    drv = RecordingInputDriver()
    runner = TaskRunner(tmp_path / "logs")

    def factory(t: Task, hooks: RunHooks) -> RunContext:
        ctx = ctx_for(frames, drv, control=hooks.control)
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
    return time.monotonic() - t0, runner.last_result, drv


def test_stop_ends_mo_the_bai_within_one_second(qapp, tmp_path: Path) -> None:
    task = MoTheBai(MoTheBaiParams(quantity=1000, delay_ms=0, draw_delay_ms=60_000))
    elapsed, result, drv = _run_and_stop(task, [blank()], tmp_path)
    assert elapsed < 1.0
    assert result.status is RunStatus.STOPPED
    assert len(drv.clicks) == 3 + 1 + 1  # back x3, select, first "rút SLL"


def test_stop_ends_khoa_ky_within_one_second(qapp, tmp_path: Path) -> None:
    task = KhoaKy(KhoaKyParams(upgrades_per_tech=5, delay_ms=0, upgrade_delay_ms=60_000))
    elapsed, result, drv = _run_and_stop(task, [RESEARCH], tmp_path)
    assert elapsed < 1.0
    assert result.status is RunStatus.STOPPED
    assert len(drv.clicks) == 2  # tech 1, first "Tăng cấp"
