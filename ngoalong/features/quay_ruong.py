"""Quay Tinh Tú: open the Tinh Tú lucky chests, optionally buying chests first (docs/08 "Feature
flows" 5; rule-based since R2).

The rules work on the Tinh Tú dialog (``chest.wheel``); the loop opens it from any map through
the top icon rows (``nav.yaml``) and closes whatever dialog is in the way. They live in
``ChestJob`` so that the Võ Khôi Tháp loop opens chests the same way when Quân lệnh runs out.

* **Buy** (setting ``buy``): Mua → quantity box (``Text``: End, Backspaces, the number, Deletes; an
  emptied box puts "1" back in front of the caret, the Deletes remove it; read back, typed again
  while wrong, never confirmed wrong) → 确定 → the spend-Vàng box 确定 (shown after a browser reload, its
  "don't remind" box is never ticked); the chest count must rise. Three failed tries (not enough Vàng, ...) give up buying and go on opening.
* **Open, fast**: the user's trick (Tự mở → 确定 → Ngưng mở opens one chest per cycle), done in
  **bursts**: one ``Burst`` action clicks the three points ``batch`` cycles over (at most the
  chests left), ``click_ms`` apart, without looking; then the loop looks again. The count drops
  ~1–1.5 s after the burst (the server answers late), so the burst's expectation waits until the
  count reached ``before − cycles``, or stopped falling for a second. A confirm box left over by a
  missed click is closed (✕), auto-open left running is stopped (Ngưng mở), and the next burst
  takes what is left. Measured 2026-10-10: every cycle opened its chest with the clicks posted 0, 5,
  10 or 20 ms apart (50 of 50 each).
* **Count**: every drop of the chest count is a chest opened; the run ends when the count reads 0
  or the requested number is opened.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from ngoalong.core.geometry import Point
from ngoalong.engine.actions import Action, Burst, Click, Text
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
AUTO_OK = Point(438, 364)  # 确定 of the "Tự mở N Rương may mắn không?" box
AUTO_CLOSE = Point(638, 227)  # its ✕
BATCH = 50  # chests per burst (default)
CLICK_MS = 5  # between the clicks of a burst (default)
QUIET_S = 1.0  # the count has not moved this long after a burst: every chest it opened is counted
BUY_TRIES = 3
VANG_PER_CHEST = 240


class QuayRuongParams(TaskParams):
    opens: int = Field(0, ge=0, le=100_000, title="Số rương cần mở",
                       description="0 = mở đến khi hết rương")
    buy: int = Field(0, ge=0, le=10_000, title="Mua thêm rương trước khi mở",
                     description=f"{VANG_PER_CHEST} Vàng mỗi rương; 0 = không mua")
    batch: int = Field(BATCH, ge=1, le=500, title="Số rương mỗi đợt mở nhanh",
                       description="mỗi đợt bấm Tự mở → 确定 → Ngưng mở liền N lần rồi đọc lại số rương")
    click_ms: int = Field(CLICK_MS, ge=0, le=1000, title="Khoảng cách giữa các click khi mở nhanh (ms)")
    max_minutes: int = Field(0, ge=0, le=24 * 60, title="Thời gian chạy tối đa (phút)",
                             description="0 = không giới hạn")


def chests_of(snap: Snapshot) -> int | None:
    return snap.value(WHEEL, "chests")


class ChestState(BaseModel):
    """What one chest job did (part of a feature's Memory)."""

    opened: int = 0
    bought: int = 0
    buy_tries: int = 0
    buy_given_up: bool = False
    chests: int | None = None  # last count read


class ChestJob:
    """The Tinh Tú wheel rules: buy ``buy()`` chests, then open ``opens()`` (0 = every chest) in
    bursts of ``speed()``.

    ``state`` returns the ChestState to work on (a feature may start a new one per job); ``active``
    switches the rules off while the feature does something else. The feature calls ``track`` from
    its goal (every tick) and asks ``finished``.
    """

    def __init__(self, feature: Feature, state: Callable[[], ChestState], opens: Callable[[], int],
                 buy: Callable[[], int], active: Callable[[], bool] = lambda: True,
                 speed: Callable[[], tuple[int, int]] = lambda: (BATCH, CLICK_MS)) -> None:
        self.f = feature
        self.state = state
        self.opens = opens
        self.buy = buy
        self.active = active
        self.speed = speed  # (chests per burst, ms between its clicks)

    # --- memory ---------------------------------------------------------------------------------
    def track(self, snap: Snapshot) -> int | None:
        """Read the chest count; every drop since the last read is a chest opened."""
        st = self.state()
        n = chests_of(snap)
        if n is None:
            return st.chests
        last = st.chests
        if last is not None and n < last:
            st.opened += last - n
            self.f.actor.count("chests", last - n)
            self.f.actor.progress(st.opened, self.opens() or None)
        st.chests = n
        return n

    def buying(self) -> bool:
        st = self.state()
        return st.bought < self.buy() and not st.buy_given_up

    def remaining(self) -> int:
        """Chests still to open (the whole count when no number is set)."""
        st = self.state()
        if self.opens():
            return self.opens() - st.opened
        return st.chests or 0

    def finished(self, snap: Snapshot) -> bool:
        """The job is done: on the wheel, nothing to buy, 0 left, or the number opened with auto-open
        off. At 0 chests the button may still read "Ngưng mở" (it opens nothing then, and a click
        on it only shows "Không đủ Rương may mắn"), so it is not looked at."""
        n = chests_of(snap)
        if snap.top != WHEEL or n is None or self.buying():
            return False
        if n == 0:
            return True
        return bool(self.opens() and self.state().opened >= self.opens()) and not snap.value(WHEEL, "auto_open")

    # --- rules ----------------------------------------------------------------------------------
    def rules(self) -> list[Rule]:
        on = self.active
        return [
            Rule("mua rương", on=WHEEL, when=lambda s: on() and (not s.auto_open or s.chests == 0) and self.buying(),
                 do=self._open_buy),
            Rule("số lượng mua", on=BUY, when=lambda s: on() and self.buying() and s.qty != self._want_buy(),
                 do=lambda s: Text(str(self._want_buy()), at=QTY_BOX, label="số rương mua",
                                   within=2, expect=lambda n: n.value(BUY, "qty") == self._want_buy())),
            Rule("xác nhận mua", on=BUY, when=lambda s: on() and self.buying() and s.qty == self._want_buy(),
                 do=lambda s: Click(s.ok, label=f"mua {self._want_buy()} rương", within=5,
                                    expect=lambda n: n.has(GOLD) or self._more_chests(n), then=self._bought)),
            Rule("xác nhận Vàng", on=GOLD, when=lambda s: on() and self.buying() and WHEEL in self.f.snap.stack,
                 do=lambda s: Click(s.ok, label=f"dùng {self._want_buy() * VANG_PER_CHEST} Vàng", within=5,
                                    expect=self._more_chests, then=self._bought)),
            Rule("ngưng mở", on=WHEEL, when=lambda s: on() and s.auto_open and (s.chests or 0) > 0,
                 do=lambda s: Click(s.auto_button, label="Ngưng mở", within=3, settle=0.2,
                                    expect=lambda n: n.value(WHEEL, "auto_open") is False)),
            # a box a burst left open (rare: the expectation waits until the clicks are worked off):
            # 确定 is the cycle's next click (one more chest, then "ngưng mở"); with none left, ✕
            Rule("hộp tự mở còn lại", on=AUTO, when=lambda s: on() and not self.buying(),
                 do=lambda s: Click(AUTO_OK, label="确定 hộp Tự mở còn lại", within=3, settle=0.2,
                                    expect=lambda n: n.top == WHEEL)
                 if (chests_of(self.f.snap) or 0) > 0 else
                 Click(AUTO_CLOSE, label="✕ hộp Tự mở còn lại", within=3, expect=lambda n: n.top == WHEEL)),
            Rule("mở nhanh", on=WHEEL,
                 when=lambda s: on() and (s.chests or 0) > 0 and self.remaining() >= 1 and not self.buying(),
                 do=self._burst),
        ]

    def _burst(self, s: ScreenState) -> Action:
        """One burst of the toggle trick on as many chests as are left (at most ``batch``)."""
        batch, click_ms = self.speed()
        before = int(s.chests)
        k = max(1, min(batch, before, self.remaining()))
        t0 = time.monotonic()
        seen = {"n": before, "at": t0}

        def settled(snap: Snapshot) -> bool:
            """The game has worked off the clicks: every chest counted and the wheel clean, or the
            count stopped moving (a missed click; the rules then tidy up and open the rest)."""
            n = chests_of(snap)
            if n is None:
                return False
            now = time.monotonic()
            if n != seen["n"]:
                seen["n"], seen["at"] = n, now
            clean = snap.top == WHEEL and not snap.value(WHEEL, "auto_open")
            quiet = now - seen["at"]
            return (n <= before - k and clean) or (n < before and quiet >= QUIET_S) or quiet >= 3 * QUIET_S

        def report() -> None:
            after = chests_of(self.f.snap) if self.f.snap is not None else None
            if after is None:
                return
            opened = before - after
            line = f"Mở nhanh {k} lượt: rương {before} → {after} (mở {opened}, {time.monotonic() - t0:.1f}s)"
            if opened < k:
                line += f", {k - opened} lượt không mở"
            self.f.actor.log(line)

        return Burst((s.auto_button, AUTO_OK, s.auto_button), times=k, interval=click_ms / 1000,
                     label="Tự mở → 确定 → Ngưng mở", within=10, settle=0.1, expect=settled, then=report)

    def _more_chests(self, snap: Snapshot) -> bool:
        n = chests_of(snap)
        return snap.top == WHEEL and n is not None and n > (self.state().chests or 0)

    def _want_buy(self) -> int:
        return self.buy() - self.state().bought

    def _open_buy(self, s: ScreenState) -> Action | None:
        st = self.state()
        if st.buy_tries >= BUY_TRIES:
            st.buy_given_up = True
            self.f.actor.log(f"Mua rương không được sau {BUY_TRIES} lần (thiếu Vàng?); chỉ mở rương đang có",
                             "warning")
            return None
        st.buy_tries += 1
        return Click(MUA, label="Mua", expect=BUY)

    def _bought(self) -> None:
        st = self.state()
        before, now = st.chests, chests_of(self.f.snap) if self.f.snap is not None else None
        if before is not None and now is not None and now > before:
            st.bought += now - before
            st.chests = now
            self.f.actor.count("chests_bought", now - before)
            self.f.actor.log(f"Đã mua {now - before} rương (còn {now})")


@register
class QuayRuong(Feature):
    id = "quay_ruong"
    title = "Quay Tinh Tú"
    description = ("Mở rương may mắn Tinh Tú (bật/tắt Tự mở liên tục theo từng đợt, khoảng 20 rương mỗi giây), "
                   "có thể mua thêm rương trước. Bắt đầu từ bất kỳ bản đồ nào: bot tự mở bảng Tinh Tú")
    order = 50
    Params = QuayRuongParams
    entry = WHEEL
    answers = frozenset({GOLD})

    params: QuayRuongParams

    class Memory(BaseModel):
        chest: ChestState = ChestState()

    m: Memory

    def __init__(self, params: Any = None) -> None:
        super().__init__(params)
        self.job = ChestJob(self, lambda: self.m.chest, lambda: self.params.opens, lambda: self.params.buy,
                            speed=lambda: (self.params.batch, self.params.click_ms))

    def goal(self, snap: Snapshot) -> str | None:
        self.job.track(snap)
        if not self.job.finished(snap):
            return None
        return self.summary() + ("" if self.params.opens and self.m.chest.opened >= self.params.opens
                                 else " (hết rương)")

    def summary(self) -> str:
        st = self.m.chest
        s = f"Đã mở {st.opened} rương"
        if st.bought:
            s += f", mua {st.bought} rương ({st.bought * VANG_PER_CHEST} Vàng)"
        return s

    def max_run_s(self) -> float:
        return self.params.max_minutes * 60.0

    def rules(self) -> list[Rule]:
        return self.job.rules()
