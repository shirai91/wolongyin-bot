"""P2 Tri Kỷ (TRIKY): portrait search in Tửu Quán, talk, refresh; attempts = searches (#17).

Offline: the live Tửu Quán screenshot (``17-tuu-quan-tri-ky.png``) with legacy portraits pasted on
the visitor cards, and the talk result box (``18-tuu-quan-visitor.png``). The simulator moves
between them on the clicks the fake input driver records: a click on a visitor card talks to it
(as on ngoalongviet.com, docs/07), 确定 closes the result box, a refresh button brings the next
scripted set of visitors.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest

from conftest import PROFILE, ROOT
from ngoalong.core.capture import read_image
from ngoalong.core.coords import CoordMap
from ngoalong.core.drivers.fake import InputEvent, RecordingInputDriver
from ngoalong.core.drivers.file import FileCapturer
from ngoalong.core.geometry import Rect
from ngoalong.core.locator import CanvasLocator
from ngoalong.core.scenes import SceneDetector
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher
from ngoalong.debug.sink import PngSink
from ngoalong.engine import registry
from ngoalong.engine.context import RunContext
from ngoalong.engine.control import RunControl
from ngoalong.engine.runner import RunStatus, execute
from ngoalong.features import tri_ky as tk
from ngoalong.features.tri_ky import RefreshButton, TriKy, TriKyCam, TriKyParams, TriKyTim

SHOTS = ROOT / "docs" / "game screenshots"
LIVE = SHOTS / "live"
LIVE_ORIGIN = (180, 110)
STORE = TemplateStore(PROFILE / "templates")
COORDS = CoordMap.load(PROFILE / "coords")
SCENES = SceneDetector.load(PROFILE / "scenes", templates_dir=PROFILE / "templates")
LOCATOR = CanvasLocator.from_profile(PROFILE)

CARD_AT = {1: (48, 68), 2: (182, 68)}  # where a portrait is pasted on visitor card 1 / 2 (canvas)
CARD_BOX = {1: (40, 52, 105, 125), 2: (172, 52, 105, 125)}  # card rectangles (x, y, w, h)
REFRESH_AT = {"other_city": (443, 563), "invite_wine": (585, 563), "invite_fast": (728, 563)}
OK_AT = (680, 355)

Visitors = tuple[tuple[int, str], ...]  # (card, template name)


@lru_cache(maxsize=None)
def _img(path: Path) -> np.ndarray:
    return read_image(path)


@lru_cache(maxsize=None)
def tavern_frame(visitors: Visitors) -> np.ndarray:
    out = _img(LIVE / "17-tuu-quan-tri-ky.png").copy()
    for card, name in visitors:
        patch = STORE.get(name).image
        x, y = LIVE_ORIGIN[0] + CARD_AT[card][0], LIVE_ORIGIN[1] + CARD_AT[card][1]
        out[y:y + patch.shape[0], x:x + patch.shape[1]] = patch
    return out


def _in(p: tuple[float, float], box: tuple[int, int, int, int]) -> bool:
    x, y, w, h = box
    return x <= p[0] < x + w and y <= p[1] < y + h


def _near(p: tuple[float, float], c: tuple[int, int], d: int = 3) -> bool:
    return abs(p[0] - c[0]) <= d and abs(p[1] - c[1]) <= d


class TavernSim:
    """``script[i]`` = visitors after ``i`` refreshes (the last set stays)."""

    def __init__(self, script: list[Visitors] | None = None, start: str = "tavern",
                 talk_opens: bool = True, visitor_leaves: bool = True) -> None:
        self.script = list(script or [()])
        self.index = 0
        self.visitors: list[tuple[int, str]] = list(self.script[0])
        self.state = start
        self.talk_opens = talk_opens
        self.visitor_leaves = visitor_leaves
        self.talked: list[str] = []
        self.refreshes: list[str] = []
        self.ctx: RunContext | None = None
        self.log: list[tuple[str, tuple[float, float]]] = []
        self._talk = FileCapturer([_img(LIVE / "18-tuu-quan-visitor.png")], loop=True)
        self._city = FileCapturer([_img(LIVE / "01-city-main.png")], loop=True)

    def _frame(self) -> np.ndarray:
        if self.state == "talk":
            return self._talk.grab()
        if self.state == "city":
            return self._city.grab()
        return FileCapturer([tavern_frame(tuple(self.visitors))]).grab()

    def grab(self) -> np.ndarray:
        return self._frame()

    def client_size(self) -> tuple[int, int]:
        f = self._frame()
        return (int(f.shape[1]), int(f.shape[0]))

    def on_event(self, ev: InputEvent) -> None:
        assert ev.kind == "click" and ev.point is not None
        assert self.ctx is not None and self.ctx.last_canvas is not None
        p = self.ctx.last_canvas.to_canvas(ev.point)
        self.log.append((self.state, p))
        if self.state == "talk" and _near(p, OK_AT):
            self.state = "tavern"
            return
        if self.state != "tavern":
            return
        for card, name in list(self.visitors):
            if _in(p, CARD_BOX[card]):
                if self.talk_opens:
                    self.talked.append(name)
                    self.state = "talk"
                    if self.visitor_leaves:
                        self.visitors.remove((card, name))
                return
        for obj, at in REFRESH_AT.items():
            if _near(p, at):
                self.refreshes.append(obj)
                self.index = min(self.index + 1, len(self.script) - 1)
                self.visitors = list(self.script[self.index])


def make_ctx(sim: TavernSim, png_dir: Path | None = None) -> RunContext:
    ctx = RunContext(
        capturer=sim, input_driver=RecordingInputDriver(on_event=sim.on_event), coords=COORDS,
        matcher=Matcher(STORE, history=64), canvas_source=CanvasLocator.from_profile(PROFILE),
        scenes=SCENES, control=RunControl(), png_sink=PngSink(png_dir) if png_dir is not None else None,
        poll_interval_s=0.02,
    )
    sim.ctx = ctx
    return ctx


def params(**kw) -> TriKyParams:
    base = dict(delay_ms=0, search_delay_ms=0, scene_timeout_s=2.0, attempts=3)
    base.update(kw)
    return TriKyParams(**base)


T3 = TriKyTim.NGO_QUOC_THAI.template  # "tri_ky.tim.3"
T0 = TriKyTim.HOA_MAN.template
C8 = TriKyCam.DIEU_THUYEN.template


# --- runs ---------------------------------------------------------------------------------------
def test_attempts_count_searches_not_talks() -> None:
    """#17: with no selected companion on screen v1.8.0 refreshed forever; here it stops."""
    sim = TavernSim([()])
    ctx = make_ctx(sim)
    progress: list[tuple[int, int | None]] = []
    ctx._on_progress = lambda d, t: progress.append((d, t))
    r = execute(TriKy(params(purple=[TriKyTim.NGO_QUOC_THAI], attempts=4)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sim.refreshes == ["invite_wine"] * 3  # between the 4 searches, not after the last
    assert progress == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert "Đã tìm 4 lần" in r.message and "0 tri kỷ" in r.message


def test_talks_to_a_selected_companion() -> None:
    sim = TavernSim([(), ((1, T3),), ()])
    ctx = make_ctx(sim)
    r = execute(TriKy(params(purple=[TriKyTim.NGO_QUOC_THAI], attempts=3)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sim.talked == [T3]
    states = [s for s, _p in sim.log]
    assert states == ["tavern", "tavern", "talk", "tavern"]  # refresh, portrait, 确定, refresh
    portrait_click = sim.log[1][1]
    assert _in(portrait_click, CARD_BOX[1])
    assert _near(sim.log[2][1], OK_AT, 1)
    assert "1 tri kỷ: Ngô Quốc Thái" in r.message


def test_two_companions_at_once_and_orange_refresh() -> None:
    sim = TavernSim([((1, C8), (2, T0)), ()])
    ctx = make_ctx(sim)
    r = execute(TriKy(params(purple=[TriKyTim.HOA_MAN], orange=[TriKyCam.DIEU_THUYEN], attempts=2)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sorted(sim.talked) == sorted([C8, T0])
    assert sim.refreshes == ["invite_fast"]  # an orange companion is selected (v1.8.0 rule)


def test_unselected_portrait_is_ignored() -> None:
    sim = TavernSim([((1, C8),)])
    ctx = make_ctx(sim)
    r = execute(TriKy(params(purple=[TriKyTim.HOA_MAN], attempts=2)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sim.talked == []
    assert [s for s, _p in sim.log] == ["tavern"]  # the refresh only


def test_a_visitor_who_stays_is_talked_to_once_per_search() -> None:
    sim = TavernSim([((1, T3),)], visitor_leaves=False)
    ctx = make_ctx(sim)
    r = execute(TriKy(params(purple=[TriKyTim.NGO_QUOC_THAI], attempts=2)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sim.talked == [T3, T3]  # once in each of the 2 searches


def test_talk_limit() -> None:
    sim = TavernSim([((1, C8), (2, T0))])
    ctx = make_ctx(sim)
    r = execute(TriKy(params(purple=[TriKyTim.HOA_MAN], orange=[TriKyCam.DIEU_THUYEN], attempts=5,
                             max_talks=1)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert len(sim.talked) == 1 and sim.refreshes == []
    assert "Đã tìm 1 lần" in r.message


@pytest.mark.parametrize("button,obj", [
    (RefreshButton.OTHER_CITY, "other_city"),
    (RefreshButton.INVITE_WINE, "invite_wine"),
    (RefreshButton.INVITE_FAST, "invite_fast"),
])
def test_refresh_button_choice(button: RefreshButton, obj: str) -> None:
    sim = TavernSim([()])
    ctx = make_ctx(sim)
    r = execute(TriKy(params(orange=[TriKyCam.CHAN_CO], attempts=2, refresh=button)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sim.refreshes == [obj]


def test_no_selection_fails_without_clicking() -> None:
    sim = TavernSim()
    ctx = make_ctx(sim)
    r = execute(TriKy(params()), ctx)
    assert r.status is RunStatus.FAILED and "Chưa chọn" in r.message
    assert sim.log == []


def test_refuses_another_screen(tmp_path: Path) -> None:
    sim = TavernSim(start="city")
    ctx = make_ctx(sim, png_dir=tmp_path)
    r = execute(TriKy(params(purple=[TriKyTim.HOA_MAN], scene_timeout_s=0.5)), ctx)
    assert r.status is RunStatus.FAILED and "tavern" in r.message
    assert sim.log == []


def test_no_talk_result_stops_with_a_screenshot(tmp_path: Path) -> None:
    sim = TavernSim([((1, T3),)], talk_opens=False)
    ctx = make_ctx(sim, png_dir=tmp_path)
    r = execute(TriKy(params(purple=[TriKyTim.NGO_QUOC_THAI], scene_timeout_s=0.5)), ctx)
    assert r.status is RunStatus.FAILED and "hội đàm" in r.message
    assert r.screenshot is not None and r.screenshot.is_file()
    assert len(sim.log) == 1


def test_stop_during_a_long_search_delay() -> None:
    import threading
    import time

    sim = TavernSim([()])
    ctx = make_ctx(sim)
    task = TriKy(params(purple=[TriKyTim.HOA_MAN], attempts=1000, search_delay_ms=60_000))
    threading.Timer(0.5, ctx.control.stop).start()
    t0 = time.monotonic()
    r = execute(task, ctx)
    assert r.status is RunStatus.STOPPED
    assert time.monotonic() - t0 < 3


# --- templates ----------------------------------------------------------------------------------
ALL = [*TriKyTim, *TriKyCam]


def _all_screens() -> list[Path]:
    return sorted(SHOTS.glob("*.png")) + sorted(LIVE.glob("*.png"))


def test_every_portrait_has_a_template() -> None:
    for p in ALL:
        t = STORE.get(p.template)
        assert t.size[0] <= 105 and t.size[1] <= tk.VISITOR_AREA.h  # fits a card and the search band


def test_portraits_never_match_stored_screens() -> None:
    m = Matcher(STORE)
    for path in _all_screens():
        img = _img(path)
        fix = LOCATOR.locate(img)
        roi = fix.rect_to_frame(tk.VISITOR_AREA)  # where the feature searches
        for p in ALL:
            d = m.search(p.template, roi, tk.DEFAULT_THRESHOLD, frame=img, scale=fix.scale)
            assert not d.found, f"{p.template} on {path.name}: {d.score:.2f}"


def test_each_portrait_is_told_apart_from_the_others() -> None:
    """On a card, only its own portrait passes the threshold (the lists are searched together)."""
    m = Matcher(STORE)
    for p in ALL:
        img = tavern_frame(((1, p.template),))
        fix = LOCATOR.locate(img)
        roi = fix.rect_to_frame(Rect(0, 40, 300, 160))  # cards 1-2 of the visitor band
        found = [q for q in ALL if m.search(q.template, roi, tk.DEFAULT_THRESHOLD, frame=img).found]
        assert found == [p], f"{p.template}: {[q.template for q in found]}"


# --- params, form, registry ---------------------------------------------------------------------
def test_names_and_groups_follow_the_v180_gui() -> None:
    assert len(TriKyTim) == 19 and len(TriKyCam) == 21
    assert TriKyTim(0).label == "Hoa Man" and TriKyTim(18).label == "Phàn Thị"
    assert TriKyTim(11).group == "Vật phòng" and TriKyTim(16).group == "Chiến phòng"
    assert TriKyCam(8).label == "Điêu Thuyền" and TriKyCam(20).label == "Phan Kim Liên"
    assert TriKyCam(17).group == "Mod"
    assert TriKyTim(3).template == "tri_ky.tim.3" and TriKyCam(3).template == "tri_ky.cam.3"
    assert len({p.label for p in ALL}) == 40


def test_params() -> None:
    p = TriKyParams(purple=[5, 1, 5], orange=[TriKyCam.TIEU_KIEU])
    assert p.purple == [TriKyTim.QUACH_THI, TriKyTim.TAO_TIET]
    assert p.refresh_object() == "invite_fast"
    assert TriKyParams(purple=[1]).refresh_object() == "invite_wine"
    assert TriKyParams().targets() == []
    for bad in ({"attempts": 0}, {"purple": [19]}, {"orange": [21]}, {"match_threshold": 0.3}):
        with pytest.raises(ValueError):
            TriKyParams(**bad)
    assert TriKy.import_legacy({"TK_INPUT_SOLAN": "5"}) == {}
    dumped = p.model_dump(mode="json")
    assert dumped["purple"] == [1, 5] and TriKyParams.model_validate(dumped) == p


def test_v180_offsets_are_kept_as_a_record() -> None:
    """docs/07: the tavern layout changed; the v1.8.0 offsets stay in coords/tri_ky.yaml unused."""
    assert COORDS.offset("tri_ky.refresh_purple") == (570, 475)
    assert COORDS.offset("tri_ky.refresh_orange") == (670, 475)
    tavern = SCENES.get("tavern")
    for obj in REFRESH_AT:
        assert tuple(tavern.objects[obj].offset) == REFRESH_AT[obj]


def test_registered() -> None:
    ids = {c.id for c in registry.discover()}
    assert "tri_ky" in ids and registry.get_feature("tri_ky") is TriKy
    assert TriKy.start_scene == "tavern"


def test_check_list_form(qapp) -> None:
    from ngoalong.ui.forms import ParamsForm
    from ngoalong.ui.widgets.check_list import CheckListEditor

    form = ParamsForm(TriKyParams, TriKyParams(purple=[2]))
    ed = form.editors["purple"]
    assert isinstance(ed, CheckListEditor) and isinstance(form.editors["orange"], CheckListEditor)
    assert len(ed.boxes) == 19 and ed.get() == [2]
    changes: list[int] = []
    form.changed.connect(lambda: changes.append(1))
    ed.boxes[TriKyTim.TAO_HOA].setChecked(True)
    assert form.value().purple == [TriKyTim.TU_THI, TriKyTim.TAO_HOA] and changes
    ed.all_box.setChecked(True)
    assert form.value().purple == list(TriKyTim)
    ed.boxes[TriKyTim.HOA_MAN].setChecked(False)
    assert not ed.all_box.isChecked() and len(form.value().purple) == 18
    ed.all_box.setChecked(True)
    ed.all_box.setChecked(False)
    assert form.value().purple == []
    form.set_value(TriKyParams(orange=list(TriKyCam)))
    assert form.editors["orange"].all_box.isChecked()
    assert ed.boxes[TriKyTim.HOA_MAN].text() == "Hoa Man"


def test_check_list_rejects_non_enum_lists() -> None:
    from ngoalong.ui.widgets.check_list import enum_of

    assert enum_of(list[TriKyCam]) is TriKyCam
    with pytest.raises(TypeError):
        enum_of(list[int])
