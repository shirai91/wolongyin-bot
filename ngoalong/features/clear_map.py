"""Clear Phó Bản: attack the next army of the campaign field again and again, then move on to the
next dungeon with "»" (docs/08 "Feature flows" 3; rule-based since R3; v1.8.0 ``CLEARMAP``).

The rules work on the campaign field (``field``) and its dialogs; the loop opens the field from any
map with "Phó Bản" (bottom-right cluster, ``nav.yaml``) and closes whatever dialog is in the way.

* **Next army**: the game marks it with a light-blue beam (``field.beam``). The beam flickers, so it
  must be seen on 2 ticks in a row; it is picked by size (blue map decorations are wider or
  taller), and the army stands ~55 px below its lower tip. Over an army at the top of the map the
  beam is cut by the canvas edge (only its lower part shows): it is taken when it is still tall
  enough. On a bright map (sand) the beam washes out to near-white: the probe takes the pale glow
  around a saturated blue centre. A blob on the "◀" panel tab (it glows pale blue) is ignored. The top icon rows and the left notice list are folded away first (armies near the top or
  left sit under them; the icon rows are shown again at the end).
* **Attack**: click the army → army dialog. "Phát chiến đấu" is kept **off** (unticked if needed),
  so Tấn công resolves the fight at once: the dialog closes and a green "Thắng Trận" line shows
  (1 Quân lệnh per attack). An army **without** the "Phát chiến đấu" box either opens Đoàn chiến (a
  group army "(Đoàn-Tinh)": Lập đội → team room → Khai chiến → battle → 攻方胜利 → Thoát) or plays
  its battle (a boss army such as 魔王董卓 "(Đội-Tướng)": battle → 战胜 / 战败 box → Thoát). On the
  battle screen "Kết quả" (bottom right, once the battle has loaded) skips straight to the result.
* **Progress** is read from the beam: when it moves to another army the last one fell; an attack
  with no green line and the beam still over the same army (and its troop count "N/M" not lower)
  is a loss. ``max_losses`` such attacks in a row end the run.
* **Next dungeon**: no beam for ``NO_BEAM_S`` seconds = this dungeon is cleared. With "sang phó bản
  kế tiếp" on, "»" is pressed (it is missing on the last dungeon); a dungeon that shows no beam
  right after the switch ends the run (not unlocked yet, or cleared), as in v1.8.0.
* Stops: ``max_battles``, ``max_maps``, ``max_minutes``, Quân lệnh 0.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from ngoalong.core.geometry import Point, Rect
from ngoalong.engine.actions import Action, Click, Fail, Wait, or_after
from ngoalong.engine.feature import Feature, Rule
from ngoalong.engine.registry import register
from ngoalong.engine.task import TaskParams
from ngoalong.perception import Snapshot
from ngoalong.perception.readers import ScreenState

FIELD = "field"
ARMY = "field.army_dialog"
GROUP_LIST = "field.group_list"
GROUP_ROOM = "field.group_room"
BATTLE = "battle"
RESULT = "battle.result"
GROUP_RESULT = "battle.group_result"
ARMY_SCREENS = (ARMY, GROUP_LIST, GROUP_ROOM)

PLAY_TICK = Point(550, 411)
ARMY_CLOSE = Point(638, 184)
RESULT_EXIT = Point(540, 408)  # Thoát of the 战胜 / 战败 box (when its template is not found)
GROUP_EXIT = Point(960, 577)  # Thoát bottom right after a group battle
# The beam: 9-16 x 30-50 px live (frames 359, 367, 425, 480, 485); its tip bobs a few px. Cut by the
# top edge over an army at the top of the map (10 x 34 px at y=0, user report 2026-10-10).
BEAM_W = (6, 18)
BEAM_H = (25, 60)
BEAM_CUT_H = 20  # a beam that starts at y=0 is taken from this height (the probe's min_h)
ARMY_BELOW = (55, 35, 75)  # px below the beam's lower tip, per try (the army sprite is ~50 px tall)
SAME_DX, SAME_DY = 25, 40  # the beam is over the same army (it bobs up and down)
OPEN_TRIES = 3  # clicks under one beam that open no dialog before the run fails
ATTACK_TRIES = 3  # Tấn công presses that leave the dialog open
NO_BEAM_S = 4.0  # no beam for this long on the bare field: the dungeon is cleared
WON_WAIT_S = 2.0  # after Tấn công: the green "Thắng Trận" line shows within this
# Where an army can be clicked: not under the bottom bar, the chat, the bottom-right cluster or the
# HUD corners on top (player panel top left; year bar, Thư / Hạng / ... and Quốc chiến top right;
# both stay when the icon rows are folded) and the "◀" panel tab on the right edge (pale blue: its
# glow can pass for a beam). The top centre is open map (an army at ~450,89 there).
CLICK_AREA = Rect(0, 20, 1000, 505)
PANEL_TAB = Rect(965, 150, 35, 60)
COVERED = (Rect(860, 455, 140, 145), Rect(0, 0, 355, 128), Rect(745, 0, 255, 128), PANEL_TAB)


class ClearMapParams(TaskParams):
    next_map: bool = Field(True, title="Hết đạo quân thì sang phó bản kế tiếp (»)")
    max_maps: int = Field(0, ge=0, le=100, title="Số phó bản được chuyển sang tối đa (0 = không giới hạn)")
    max_battles: int = Field(0, ge=0, le=100_000, title="Số trận tối đa (0 = không giới hạn)")
    max_losses: int = Field(3, ge=1, le=100, title="Dừng khi thua liên tiếp bấy nhiêu trận",
                            description="Thua = đánh mà đạo quân vẫn còn (tia sáng xanh không chuyển đi)")
    battle_timeout_s: float = Field(
        180.0, ge=10.0, le=3600.0, title="Thời gian tối đa mỗi trận (giây)",
        description="Trận đoàn chiến (hoặc trận có Phát chiến đấu) không kết thúc sau thời gian này thì gỡ rối",
    )
    max_minutes: int = Field(0, ge=0, le=24 * 60, title="Thời gian chạy tối đa (phút)",
                             description="0 = không giới hạn")


class Beam(BaseModel):
    x: int  # centre
    tip: int  # lower end

    def same(self, other: Beam | None) -> bool:
        return other is not None and abs(self.x - other.x) <= SAME_DX and abs(self.tip - other.tip) <= SAME_DY


@register
class ClearMap(Feature):
    id = "clear_map"
    title = "Clear Phó Bản"
    description = (
        "Đánh lần lượt đạo quân có tia sáng xanh trên bản đồ Phó Bản (tắt Phát chiến đấu cho nhanh, đánh cả "
        "đoàn chiến), hết thì sang phó bản kế tiếp. Bắt đầu từ bất kỳ màn hình nào: bot tự mở Phó Bản"
    )
    order = 80
    Params = ClearMapParams
    entry = FIELD

    params: ClearMapParams

    class Memory(BaseModel):
        battles: int = 0
        wins: int = 0
        losses: int = 0
        loss_streak: int = 0
        maps: int = 0
        beam_ticks: int = 0  # ticks in a row with a beam seen
        no_beam_since: float | None = None
        target: Beam | None = None  # the beam of the army attacked last
        judged: bool = True  # the last attack's outcome is known
        troops: tuple[int, int] | None = None  # "N/M" of the army attacked last
        open_tries: int = 0
        no_dialog: bool = False  # OPEN_TRIES clicks under the beam opened nothing: the run fails
        by_troops: bool = False  # the last attack is judged by the troop count when its dialog opens again
        attack_tries: int = 0
        opened: bool = False  # the army dialog on screen was opened by this run
        switched: bool = False  # "»" was pressed and no beam was seen since
        counted: bool = False  # the result box on screen was counted
        exit_clicks: int = 0  # Thoát clicks on the result box on screen
        hid_icons: bool = False
        finished: str = ""  # why the run ends (set once the work is over)

    m: Memory

    def max_run_s(self) -> float:
        return self.params.max_minutes * 60.0

    # --- goal --------------------------------------------------------------------------------------
    def goal(self, snap: Snapshot) -> str | None:
        p = self.params
        if snap.top == FIELD:
            self.m.opened = False
            self.m.counted = False
            self.m.exit_clicks = 0
            hud = snap.hud
            ql = hud.quan_lenh if hud is not None else None
            if ql is not None and ql[0] == 0 and not self.m.finished:
                self._finish("hết Quân lệnh")
        if p.max_battles and self.m.battles >= p.max_battles and not self.m.finished:
            self._finish(f"đã đủ {p.max_battles} trận")
        if self.m.finished and snap.top == FIELD and not self.m.hid_icons:
            return f"{self.summary()}: {self.m.finished}"
        return None

    def _finish(self, why: str) -> None:
        self.m.finished = why
        self.actor.log(f"Kết thúc: {why}")

    def summary(self) -> str:
        m = self.m
        s = f"{m.battles} trận (thắng {m.wins}, thua {m.losses})"
        return s + (f", sang {m.maps} phó bản" if m.maps else "")

    # --- rules -------------------------------------------------------------------------------------
    def rules(self) -> list[Rule]:
        working = lambda: not self.m.finished  # noqa: E731
        return [
            Rule("thu gọn thông báo", on=FIELD, when=lambda s: working() and s.notice,
                 do=lambda s: Click(self.snap.point("hud.notice_open"), label="thu gọn danh sách thông báo",
                                    within=2, expect=lambda n: n.value(FIELD, "notice") is False)),
            Rule("ẩn biểu tượng", on=FIELD, when=lambda s: working() and s.icons_shown,
                 do=lambda s: Click(self.snap.point("hud.icons_shown"), label="ẩn hàng biểu tượng", within=2,
                                    expect=lambda n: n.value(FIELD, "icons_shown") is False,
                                    then=self._hid_icons)),
            Rule("hiện lại biểu tượng", on=FIELD, when=lambda s: bool(self.m.finished) and self.m.hid_icons,
                 do=self._show_icons),
            Rule("tìm đạo quân", on=FIELD, when=lambda s: working(), do=self._field),
            # the army dialog: only one this run opened (a dialog left open by the user is closed)
            Rule("đoàn chiến / boss", on=ARMY, when=lambda s: self.m.opened and s.group,
                 do=lambda s: Click(s.attack, label=f"Tấn công (trận {self.m.battles + 1}, đánh có màn trận)", within=6,
                                    expect=lambda n: n.has(GROUP_LIST) or n.has(GROUP_ROOM) or n.base == BATTLE)),
            Rule("tắt Phát chiến đấu", on=ARMY, when=lambda s: self.m.opened and s.play_battle,
                 do=lambda s: Click(PLAY_TICK, label="bỏ chọn Phát chiến đấu", within=2,
                                    expect=lambda n: n.value(ARMY, "play_battle") is False)),
            Rule("tấn công", on=ARMY, when=lambda s: self.m.opened, do=self._attack),
            Rule("lập đội", on=GROUP_LIST, when=lambda s: self.m.opened,
                 do=lambda s: Click(s.create, label="Lập đội", within=6, expect=GROUP_ROOM)),
            Rule("khai chiến", on=GROUP_ROOM,
                 do=lambda s: Click(s.start, label="Khai chiến", within=20, then=self._battle_started,
                                    expect=lambda n: n.base == BATTLE or n.top == FIELD)),
            Rule("bỏ qua trận", on=BATTLE, when=lambda s: s.skip is not None,
                 do=lambda s: Click(s.skip, label="Kết quả (bỏ qua trận)", within=4,
                                    expect=or_after(lambda n: n.has(RESULT) or n.has(GROUP_RESULT)
                                                    or n.base != BATTLE, 2.0))),
            Rule("đang đánh", on=BATTLE,
                 do=lambda s: Wait(0.25, label="đang đánh", patience=self.params.battle_timeout_s)),
            Rule("kết quả", on=RESULT, do=self._result),
            Rule("kết quả đoàn chiến", on=GROUP_RESULT, do=self._result),
        ]

    def _hid_icons(self) -> None:
        self.m.hid_icons = True

    def _show_icons(self, s: ScreenState) -> Action:
        p = self.snap.point("hud.icons_hidden")
        if p is None:
            self.m.hid_icons = False
            return Wait(0.1, label="hàng biểu tượng đã hiện")
        return Click(p, label="hiện lại hàng biểu tượng", within=2,
                     expect=lambda n: n.value(FIELD, "icons_shown") is True, then=self._icons_back)

    def _icons_back(self) -> None:
        self.m.hid_icons = False

    # --- the field ---------------------------------------------------------------------------------
    def _beam(self, s: ScreenState) -> Beam | None:
        found = [Beam(x=r.center.x, tip=r.bottom) for r in s.beams
                 if not PANEL_TAB.contains(r.center) and BEAM_W[0] <= r.w <= BEAM_W[1]
                 and (BEAM_H[0] <= r.h <= BEAM_H[1] or (r.y <= 0 and BEAM_CUT_H <= r.h <= BEAM_H[1]))]
        if not found:
            return None
        last = self.m.target
        return next((b for b in found if b.same(last)), found[0])

    def _field(self, s: ScreenState) -> Action:
        beam = self._beam(s)
        now = time.monotonic()
        if beam is None:
            self.m.beam_ticks = 0
            if self.m.no_beam_since is None:
                self.m.no_beam_since = now
            if now - self.m.no_beam_since < NO_BEAM_S:
                return Wait(0.25, label="tìm tia sáng xanh", patience=NO_BEAM_S + 30)
            return self._cleared(s)
        self.m.no_beam_since = None
        if self.m.no_dialog:
            return Fail(f"bấm {OPEN_TRIES} lần dưới tia sáng xanh mà không mở được bảng đạo quân")
        self.m.beam_ticks += 1
        if self.m.beam_ticks < 2:  # it flickers: seen on 2 ticks in a row
            return Wait(0.2, label="tia sáng xanh (xác nhận)")
        if self.m.switched:
            self.m.switched = False
        self._judge(beam)
        if self.m.loss_streak >= self.params.max_losses:
            self._finish(f"thua {self.m.loss_streak} trận liên tiếp (đạo quân vẫn còn): hãy tăng lực chiến")
            return Wait(0.05, label="dừng")
        army = Point(beam.x, beam.tip + ARMY_BELOW[self.m.open_tries % len(ARMY_BELOW)])
        if not CLICK_AREA.contains(army) or any(r.contains(army) for r in COVERED):
            return Fail(f"đạo quân dưới tia sáng xanh ({army.x},{army.y}) bị giao diện che, không bấm được")
        self.m.target = beam
        return Click(army, label=f"đạo quân ({army.x},{army.y})", within=3,
                     expect=or_after(lambda n: any(n.has(x) for x in ARMY_SCREENS), 2.5), then=self._army_opened)

    def _army_opened(self) -> None:
        snap = self.snap
        if snap is not None and any(snap.has(x) for x in ARMY_SCREENS):
            self.m.opened, self.m.open_tries, self.m.attack_tries = True, 0, 0
            return
        self.m.open_tries += 1
        if self.m.open_tries >= OPEN_TRIES:
            self.m.no_dialog = True

    def _judge(self, beam: Beam) -> None:
        """The outcome of the last attack, from where the beam is now."""
        if self.m.judged or self.m.target is None:
            return
        self.m.judged = True
        if not beam.same(self.m.target):
            self._won("tia sáng xanh đã chuyển sang đạo quân khác")
        elif self.m.troops is not None:
            self.m.by_troops = True  # an army with a count "N/M": its dialog tells
        else:
            self._lost("đạo quân vẫn còn")

    def _cleared(self, s: ScreenState) -> Action:
        """No beam on this dungeon any more."""
        if not self.m.judged and self.m.target is not None:
            self.m.judged = True
            self._won("không còn tia sáng xanh")
        p = self.params
        if self.m.switched:
            self._finish("phó bản mới không có đạo quân (chưa mở hoặc đã đánh hết)")
            return Wait(0.05, label="dừng")
        if not p.next_map:
            self._finish("đã hết đạo quân")
            return Wait(0.05, label="dừng")
        if p.max_maps and self.m.maps >= p.max_maps:
            self._finish(f"đã sang {self.m.maps} phó bản")
            return Wait(0.05, label="dừng")
        if s.next_map is None:
            self._finish("đã hết đạo quân, đây là phó bản cuối (không có »)")
            return Wait(0.05, label="dừng")
        return Click(s.next_map, label=f"phó bản kế tiếp » ({self.m.maps + 1})", within=4,
                     expect=or_after(lambda n: False, 3.0), then=self._switched)

    def _switched(self) -> None:
        self.m.maps += 1
        self.m.switched = True
        self.m.no_beam_since = None
        self.m.target = None
        self.actor.count("maps")
        self.actor.log(f"Sang phó bản kế tiếp ({self.m.maps})")

    # --- the army dialog ---------------------------------------------------------------------------
    def _attack(self, s: ScreenState) -> Action:
        if self.m.attack_tries >= ATTACK_TRIES:
            return Fail(f"bấm Tấn công {ATTACK_TRIES} lần mà bảng đạo quân vẫn mở (hết Quân lệnh, thiếu lính?)")
        self.m.attack_tries += 1
        if self.m.by_troops and self.m.attack_tries == 1:
            self.m.by_troops = False
            before, now = self.m.troops, s.troops
            if before is not None and now is not None and now[0] < before[0]:
                self._won(f"số lượng {before[0]}/{before[1]} → {now[0]}/{now[1]}")
            else:
                self._lost("đạo quân vẫn còn, số lượng không giảm")
            if self.m.loss_streak >= self.params.max_losses:
                self._finish(f"thua {self.m.loss_streak} trận liên tiếp (đạo quân vẫn còn): hãy tăng lực chiến")
                return Click(ARMY_CLOSE, label="đóng bảng đạo quân", within=3, expect=lambda n: not n.has(ARMY))
        self.m.troops = s.troops
        return Click(s.attack, label=f"Tấn công (trận {self.m.battles + 1})", within=WON_WAIT_S + 3,
                     expect=or_after(lambda n: n.top == FIELD and bool(n.value(FIELD, "won")), WON_WAIT_S),
                     then=self._attacked)

    def _attacked(self) -> None:
        snap = self.snap
        if snap is None or snap.has(ARMY):
            return  # still open: pressed again (bounded by ATTACK_TRIES)
        if snap.base == BATTLE:  # "Phát chiến đấu" was on after all: the result box tells
            return
        self.m.battles += 1
        self.actor.count("battles")
        self.actor.progress(self.m.battles, self.params.max_battles or None)
        if snap.value(FIELD, "won"):
            self._won("Thắng Trận")
            self.m.judged = True
        else:
            self.m.judged = False  # the beam tells on the next ticks

    def _battle_started(self) -> None:
        self.m.counted = False

    def _result(self, s: ScreenState) -> Action:
        if not self.m.counted:
            self.m.counted = True
            self.m.battles += 1
            self.actor.count("battles")
            self.actor.progress(self.m.battles, self.params.max_battles or None)
            self.m.judged = True
            if s.result == "win":
                self._won("战胜 / 攻方胜利")
            elif s.result == "lose":
                self._lost("战败")
            else:
                self.m.judged = False  # the loss text of a group battle is unknown: the beam tells
        exit_at = s.exit or (RESULT_EXIT if self.snap.top == RESULT else GROUP_EXIT)
        self.m.exit_clicks += 1  # a click while the box still animates in does nothing: click again
        return Click(exit_at, label=f"Thoát ({self.m.exit_clicks})", within=5,
                     expect=or_after(lambda n: not n.has(RESULT) and not n.has(GROUP_RESULT), 3.0))

    def _won(self, why: str) -> None:
        self.m.wins += 1
        self.m.loss_streak = 0
        self.actor.log(f"Trận {self.m.battles}: thắng ({why})")

    def _lost(self, why: str) -> None:
        self.m.losses += 1
        self.m.loss_streak += 1
        self.actor.log(f"Trận {self.m.battles}: thua ({why}), {self.m.loss_streak} trận liên tiếp", "warning")

    @classmethod
    def import_legacy(cls, ini: Mapping[str, str]) -> dict[str, Any]:
        """v1.8.0 saved only a delay and a template wait (``CPB_INPUT_MS``, ``CPB_INPUT_S``): neither
        exists on the decision loop."""
        return {}
