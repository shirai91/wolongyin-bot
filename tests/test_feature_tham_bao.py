"""P2 Thám Bảo (v1.8.0 THAMBAO), offline.

The Thám Bảo screen was not reached on ngoalongviet.com (docs/07), so the simulator draws synthetic
canvas frames (1000x600 noise) with the v1.8.0 ThamBao images pasted where a state shows them, and
moves on when the fake input driver records a click on a v1.8.0 offset. Click order is compared with
a transliteration of ``THAMBAO`` (v1.8.0 ``app_logic_only.au3``).
"""

from __future__ import annotations

import threading
import time
from functools import lru_cache

import numpy as np
import pytest

from conftest import PROFILE, ROOT
from ngoalong.core.capture import read_image
from ngoalong.core.coords import CoordMap
from ngoalong.core.drivers.fake import InputEvent, RecordingInputDriver
from ngoalong.core.scenes import SceneDetector
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher
from ngoalong.engine import registry
from ngoalong.engine.context import FixedCanvas, RunContext
from ngoalong.engine.control import RunControl
from ngoalong.engine.runner import RunStatus, execute
from ngoalong.features import tham_bao as tb
from ngoalong.features.tham_bao import ThamBao, ThamBaoParams

STORE = TemplateStore(PROFILE / "templates")
COORDS = CoordMap.load(PROFILE / "coords")
SCENES = SceneDetector.load(PROFILE / "scenes", templates_dir=PROFILE / "templates")

AT = {  # canvas top-left of each marker in the synthetic frames
    "tham_bao.sweep": (600, 420), "tham_bao.sweep_alt": (590, 418), "tham_bao.opponent": (520, 280),
    "tham_bao.result": (470, 200), "tham_bao.lost": (300, 330), "tham_bao.claimed": (650, 200),
    "tower.result.win_zh": (467, 180),  # 战胜 on the live result box
    "tham_bao.reward_x2": (700, 150),  # x2 reward bag (TB_QuaX2, 50x54)
}
SWEEP_C = (609, 424)  # centre of tham_bao.sweep (18x8) at (600, 420)
SWEEP_ALT_C = (609, 424)  # centre of tham_bao.sweep_alt (38x13) at (590, 418)
BAG_C = (725, 177)  # centre of tham_bao.reward_x2 at (700, 150)

# v1.8.0 offsets (THAMBAO)
START, FIGHT, NO_OPP = (690, 345), (590, 380), (680, 380)
RES_CLOSE, RES_OK, LOST_OK = (822, 578), (538, 409), (440, 390)
STEP_A, STEP_B, CLAIMED_OK = (780, 510), (685, 440), (702, 486)


@lru_cache(maxsize=1)
def _background() -> np.ndarray:
    return np.random.default_rng(7).integers(0, 256, (600, 1000, 3), dtype=np.uint8)


def _near(p: tuple[int, int], c: tuple[int, int], d: int = 3) -> bool:
    return abs(p[0] - c[0]) <= d and abs(p[1] - c[1]) <= d


class ThamBaoSim:
    """States: board (sweep shown) -> start -> opponent | play -> ... -> claimed -> board."""

    def __init__(self, opponent: bool = True, result_shows: bool = True, alt: bool = False,
                 lost_after_steps: bool = False, start: str = "board", box_after_result: bool = False,
                 x2_offer: bool = False, x2_stuck: bool = False) -> None:
        self.state = start
        self.opponent = opponent
        self.result_shows = result_shows
        self.alt = alt
        self.lost_after_steps = lost_after_steps
        self.box_after_result = box_after_result  # the first "Thoát" is ignored: the box stays
        self.x2_offer = x2_offer  # after step 2: the x2 bag shows next to the "claimed" marker
        self.x2_stuck = x2_stuck  # after step 2: only the bag shows and clicking it does nothing
        self.x2_taken = 0
        self.swept = 0
        self.log: list[tuple[str, tuple[int, int]]] = []

    def grab(self) -> np.ndarray:
        img = _background().copy()
        shown = {
            "board": ["tham_bao.sweep_alt" if self.alt else "tham_bao.sweep"],
            "opponent": ["tham_bao.opponent"],
            "result": ["tham_bao.result"],
            "lost": ["tham_bao.lost"],
            "claimed": ["tham_bao.claimed"],
            "box": ["tower.result.win_zh"],
            "x2_offer": ["tham_bao.reward_x2", "tham_bao.claimed"],
            "x2_stuck": ["tham_bao.reward_x2"],
        }.get(self.state, [])
        for name in shown:
            t = STORE.get(name).image
            x, y = AT[name]
            img[y:y + t.shape[0], x:x + t.shape[1]] = t
        return img

    def client_size(self) -> tuple[int, int]:
        return (1000, 600)

    def on_event(self, ev: InputEvent) -> None:
        if ev.kind != "click" or ev.point is None:
            return
        p = (ev.point.x, ev.point.y)
        s = self.state
        self.log.append((s, p))
        if s == "board" and _near(p, SWEEP_C):
            self.swept += 1
        elif s == "board" and _near(p, START) and self.swept:
            self.swept = 0
            self.state = "opponent" if self.opponent else "play"
        elif s == "opponent" and _near(p, FIGHT):
            self.state = "result" if self.result_shows else "play"
        elif s == "opponent" and _near(p, STEP_B) and self.lost_after_steps:
            self.state = "lost"
        elif s == "result" and _near(p, RES_CLOSE):
            self.state = "result_closing"
        elif s == "result_closing" and _near(p, RES_OK):
            self.state = "box" if self.box_after_result else "play"
        elif s == "box" and _near(p, RES_OK):
            self.state = "play"
        elif s == "play" and _near(p, STEP_A):
            self.state = "play2"
        elif s == "play2" and _near(p, STEP_B):
            self.state = "x2_stuck" if self.x2_stuck else "x2_offer" if self.x2_offer else "claimed"
        elif s == "x2_offer" and _near(p, BAG_C):
            self.x2_taken += 1
            self.state = "claimed"
        elif s in ("claimed", "x2_offer") and _near(p, CLAIMED_OK):
            self.state = "board"
        elif s == "lost" and _near(p, LOST_OK):
            self.state = "board"

    @property
    def points(self) -> list[tuple[int, int]]:
        return [p for _s, p in self.log]


def make_ctx(sim: ThamBaoSim, control: RunControl | None = None) -> tuple[RunContext, RecordingInputDriver]:
    drv = RecordingInputDriver(on_event=sim.on_event)
    ctx = RunContext(
        capturer=sim, input_driver=drv, coords=COORDS, matcher=Matcher(STORE, history=64),
        canvas_source=FixedCanvas(), scenes=SCENES, control=control, poll_interval_s=0.02,
    )
    return ctx, drv


def params(**kw) -> ThamBaoParams:
    base = dict(delay_ms=0, max_rounds=1, scene_timeout_s=0.5)
    base.update(kw)
    return ThamBaoParams.model_construct(**{**ThamBaoParams().model_dump(), **base})


@pytest.fixture(autouse=True)
def fast_result_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tb, "RESULT_WAIT_S", 0.3)
    monkeypatch.setattr(tb, "RESULT_CLOSE_PAUSE_MS", 0)


# --- v1.8.0 transliteration ---------------------------------------------------------------------
def v180_round(fight: bool, opponent: bool, result: bool) -> list[tuple[int, int]]:
    """Clicks of one THAMBAO round on the happy path (one pass of the inner loop)."""
    out = [SWEEP_C, SWEEP_C, START]
    if fight:
        if opponent:
            out.append(FIGHT)
            if result:
                out += [RES_CLOSE, RES_OK]
        else:
            out.append(NO_OPP)
    out += [STEP_A, STEP_B, CLAIMED_OK]
    return out


# --- profile ------------------------------------------------------------------------------------
def test_registered_with_a_working_x2_option() -> None:
    assert registry.get_feature("tham_bao") is ThamBao
    f = ThamBaoParams.model_fields["reward_x2"]  # P3: #16's dead checkbox now does something
    assert f.default is False and f.title == "Nhận quà x2"
    for name, f in ThamBaoParams.model_fields.items():
        assert f.title, name
    assert ThamBao.import_legacy({"TB_INPUT_SLEEP": "100"}) == {}  # v1.8.0 did not save Thám Bảo inputs
    o = SCENES.get(tb.SCENE).objects[tb.REWARD_X2]
    assert o.template == "tham_bao.reward_x2" and STORE.get(o.template).size == (50, 54)  # TB_QuaX2


@lru_cache(maxsize=1)
def _stored_canvases() -> list[tuple[str, np.ndarray]]:
    o = (180, 110)
    live = ROOT / "docs" / "game screenshots" / "live"
    out = [(p.name, read_image(p)[o[1]:o[1] + 600, o[0]:o[0] + 1000]) for p in sorted(live.glob("*.png"))]
    ref = {"main-game.png": (273, 109), "dungeon.png": (272, 110), "worldmap-ui.png": (270, 107),
           "tower-ui.png": (272, 106), "tower-ui-enter-confirmation.png": (270, 110),
           "chest-rolling-ui.png": (271, 112), "tower-in-dungeon.png": (272, 110)}
    for n, (x, y) in ref.items():
        out.append((n, read_image(ROOT / "docs" / "game screenshots" / n)[y:y + 600, x:x + 1000]))
    return out


@pytest.mark.parametrize("obj", tb.MARKERS)
def test_legacy_markers_do_not_fire_on_stored_screens(obj: str) -> None:
    o = SCENES.get(tb.SCENE).objects[obj]
    assert o.template and o.threshold
    m = Matcher(STORE)
    for name, canvas in _stored_canvases():
        d = m.search(o.template, None, o.threshold, frame=np.ascontiguousarray(canvas))
        assert not d.found, f"{obj} fires on {name} ({d.score:.2f})"


def test_markers_are_found_on_synthetic_frames() -> None:
    sim = ThamBaoSim()
    ctx, _ = make_ctx(sim)
    for state, obj in (("board", "sweep"), ("opponent", "opponent"), ("result", "result"),
                       ("lost", "lost"), ("claimed", "claimed")):
        sim.state = state
        assert ctx.find_object(tb.SCENE, obj).found, obj
        assert not ctx.find_object(tb.SCENE, "claimed" if obj != "claimed" else "lost").found
    sim.state, sim.alt = "board", True
    assert ctx.find_object(tb.SCENE, "sweep_alt").found and not ctx.find_object(tb.SCENE, "sweep").found
    assert not ctx.find_object(tb.SCENE, tb.REWARD_X2).found
    sim.state = "x2_offer"
    bag = ctx.find_object(tb.SCENE, tb.REWARD_X2)
    assert bag.found and (bag.point.x, bag.point.y) == BAG_C


def test_live_battle_screen_markers() -> None:
    """Live capture of a battle result: TB_KetQua matches the "第N回合" round label, 战胜 the box."""
    from ngoalong.core.locator import CanvasLocator

    frame = read_image(ROOT / "tests" / "fixtures" / "screens" / "clear_map" / "live-battle-result-win.png")
    fix = CanvasLocator.from_profile(PROFILE).locate(frame[:, :, :3])
    assert fix.reliable
    found = {o: SCENES.find_object(frame[:, :, :3], fix, tb.SCENE, o).found
             for o in (*tb.MARKERS, *tb.RESULT_BOXES)}
    assert found == {"sweep": False, "sweep_alt": False, "opponent": False, "result": True,
                     "lost": False, "claimed": False, "reward_x2": False, "win_box": True, "lose_box": False}


# --- runs ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("opponent", [True, False])
def test_one_round_matches_v180(opponent: bool) -> None:
    sim = ThamBaoSim(opponent=opponent)
    ctx, _ = make_ctx(sim)
    msg = ThamBao(params(fight=True)).run(ctx)
    assert sim.points == v180_round(True, opponent, True)
    assert sim.state == "board"
    assert msg == f"Đã chạy 1 lượt Thám Bảo (đánh {1 if opponent else 0} trận)"


def test_x2_reward_bag_is_claimed_each_round() -> None:
    sim = ThamBaoSim(x2_offer=True)
    ctx, _ = make_ctx(sim)
    msg = ThamBao(params(reward_x2=True, max_rounds=2)).run(ctx)
    one = v180_round(True, True, True)
    one.insert(one.index(CLAIMED_OK), BAG_C)  # the bag after step 2, before the claimed check
    assert sim.points == one * 2
    assert sim.x2_taken == 2 and sim.state == "board"
    assert msg == "Đã chạy 2 lượt Thám Bảo (đánh 2 trận, nhận quà x2 2 lần)"


def test_run_history_counters() -> None:
    sim = ThamBaoSim(x2_offer=True)
    ctx, _ = make_ctx(sim)
    res = execute(ThamBao(params(reward_x2=True, max_rounds=2)), ctx)
    assert res.status is RunStatus.DONE, res.message
    assert res.counters["rounds"] == 2 and res.counters["battles"] == 2 and res.counters["x2"] == 2


def test_x2_off_never_clicks_the_bag() -> None:
    sim = ThamBaoSim(x2_offer=True)
    ctx, _ = make_ctx(sim)
    msg = ThamBao(params()).run(ctx)
    assert BAG_C not in sim.points and sim.x2_taken == 0
    assert sim.points == v180_round(True, True, True) and sim.state == "board"
    assert msg == "Đã chạy 1 lượt Thám Bảo (đánh 1 trận)"  # unchanged summary without the option


def test_x2_without_a_bag_plays_the_normal_round() -> None:
    sim = ThamBaoSim()
    ctx, _ = make_ctx(sim)
    msg = ThamBao(params(reward_x2=True)).run(ctx)
    assert sim.points == v180_round(True, True, True)
    assert msg.endswith("nhận quà x2 0 lần)")


def test_x2_bag_is_clicked_at_most_once_per_round() -> None:
    """A bag that stays on screen is not clicked again (no second x2 payment); the round is bounded."""
    sim = ThamBaoSim(x2_stuck=True, opponent=False)
    ctx, _ = make_ctx(sim)
    t0 = time.monotonic()
    res = execute(ThamBao(params(reward_x2=True, fight=False, round_timeout_s=0.5)), ctx)
    assert res.status is RunStatus.FAILED and "Càn quét" in res.message
    assert sim.points.count(BAG_C) == 1 and sim.points.count(STEP_B) > 1
    assert time.monotonic() - t0 < 10


def test_alternative_sweep_button() -> None:
    sim = ThamBaoSim(alt=True)
    ctx, _ = make_ctx(sim)
    ThamBao(params()).run(ctx)
    assert sim.points[:2] == [SWEEP_ALT_C, SWEEP_ALT_C]


def test_fight_off_ignores_opponents_and_handles_the_lost_popup() -> None:
    sim = ThamBaoSim(opponent=True, lost_after_steps=True)
    ctx, _ = make_ctx(sim)
    msg = ThamBao(params(fight=False)).run(ctx)
    pts = sim.points
    assert FIGHT not in pts and NO_OPP not in pts  # v1.8.0: neither click without "Chiến"
    assert pts[:5] == [SWEEP_C, SWEEP_C, START, STEP_A, STEP_B]
    assert pts[5] == LOST_OK and sim.state == "board"
    assert "đánh 0 trận" in msg


def test_a_result_box_left_open_is_closed_with_thoat() -> None:
    sim = ThamBaoSim(box_after_result=True)
    ctx, _ = make_ctx(sim)
    ThamBao(params()).run(ctx)
    pts = sim.points
    i = pts.index(RES_CLOSE)
    # v1.8.0's clicks, then step A/B (ignored while the box shows), then "Thoát" again next pass
    assert pts[i:i + 4] == [RES_CLOSE, RES_OK, STEP_A, STEP_B]
    assert pts[i + 4] == RES_OK and sim.state == "board"


def test_missing_fight_result_is_bounded() -> None:
    sim = ThamBaoSim(result_shows=False)
    ctx, _ = make_ctx(sim)
    ThamBao(params()).run(ctx)
    assert RES_CLOSE not in sim.points and sim.points[3] == FIGHT and sim.state == "board"


def test_several_rounds_with_progress() -> None:
    sim = ThamBaoSim()
    ctx, _ = make_ctx(sim)
    progress: list[tuple[int, int | None]] = []
    ctx._on_progress = lambda d, t: progress.append((d, t))
    msg = ThamBao(params(max_rounds=2)).run(ctx)
    assert msg == "Đã chạy 2 lượt Thám Bảo (đánh 2 trận)"
    assert progress == [(1, 2), (2, 2)]
    assert sim.points == v180_round(True, True, True) * 2


def test_refuses_another_screen_without_clicking() -> None:
    sim = ThamBaoSim(start="nothing")
    ctx, drv = make_ctx(sim)
    t0 = time.monotonic()
    res = execute(ThamBao(params()), ctx)
    assert res.status is RunStatus.FAILED and "Thám Bảo" in res.message
    assert drv.events == [] and time.monotonic() - t0 < 10


def test_starts_mid_round() -> None:
    sim = ThamBaoSim(start="claimed")
    ctx, _ = make_ctx(sim)
    ThamBao(params()).run(ctx)
    # no sweep button: v1.8.0 still clicked "start", then played the round on
    assert sim.points[0] == START and CLAIMED_OK in sim.points and sim.state == "board"


def test_round_is_bounded() -> None:
    sim = ThamBaoSim(start="opponent")  # the round never ends: no step leads anywhere
    sim.on_event = lambda ev: sim.log.append((sim.state, (ev.point.x, ev.point.y))) if ev.point else None
    ctx, _ = make_ctx(sim)
    t0 = time.monotonic()
    res = execute(ThamBao(params(fight=False, round_timeout_s=0.5)), ctx)
    assert res.status is RunStatus.FAILED and "Càn quét" in res.message
    assert time.monotonic() - t0 < 10


def test_stop_mid_round() -> None:
    sim = ThamBaoSim(start="opponent")
    sim.on_event = lambda ev: None
    control = RunControl()
    ctx, _ = make_ctx(sim, control)
    threading.Timer(0.8, control.stop).start()
    t0 = time.monotonic()
    res = execute(ThamBao(params(fight=False, max_rounds=0, round_timeout_s=600.0)), ctx)
    assert res.status is RunStatus.STOPPED and time.monotonic() - t0 < 10


def test_params_are_validated() -> None:
    with pytest.raises(ValueError):
        ThamBaoParams(round_timeout_s=1)
    with pytest.raises(ValueError):
        ThamBaoParams(max_rounds=-1)
    p = ThamBaoParams()
    assert p.fight and p.max_rounds == 0
