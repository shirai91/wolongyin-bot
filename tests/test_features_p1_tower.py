"""P1 tower features: Cày cấp chúa công (CAYCAPCHUACONG) and Tháp Thí Luyện (THAPTHILUYEN).

Offline: a small game simulator replays the tower screenshots (``docs/game screenshots``) through
``FileCapturer`` and switches screen when the fake input driver records a click on the right
button, so every ``expect_scene`` / template wait of the features runs against real frames.
The tower dialog is drawn from the live capture ``26c-tower-dialog-6-floors.png`` with the floor
buttons of the current page (only unlocked floors are shown, as in the game) and the page number,
so paging and the "floor not unlocked" checks run against realistic pixels.
Click positions are compared, in canvas coordinates, with a transliteration of the AutoIt
functions (v1.8.0 ``app_logic_only.au3``).
"""

from __future__ import annotations

import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from conftest import PROFILE, ROOT
from ngoalong.core.capture import read_image
from ngoalong.core.coords import CoordMap
from ngoalong.core.drivers.fake import InputEvent, RecordingInputDriver
from ngoalong.core.drivers.file import FileCapturer
from ngoalong.core.geometry import Point
from ngoalong.core.input import VK_BACK, VK_RETURN
from ngoalong.core.locator import CanvasLocator
from ngoalong.core.scenes import SceneDetector
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher
from ngoalong.debug.sink import PngSink
from ngoalong.engine import registry
from ngoalong.engine.context import RunContext
from ngoalong.engine.runner import RunHooks, RunStatus, TaskRunner, execute
from ngoalong.engine.popups import PopupGuard
from ngoalong.engine.task import Task
from ngoalong.features import _tower as tower
from ngoalong.features.chua_cong import ChuaCong, ChuaCongParams
from ngoalong.features.thap_thi_luyen import ThapThiLuyen, ThapThiLuyenParams

SHOTS = ROOT / "docs" / "game screenshots"
LIVE = SHOTS / "live"
STORE = TemplateStore(PROFILE / "templates")
COORDS = CoordMap.load(PROFILE / "coords")
SCENES = SceneDetector.load(PROFILE / "scenes", templates_dir=PROFILE / "templates")
LOCATOR = CanvasLocator.from_profile(PROFILE)

V180_OK = (455, 325)  # "Đồng ý" in v1.8.0 (TOADONUTDONGY)
V180_EXIT = (960, 565)  # "Thoát" (TOADOTHOATTHAP)
V180_RESULT_OK = (538, 407)  # THAPTHILUYEN win box OK
SOLDIER_AT = (336, 432)  # canvas centre of the TTL_Linh match on tower-in-dungeon.png


# --- frames -------------------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _img(path: Path) -> np.ndarray:
    return read_image(path)


def _paste(base: np.ndarray, origin: tuple[int, int], at: tuple[int, int], patch: np.ndarray) -> np.ndarray:
    out = base.copy()
    x, y = origin[0] + at[0], origin[1] + at[1]
    out[y:y + patch.shape[0], x:x + patch.shape[1]] = patch
    return out


@lru_cache(maxsize=None)
def frames() -> dict[str, np.ndarray]:
    battle = _img(SHOTS / "tower-in-dungeon.png")
    bo = (272, 108)  # canvas origin of tower-in-dungeon.png (template_crops.yaml)
    ok_btn = STORE.get("tower.confirm.ok").image
    return {
        "city": _img(LIVE / "01-city-main.png"),
        "panel": _img(LIVE / "25-tower-building-panel.png"),
        "dialog": _img(LIVE / "26c-tower-dialog-6-floors.png"),  # drawn per page by dialog_frame()
        "diem_tuong": _img(LIVE / "26d-tower-diem-tuong-tab.png"),  # same dialog, "Điểm tướng" tab
        "confirm": _img(SHOTS / "tower-ui-enter-confirmation.png"),
        "battle": battle,
        "win": _paste(battle, bo, (430, 250), STORE.get("tower.result.win").image),
        "win_zh": _paste(battle, bo, (470, 255), STORE.get("tower.result.win_zh").image),
        "lose": _paste(battle, bo, (445, 250), STORE.get("tower.result.lose").image),
        # a confirm box after "Thoát": the 确定 button over the battle screen (tower.exit_confirm)
        "exit_confirm": _paste(battle, bo, (420, 320), ok_btn),
        "field": _img(SHOTS / "dungeon.png"),
    }


DIALOG_ORIGIN = (180, 110)  # canvas origin of 26c-tower-dialog-6-floors.png (Tầng 1-6 shown)
SLOT_CX, SLOT_HALF_W, SLOT_HALF_H = 248, 52, 12


@lru_cache(maxsize=None)
def _dialog_parts() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    base = _img(LIVE / "26c-tower-dialog-6-floors.png")
    ox, oy = DIALOG_ORIGIN

    def cut(cy: int) -> np.ndarray:
        return base[oy + cy - SLOT_HALF_H:oy + cy + SLOT_HALF_H,
                    ox + SLOT_CX - SLOT_HALF_W:ox + SLOT_CX + SLOT_HALF_W].copy()

    return base, cut(243), cut(393)  # a floor button (Tầng 2), an empty slot (slot 8)


BOX_INNER = (221, 469, 54, 14)  # canvas x, y, w, h inside the page box frame
BOX_TEXT_CX = 247  # the live "1" is centred at canvas x 247 (columns 245..249)


@lru_cache(maxsize=None)
def _glyph(ch: str) -> np.ndarray:
    """BGR image of one page-box character: the live "1", other digits drawn in grey."""
    base = _img(LIVE / "26c-tower-dialog-6-floors.png")
    ox, oy = DIALOG_ORIGIN
    if ch == "1":
        return base[oy + 471:oy + 482, ox + 244:ox + 251].copy()  # the real anti-aliased "1"
    img = np.zeros((11, 8, 3), np.uint8)
    cv2.putText(img, ch, (0, 10), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)
    return img


def _draw_box_text(out: np.ndarray, text: str) -> None:
    ox, oy = DIALOG_ORIGIN
    x, y, w, h = BOX_INNER
    out[oy + y:oy + y + h, ox + x:ox + x + w] = 0  # the box interior is black
    glyphs = [_glyph(c) for c in text]
    total = sum(g.shape[1] for g in glyphs)
    left = ox + BOX_TEXT_CX - total // 2
    for g in glyphs:
        out[oy + 471:oy + 471 + g.shape[0], left:left + g.shape[1]] = g
        left += g.shape[1]


@lru_cache(maxsize=None)
def dialog_frame(page: int, unlocked: int, box_text: str | None = None) -> np.ndarray:
    """The tower dialog on ``page`` (0-based) with floors 1..``unlocked`` unlocked.

    ``box_text`` is what the page box shows while it is being edited (None: the page number).
    """
    base, button, empty = _dialog_parts()
    out = base.copy()
    ox, oy = DIALOG_ORIGIN
    for s in range(1, 11):
        cy = 218 + 25 * (s - 1)
        out[oy + cy - SLOT_HALF_H:oy + cy + SLOT_HALF_H,
            ox + SLOT_CX - SLOT_HALF_W:ox + SLOT_CX + SLOT_HALF_W] = button if page * 10 + s <= unlocked else empty
    _draw_box_text(out, str(page + 1) if box_text is None else box_text)
    return out


@lru_cache(maxsize=None)
def reward_frame() -> np.ndarray:
    """The tower battle with the live "Chúc mừng ngài được" reward modal on top (user report)."""
    from test_popups import reward_over

    return reward_over(frames()["battle"], (272, 108))


def _near(p: tuple[float, float], c: tuple[int, int], dx: int, dy: int) -> bool:
    return abs(p[0] - c[0]) <= dx and abs(p[1] - c[1]) <= dy


# --- simulator ----------------------------------------------------------------------------------
class TowerSim:
    """Capturer + click handler: shows one screenshot per game state, moves on matching clicks."""

    def __init__(
        self,
        start: str,
        results: list[str] | None = None,
        exit_confirm: bool = False,
        ok_to: str = "dialog",
        sweep_opens: bool = True,
        soldier_starts: bool = True,
        auto_result_grabs: int | None = None,
        exit_ignored: int = 0,
        win_frame: str = "win",
        unlocked: int = 999,
        popup_battles: int = 0,
        page: int = 0,
        keys_reach_box: bool = True,
        tab_switches: bool = True,
    ) -> None:
        self.state = start
        self.keys_reach_box = keys_reach_box  # False: typed keys never reach the page box
        self.tab_switches = tab_switches  # False: clicking the "Tháp" tab does nothing
        self.box_text: str | None = None  # page box text while editing (None: shows the page)
        self.box_selected = False
        self.popup_battles = popup_battles  # battles that open the "Chúc mừng ngài được" modal
        self.popup_shown = False
        self.popup_clicks = 0
        self.unlocked = unlocked  # highest unlocked floor; a win on it unlocks the next one
        self.page = page  # 0-based page of the floor list
        self.selected = 1  # the game keeps Tầng 1 selected until another button is clicked
        self.entered: list[int] = []  # floor entered by each confirmed "Vượt Tháp"
        self.caps = {k: FileCapturer([v], loop=True) for k, v in frames().items()}
        self.results = list(results or [])
        self.exit_confirm = exit_confirm
        self.ok_to = ok_to
        self.sweep_opens = sweep_opens
        self.soldier_starts = soldier_starts
        self.auto_result_grabs = auto_result_grabs
        self.exit_ignored = exit_ignored
        self.win_frame = win_frame
        self.battles = 0
        self._battle_grabs = 0
        self.ctx: RunContext | None = None
        self.log: list[tuple[str, tuple[float, float], bool]] = []  # (state, canvas point, double)
        self.other: list[InputEvent] = []

    # Capturer
    def grab(self) -> np.ndarray:
        if self.state == "battle" and self.auto_result_grabs is not None:
            self._battle_grabs += 1
            if self._battle_grabs > self.auto_result_grabs:
                self._to_result()
        if self.state == "dialog":
            return dialog_frame(self.page, self.unlocked, self.box_text)
        if self.state == "battle" and self.popup_shown:
            return reward_frame()
        return self.caps[self.state].grab()

    def client_size(self) -> tuple[int, int]:
        f = self.grab() if self.state == "dialog" else None
        if f is not None:
            return (int(f.shape[1]), int(f.shape[0]))
        return self.caps[self.state].client_size()

    @property
    def max_page(self) -> int:  # pages with at least one unlocked floor
        return (self.unlocked - 1) // 10 + 1

    def _box_key(self, ev: InputEvent) -> None:
        """The page box as a text field: Backspace / typing edit it, Enter jumps or rolls back."""
        if self.state != "dialog" or not self.keys_reach_box or self.box_text is None:
            return
        if ev.kind == "key" and ev.vk == VK_BACK:
            self.box_text = "" if self.box_selected else self.box_text[:-1]
            self.box_selected = False
        elif ev.kind == "text" and ev.text:
            self.box_text = ("" if self.box_selected else self.box_text) + ev.text
            self.box_selected = False
        elif ev.kind == "key" and ev.vk == VK_RETURN:
            n = int(self.box_text) if self.box_text.isdigit() else 0
            if 1 <= n <= self.max_page:
                self.page = n - 1
            self.box_text = None  # shows the (new, or unchanged = rolled back) page number

    def _to_result(self) -> None:
        outcome = self.results[self.battles] if self.battles < len(self.results) else "win"
        self.battles += 1
        self.state = self.win_frame if outcome == "win" else "lose"
        if outcome == "win" and self.entered and self.entered[-1] >= self.unlocked:
            self.unlocked = self.entered[-1] + 1

    def _go(self, state: str) -> None:
        self.state = state
        self._battle_grabs = 0
        if state == "battle" and self.popup_battles > 0:
            self.popup_battles -= 1
            self.popup_shown = True

    # InputDriver callback
    def on_event(self, ev: InputEvent) -> None:
        if ev.kind != "click" or ev.point is None:
            self.other.append(ev)
            self._box_key(ev)
            return
        assert self.ctx is not None and self.ctx.last_canvas is not None
        p = self.ctx.last_canvas.to_canvas(ev.point)
        s = self.state
        if self.popup_shown:  # the modal takes every click; only its 确定 closes it
            if _near(p, (495, 352), 12, 8):
                self.popup_shown = False
                self.popup_clicks += 1
            else:
                self.log.append(("popup", p, ev.double))
            return
        self.log.append((s, p, ev.double))
        if s == "city" and _near(p, (460, 320), 30, 30):
            self._go("panel")
        elif s == "panel" and _near(p, (521, 321), 30, 12):
            self._go("dialog")
        elif s == "dialog" and 331 <= p[0] <= 406 and 458 <= p[1] <= 480:  # "Vượt Tháp"
            if self.sweep_opens:
                self._go("confirm")
        elif s == "dialog" and _near(p, (290, 475), 8, 8):  # page next: only to a page with a floor
            if self.unlocked > (self.page + 1) * 10:
                self.page += 1
        elif s == "dialog" and _near(p, (208, 475), 8, 8):  # page back
            self.page = max(0, self.page - 1)
        elif s == "dialog" and _near(p, (255, 477), 22, 8):  # the page box
            if ev.double and self.keys_reach_box:
                self.box_text, self.box_selected = str(self.page + 1), True  # select all
        elif s == "diem_tuong" and _near(p, (222, 159), 20, 10):  # the "Tháp" tab
            if self.tab_switches:
                self._go("dialog")
        elif s == "dialog" and 200 <= p[0] <= 296 and 206 <= p[1] <= 455:  # a floor slot
            floor = self.page * 10 + round((p[1] - 215) / 25) + 1
            if floor <= self.unlocked:  # an empty slot does nothing
                self.selected = floor
        elif s == "confirm" and _near(p, (453, 331), 33, 11):
            self.entered.append(self.selected)
            self._go("battle")
        elif s == "confirm" and _near(p, (546, 331), 33, 11):
            self._go("dialog")
        elif s == "battle" and _near(p, (960, 563), 34, 10):
            if self.exit_ignored:
                self.exit_ignored -= 1
            else:
                self._go("exit_confirm" if self.exit_confirm else "dialog")
        elif s == "battle" and _near(p, SOLDIER_AT, 12, 12) and self.soldier_starts:
            self._to_result()
        elif s == "exit_confirm" and _near(p, (453, 331), 33, 11):
            self._go("dialog")
        elif s in ("win", "win_zh") and _near(p, (538, 408), 30, 12):
            self._go(self.ok_to)

    def clicks_in(self, state: str) -> list[tuple[float, float]]:
        return [p for s, p, _d in self.log if s == state]


POPUPS = PopupGuard.load(PROFILE / "popups.yaml")


def make_ctx(sim: TowerSim, control=None, png_dir: Path | None = None) -> tuple[RunContext, RecordingInputDriver]:
    drv = RecordingInputDriver(on_event=sim.on_event)
    matcher = Matcher(STORE, history=64)
    ctx = RunContext(
        capturer=sim,
        input_driver=drv,
        coords=COORDS,
        matcher=matcher,
        canvas_source=CanvasLocator.from_profile(PROFILE),
        scenes=SCENES,
        control=control,
        png_sink=PngSink(png_dir) if png_dir is not None else None,
        poll_interval_s=0.05,
        popups=PopupGuard(POPUPS.popups, settle_s=0.05),
    )
    sim.ctx = ctx
    return ctx, drv


# --- v1.8.0 transliteration ---------------------------------------------------------------------
# 10 floors per page, as v1.8.0: the live list has 10 slots and shows only unlocked floors (docs/07).
# The parity oracles run the v1.8.0 algorithm with the page size as a parameter.
LEGACY_PER_PAGE = 10
PER_PAGE = 10


def v180_paging(floor: int, per: int = LEGACY_PER_PAGE) -> tuple[int, int]:
    """TIENTHEM / VITRI of the AutoIt code; ``+ 4294967295`` read as the intended ``- 1``."""
    if floor <= per:
        return 0, floor
    flag = floor / per - int(floor / per)
    tienthem = int(floor / per) if flag > 0 else int(floor / per) - 1
    return tienthem, floor - tienthem * per


def v180_slot(vitri: int) -> tuple[int, int]:
    return (245, 215 + 25 * (vitri - 1))


Step = tuple[str, tuple[int, int]]


def v180_caycapchuacong(tokens: int, floor: int, per: int = PER_PAGE) -> list[Step]:
    seq: list[Step] = [("open_tower", (525, 320)), ("open_tower", (525, 320))]
    seq += [("page_back", (208, 475))] * 26
    tienthem, vitri = v180_paging(floor, per)
    seq += [("page_next", (290, 475))] * tienthem
    seq.append(("slot", v180_slot(vitri)))
    for _ in range(tokens // 3):
        seq += [("sweep", (395, 467)), ("ok", V180_OK), ("exit", V180_EXIT), ("ok", V180_OK)]
    return seq


def v180_thapthiluyen(start: int, end: int, results: list[str], per: int = PER_PAGE) -> list[Step]:
    seq: list[Step] = [("open_tower", (525, 320)), ("open_tower", (525, 320))]
    seq += [("send_box", (255, 477))] * 2  # double clicks, then "1" + Enter
    tienthem, vitri = v180_paging(start, per)
    seq += [("page_next", (290, 475))] * tienthem
    solanvuot = end - start + 1
    i = 0
    while solanvuot > 0:
        seq += [("slot", v180_slot(vitri)), ("challenge", (338, 467)), ("ok", V180_OK),
                ("soldier", SOLDIER_AT)]
        outcome = results[i] if i < len(results) else "win"
        i += 1
        if outcome == "lose":
            solanvuot = 0
        else:
            seq.append(("result_ok", V180_RESULT_OK))
        if vitri == per:
            vitri = 1
            seq.append(("page_next", (290, 475)))
        else:
            vitri += 1
        solanvuot -= 1
    return seq


# Template-based clicks land on the button centre the port found, not on v1.8.0's offset:
# allow the button's half size around the v1.8.0 point.
TOLERANCE: dict[str, tuple[int, int]] = {"ok": (6, 8), "exit": (2, 4), "soldier": (1, 1), "result_ok": (0, 2)}


def assert_parity(actual: list[tuple[float, float]], expected: list[Step]) -> None:
    assert len(actual) == len(expected), (
        f"{len(actual)} clicks, expected {len(expected)}: {[n for n, _ in expected]}"
    )
    for k, (p, (name, (ex, ey))) in enumerate(zip(actual, expected, strict=True)):
        dx, dy = TOLERANCE.get(name, (0, 0))
        assert abs(p[0] - ex) <= dx and abs(p[1] - ey) <= dy, f"click {k} ({name}): {p} vs v1.8.0 {(ex, ey)}"


def _without(seq: list[Step], *names: str) -> list[Step]:
    return [s for s in seq if s[0] not in names]


# --- floor arithmetic ---------------------------------------------------------------------------
@pytest.mark.parametrize("floor", [1, 2, 5, 9, 10, 11, 12, 19, 20, 21, 30, 99, 100, 101, 220])
def test_floor_position_matches_v180(floor: int) -> None:
    """Same arithmetic as v1.8.0's 10-per-page list, and for other page sizes."""
    assert tower.floor_position(floor, per_page=tower.LEGACY_FLOORS_PER_PAGE) == v180_paging(floor)
    assert tower.floor_position(floor, per_page=PER_PAGE) == v180_paging(floor, PER_PAGE)
    assert tower.floor_position(floor, per_page=5) == v180_paging(floor, 5)


@pytest.mark.parametrize(
    "floor,expected",
    [(1, (0, 1)), (5, (0, 5)), (6, (0, 6)), (10, (0, 10)), (11, (1, 1)), (20, (1, 10)), (21, (2, 1)),
     (80, (7, 10))],
)
def test_floor_position_formula(floor: int, expected: tuple[int, int]) -> None:
    import math

    assert tower.floor_position(floor) == expected  # default: 10 floors per page
    page = math.ceil(floor / 10) - 1
    assert expected == (page, floor - page * 10)


def test_floor_position_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        tower.floor_position(0)
    with pytest.raises(ValueError):
        tower.floor_position(5, per_page=0)


def test_slot_points_are_the_live_floor_rows() -> None:
    """10 slots per page at v1.8.0's rows (Tầng 1 at y 218 live, 25 px apart)."""
    ctx, _ = make_ctx(TowerSim("dialog"))
    assert tower.per_page(ctx) == 10
    for n in range(1, 11):
        assert tower.slot_point(ctx, n) == Point(*v180_slot(n))
    for bad in (0, 11):
        with pytest.raises(ValueError):
            tower.slot_point(ctx, bad)


@pytest.mark.parametrize("name,unlocked", [("26c-tower-dialog-6-floors.png", 6), ("26-tower-dialog.png", 5)])
def test_unlocked_slots_on_live_captures(name: str, unlocked: int) -> None:
    """The floor list shows only unlocked floors: count the filled slots on real captures."""
    ctx, _ = make_ctx(TowerSim("dialog"))
    ctx.capturer = FileCapturer([_img(LIVE / name)], loop=True)
    states = tower.slot_states(ctx)
    assert states == [True] * unlocked + [False] * (10 - unlocked)
    assert tower.unlocked_slots(ctx) == unlocked


def test_page_size_comes_from_the_profile() -> None:
    assert COORDS.value("thap.floors_per_page") == 10 and COORDS.value("thap.floor_step_y") == 25
    other = CoordMap({"thap": {**COORDS.raw("thap"), "floors_per_page": 8, "floor_step_y": 20}})
    ctx, _ = make_ctx(TowerSim("dialog"))
    ctx.coords = other
    assert tower.per_page(ctx) == 8
    assert tower.slot_point(ctx, 8) == Point(245, 215 + 20 * 7)


def test_coords_file_holds_the_v180_values() -> None:
    assert COORDS.offset("thap.sweep_button") == (395, 467)
    assert COORDS.offset("thap.challenge_button") == (338, 467)
    assert COORDS.offset("thap.page_back") == (208, 475)
    assert COORDS.offset("thap.page_next") == (290, 475)
    assert COORDS.offset("thap.ttl_send_box") == (255, 477)
    assert (COORDS.value("thap.floor_list_x"), COORDS.value("thap.floor_list_y0")) == (245, 215)
    assert tower.LEGACY_FLOORS_PER_PAGE == 10  # v1.8.0's page size, kept for the legacy oracle only


# --- Cày cấp chúa công --------------------------------------------------------------------------
def test_chua_cong_parity_from_the_dialog() -> None:
    sim = TowerSim("dialog", exit_confirm=True)
    ctx, drv = make_ctx(sim)
    progress: list[tuple[int, int | None]] = []
    ctx._on_progress = lambda d, t: progress.append((d, t))
    r = execute(ChuaCong(ChuaCongParams(tokens=7, floor=12, delay_ms=0, scene_timeout_s=3)), ctx)
    assert r.status is RunStatus.DONE, r.message
    expected = _without(v180_caycapchuacong(7, 12), "open_tower")  # the dialog was already open
    assert_parity([p for _s, p, _d in sim.log], expected)
    assert not any(d for _s, _p, d in sim.log)
    assert progress == [(1, 2), (2, 2)]
    assert "2 lần" in r.message and "6 Thí luyện lệnh" in r.message
    # each click happened on the screen v1.8.0 expected it on: back x26, next x1 (floor 12 = page 2
    # slot 2), slot
    states = [s for s, _p, _d in sim.log]
    assert states[:28] == ["dialog"] * 28
    assert states[28:] == ["dialog", "confirm", "battle", "exit_confirm"] * 2
    assert sim.entered == [12, 12]


def test_chua_cong_without_exit_confirm_box() -> None:
    """v1.8.0's second "Đồng ý" is pressed only when a confirm box actually shows."""
    sim = TowerSim("dialog")
    ctx, _ = make_ctx(sim)
    r = execute(ChuaCong(ChuaCongParams(tokens=3, floor=10, delay_ms=0, scene_timeout_s=3)), ctx)
    assert r.status is RunStatus.DONE, r.message
    v180 = _without(v180_caycapchuacong(3, 10), "open_tower")
    expected = v180[:-1]  # no confirm box after "Thoát"
    assert_parity([p for _s, p, _d in sim.log], expected)
    dialog = sim.clicks_in("dialog")
    assert dialog[26] == (245, 440)  # floor 10 = page 1 slot 10, the last row; no "next"
    assert (290, 475) not in dialog and sim.entered == [10]


def test_chua_cong_default_floor_6_is_slot_6() -> None:
    """Default floor 6 (CCCC_TANGTHAP) = page 1 slot 6 (Tầng 6 at y 343 live), no "page next"."""
    assert ChuaCongParams().floor == 6
    sim = TowerSim("dialog", unlocked=6)
    ctx, _ = make_ctx(sim)
    r = execute(ChuaCong(ChuaCongParams(tokens=3, delay_ms=0, scene_timeout_s=3)), ctx)
    assert r.status is RunStatus.DONE, r.message
    dialog = sim.clicks_in("dialog")
    assert dialog[:26] == [(208, 475)] * 26
    assert dialog[26] == (245, 340)
    assert (290, 475) not in dialog
    assert sim.entered == [6]


def test_chua_cong_refuses_a_locked_floor(tmp_path: Path) -> None:
    sim = TowerSim("dialog", unlocked=5)
    ctx, _ = make_ctx(sim, png_dir=tmp_path)
    r = execute(ChuaCong(ChuaCongParams(tokens=3, floor=6, delay_ms=0, scene_timeout_s=3)), ctx)
    assert r.status is RunStatus.FAILED
    assert "Tầng 6 chưa mở khoá" in r.message and "tầng 5" in r.message
    assert sim.entered == [] and len(sim.clicks_in("dialog")) == 26  # never clicked a slot or "Vượt Tháp"


def test_chua_cong_opens_the_tower_from_the_city() -> None:
    sim = TowerSim("city")
    ctx, _ = make_ctx(sim)
    r = execute(ChuaCong(ChuaCongParams(tokens=3, floor=1, delay_ms=0, scene_timeout_s=3)), ctx)
    assert r.status is RunStatus.DONE, r.message
    (city_click,) = sim.clicks_in("city")
    (panel_click,) = sim.clicks_in("panel")
    assert city_click == (460, 320) and panel_click == (521, 321)  # docs/07, not v1.8.0's (525, 320)
    assert len(sim.clicks_in("dialog")) == 26 + 1 + 1  # back x26, slot, "Vượt Tháp"


def test_chua_cong_cancels_a_leftover_confirm_box() -> None:
    sim = TowerSim("confirm")
    ctx, _ = make_ctx(sim)
    r = execute(ChuaCong(ChuaCongParams(tokens=3, floor=1, delay_ms=0, scene_timeout_s=3)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sim.log[0][0] == "confirm" and _near(sim.log[0][1], (546, 331), 1, 1)  # 取消


def test_chua_cong_stops_when_no_confirm_box_appears(tmp_path: Path) -> None:
    """Out of tokens: "Vượt Tháp" opens nothing; the run stops instead of clicking blind."""
    sim = TowerSim("dialog", sweep_opens=False)
    ctx, _ = make_ctx(sim, png_dir=tmp_path)
    r = execute(ChuaCong(ChuaCongParams(tokens=30, floor=3, delay_ms=0, scene_timeout_s=0.6)), ctx)
    assert r.status is RunStatus.FAILED
    assert "tower.confirm" in r.message and "Thí luyện lệnh" in r.message
    assert r.screenshot is not None and r.screenshot.is_file()
    assert len(sim.log) == 26 + 1 + 1  # nothing after the first "Vượt Tháp"


def test_chua_cong_refuses_a_wrong_screen(tmp_path: Path) -> None:
    sim = TowerSim("field")
    ctx, drv = make_ctx(sim, png_dir=tmp_path)
    r = execute(ChuaCong(ChuaCongParams(tokens=3, delay_ms=0, scene_timeout_s=0.5)), ctx)
    assert r.status is RunStatus.FAILED and "field" in r.message
    assert drv.clicks == []


def test_chua_cong_does_not_open_from_the_city_when_disabled(tmp_path: Path) -> None:
    sim = TowerSim("city")
    ctx, drv = make_ctx(sim, png_dir=tmp_path)
    r = execute(ChuaCong(ChuaCongParams(tokens=3, open_from_city=False, delay_ms=0)), ctx)
    assert r.status is RunStatus.FAILED and "city" in r.message
    assert drv.clicks == []


def test_chua_cong_retries_thoat(monkeypatch) -> None:
    monkeypatch.setattr(tower, "EXIT_RETRY_S", 0.2)
    sim = TowerSim("dialog", exit_ignored=1)
    ctx, _ = make_ctx(sim)
    r = execute(ChuaCong(ChuaCongParams(tokens=3, floor=1, delay_ms=0, scene_timeout_s=3)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert len(sim.clicks_in("battle")) == 2


def test_chua_cong_params_and_entries() -> None:
    assert ChuaCong(ChuaCongParams(tokens=1000)).entries == 333
    assert ChuaCong(ChuaCongParams(tokens=3)).entries == 1
    with pytest.raises(ValueError):
        ChuaCongParams(tokens=2)
    with pytest.raises(ValueError):
        ChuaCongParams(floor=0)
    with pytest.raises(ValueError):
        ChuaCongParams(delay_ms=-1)


def test_chua_cong_legacy_import() -> None:
    ini = {"CCCC_QUANLENH": "1000", "CCCC_SLEEP": "500", "CCCC_TANGTHAP": "6"}
    out = ChuaCong.import_legacy(ini)
    assert out == {"tokens": 1000, "delay_ms": 500, "floor": 6}
    assert ChuaCongParams.model_validate(out).floor == 6
    assert ChuaCong.import_legacy({"CCCC_QUANLENH": "", "CCCC_SLEEP": "abc", "CCCC_TANGTHAP": "0"}) == {}
    assert ChuaCong.import_legacy({"CCCC_QUANLENH": "2"}) == {}


# --- Tháp Thí Luyện -----------------------------------------------------------------------------
def test_thap_thi_luyen_parity_across_a_page() -> None:
    sim = TowerSim("dialog")
    ctx, drv = make_ctx(sim)
    progress: list[tuple[int, int | None]] = []
    ctx._on_progress = lambda d, t: progress.append((d, t))
    params = ThapThiLuyenParams(start_floor=19, end_floor=21, delay_ms=0, scene_timeout_s=3,
                                battle_timeout_s=10)
    r = execute(ThapThiLuyen(params), ctx)
    assert r.status is RunStatus.DONE, r.message
    # default strategy (back to page 1, then next): already on page 1, so v1.8.0's blind page-box
    # typing is replaced by nothing at all
    v180 = _without(v180_thapthiluyen(19, 21, []), "open_tower", "send_box")
    assert_parity([p for _s, p, _d in sim.log], v180)
    # 10 floors per page: floor 19 = 1x "next" + slot 9, floor 20 = slot 10, then "next", floor 21 = slot 1
    dialog = sim.clicks_in("dialog")
    assert dialog[0:2] == [(290, 475), (245, 415)]
    assert (245, 440) in dialog and dialog.count((290, 475)) == 2
    assert sim.entered == [19, 20, 21]
    assert sim.other == [] and not any(d for _s, _p, d in sim.log)  # no key, no double click
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert "19" in r.message and "21" in r.message


def test_thap_thi_luyen_screens_of_each_click() -> None:
    sim = TowerSim("dialog")
    ctx, _ = make_ctx(sim)
    r = execute(ThapThiLuyen(ThapThiLuyenParams(start_floor=1, end_floor=2, delay_ms=0, scene_timeout_s=3,
                                                battle_timeout_s=10)), ctx)
    assert r.status is RunStatus.DONE, r.message
    per_floor = ["dialog", "dialog", "confirm", "battle", "win"]
    assert [s for s, _p, _d in sim.log] == per_floor * 2


def test_thap_thi_luyen_stops_on_a_loss(tmp_path: Path) -> None:
    sim = TowerSim("dialog", results=["win", "lose"])
    ctx, _ = make_ctx(sim, png_dir=tmp_path)
    r = execute(ThapThiLuyen(ThapThiLuyenParams(start_floor=9, end_floor=13, delay_ms=0, scene_timeout_s=3,
                                                battle_timeout_s=10)), ctx)
    assert r.status is RunStatus.DONE
    assert "thất bại ở tầng 10" in r.message and "1/5" in r.message
    expected = _without(v180_thapthiluyen(9, 13, ["win", "lose"]), "open_tower", "send_box")
    # floor 10 is the last slot of the page: v1.8.0 still pressed "page next" after the loss; the
    # port stops on the result screen instead
    assert expected[-1][0] == "page_next"
    assert_parity([p for _s, p, _d in sim.log], expected[:-1])
    assert sim.state == "lose" and sim.battles == 2
    assert list(tmp_path.glob("*.png"))  # the loss screen is saved


def test_thap_thi_luyen_starts_at_floor_6_not_1() -> None:
    """Regression (user report): start 6 with Tầng 1-6 unlocked climbed from Tầng 1.

    With the old 5-per-page setting, floor 6 meant "page next + slot 1"; page 2 did not exist yet,
    so the click on slot 1 selected Tầng 1. Now: slot 6 on page 1, and each win unlocks the next
    floor, across the page boundary after Tầng 10.
    """
    sim = TowerSim("dialog", unlocked=6)
    ctx, _ = make_ctx(sim)
    r = execute(ThapThiLuyen(ThapThiLuyenParams(start_floor=6, end_floor=12, delay_ms=0, scene_timeout_s=3,
                                                battle_timeout_s=10)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sim.entered == [6, 7, 8, 9, 10, 11, 12]
    dialog = sim.clicks_in("dialog")
    assert dialog[0] == (245, 340)  # Tầng 6, no "page next" before it
    assert dialog.count((290, 475)) == 1  # only after Tầng 10
    assert sim.page == 1


def test_thap_thi_luyen_refuses_a_locked_start_floor(tmp_path: Path) -> None:
    sim = TowerSim("dialog", unlocked=5)
    ctx, _ = make_ctx(sim, png_dir=tmp_path)
    r = execute(ThapThiLuyen(ThapThiLuyenParams(start_floor=6, end_floor=80, delay_ms=0, scene_timeout_s=3,
                                                battle_timeout_s=10)), ctx)
    assert r.status is RunStatus.FAILED
    assert "Tầng 6 chưa mở khoá" in r.message and "tầng 5" in r.message
    assert sim.entered == []  # never entered the tower (before the fix: Tầng 1)
    assert not any(331 <= p[0] <= 406 for p in sim.clicks_in("dialog"))  # no "Vượt Tháp"


def test_thap_thi_luyen_stops_when_the_page_does_not_turn(tmp_path: Path) -> None:
    sim = TowerSim("dialog", unlocked=8)
    ctx, _ = make_ctx(sim, png_dir=tmp_path)
    r = execute(ThapThiLuyen(ThapThiLuyenParams(start_floor=12, end_floor=15, delay_ms=0, scene_timeout_s=3,
                                                battle_timeout_s=10)), ctx)
    assert r.status is RunStatus.FAILED
    assert "Không sang được trang 2" in r.message and "tầng 11" in r.message
    assert sim.entered == []
    assert sim.clicks_in("dialog")[-1] == (290, 475)  # stopped right after the "page next" click


# --- reaching the start page (user report: on page 11, start 102 still began on the wrong page) ---
def _params(start: int, end: int, strategy: tower.PageStrategy) -> ThapThiLuyenParams:
    return ThapThiLuyenParams(start_floor=start, end_floor=end, delay_ms=0, scene_timeout_s=3,
                              battle_timeout_s=10, page_strategy=strategy)


def test_back_strategy_from_page_11() -> None:
    sim = TowerSim("dialog", page=10, unlocked=120)
    ctx, _ = make_ctx(sim)
    r = execute(ThapThiLuyen(_params(102, 103, tower.PageStrategy.BACK_THEN_NEXT)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sim.entered == [102, 103]
    dialog = sim.clicks_in("dialog")
    assert dialog[:10] == [(208, 475)] * 10  # ◀ until the box shows 1 (each click checked)
    assert dialog[10:20] == [(290, 475)] * 10  # ▶ to page 11
    assert dialog[20] == (245, 240)  # slot 2 = Tầng 102
    assert sim.other == []  # clicks only, no keys


def test_type_strategy_from_page_11() -> None:
    sim = TowerSim("dialog", page=10, unlocked=120)
    ctx, drv = make_ctx(sim)
    r = execute(ThapThiLuyen(_params(102, 103, tower.PageStrategy.TYPE_PAGE)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sim.entered == [102, 103]
    keys = [(e.kind, e.text, e.vk) for e in sim.other]
    assert keys == [("key", None, VK_BACK), ("text", "11", None), ("key", None, VK_RETURN)]
    assert sim.log[0][2] is True and _near(sim.log[0][1], (255, 477), 1, 1)  # double click selects all
    assert (208, 475) not in sim.clicks_in("dialog")


def test_type_strategy_goes_to_another_page() -> None:
    sim = TowerSim("dialog", page=10, unlocked=120)
    ctx, _ = make_ctx(sim)
    r = execute(ThapThiLuyen(_params(35, 35, tower.PageStrategy.TYPE_PAGE)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sim.entered == [35]
    assert [(e.kind, e.text) for e in sim.other if e.kind == "text"] == [("text", "4")]


def test_type_strategy_stops_when_the_box_rolls_back(tmp_path: Path) -> None:
    """Typed page 12 does not exist yet (floors up to 105 unlocked): the box falls back to 11."""
    sim = TowerSim("dialog", page=10, unlocked=105)
    ctx, _ = make_ctx(sim, png_dir=tmp_path)
    r = execute(ThapThiLuyen(_params(115, 120, tower.PageStrategy.TYPE_PAGE)), ctx)
    assert r.status is RunStatus.FAILED
    assert "không giữ trang 12" in r.message
    assert sim.entered == [] and sim.page == 10


def test_type_strategy_never_presses_enter_when_keys_miss_the_box(tmp_path: Path) -> None:
    """The safety rule: if Backspace does not clear the box, no other key (above all Enter) is sent."""
    sim = TowerSim("dialog", page=10, unlocked=120, keys_reach_box=False)
    ctx, _ = make_ctx(sim, png_dir=tmp_path)
    r = execute(ThapThiLuyen(_params(102, 103, tower.PageStrategy.TYPE_PAGE)), ctx)
    assert r.status is RunStatus.FAILED and "không bấm Enter" in r.message
    assert [(e.kind, e.vk) for e in sim.other] == [("key", VK_BACK)]
    assert sim.entered == []


@pytest.mark.parametrize("strategy", list(tower.PageStrategy))
def test_switches_to_the_thap_tab_before_any_key(strategy: tower.PageStrategy) -> None:
    """Started on the "Điểm tướng" tab (same dialog title, no floor list): click "Tháp" first."""
    sim = TowerSim("diem_tuong", page=10, unlocked=120)
    ctx, _ = make_ctx(sim)
    r = execute(ThapThiLuyen(_params(102, 102, strategy)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sim.log[0][0] == "diem_tuong" and _near(sim.log[0][1], (222, 159), 1, 1)
    assert len(sim.clicks_in("diem_tuong")) == 1  # nothing else was clicked on that tab
    assert sim.entered == [102]


@pytest.mark.parametrize("strategy", list(tower.PageStrategy))
def test_stops_without_keys_when_the_floor_list_never_shows(strategy: tower.PageStrategy, tmp_path: Path) -> None:
    sim = TowerSim("diem_tuong", tab_switches=False)
    ctx, _ = make_ctx(sim, png_dir=tmp_path)
    r = execute(ThapThiLuyen(_params(102, 102, strategy)), ctx)
    assert r.status is RunStatus.FAILED and "thẻ \"Tháp\"" in r.message
    assert sim.other == []  # not a single key on the wrong tab
    assert sim.clicks_in("diem_tuong") == [(222, 159)]


def test_page_reading_on_live_captures() -> None:
    """Page-1 detection works on real frames, also with chat text over the box."""
    for name, expect in (("26c-tower-dialog-6-floors.png", True), ("26-tower-dialog.png", True)):
        ctx, _ = make_ctx(TowerSim("dialog"))
        ctx.capturer = FileCapturer([_img(LIVE / name)], loop=True)
        assert tower.is_page_one(ctx) is expect, name
        assert tower.floor_list_shown(ctx)
    ctx, _ = make_ctx(TowerSim("dialog"))
    ctx.capturer = FileCapturer([_img(LIVE / "26d-tower-diem-tuong-tab.png")], loop=True)
    assert not tower.floor_list_shown(ctx)


def test_thap_thi_luyen_closes_a_reward_popup_in_battle() -> None:
    """Regression (user report): "Chúc mừng ngài được … M.C+4500" opened over the battle and the
    run waited for the result until its time limit. The popup guard closes it and the climb goes on."""
    sim = TowerSim("dialog", popup_battles=2, soldier_starts=False, auto_result_grabs=3)
    ctx, _ = make_ctx(sim)
    r = execute(ThapThiLuyen(ThapThiLuyenParams(start_floor=35, end_floor=37, delay_ms=0, scene_timeout_s=3,
                                                battle_timeout_s=10)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sim.entered == [35, 36, 37]
    assert sim.popup_clicks == 2 and ctx.counters["popups"] == 2
    assert not [p for s, p, _d in sim.log if s == "popup"]  # nothing else was clicked on the modal


def test_thap_thi_luyen_started_inside_the_tower_leaves_it_first() -> None:
    """Regression (user log): starting while the battle screen was shown failed with
    "Hãy về Thành chính hoặc mở bảng Tháp Thí Luyện"; now it exits to the dialog first."""
    sim = TowerSim("battle", exit_confirm=True)
    ctx, _ = make_ctx(sim)
    r = execute(ThapThiLuyen(ThapThiLuyenParams(start_floor=3, end_floor=3, delay_ms=0, scene_timeout_s=3,
                                                battle_timeout_s=10)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sim.entered == [3]
    assert _near(sim.log[0][1], (960, 563), 2, 2)  # "Thoát" first


def test_thap_thi_luyen_chinese_result_banner() -> None:
    sim = TowerSim("dialog", win_frame="win_zh")
    ctx, _ = make_ctx(sim)
    r = execute(ThapThiLuyen(ThapThiLuyenParams(start_floor=3, end_floor=3, delay_ms=0, scene_timeout_s=3,
                                                battle_timeout_s=10)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert len(sim.clicks_in("win_zh")) == 1


def test_thap_thi_luyen_leaves_the_battle_after_a_win() -> None:
    """The result OK may leave the battle screen up: the port clicks "Thoát" and waits for the dialog."""
    sim = TowerSim("dialog", ok_to="battle", exit_confirm=True)
    ctx, _ = make_ctx(sim)
    r = execute(ThapThiLuyen(ThapThiLuyenParams(start_floor=1, end_floor=2, delay_ms=0, scene_timeout_s=3,
                                                battle_timeout_s=10)), ctx)
    assert r.status is RunStatus.DONE, r.message
    battle = sim.clicks_in("battle")
    assert len(battle) == 4  # per floor: soldier, then "Thoát"
    assert _near(battle[1], (960, 563), 2, 2) and len(sim.clicks_in("exit_confirm")) == 2
    assert sim.state == "dialog"


def test_thap_thi_luyen_without_soldier_click() -> None:
    sim = TowerSim("dialog", soldier_starts=False, auto_result_grabs=3)
    ctx, _ = make_ctx(sim)
    r = execute(ThapThiLuyen(ThapThiLuyenParams(start_floor=1, end_floor=1, delay_ms=0, scene_timeout_s=3,
                                                battle_timeout_s=10, click_soldier=False)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sim.clicks_in("battle") == []


def test_thap_thi_luyen_soldier_missing_is_not_fatal() -> None:
    """No unit found within 3 s: log it and keep waiting for the result (v1.8.0 did the same)."""
    sim = TowerSim("dialog", soldier_starts=False, auto_result_grabs=25)
    ctx, _ = make_ctx(sim)
    own = TemplateStore(PROFILE / "templates")  # not the shared STORE: this one gets a fake unit
    noise = np.random.default_rng(7).integers(0, 255, (6, 11, 3), dtype=np.uint8)  # matches nothing
    own.register("tower.battle.soldier", noise)
    ctx.matcher = Matcher(own, history=64)
    r = execute(ThapThiLuyen(ThapThiLuyenParams(start_floor=2, end_floor=2, delay_ms=0, scene_timeout_s=3,
                                                battle_timeout_s=20)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sim.clicks_in("battle") == []


def test_thap_thi_luyen_battle_timeout(tmp_path: Path) -> None:
    sim = TowerSim("dialog", soldier_starts=False)
    ctx, _ = make_ctx(sim, png_dir=tmp_path)
    params = ThapThiLuyenParams(start_floor=1, end_floor=3, delay_ms=0, scene_timeout_s=3)
    params = params.model_copy(update={"battle_timeout_s": 0.5})  # below the UI minimum, for speed
    t0 = time.monotonic()
    r = execute(ThapThiLuyen(params), ctx)
    assert time.monotonic() - t0 < 8
    assert r.status is RunStatus.FAILED
    assert "tầng 1" in r.message and "Thắng/Bại" in r.message
    assert r.screenshot is not None and r.screenshot.is_file()


def test_thap_thi_luyen_opens_from_the_city() -> None:
    sim = TowerSim("city")
    ctx, _ = make_ctx(sim)
    r = execute(ThapThiLuyen(ThapThiLuyenParams(start_floor=1, end_floor=1, delay_ms=0, scene_timeout_s=3,
                                                battle_timeout_s=10)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert sim.clicks_in("city") == [(460, 320)] and sim.clicks_in("panel") == [(521, 321)]


def test_thap_thi_luyen_params_validation() -> None:
    with pytest.raises(ValueError, match="tầng bắt đầu"):
        ThapThiLuyenParams(start_floor=8, end_floor=5)
    with pytest.raises(ValueError):
        ThapThiLuyenParams(start_floor=0)
    with pytest.raises(ValueError):
        ThapThiLuyenParams(battle_timeout_s=1)
    p = ThapThiLuyenParams(start_floor=5, end_floor=5)
    assert (p.start_floor, p.end_floor) == (5, 5)


def test_thap_thi_luyen_rejects_an_inverted_range_at_run_time() -> None:
    p = ThapThiLuyenParams(start_floor=1, end_floor=2)
    p.start_floor = 9  # assignment validates the field alone
    sim = TowerSim("dialog")
    ctx, drv = make_ctx(sim)
    r = execute(ThapThiLuyen(p), ctx)
    assert r.status is RunStatus.FAILED and drv.clicks == []


def test_thap_thi_luyen_legacy_import() -> None:
    assert ThapThiLuyen.import_legacy({"TTL_INPUT_BATDAU": "1", "TTL_INPUT_KETTHUC": "5"}) == {
        "start_floor": 1, "end_floor": 5}
    assert ThapThiLuyen.import_legacy({"TTL_INPUT_BATDAU": "Tầng", "TTL_INPUT_KETTHUC": ""}) == {}
    out = ThapThiLuyen.import_legacy({"TTL_INPUT_BATDAU": "12", "TTL_INPUT_KETTHUC": "3"})
    assert out == {"start_floor": 12, "end_floor": 12}
    assert ThapThiLuyenParams.model_validate(out).end_floor == 12
    out = ThapThiLuyen.import_legacy({"TTL_INPUT_BATDAU": "9"})
    assert ThapThiLuyenParams.model_validate(out).end_floor == 9


# --- templates and scenes -----------------------------------------------------------------------
def _all_screens() -> list[Path]:
    return sorted(SHOTS.glob("*.png")) + sorted(LIVE.glob("*.png"))


def test_result_templates_never_match_stored_screens() -> None:
    from ngoalong.features.thap_thi_luyen import LOSE, RESULT_THRESHOLD, WIN

    m = Matcher(STORE)
    for path in _all_screens():
        img = _img(path)
        fix = LOCATOR.locate(img)
        for name in WIN + LOSE:
            d = m.search(name, fix.rect, RESULT_THRESHOLD, frame=img, scale=fix.scale)
            assert not d.found, f"{name} on {path.name}: {d.score:.2f}"


def test_result_templates_found_on_synthetic_results() -> None:
    from ngoalong.features.thap_thi_luyen import RESULT_THRESHOLD

    m = Matcher(STORE)
    for key, name in (("win", "tower.result.win"), ("win_zh", "tower.result.win_zh"), ("lose", "tower.result.lose")):
        img = frames()[key]
        fix = LOCATOR.locate(img)
        assert m.search(name, fix.rect, RESULT_THRESHOLD, frame=img).found


def test_soldier_template_only_on_the_battle_screen() -> None:
    from ngoalong.features.thap_thi_luyen import SOLDIER, SOLDIER_THRESHOLD

    m = Matcher(STORE)
    for path in _all_screens():
        img = _img(path)
        fix = LOCATOR.locate(img)
        d = m.search(SOLDIER, fix.rect, SOLDIER_THRESHOLD, frame=img, scale=fix.scale)
        if path.name == "tower-in-dungeon.png":
            assert d.found and fix.to_canvas(d.rect.center) == SOLDIER_AT
        else:
            assert not d.found, f"{path.name}: {d.score:.2f}"


def test_exit_confirm_scene() -> None:
    img = frames()["exit_confirm"]
    assert SCENES.detect(img, LOCATOR.locate(img)).name == "tower.exit_confirm"
    real = frames()["confirm"]
    assert SCENES.detect(real, LOCATOR.locate(real)).name == "tower.confirm"  # priority 10 wins


# --- registry, Stop -----------------------------------------------------------------------------
def test_features_are_registered() -> None:
    ids = {c.id for c in registry.discover()}
    assert {"chua_cong", "thap_thi_luyen"} <= ids
    assert registry.get_feature("chua_cong") is ChuaCong
    assert registry.get_feature("thap_thi_luyen") is ThapThiLuyen
    assert ChuaCong.start_scene == ThapThiLuyen.start_scene == "tower.dialog"
    assert "_tower" not in ids


def _run_until(qapp, task: Task, sim: TowerSim, ready, tmp_path: Path) -> tuple[float, Any]:
    runner = TaskRunner(tmp_path / "logs")

    def factory(t: Task, hooks: RunHooks) -> RunContext:
        ctx, _ = make_ctx(sim, control=hooks.control)
        ctx._on_log = hooks.on_log
        return ctx

    runner.start(task, factory)
    deadline = time.monotonic() + 20
    while not ready() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready(), "the task never reached the expected state"
    time.sleep(0.15)
    t0 = time.monotonic()
    runner.stop()
    assert runner.wait(3.0)
    return time.monotonic() - t0, runner.last_result


def test_stop_during_the_battle_wait(qapp, tmp_path: Path) -> None:
    sim = TowerSim("dialog", soldier_starts=False)
    task = ThapThiLuyen(ThapThiLuyenParams(start_floor=1, end_floor=5, delay_ms=0, battle_timeout_s=600))
    elapsed, result = _run_until(qapp, task, sim, lambda: sim.state == "battle" and len(sim.log) >= 3, tmp_path)
    assert elapsed < 1.0
    assert result.status is RunStatus.STOPPED


def test_stop_during_a_long_delay(qapp, tmp_path: Path) -> None:
    sim = TowerSim("dialog")
    task = ChuaCong(ChuaCongParams(tokens=300, floor=2, delay_ms=60_000))
    elapsed, result = _run_until(qapp, task, sim, lambda: len(sim.log) >= 27, tmp_path)
    assert elapsed < 1.0
    assert result.status is RunStatus.STOPPED
