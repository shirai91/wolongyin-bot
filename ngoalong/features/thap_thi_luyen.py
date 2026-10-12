"""Tháp Thí Luyện: climb the trial tower floor by floor, or sweep a floor already won with "Tự vượt
tháp" (docs/08 "Feature flows" 4; rule-based since R2).

The rules work on the tower dialog (``tower.dialog``), its boxes, the floor map and the result
box; the loop opens the dialog from any map (city → the tower building's panel → "Tháp Thí
Luyện", ``nav.yaml``) and closes whatever dialog is in the way.

* **Page**: page N lists floors 10(N-1)+1 .. 10N (only floors reached). The page box takes typing:
  double-click, End, Backspaces, the number, Deletes, Enter (``Text``), read back. The dialog reopens on its last tab and page, so
  both are read, never assumed.
* **Climb** ("Vượt tháp"): select the floor, Vượt Tháp → 确定 (3 Thí luyện lệnh) → floor map: tick
  "Tự Vượt Tháp" if needed, click the guard under the light-blue crystal ONCE (the crystal must be
  seen on 2 ticks: it flickers), wait for 战胜 / 战败. A win moves to the next floor; a loss leaves
  the floor (Thoát → 确定) and ends the run.
* **Sweep** ("Tự vượt tháp"): only a floor already won may be swept: the next floor must be listed
  (else the run fails with that message). Tự Vượt Tháp → type the number of runs → 确定 → 确定;
  the runs done are counted from the Thí luyện lệnh spent (3 per run).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from ngoalong.core.geometry import Point
from ngoalong.engine.actions import Action, Click, Fail, Text, Wait
from ngoalong.engine.feature import Feature, Rule
from ngoalong.engine.registry import register
from ngoalong.engine.task import TaskParams
from ngoalong.perception import Snapshot
from ngoalong.perception.readers import ScreenState

DIALOG = "tower.dialog"
SWEEP = "tower.sweep"
FLOOR = "tower.floor"
RESULT = "tower.result"
CONFIRM = "confirm.ok_cancel"

TAB_THAP = Point(220, 157)  # "Tháp" tab of the medium dialog
PAGE_BOX = Point(247, 476)  # page box between ◀ ▶
SWEEP_BOX = Point(700, 253)  # "Hãy nhập số lần vào" count box
SWEEP_OK = Point(655, 464)  # 确定 of the sweep panel
AUTO_TICK = Point(741, 564)  # "☑ Tự Vượt Tháp" on the floor map
GUARD_BELOW_MARKER = 50  # the guard stands ~50 px below the crystal's bottom
TOKENS_PER_RUN = 3
PER_PAGE = 10
MAX_FLOOR = 999
# The crystal is 8-14 x 29-39 px; ice floors (61+) have blue torches and orbs of the same colour
# (18x63 at 813,188 and 9x37 at 672,96 on floor 61, frame 449), so size and height pick it out.
CRYSTAL_W = (6, 15)
CRYSTAL_H = (26, 44)
ENEMY_CLICKS = 3  # clicks on the guard per floor map before giving up (normally one)


class TowerMode(StrEnum):
    CLIMB = "climb"
    SWEEP = "sweep"

    @property
    def label(self) -> str:
        return {TowerMode.CLIMB: "Vượt tháp (đánh từng tầng)",
                TowerMode.SWEEP: "Tự vượt tháp (quét một tầng đã thắng)"}[self]


def page_of(floor: int) -> int:
    return (floor - 1) // PER_PAGE + 1


class ThapThiLuyenParams(TaskParams):
    mode: TowerMode = Field(TowerMode.CLIMB, title="Chế độ")
    start_floor: int = Field(1, ge=1, le=MAX_FLOOR, title="Tầng bắt đầu",
                             description="Tự vượt tháp: tầng được quét (phải là tầng đã thắng)")
    end_floor: int = Field(5, ge=1, le=MAX_FLOOR, title="Tầng kết thúc", description="Chỉ dùng khi Vượt tháp")
    sweep_runs: int = Field(10, ge=1, le=100_000, title="Số lần tự vượt tháp",
                            description=f"Chỉ dùng khi Tự vượt tháp; mỗi lần tốn {TOKENS_PER_RUN} Thí luyện lệnh")
    battle_timeout_s: float = Field(300.0, ge=10.0, le=3600.0, title="Thời gian tối đa mỗi tầng (giây)",
                                    description="Không thấy Thắng/Bại sau thời gian này thì gỡ rối rồi dừng")
    max_minutes: int = Field(0, ge=0, le=24 * 60, title="Thời gian chạy tối đa (phút)",
                             description="0 = không giới hạn")

    @field_validator("end_floor")
    @classmethod
    def _not_below_start(cls, v: int, info: ValidationInfo) -> int:
        start = info.data.get("start_floor")
        if start is not None and v < start and info.data.get("mode") != TowerMode.SWEEP:
            raise ValueError(f"phải lớn hơn hoặc bằng tầng bắt đầu ({start})")
        return v


@register
class ThapThiLuyen(Feature):
    id = "thap_thi_luyen"
    title = "Tháp Thí Luyện"
    description = (
        "Vượt tháp: đánh lần lượt từ tầng bắt đầu đến tầng kết thúc, dừng khi thua. Tự vượt tháp: quét "
        "nhiều lần một tầng đã thắng. Bắt đầu từ bất kỳ bản đồ nào: bot tự mở bảng Tháp Thí Luyện"
    )
    order = 75
    Params = ThapThiLuyenParams
    entry = DIALOG

    params: ThapThiLuyenParams

    class Memory(BaseModel):
        floor: int = 1  # climb: the floor being climbed; sweep: the floor swept
        won: int = 0
        lost: int | None = None
        leaving: bool = False  # leave the floor map (a loss, or a floor out of range)
        enemy_clicks: int = 0
        marker_ticks: int = 0  # consecutive ticks with the crystal seen
        won_checked: bool = False  # sweep: the next floor is listed, so this one was won
        missing_ticks: int = 0
        swept: int = 0
        tokens_before: int | None = None

    m: Memory

    def begin(self) -> None:
        self.m = self.Memory(floor=self.params.start_floor)

    @property
    def sweeping(self) -> bool:
        return self.params.mode == TowerMode.SWEEP

    def max_run_s(self) -> float:
        return self.params.max_minutes * 60.0

    def summary(self) -> str:
        p = self.params
        if self.sweeping:
            return f"Đã tự vượt tầng {p.start_floor} {self.m.swept} lần ({self.m.swept * TOKENS_PER_RUN} Thí luyện lệnh)"
        return f"Đã thắng {self.m.won} tầng (từ tầng {p.start_floor})"

    # --- goal -----------------------------------------------------------------------------------
    def goal(self, snap: Snapshot) -> str | None:
        if snap.top != DIALOG:
            return None
        p = self.params
        tokens = snap.value(DIALOG, "tokens")
        if self.m.lost is not None:
            return f"Vượt tháp thất bại ở tầng {self.m.lost}! Hãy xếp lại trận pháp. {self.summary()}"
        if self.sweeping:
            if self.m.swept >= p.sweep_runs:
                return self.summary()
        elif self.m.floor > p.end_floor:
            return f"Đã vượt xong từ tầng {p.start_floor} đến tầng {p.end_floor}"
        if tokens is not None and tokens < TOKENS_PER_RUN:
            return f"Hết Thí luyện lệnh ({tokens}). {self.summary()}"
        return None

    # --- rules ----------------------------------------------------------------------------------
    def rules(self) -> list[Rule]:
        return [
            # the dialog: tab, page, floor, then climb or sweep
            Rule("tab Tháp", on=DIALOG, when=lambda s: s.tab != "Tháp",
                 do=lambda s: Click(TAB_THAP, label="tab Tháp", expect=lambda n: n.value(DIALOG, "tab") == "Tháp")),
            Rule("trang", on=DIALOG, when=lambda s: s.page != self._want_page(),
                 do=lambda s: Text(str(self._want_page()), at=PAGE_BOX, label=f"trang {self._want_page()}",
                                   expect=lambda n: n.value(DIALOG, "page") == self._want_page())),
            Rule("tầng có trong danh sách", on=DIALOG, do=self._check_listed),
            Rule("chọn tầng", on=DIALOG, when=lambda s: s.selected != self.m.floor, do=self._select),
            Rule("vượt tháp", on=DIALOG, when=lambda s: not self.sweeping,
                 do=lambda s: Click(s.climb, label=f"Vượt Tháp tầng {self.m.floor}", expect=CONFIRM)),
            Rule("tự vượt tháp", on=DIALOG, when=lambda s: self.sweeping,
                 do=lambda s: Click(s.sweep, label="Tự Vượt Tháp", expect=SWEEP)),
            # boxes
            Rule("xác nhận vào tháp", on=CONFIRM, when=lambda s: self._confirm_is("enter"),
                 do=lambda s: Click(s.ok, label=f"vào tầng {self.m.floor} (3 Thí luyện lệnh)", expect=FLOOR,
                                    within=20, then=self._entered)),
            Rule("xác nhận tự vượt", on=CONFIRM, when=lambda s: self._confirm_is("sweep"), do=self._confirm_sweep),
            Rule("xác nhận rời tháp", on=CONFIRM, when=lambda s: self._confirm_is("leave"),
                 do=lambda s: Click(s.ok if self.m.leaving else s.cancel,
                                    label="rời tầng" if self.m.leaving else "ở lại tầng",
                                    expect=DIALOG if self.m.leaving else FLOOR, within=10)),
            Rule("số lần", on=SWEEP, when=lambda s: self.sweeping and s.runs != self._want_runs(),
                 do=lambda s: Text(str(self._want_runs()), at=SWEEP_BOX, label="số lần",
                                   expect=lambda n: n.value(SWEEP, "runs") == self._want_runs())),
            Rule("确定 tự vượt", on=SWEEP, when=lambda s: self.sweeping and s.runs == self._want_runs() > 0,
                 do=lambda s: Click(SWEEP_OK, label=f"tự vượt {self._want_runs()} lần", expect=CONFIRM)),
            # the floor map
            Rule("tầng", on=FLOOR, do=self._floor),
            Rule("kết quả", on=RESULT, do=self._result),
        ]

    # --- dialog -----------------------------------------------------------------------------------
    def _want_page(self) -> int:
        f = self.m.floor
        if self.sweeping and not self.m.won_checked:
            return page_of(f + 1)  # the next floor's page tells that this one was won
        return page_of(f)

    def _check_listed(self, s: ScreenState) -> Action | None:
        floors = [n for n in s.floors if n is not None]
        f = self.m.floor
        if self.sweeping and not self.m.won_checked:
            if f + 1 in floors:
                self.m.won_checked = True
                return None if page_of(f) == s.page else Wait(0.1, label="tầng đã thắng")
            return self._missing(floors, f"Tầng {f} chưa thắng: chỉ tự vượt tháp được tầng đã thắng")
        if f in floors:
            self.m.missing_ticks = 0
            return None
        return self._missing(floors, f"Tầng {f} chưa mở")

    def _missing(self, floors: list[int], message: str) -> Action:
        """The floor list is read twice before the run gives up on it."""
        self.m.missing_ticks += 1
        if self.m.missing_ticks < 3:
            return Wait(0.4, label="đọc lại danh sách tầng")
        top = max(floors) if floors else None
        return Fail(message + (f" (trang này có tầng {min(floors)}-{top})" if floors else ""))

    def _select(self, s: ScreenState) -> Action | None:
        row = next((r for r in s.rows if r.number == self.m.floor), None)
        if row is None:
            return None
        return Click(row.center, label=f"tầng {self.m.floor}",
                     expect=lambda n: n.value(DIALOG, "selected") == self.m.floor)

    def _confirm_is(self, what: str) -> bool:
        st = self.snap.stack if self.snap is not None else ()
        if FLOOR in st:
            return what == "leave"
        if SWEEP in st:
            return what == "sweep" and self.sweeping
        return DIALOG in st and what == "enter" and not self.sweeping

    def _want_runs(self) -> int:
        tokens = self.snap.value(DIALOG, "tokens") if self.snap is not None else None
        left = self.params.sweep_runs - self.m.swept
        if tokens is not None:
            left = min(left, tokens // TOKENS_PER_RUN)
        return max(0, left)

    def _confirm_sweep(self, s: ScreenState) -> Action:
        self.m.tokens_before = self.snap.value(DIALOG, "tokens") if self.snap is not None else None
        return Click(s.ok, label=f"确定 tự vượt tầng {self.m.floor}", within=6,
                     expect=lambda n: n.top == DIALOG, change=lambda n: n.value(DIALOG, "tokens"),
                     then=self._swept)

    def _swept(self) -> None:
        now = self.snap.value(DIALOG, "tokens") if self.snap is not None else None
        before = self.m.tokens_before
        if now is None or before is None or now >= before:
            return
        runs = (before - now) // TOKENS_PER_RUN
        self.m.swept += runs
        self.actor.count("sweeps", runs)
        self.actor.progress(self.m.swept, self.params.sweep_runs)
        self.actor.log(f"Tự vượt tầng {self.m.floor}: {runs} lần (Thí luyện lệnh {before} → {now})")

    # --- floor map --------------------------------------------------------------------------------
    def _entered(self) -> None:
        self.m.enemy_clicks = 0
        self.m.marker_ticks = 0
        self.m.leaving = False

    def _floor(self, s: ScreenState) -> Action:
        p = self.params
        n = s.floor
        if not self.m.leaving and n is not None and n != self.m.floor:
            if self.sweeping or not (p.start_floor <= n <= p.end_floor):
                self.actor.log(f"Đang ở tầng {n} (không thuộc việc cần làm): rời tầng", "warning")
                self.m.leaving = True
            else:
                self.actor.log(f"Đang ở tầng {n}: đánh tiếp tầng này")
                self.m.floor = n
        if self.sweeping:
            self.m.leaving = True
        if self.m.leaving:
            return Click(s.exit, label="Thoát tầng", expect=lambda n: n.top != FLOOR, within=5)
        if not s.auto_climb:
            return Click(AUTO_TICK, label="☑ Tự Vượt Tháp", expect=lambda n: n.value(FLOOR, "auto_climb") is True)
        if s.started or self.m.enemy_clicks >= ENEMY_CLICKS:
            return Wait(1.0, label=f"đang đánh tầng {self.m.floor}", patience=p.battle_timeout_s)
        marks = [r for r in s.marker if CRYSTAL_W[0] <= r.w <= CRYSTAL_W[1] and CRYSTAL_H[0] <= r.h <= CRYSTAL_H[1]]
        self.m.marker_ticks = self.m.marker_ticks + 1 if marks else 0
        if self.m.marker_ticks < 2:  # the crystal flickers: seen on 2 ticks in a row
            return Wait(0.3, label="tìm pha lê trên lính canh", patience=20)
        r = max(marks, key=lambda b: b.bottom)  # the path starts at the bottom: the lowest one is the first guard's
        guard = Point(r.center.x, min(590, r.bottom + GUARD_BELOW_MARKER))
        self.m.enemy_clicks += 1
        return Click(guard, label=f"lính canh đầu tiên ({self.m.enemy_clicks})", within=10,
                     expect=lambda n: n.value(FLOOR, "started") is True or n.has(RESULT))

    def _result(self, s: ScreenState) -> Action:
        if s.result == "win":
            return Click(s.exit, label=f"thắng tầng {self.m.floor}: Thoát", expect=DIALOG, within=10, then=self._won)
        if s.result == "lose":
            if self.m.lost is None:
                self.actor.log(f"Thua ở tầng {self.m.floor}", "warning")
            self.m.lost, self.m.leaving = self.m.floor, True
            return Click(s.exit, label=f"thua tầng {self.m.floor}: Thoát", expect=lambda n: n.top != RESULT, within=5)
        return Wait(0.3, label="đọc kết quả")

    def _won(self) -> None:
        self.m.won += 1
        self.actor.count("floors")
        self.actor.log(f"Thắng tầng {self.m.floor}")
        p = self.params
        self.actor.progress(self.m.won, p.end_floor - p.start_floor + 1)
        self.m.floor += 1
