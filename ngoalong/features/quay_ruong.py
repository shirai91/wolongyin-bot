"""Quay Tinh Tú: open the Tinh Tú lucky chests, optionally buying chests first (docs/08 "Feature
flows" 5; rule-based since R2).

The rules work on the Tinh Tú dialog (``chest.wheel``); the loop opens it from any map through
the top icon rows (``nav.yaml``) and closes whatever dialog is in the way.

* **Buy** (setting ``buy``): Mua → quantity box (double-click selects the number, type over it;
  an emptied box resets to 1) → 确定 → the spend-Vàng box 确定 (shown after a browser reload, its
  "don't remind" box is never ticked); the chest count must rise. Three failed tries (not enough Vàng, ...) give up buying and go on opening.
* **Open, fast**: the user's trick, verified live: Tự mở → 确定 → Ngưng mở at once opens one chest
  per cycle in ~1.5 s instead of one every ~4 s with auto-open left on. The last chest of a
  requested number is opened with 开始抽奖 (exactly one).
* **Count**: every drop of the chest count is a chest opened; the run ends when the count reads 0
  or the requested number is opened.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from ngoalong.core.geometry import Point
from ngoalong.engine.actions import Action, Click, Text
from ngoalong.engine.feature import Feature, Rule
from ngoalong.engine.registry import register
from ngoalong.engine.task import TaskParams
from ngoalong.perception import Snapshot
from ngoalong.perception.readers import ScreenState

WHEEL = "chest.wheel"
BUY = "chest.buy"
AUTO = "chest.auto_open_confirm"
GOLD = "confirm.gold"  # "Dùng 240 vàng, mua N Rương may mắn" (asked again after a browser reload)
MUA = Point(783, 143)  # "Mua" next to the chest count (large dialog, fixed place)
QTY_BOX = Point(437, 286)  # quantity box of the buy / auto-open boxes
BUY_TRIES = 3
VANG_PER_CHEST = 240


class QuayRuongParams(TaskParams):
    opens: int = Field(0, ge=0, le=100_000, title="Số rương cần mở",
                       description="0 = mở đến khi hết rương")
    buy: int = Field(0, ge=0, le=10_000, title="Mua thêm rương trước khi mở",
                     description=f"{VANG_PER_CHEST} Vàng mỗi rương; 0 = không mua")
    max_minutes: int = Field(0, ge=0, le=24 * 60, title="Thời gian chạy tối đa (phút)",
                             description="0 = không giới hạn")


def _chests(snap: Snapshot) -> int | None:
    return snap.value(WHEEL, "chests")


@register
class QuayRuong(Feature):
    id = "quay_ruong"
    title = "Quay Tinh Tú"
    description = ("Mở rương may mắn Tinh Tú (bật/tắt Tự mở liên tục: ~1,5 giây mỗi rương), có thể mua thêm "
                   "rương trước. Bắt đầu từ bất kỳ bản đồ nào: bot tự mở bảng Tinh Tú")
    order = 50
    Params = QuayRuongParams
    entry = WHEEL
    answers = frozenset({GOLD})

    params: QuayRuongParams

    class Memory(BaseModel):
        opened: int = 0
        bought: int = 0
        buy_tries: int = 0
        buy_given_up: bool = False
        chests: int | None = None  # last count read

    m: Memory

    # --- memory ---------------------------------------------------------------------------------
    def _track(self, snap: Snapshot) -> int | None:
        """Read the chest count; every drop since the last read is a chest opened."""
        n = _chests(snap)
        if n is None:
            return self.m.chests
        last = self.m.chests
        if last is not None and n < last:
            self.m.opened += last - n
            self.actor.count("chests", last - n)
            self.actor.progress(self.m.opened, self.params.opens or None)
        self.m.chests = n
        return n

    def _buying(self) -> bool:
        return self.m.bought < self.params.buy and not self.m.buy_given_up

    def _remaining(self) -> int:
        """Chests still to open (the whole count when no number is set)."""
        if self.params.opens:
            return self.params.opens - self.m.opened
        return self.m.chests or 0

    def goal(self, snap: Snapshot) -> str | None:
        n = self._track(snap)
        if snap.top != WHEEL or n is None or snap.value(WHEEL, "auto_open") or self._buying():
            return None
        if self.params.opens and self.m.opened >= self.params.opens:
            return self.summary()
        if n == 0:
            return self.summary() + " (hết rương)"
        return None

    def summary(self) -> str:
        s = f"Đã mở {self.m.opened} rương"
        if self.m.bought:
            s += f", mua {self.m.bought} rương ({self.m.bought * VANG_PER_CHEST} Vàng)"
        return s

    def max_run_s(self) -> float:
        return self.params.max_minutes * 60.0

    # --- rules ----------------------------------------------------------------------------------
    def rules(self) -> list[Rule]:
        return [
            Rule("mua rương", on=WHEEL, when=lambda s: not s.auto_open and self._buying(), do=self._open_buy),
            Rule("số lượng mua", on=BUY, when=lambda s: self._buying() and s.qty != self._want_buy(),
                 do=lambda s: Text(str(self._want_buy()), at=QTY_BOX, clear=0, label="số rương mua",
                                   within=2, expect=lambda n: n.value(BUY, "qty") == self._want_buy())),
            Rule("xác nhận mua", on=BUY, when=lambda s: self._buying() and s.qty == self._want_buy(),
                 do=lambda s: Click(s.ok, label=f"mua {self._want_buy()} rương", within=5,
                                    expect=lambda n: n.has(GOLD) or self._more_chests(n), then=self._bought)),
            Rule("xác nhận Vàng", on=GOLD, when=lambda s: self._buying() and WHEEL in self.snap.stack,
                 do=lambda s: Click(s.ok, label=f"dùng {self._want_buy() * VANG_PER_CHEST} Vàng", within=5,
                                    expect=self._more_chests, then=self._bought)),
            Rule("ngưng mở", on=WHEEL, when=lambda s: s.auto_open,
                 do=lambda s: Click(s.auto_button, label="Ngưng mở", within=3, settle=0.2,
                                    expect=lambda n: n.value(WHEEL, "auto_open") is False)),
            Rule("tự mở", on=WHEEL, when=lambda s: (s.chests or 0) > 0 and self._remaining() >= 2,
                 do=lambda s: Click(s.auto_button, label="Tự mở", expect=AUTO, within=2, settle=0.15)),
            Rule("xác nhận tự mở", on=AUTO, when=lambda s: self._remaining() >= 2 and not self._buying(),
                 do=lambda s: Click(s.ok, label="确定 tự mở", within=2, settle=0.2,
                                    expect=lambda n: n.top == WHEEL)),
            Rule("mở một rương", on=WHEEL, when=lambda s: (s.chests or 0) > 0 and self._remaining() == 1,
                 do=lambda s: Click(s.spin, label="开始抽奖", change=_chests, within=8)),
        ]

    def _more_chests(self, snap: Snapshot) -> bool:
        n = _chests(snap)
        return snap.top == WHEEL and n is not None and n > (self.m.chests or 0)

    def _want_buy(self) -> int:
        return self.params.buy - self.m.bought

    def _open_buy(self, s: ScreenState) -> Action | None:
        if self.m.buy_tries >= BUY_TRIES:
            self.m.buy_given_up = True
            self.actor.log(f"Mua rương không được sau {BUY_TRIES} lần (thiếu Vàng?); chỉ mở rương đang có",
                           "warning")
            return None
        self.m.buy_tries += 1
        return Click(MUA, label="Mua", expect=BUY)

    def _bought(self) -> None:
        before, now = self.m.chests, _chests(self.snap) if self.snap is not None else None
        if before is not None and now is not None and now > before:
            self.m.bought += now - before
            self.m.chests = now
            self.actor.count("chests_bought", now - before)
            self.actor.log(f"Đã mua {now - before} rương (còn {now})")

    @classmethod
    def import_legacy(cls, ini: Mapping[str, str]) -> dict[str, Any]:
        try:
            value = int(ini.get("QR_SOLUONG", "").strip())
        except ValueError:
            return {}
        return {"opens": value} if value >= 1 else {}
