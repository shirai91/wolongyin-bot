"""P2 Clear Phó Bản (v1.8.0 CLEARMAP), offline.

A small game simulator composes frames from the live field captures (``docs/game screenshots/live``:
04 Lưu Chương with two red-flag armies (the light-blue beam over A), 06 the same map with the HUD collapsed, 08 Công Tôn with
"»", 09 the army dialog, 01 the city) and switches state when the fake input driver records a click
on the right spot, so every scene check, template wait and click of the feature runs on real pixels.
Popups no live capture shows (result boxes, recruit dialog, Button_KetQua) are pasted from their
templates.
"""

from __future__ import annotations

import threading
import time
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest

from conftest import PROFILE, ROOT
from ngoalong.core.capture import read_image
from ngoalong.core.coords import CoordMap
from ngoalong.core.drivers.fake import InputEvent, RecordingInputDriver
from ngoalong.core.geometry import Rect
from ngoalong.core.input import VK_ESCAPE
from ngoalong.core.locator import CanvasLocator
from ngoalong.core.scenes import SceneDetector
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher
from ngoalong.engine import registry
from ngoalong.engine.context import RunContext
from ngoalong.engine.control import RunControl
from ngoalong.engine.runner import RunStatus, execute
from ngoalong.features import clear_map as cm
from ngoalong.features.clear_map import ClearMap, ClearMapParams

LIVE = ROOT / "docs" / "game screenshots" / "live"
STORE = TemplateStore(PROFILE / "templates")
COORDS = CoordMap.load(PROFILE / "coords")
SCENES = SceneDetector.load(PROFILE / "scenes", templates_dir=PROFILE / "templates")
O = (180, 110)  # canvas origin in every live capture

FLAG_A = (401, 393)  # top-left of the red flags on 04 / 06 (canvas)
FLAG_B = (347, 294)
FLAG_SIZE = (14, 8)
BEAM = Rect(396, 313, 21, 42)  # the light-blue "next army" beam over FLAG_A on 04 / 06 (canvas)
DIALOG = Rect(345, 165, 312, 270)  # army dialog box on 09
RESULT_AT = (430, 250)
RECRUIT_AT = (400, 300)
KETQUA_AT = (480, 90)


# --- frames -------------------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _img(name: str) -> np.ndarray:
    return read_image(LIVE / name)


def _paste(img: np.ndarray, at: tuple[int, int], patch: np.ndarray) -> None:
    x, y = O[0] + at[0], O[1] + at[1]
    img[y:y + patch.shape[0], x:x + patch.shape[1]] = patch[:, :, :3]


def _fill(img: np.ndarray, r: Rect, bgr: tuple[int, int, int]) -> None:
    img[O[1] + r.y:O[1] + r.bottom, O[0] + r.x:O[0] + r.right] = bgr


def _tpl(name: str) -> np.ndarray:
    return STORE.get(name).image


def _flag_click(flag: tuple[int, int]) -> tuple[int, int]:
    """Where the feature clicks a flag: match centre + enemy_click_offset_from_match (0, 10)."""
    return (flag[0] + FLAG_SIZE[0] // 2, flag[1] + FLAG_SIZE[1] // 2 + 10)


def _near(p: tuple[float, float], c: tuple[int, int], dx: int = 10, dy: int = 10) -> bool:
    return abs(p[0] - c[0]) <= dx and abs(p[1] - c[1]) <= dy


# --- simulator ----------------------------------------------------------------------------------
class FieldSim:
    """Capturer + click handler for the dungeon map.

    ``outcomes``: what each "Tấn công" leads to, in order (the last one repeats):
    ``win`` / ``lose`` (result box, OK closes it; a win removes the army), ``ketqua`` (v1.8.0's
    Button_KetQua popup), ``silent`` (back to the map, army gone, no box), ``silent_alive`` (back to
    the map, army still there), ``recruit`` (the recruit dialog), ``hang`` (an unknown screen forever),
    ``ignore`` (the army dialog stays).

    The beam marks ``marked`` when set (and alive), else the first live army of ``flags``;
    ``beam=False`` shows none.
    """

    def __init__(self, start: str = "field", map_: str = "luu", outcomes: list[str] | None = None,
                 flags: tuple[tuple[int, int], ...] = (FLAG_A, FLAG_B), beam: bool = True,
                 marked: tuple[int, int] | None = None) -> None:
        self.state = start
        self.map = map_
        self.hud_expanded = True
        self.tracker = True
        self.alive = set(flags) if map_ == "luu" else set()
        self._luu_flags = set(flags)
        self._order = flags
        self.beam = beam
        self.marked = marked
        self.outcomes = list(outcomes or ["win"])
        self.target: tuple[int, int] | None = None
        self.result = ""
        self.ctx: RunContext | None = None
        self.log: list[tuple[str, tuple[float, float], bool]] = []
        self.keys: list[int] = []
        self.attacks = 0

    # Capturer
    def grab(self) -> np.ndarray:
        return self._frame()

    def client_size(self) -> tuple[int, int]:
        f = _img("04-pho-ban.png")
        return (int(f.shape[1]), int(f.shape[0]))

    def _frame(self) -> np.ndarray:
        if self.state == "city":
            return _img("01-city-main.png").copy()
        if self.state == "hang":
            return np.zeros_like(_img("04-pho-ban.png"))
        if self.map == "luu":
            img = _img("04-pho-ban.png" if self.hud_expanded else "06-field-hud-collapsed.png").copy()
        else:
            img = _img("08-dungeon-map-cong-ton.png").copy()
        if not self.tracker:
            _fill(img, Rect(805, 163, 24, 30), (60, 70, 50))
        if self.map == "luu":  # armies that are gone (or not in this scenario) lose their red flag
            for f in {FLAG_A, FLAG_B} - self.alive:
                _fill(img, Rect(f[0] - 2, f[1] - 2, FLAG_SIZE[0] + 4, FLAG_SIZE[1] + 4), (70, 130, 120))
            beam = _img("04-pho-ban.png")[O[1] + BEAM.y:O[1] + BEAM.bottom, O[0] + BEAM.x:O[0] + BEAM.right].copy()
            _fill(img, BEAM, (70, 130, 120))
            target = self.beam_target()
            if target is not None:  # the beam sits 80 px above the flag's top, as on 04
                _paste(img, (target[0] + BEAM.x - FLAG_A[0], target[1] + BEAM.y - FLAG_A[1]), beam)
        if self.state == "army":
            d = _img("09-dungeon-army-dialog.png")
            img[O[1] + DIALOG.y:O[1] + DIALOG.bottom, O[0] + DIALOG.x:O[0] + DIALOG.right] = \
                d[O[1] + DIALOG.y:O[1] + DIALOG.bottom, O[0] + DIALOG.x:O[0] + DIALOG.right]
        elif self.state == "result":
            _paste(img, RESULT_AT, _tpl("tower.result.win" if self.result == "win" else "tower.result.lose"))
        elif self.state == "recruit":
            _paste(img, RECRUIT_AT, _tpl("clear_map.recruit.max"))
        elif self.state == "ketqua":
            _paste(img, KETQUA_AT, _tpl("clear_map.result"))
        return img

    def beam_target(self) -> tuple[int, int] | None:
        if not self.beam or self.map != "luu":
            return None
        if self.marked in self.alive:
            return self.marked
        return next((f for f in self._order if f in self.alive), None)

    # InputDriver callback
    def on_event(self, ev: InputEvent) -> None:
        if ev.kind == "key":
            self.keys.append(ev.vk or 0)
            return
        if ev.kind != "click" or ev.point is None:
            return
        assert self.ctx is not None and self.ctx.last_canvas is not None
        p = self.ctx.last_canvas.to_canvas(ev.point)
        s = self.state
        self.log.append((s, p, ev.double))
        if s == "city" and _near(p, (899, 549)):
            self.state = "field"
        elif s == "field":
            if self.map == "luu" and self.hud_expanded and _near(p, (831, 79)):
                self.hud_expanded = False
            elif self.tracker and _near(p, (817, 178)):
                self.tracker = False
            elif self.map == "cong_ton" and _near(p, (579, 578)):
                self.map = "luu"
                self.alive = set(self._luu_flags)
            else:
                for f in self.alive:
                    if _near(p, _flag_click(f), 6, 6):
                        self.state, self.target = "army", f
        elif s == "army":
            if _near(p, (584, 304), 28, 11):
                self._attack()
            elif _near(p, (638, 185)) or _near(p, (638, 184), 12, 12):
                self.state = "field"
        elif s == "result" and _near(p, (538, 409), 30, 12):
            if self.result == "win":
                self.alive.discard(self.target)
            self.state = "field"
        elif s == "ketqua" and _near(p, (538, 409), 30, 12):
            self.alive.discard(self.target)
            self.state = "field"
        elif s == "recruit" and _near(p, (803, 120)):
            self.state = "army"

    def _attack(self) -> None:
        self.attacks += 1
        o = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if o in ("win", "lose"):
            self.state, self.result = "result", o
        elif o == "silent":
            self.alive.discard(self.target)
            self.state = "field"
        elif o == "silent_alive":
            self.state = "field"
        elif o in ("recruit", "ketqua", "hang"):
            self.state = o
        # "ignore": the dialog stays

    def clicks(self, state: str | None = None) -> list[tuple[float, float]]:
        return [p for s, p, _d in self.log if state is None or s == state]


def make_ctx(sim: FieldSim, control: RunControl | None = None) -> tuple[RunContext, RecordingInputDriver]:
    drv = RecordingInputDriver(on_event=sim.on_event)
    ctx = RunContext(
        capturer=sim, input_driver=drv, coords=COORDS, matcher=Matcher(STORE, history=64),
        canvas_source=CanvasLocator.from_profile(PROFILE), scenes=SCENES, control=control,
        poll_interval_s=0.05,
    )
    sim.ctx = ctx
    return ctx, drv


def params(**kw) -> ClearMapParams:
    base = dict(delay_ms=0, wait_s=0.5, max_misses=1)
    base.update(kw)
    return ClearMapParams.model_construct(**{**ClearMapParams().model_dump(), **base})


@pytest.fixture(autouse=True)
def fast_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cm, "SETTLE_MIN_S", 0.3)
    monkeypatch.setattr(cm, "ATTACK_RETRY_S", 0.3)


# --- templates and profile ----------------------------------------------------------------------
def test_registered_with_vietnamese_form() -> None:
    assert registry.get_feature("clear_map") is ClearMap
    assert ClearMap.start_scene == "field"
    for name, f in ClearMapParams.model_fields.items():
        assert f.title, name


def test_red_flags_found_only_on_dungeon_maps() -> None:
    m = Matcher(STORE)
    sim = FieldSim()
    ctx, _ = make_ctx(sim)
    regions = cm.search_regions(ctx, hud_hidden=True)

    def best(name: str, r: Rect) -> tuple[int, int] | None:
        roi = Rect(O[0] + r.x, O[1] + r.y, r.w, r.h)
        d = m.search(cm.ENEMY, roi, cm.ENEMY_THRESHOLD, frame=_img(name))
        return (d.rect.x - O[0], d.rect.y - O[1]) if d.found else None

    for name in ("04-pho-ban.png", "06-field-hud-collapsed.png"):
        for flag in (FLAG_A, FLAG_B):  # each flag on its own, exactly where it is
            assert best(name, Rect(flag[0] - 10, flag[1] - 10, 34, 28)) == flag
        assert best(name, regions[0]) in (FLAG_A, FLAG_B)  # both are in the centre region
    for name in ("08-dungeon-map-cong-ton.png", "07-dungeon-switch-banner.png", "01-city-main.png",
                 "02-world-map.png", "13-event-dungeon-from-top-pho-ban.png", "12-khu-vuc.png"):
        assert all(best(name, r) is None for r in regions), name


def test_search_regions_avoid_hud_chat_and_bottom_bar() -> None:
    ctx, _ = make_ctx(FieldSim())
    # docs/07: chat log (0..275, 385..545), chat bar (3..275, 559..596), dungeon/bottom bars (y >= 560)
    chat_log, chat_bar, bottom = Rect(0, 385, 276, 215), Rect(0, 555, 276, 45), Rect(400, 560, 600, 40)
    for r in cm.search_regions(ctx, hud_hidden=False):
        for bad in (chat_log, chat_bar, bottom):
            assert r.intersect(bad).is_empty(), (r, bad)
        assert Rect(0, 0, 1000, 600).intersect(r) == r
    assert len(cm.search_regions(ctx, hud_hidden=True)) == len(cm.search_regions(ctx, hud_hidden=False)) + 1


@lru_cache(maxsize=1)
def _stored_canvases() -> list[tuple[str, np.ndarray]]:
    out = [(p.name, read_image(p)[O[1]:O[1] + 600, O[0]:O[0] + 1000]) for p in sorted(LIVE.glob("*.png"))]
    ref = {"main-game.png": (273, 109), "dungeon.png": (272, 110), "worldmap-ui.png": (270, 107),
           "tower-ui.png": (272, 106), "tower-ui-enter-confirmation.png": (270, 110),
           "chest-rolling-ui.png": (271, 112), "tower-in-dungeon.png": (272, 110)}
    for n, (x, y) in ref.items():
        out.append((n, read_image(ROOT / "docs" / "game screenshots" / n)[y:y + 600, x:x + 1000]))
    return out


@pytest.mark.parametrize("obj", ["result", "win", "win_zh", "lose", "lose_zh", "recruit_max", "level_up"])
def test_legacy_markers_do_not_fire_on_stored_screens(obj: str) -> None:
    """The popups' v1.8.0 images stay under their threshold on all 47 stored screens."""
    o = SCENES.get(cm.OBJECTS).objects[obj]
    assert o.template and o.threshold
    m = Matcher(STORE)
    for name, canvas in _stored_canvases():
        d = m.search(o.template, None, o.threshold, frame=np.ascontiguousarray(canvas))
        assert not d.found, f"{obj} fires on {name} ({d.score:.2f})"


def test_hud_objects_match_v180_images_on_live_field() -> None:
    from ngoalong.core.locator import CanvasFix, Confidence

    fix = CanvasFix(Rect(O[0], O[1], 1000, 600), 1.0, Confidence.HIGH)
    expanded, collapsed = _img("04-pho-ban.png"), _img("06-field-hud-collapsed.png")
    assert SCENES.find_object(expanded, fix, cm.OBJECTS, "hud_toggle").found
    assert not SCENES.find_object(collapsed, fix, cm.OBJECTS, "hud_toggle").found
    assert SCENES.find_object(expanded, fix, cm.OBJECTS, "quest_collapse").found
    assert SCENES.find_object(_img("08-dungeon-map-cong-ton.png"), fix, cm.OBJECTS, "next_dungeon").found
    assert not SCENES.find_object(expanded, fix, cm.OBJECTS, "next_dungeon").found  # last dungeon
    # the object group never wins detect(): the field scene is returned
    assert SCENES.detect(expanded, fix).name == "field"


# --- live captures (2026-10-06, read-only PrintWindow of the running game) -----------------------
FIXTURES = ROOT / "tests" / "fixtures" / "screens" / "clear_map"


class StaticScreen:
    def __init__(self, path: Path) -> None:
        self.frame = read_image(path)[:, :, :3].copy()

    def grab(self) -> np.ndarray:
        return self.frame.copy()

    def client_size(self) -> tuple[int, int]:
        return (int(self.frame.shape[1]), int(self.frame.shape[0]))


def static_ctx(path: Path) -> tuple[RunContext, RecordingInputDriver]:
    drv = RecordingInputDriver()
    ctx = RunContext(
        capturer=StaticScreen(path), input_driver=drv, coords=COORDS, matcher=Matcher(STORE, history=64),
        canvas_source=CanvasLocator.from_profile(PROFILE), scenes=SCENES, poll_interval_s=0.02,
    )
    return ctx, drv


def test_live_battle_result_is_closed_with_thoat() -> None:
    """The live win box (战胜, 'Xem lại' / 'Thoát'): its 'Thoát' is v1.8.0's (538, 409)."""
    ctx, drv = static_ctx(FIXTURES / "live-battle-result-win.png")
    task = ClearMap(params())
    res = task._result_marker(ctx)
    assert res is not None and res[0] == "win_zh"
    task._close_result(ctx, *res)
    assert ctx.last_canvas is not None
    thoat = ctx.last_canvas.to_canvas(drv.clicks[-1])
    assert _near(thoat, (540, 408), 6, 6)
    # v1.8.0's Button_KetQua is a piece of the "第N回合" round label: it marks the battle screen
    assert ctx.find_object(cm.OBJECTS, "result").found
    assert not ctx.find_object(cm.OBJECTS, "dungeon_bar").found
    assert not ctx.find_object(cm.OBJECTS, "lose_zh").found


def test_live_field_level_up_notice_and_flags() -> None:
    """Mạnh Hoạch, HUD collapsed: the level-up '+' and the new-dungeon notice ✕ (v1.8.0 images
    ChuaCongLenCap / ThongBaoPhoBanMoi match 1.00 live) and two red flags on a jungle map."""
    ctx, drv = static_ctx(FIXTURES / "live-field-manh-hoach-levelup-notice.png")
    assert ctx.detect_scene().name == "field"
    task = ClearMap(params())
    task._dismiss_popups(ctx, check_notice=True)
    fix = ctx.last_canvas
    assert fix is not None
    clicks = [(fix.to_canvas(e.point), e.double) for e in drv.events if e.kind == "click" and e.point]
    keys = [e.vk for e in drv.events if e.kind == "key"]
    assert _near(clicks[0][0], (493, 500), 3, 3)  # level-up "+"
    assert _near(clicks[1][0], (5, 1), 1, 1) and clicks[1][1]  # focus double click, then ESC x2
    assert keys == [VK_ESCAPE] * cm.ESC_PRESSES
    assert _near(clicks[2][0], (985, 314), 3, 3)  # notice ✕
    enemy = task._find_enemy(ctx, hud_hidden=True)
    # two red flags, (390, 203) and (338, 298): only the one under the beam (396, 166) is the target
    assert enemy is not None and _near(enemy.as_tuple(), (390 + 7, 203 + 4 + 10), 2, 2)
    assert task._seen(ctx, "hud_toggle") is None and task._seen(ctx, "next_dungeon") is None


# --- runs ---------------------------------------------------------------------------------------
def test_clears_the_map_then_stops_on_the_last_dungeon() -> None:
    sim = FieldSim(outcomes=["win"])
    ctx, _ = make_ctx(sim)
    progress: list[tuple[int, int | None]] = []
    ctx._on_progress = lambda d, t: progress.append((d, t))
    msg = ClearMap(params()).run(ctx)
    assert "Đã hết đạo quân" in msg and "2 trận (thắng 2" in msg
    assert not sim.alive and sim.attacks == 2
    field = sim.clicks("field")
    # v1.8.0 order: hide the HUD icon rows, then the quest tracker, then the armies
    assert _near(field[0], (831, 79), 3, 3) and _near(field[1], (817, 178), 3, 3)
    army_clicks = [p for p in field if any(_near(p, _flag_click(f), 6, 6) for f in (FLAG_A, FLAG_B))]
    assert len(army_clicks) == 2
    assert [p for p in sim.clicks("army") if _near(p, (584, 304), 28, 11)]
    assert len(sim.clicks("result")) == 2 and all(_near(p, (538, 409), 2, 2) for p in sim.clicks("result"))
    assert progress == [(1, None), (2, None)]


def test_opens_the_field_from_the_city() -> None:
    sim = FieldSim(start="city")
    ctx, _ = make_ctx(sim)
    msg = ClearMap(params(max_battles=1, hide_hud=False)).run(ctx)
    assert "Đã đủ 1 trận" in msg
    assert _near(sim.clicks("city")[0], (899, 549), 2, 2)
    assert sim.hud_expanded  # hide_hud off: the HUD is left alone


def test_refuses_to_start_elsewhere_without_clicking() -> None:
    class TowerScreen(FieldSim):
        def _frame(self) -> np.ndarray:
            return _img("26-tower-dialog.png").copy()

    sim = TowerScreen()
    ctx, drv = make_ctx(sim)
    res = execute(ClearMap(params()), ctx)
    assert res.status is RunStatus.FAILED and "Phó Bản" in res.message
    assert drv.events == []


def test_next_dungeon_then_clear() -> None:
    sim = FieldSim(map_="cong_ton", outcomes=["silent"])
    ctx, _ = make_ctx(sim)
    msg = ClearMap(params(hide_hud=False)).run(ctx)
    assert sim.map == "luu" and not sim.alive
    assert "sang 1 phó bản" in msg and "không rõ 2" in msg  # no result box: outcome unknown
    assert any(_near(p, (579, 578), 3, 3) for p in sim.clicks("field"))


def test_first_miss_after_switch_ends_the_run() -> None:
    sim = FieldSim(map_="cong_ton", flags=())  # the next dungeon has no army either
    ctx, _ = make_ctx(sim)
    msg = ClearMap(params(hide_hud=False)).run(ctx)
    assert msg.startswith("Phó bản mới không có đạo quân")
    assert sim.attacks == 0


def test_max_maps_zero_means_unlimited_but_next_map_can_be_disabled() -> None:
    sim = FieldSim(map_="cong_ton")
    ctx, _ = make_ctx(sim)
    msg = ClearMap(params(hide_hud=False, next_map=False)).run(ctx)
    assert msg.startswith("Đã hết đạo quân") and sim.map == "cong_ton"
    assert VK_ESCAPE not in sim.keys  # max_misses=1: no ESC round before giving up


def test_misses_press_escape_with_focus_click() -> None:
    sim = FieldSim(map_="cong_ton")
    ctx, _ = make_ctx(sim)
    ClearMap(params(hide_hud=False, next_map=False, max_misses=3)).run(ctx)
    assert sim.keys.count(VK_ESCAPE) == 2 * cm.ESC_PRESSES  # after misses 1 and 2
    focus = [d for s, p, d in sim.log if _near(p, (5, 1), 1, 1)]
    assert focus == [True, True]  # double click (5, 1) before each ESC round


def test_recruits_then_attacks_again() -> None:
    sim = FieldSim(outcomes=["recruit", "win", "win"])
    ctx, _ = make_ctx(sim)
    msg = ClearMap(params(hide_hud=False)).run(ctx)
    assert "chiêu binh 1 lần" in msg and "thắng 2" in msg
    rec = sim.clicks("recruit")
    corner = RECRUIT_AT
    assert _near(rec[0], (corner[0] + 100, corner[1] + 3), 1, 1)  # "Tối đa" (v1.8.0 offset)
    assert _near(rec[1], (corner[0] + 215, corner[1] + 30), 1, 1)  # confirm
    assert _near(rec[2], (803, 120), 1, 1)  # ✕


def test_too_many_recruits_stops_with_v180_message() -> None:
    sim = FieldSim(outcomes=["recruit"])
    ctx, _ = make_ctx(sim)
    res = execute(ClearMap(params(hide_hud=False, max_recruits=2)), ctx)
    assert res.status is RunStatus.FAILED and cm.RECRUIT_FAILED in res.message
    assert sim.attacks == 3  # first attack + one per recruit


def test_legacy_ketqua_popup_is_closed_like_v180() -> None:
    sim = FieldSim(outcomes=["ketqua"], flags=(FLAG_A,))
    ctx, _ = make_ctx(sim)
    msg = ClearMap(params(hide_hud=False)).run(ctx)
    clicks = sim.clicks("ketqua")
    assert _near(clicks[0], (KETQUA_AT[0] + 335, KETQUA_AT[1] + 480), 1, 1)  # Button_KetQua +(335, 480)
    assert _near(clicks[1], (538, 409), 1, 1)
    assert "1 trận" in msg


def test_battle_wait_is_bounded() -> None:
    sim = FieldSim(outcomes=["hang"])
    ctx, _ = make_ctx(sim)
    t0 = time.monotonic()
    res = execute(ClearMap(params(hide_hud=False, battle_timeout_s=1.0)), ctx)
    assert res.status is RunStatus.FAILED and "không thấy kết quả trận" in res.message
    assert time.monotonic() - t0 < 15


def test_stop_during_battle_wait() -> None:
    sim = FieldSim(outcomes=["hang"])
    control = RunControl()
    ctx, _ = make_ctx(sim, control)
    threading.Timer(1.5, control.stop).start()
    t0 = time.monotonic()
    res = execute(ClearMap(params(hide_hud=False, battle_timeout_s=600.0)), ctx)
    assert res.status is RunStatus.STOPPED
    assert time.monotonic() - t0 < 10


def test_attack_that_never_starts_is_bounded() -> None:
    sim = FieldSim(outcomes=["ignore"])
    ctx, _ = make_ctx(sim)
    res = execute(ClearMap(params(hide_hud=False)), ctx)
    assert res.status is RunStatus.FAILED and "Tấn công" in res.message
    assert sim.attacks == cm.MAX_ATTACK_CLICKS


def test_stops_after_losses_in_a_row(tmp_path: Path) -> None:
    sim = FieldSim(outcomes=["lose"])
    ctx, _ = make_ctx(sim)
    msg = ClearMap(params(hide_hud=False, max_losses=2)).run(ctx)
    assert msg.startswith("Dừng vì thua 2 trận liên tiếp")
    assert sim.attacks == 2 and len(sim.alive) == 2


def test_an_army_that_never_falls_is_given_up() -> None:
    sim = FieldSim(outcomes=["silent_alive"], flags=(FLAG_A,))
    ctx, _ = make_ctx(sim)
    res = execute(ClearMap(params(hide_hud=False)), ctx)
    assert res.status is RunStatus.FAILED and "cùng một đạo quân" in res.message
    assert sim.attacks == cm.SAME_TARGET_LIMIT


def test_attacks_only_the_army_under_the_beam() -> None:
    """Both armies carry a red flag; the beam over B makes B the target, then A once B is gone."""
    sim = FieldSim(marked=FLAG_B, outcomes=["win"])
    ctx, _ = make_ctx(sim)
    msg = ClearMap(params(hide_hud=False, max_battles=1)).run(ctx)
    assert msg.startswith("Đã đủ 1 trận") and sim.alive == {FLAG_A}
    army_clicks = [p for p in sim.clicks("field") if any(_near(p, _flag_click(f), 6, 6) for f in (FLAG_A, FLAG_B))]
    assert len(army_clicks) == 1 and _near(army_clicks[0], _flag_click(FLAG_B), 2, 2)


def test_red_flags_without_a_beam_are_not_attacked() -> None:
    sim = FieldSim(beam=False)
    ctx, _ = make_ctx(sim)
    msg = ClearMap(params(hide_hud=False, next_map=False)).run(ctx)
    assert msg.startswith("Đã hết đạo quân") and sim.attacks == 0 and len(sim.alive) == 2


def test_beam_without_a_flag_clicks_below_its_tip() -> None:
    class NoFlag(FieldSim):  # the beam over A, but A's flag hidden (e.g. covered by an effect)
        def _frame(self) -> np.ndarray:
            img = super()._frame()
            if self.state == "field":
                _fill(img, Rect(FLAG_A[0] - 2, FLAG_A[1] - 2, FLAG_SIZE[0] + 4, FLAG_SIZE[1] + 4), (70, 130, 120))
            return img

    sim = NoFlag(flags=(FLAG_A,))
    ctx, _ = make_ctx(sim)
    target = ClearMap(params(hide_hud=False))._find_enemy(ctx, hud_hidden=False)
    assert target is not None
    tip = (BEAM.x + BEAM.w // 2, BEAM.bottom)
    assert _near(target.as_tuple(), (tip[0], tip[1] + cm.BEAM_ARMY_DY), 3, 3)
    assert _near(target.as_tuple(), _flag_click(FLAG_A), 6, 6)  # still lands on the army


def test_beams_found_on_live_captures_only_once() -> None:
    for name, tip in (("04-pho-ban.png", (406, 353)), ("06-field-hud-collapsed.png", (407, 355))):
        beams = cm.find_beams(_img(name), Rect(O[0], O[1], 1000, 600))
        assert len(beams) == 1, name
        b = beams[0]
        assert _near((b.x + b.w // 2 - O[0], b.bottom - O[1]), tip, 2, 2), name
    for name in ("08-dungeon-map-cong-ton.png", "07-dungeon-switch-banner.png", "01-city-main.png"):
        assert cm.find_beams(_img(name), Rect(O[0], O[1], 1000, 600)) == [], name


def test_max_battles() -> None:
    sim = FieldSim()
    ctx, _ = make_ctx(sim)
    msg = ClearMap(params(hide_hud=False, max_battles=1)).run(ctx)
    assert msg.startswith("Đã đủ 1 trận") and sim.attacks == 1


# --- parameters ---------------------------------------------------------------------------------
def test_import_legacy() -> None:
    assert ClearMap.import_legacy({"CPB_INPUT_MS": "200", "CPB_INPUT_S": "3"}) == {"delay_ms": 200, "wait_s": 3.0}
    assert ClearMap.import_legacy({"CPB_INPUT_MS": "", "CPB_INPUT_S": "abc"}) == {}
    assert ClearMap.import_legacy({"CPB_INPUT_MS": "-1", "CPB_INPUT_S": "0"}) == {}
    assert ClearMap.import_legacy({}) == {}


def test_params_are_validated() -> None:
    with pytest.raises(ValueError):
        ClearMapParams(wait_s=0)
    with pytest.raises(ValueError):
        ClearMapParams(battle_timeout_s=1)
    p = ClearMapParams()
    assert (p.delay_ms, p.wait_s, p.max_misses, p.max_recruits) == (200, 3.0, 4, 5)
