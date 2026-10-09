"""Nâng cấp khoa kỹ: upgrade the research techs of Phủ Sách Lược (docs/08 "Feature flows" 1;
rule-based since R3).

The rules work on the ``research`` dialog (city bottom bar "Khoa kỹ"; the loop opens it from any
map and closes whatever is in the way). The dialog reopens on its last tab and slot, so both are
read, never assumed.

* **Tab** "Khoa kỹ" (gold = selected), then the 18 slots of the 6×3 grid in turn (the user's list,
  else all). The selected slot has a light frame (``research.slot``); its Cấp is read with the digit
  glyphs (``research.level``).
* **Press** "Tăng cấp": success raises Cấp. A refusal shows a red floating line ("Cấp Phủ sách lược
  không đủ, không thể thăng cấp!", ``floating_error``) or changes nothing: the slot is capped for
  this run and the next one is taken. Up to ``upgrades_per_tech`` levels per slot.
* The red line stays ~3.5 s, so one still showing from the previous slot is not taken for this
  slot's answer: then only the time limit (no new Cấp after ``PRESS_WAIT_S``) tells a refusal.
* The cap of every tech is the level of the **Phủ Sách Lược building** (Y/C "Phủ S.Lược lvN"):
  upgrade it with "Nâng cấp công trình" to unlock more levels.
* A spend-Vàng box is answered like the structures' one (setting "Dùng vàng bỏ qua thời gian chờ").
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ngoalong.core.geometry import Point
from ngoalong.engine.actions import Action, Click, Wait, or_after
from ngoalong.engine.feature import Feature, Rule
from ngoalong.engine.registry import register
from ngoalong.engine.task import TaskParams
from ngoalong.perception import Snapshot
from ngoalong.perception.readers import ScreenState

DIALOG = "research"
ERROR = "floating_error"  # red line over the grid after a refused Tăng cấp (~2 s)
GOLD = "confirm.gold"
GOLD_POLICY = "spend_gold_skip_wait"
TAB = "Khoa kỹ"
TAB_POINT = Point(220, 157)  # first tab of the medium dialog
SLOTS = 18  # 6 x 3, no scrolling
PRESS_WAIT_S = 1.5  # a press with no new Cấp after this long was refused (a new Cấp shows in ~0.4 s)


def parse_slots(text: str) -> list[int]:
    """``"1-3, 7"`` -> ``[1, 2, 3, 7]`` (1-based, sorted, unique); ``""`` -> ``[]`` (= all)."""
    out: set[int] = set()
    for raw in text.replace(";", ",").split(","):
        part = raw.strip()
        if not part:
            continue
        lo_s, sep, hi_s = part.partition("-")
        try:
            lo, hi = int(lo_s), int(hi_s) if sep else int(lo_s)
        except ValueError:
            raise ValueError(f"'{part}' không phải số hoặc khoảng số (ví dụ 1-8,12)") from None
        if not 1 <= lo <= hi <= SLOTS:
            raise ValueError(f"'{part}': ô khoa kỹ đánh số 1-{SLOTS}, khoảng viết từ nhỏ đến lớn")
        out.update(range(lo, hi + 1))
    return sorted(out)


class KhoaKyParams(TaskParams):
    upgrades_per_tech: int = Field(1, ge=1, le=10_000, title="Số cấp nâng tối đa mỗi ô khoa kỹ",
                                   description="Ô đã tối đa (cần nâng Phủ Sách Lược) thì bỏ qua")
    slots: str = Field("", title="Các ô khoa kỹ",
                       description=f"Để trống = cả {SLOTS} ô, đánh số theo hàng từ trái sang (ví dụ 1-8)")
    max_minutes: int = Field(0, ge=0, le=24 * 60, title="Thời gian chạy tối đa (phút)",
                             description="0 = không giới hạn")

    @field_validator("slots")
    @classmethod
    def _check_slots(cls, v: str) -> str:
        parse_slots(v)
        return v.strip()

    def targets(self) -> list[int]:
        """0-based slot indices, in order."""
        return [n - 1 for n in parse_slots(self.slots)] or list(range(SLOTS))


@register
class KhoaKy(Feature):
    id = "khoa_ky"
    title = "Nâng cấp khoa kỹ"
    description = (
        "Mở Phủ Sách Lược (tab Khoa kỹ) và nâng cấp lần lượt các ô khoa kỹ; ô nào đã tối đa thì bỏ qua. "
        "Bắt đầu từ bất kỳ màn hình nào"
    )
    order = 60
    Params = KhoaKyParams
    entry = DIALOG
    answers = frozenset({GOLD})

    params: KhoaKyParams

    class Memory(BaseModel):
        queue: list[int] = []  # slots still to do (0-based), current first
        ups: dict[int, int] = {}  # levels gained per slot
        first: dict[int, int] = {}  # Cấp when the slot was first read
        capped: list[int] = []  # refused (red line) or no change
        level_before: int | None = None
        error_before: bool = False  # the red line of the previous slot was still showing at the press
        gold_paid: bool = False
        no_builder: bool = False

    m: Memory

    def begin(self) -> None:
        self.m = self.Memory(queue=self.params.targets())

    def max_run_s(self) -> float:
        return self.params.max_minutes * 60.0

    # --- goal --------------------------------------------------------------------------------------
    def goal(self, snap: Snapshot) -> str | None:
        if snap.top != DIALOG:
            return None
        if not self.m.queue or self.m.no_builder:
            return self.summary()
        return None

    def summary(self) -> str:
        total = sum(self.m.ups.values())
        s = f"Nâng {total} cấp khoa kỹ"
        if total:
            s += " (" + ", ".join(f"ô {k + 1}: {self.m.first.get(k, '?')}→{self.m.first.get(k, 0) + n}"
                                  for k, n in sorted(self.m.ups.items()) if n) + ")"
        if self.m.capped:
            s += f"; {len(self.m.capped)} ô đã tối đa ({', '.join(str(k + 1) for k in sorted(self.m.capped))})"
            if not total:
                s += ": nâng cấp công trình Phủ Sách Lược để mở thêm cấp"
        if self.m.no_builder:
            s += "; dừng vì game hỏi dùng Vàng (\"Dùng vàng bỏ qua thời gian chờ\" đang tắt ở thẻ Cài đặt)"
        return s

    # --- rules ---------------------------------------------------------------------------------
    def rules(self) -> list[Rule]:
        return [
            Rule("tab Khoa kỹ", on=DIALOG, when=lambda s: s.tab != TAB,
                 do=lambda s: Click(TAB_POINT, label="tab Khoa kỹ", expect=lambda n: n.value(DIALOG, "tab") == TAB)),
            Rule("chọn ô", on=DIALOG, when=lambda s: bool(self.m.queue) and s.slot != self.m.queue[0],
                 do=self._select),
            Rule("tăng cấp", on=DIALOG, when=lambda s: bool(self.m.queue), do=self._press),
            Rule("hỏi Vàng", on=GOLD, when=lambda s: DIALOG in self.snap.stack, do=self._gold),
        ]

    def _select(self, s: ScreenState) -> Action | None:
        k = self.m.queue[0]
        slot = next((x for x in s.slots if x.index == k), None)
        if slot is None:
            return None
        return Click(slot.center, label=f"ô {k + 1}", within=2, expect=lambda n: n.value(DIALOG, "slot") == k)

    def _press(self, s: ScreenState) -> Action:
        k = self.m.queue[0]
        level = s.level
        if level is None:
            return Wait(0.2, label="đọc Cấp", patience=6)
        self.m.first.setdefault(k, level)
        if self.m.ups.get(k, 0) >= self.params.upgrades_per_tech:
            self._next(f"ô {k + 1}: đã nâng {self.m.ups[k]} cấp")
            return Wait(0.05, label="ô tiếp theo")
        self.m.level_before = level
        self.m.error_before = self.snap.has(ERROR)
        return Click(s.upgrade, label=f"Tăng cấp ô {k + 1} cấp {level}", within=PRESS_WAIT_S + 2,
                     expect=or_after(self._answered, PRESS_WAIT_S), then=self._pressed)

    def _answered(self, snap: Snapshot) -> bool:
        if snap.has(GOLD) or (snap.has(ERROR) and not self.m.error_before):
            return True
        level = snap.value(DIALOG, "level")
        return level is not None and level != self.m.level_before

    def _pressed(self) -> None:
        snap = self.snap
        if snap is None or snap.has(GOLD):
            return  # answered by the "hỏi Vàng" rule
        k = self.m.queue[0]
        paid, self.m.gold_paid = self.m.gold_paid, False
        level = snap.value(DIALOG, "level")
        if level is not None and self.m.level_before is not None and level > self.m.level_before:
            self.m.ups[k] = self.m.ups.get(k, 0) + level - self.m.level_before
            self.actor.count("levels", level - self.m.level_before)
            self.actor.progress(sum(self.m.ups.values()))
            return
        if paid:
            return  # the Vàng may only have cleared a wait: press again
        self.m.capped.append(k)
        why = "Cấp Phủ sách lược không đủ" if snap.has(ERROR) else "Cấp không đổi (đã tối đa?)"
        self._next(f"ô {k + 1} cấp {self.m.level_before}: {why}; bỏ qua")

    def _next(self, message: str) -> None:
        self.actor.log(message)
        self.m.queue.pop(0)

    def _gold(self, s: ScreenState) -> Action:
        if self.actor.policy(GOLD_POLICY):
            self.m.gold_paid = True
            return Click(s.ok, label="dùng Vàng", within=PRESS_WAIT_S + 2,
                         expect=or_after(lambda n: not n.has(GOLD) and self._answered(n), PRESS_WAIT_S),
                         then=self._pressed)
        self.m.no_builder = True
        self.actor.log("Game hỏi dùng Vàng; \"Dùng vàng bỏ qua thời gian chờ\" đang tắt (thẻ Cài đặt) nên "
                       "bấm Huỷ và dừng", "warning")
        return Click(s.cancel, label="không dùng Vàng", within=3, expect=lambda n: not n.has(GOLD))
