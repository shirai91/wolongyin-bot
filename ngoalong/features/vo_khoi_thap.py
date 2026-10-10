"""Võ Khôi Tháp: fight a tower boss again and again, a long loop the game does not automate (docs/08
"Feature flows" 6; rule-based since R3; v1.8.0 ``BOSS_VOKHOITHAP`` and its helpers).

The loop works on the full-screen tower screens; the decision loop walks there from any map
(world map → the pagoda over the "Võ Khôi Tháp" label, ``nav.yaml``) and closes what is in the way.

* **A round**: lobby (boss, "Lần này tiêu hao Quân Lệnh N": the cost of this round, read every
  round; it is not a boss property: it rises by 1 per round and resets each day) → Tấn công → team list → Lập
  đội → team room → Khai chiến → arena (countdown, three magic circles) → **click a magic circle**
  to join; the player walks to the boss and the fight takes seconds. A circle that does not join
  within ``CIRCLE_WAIT_S`` (R1 saw circles 2 and 3 do nothing) is followed by the next one while the
  countdown runs and the boss HP bar stays full. A round whose countdown ran out is left with
  Thoát → 确定 (a loss). The result box (``vkt.result``: 胜利 / 失败; never the HP bar, empty after
  both) → 确定 → world map → next round. A loss is just counted: the next round retries.
* **Boss**: setting ``boss`` = the N-th boss (‹ until the lobby text stops changing, then › N-1
  times; the lobby reopens on the last boss), 0 = keep the boss shown.
* **Silver**: when the HUD silver reads above 2,000,000,000 (the game also shows an alert then),
  buy ``silver_chests`` chests of 1,000,000,000: bottom bar Trang bị → tab Cửa hiệu → ▼ until the
  grid stops changing (it keeps its scroll between opens) → the last chest of the bottom row →
  "Bạc cần" must read 1000000000 → Mua (no confirm; a confirm box, if the game shows one, is
  answered 确定 because the shop takes silver) → the silver must drop by 1e9 → again → ✕. Any box
  that shows up on the way is saved in the run folder (``event-NN-*.png``) to be labelled.
* **Quân lệnh**: below the round's cost (HUD "Lện N/100"; n rounds in a day cost n(n+1)/2) → Tinh Tú: open every chest the fast way
  (``ChestJob``, as Quay Tinh Tú); with none left, buy ``chest_quantity`` chests if
  ``buy_chests`` is on (240 Vàng each), else the run ends. A refill that adds no Quân lệnh ends the
  run (no endless buying).
* A frozen client (it froze on a 失败 box once) is caught by the loop's watchdog: the run fails with
  the reload message.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from enum import IntEnum
from typing import Any

import cv2
import numpy as np
from pydantic import BaseModel, Field

from ngoalong.core.geometry import Point, Rect
from ngoalong.engine.actions import Action, Click, Fail, Wait, or_after
from ngoalong.engine.feature import Feature, Rule
from ngoalong.engine.registry import register
from ngoalong.engine.task import TaskParams
from ngoalong.features.quay_ruong import BATCH, CLICK_MS, VANG_PER_CHEST, ChestJob, ChestState
from ngoalong.perception import Snapshot
from ngoalong.perception.readers import ScreenState
from ngoalong.perception.screens import UNKNOWN

WORLD = "world_map"
LOBBY = "vkt.lobby"
TEAMS = "vkt.team_list"
ROOM = "vkt.team_room"
ARENA = "vkt.arena"
RESULT = "vkt.result"
WHEEL = "chest.wheel"
ALERT = "vkt.silver_alert"  # inside the 胜利 box: "Bạc đã đạt tối đa, số vượt quá 2 tỷ đã bị ác tặc cướp mất"
SHOP = "inventory"
CONFIRM = "confirm.ok_cancel"
GOLD = "confirm.gold"
NOTICE = "notice.ok"
BOXES = (CONFIRM, GOLD, NOTICE)

TOWER = Point(232, 325)  # the pagoda over the world map's "Võ Khôi Tháp" label (the label does nothing)
BOSS_PREV, BOSS_NEXT = Point(265, 308), Point(728, 308)  # ‹ › in the lobby
BOSS_TEXT = Rect(400, 415, 200, 40)  # boss name and cost (static text; the team count under it is left out)
BOSS_PRESSES = 12
BOSS_CELLS = 25  # another boss changes 44-256 cells of the half-size name/cost copy, a new cost 9
BOSS_WAIT_S = 2.5  # a press that changes nothing in this long: the end of the list (a change takes 0.5-2 s)
RESULT_OK = Point(494, 424)
ALERT_CANCEL = Point(546, 330)  # "Hủy bỏ" of the silver alert (never "Cường hóa" at 453,330: it spends silver)  # 确定 of the 胜利 / 失败 box (its template is hidden while the line animates)
TEAM_TRIES = 2  # Lập đội presses that make no team: no Quân lệnh ("Hiện không có Quân Lệnh.")
CIRCLE_WAIT_S = 3.0  # a circle click that joins shows "Ngài gia nhập thành công" within this
JOIN_FIGHT_S = 30.0  # after joining, the player walks to the boss (5-15 s seen) and the fight takes a second
EXPIRED_S = 5.0  # the countdown gone this long with the boss full: the round ran out
FULL_HP = 0.98
EMPTY_HP = 0.02  # the HP bar is empty while the arena loads, and after a win or a loss
RESULT_SETTLE_S = 3.0  # wait up to this long for the result box's 确定 to show before clicking blind

SILVER_CAP = 2_000_000_000  # silver above this is lost ("số vượt quá 2 tỷ đã bị ác tặc cướp mất")
SILVER_CHEST = 1_000_000_000
SHOP_TAB_NAME = "Cửa hiệu"
SHOP_TAB = Point(375, 159)
SHOP_DOWN = Point(651, 425)  # ▼ of the shop grid
SHOP_GRID = Rect(190, 195, 470, 245)
CHEST_X = 553  # the bottom row: 5e6 / 1e7 / 5e7 / 1e8 / 1e9 at x 335 / 390 / 445 / 500 / 553
SCROLL_PRESSES = 30
BUY_TRIES = 3
KEEP_MAX = 12  # frames of unexpected boxes saved per run


class SilverPolicy(IntEnum):
    BUY = 1
    SKIP = 2
    STOP = 3

    @property
    def label(self) -> str:
        return {SilverPolicy.BUY: "Mua rương 1 tỷ bạc rồi chạy tiếp", SilverPolicy.SKIP: "Bỏ qua, chạy tiếp",
                SilverPolicy.STOP: "Dừng"}[self]


class VoKhoiThapParams(TaskParams):
    boss: int = Field(0, ge=0, le=20, title="Boss",
                      description="0 = boss đang hiện trong sảnh; N = boss thứ N (bấm ‹ về boss đầu, rồi › N-1 lần)")
    rounds: int = Field(0, ge=0, le=1_000_000, title="Số lượt đánh (0 = đến khi bấm Dừng)")
    on_full_silver: SilverPolicy = Field(SilverPolicy.BUY, title="Khi bạc vượt 2 tỷ")
    silver_chests: int = Field(2, ge=1, le=10, title="Số rương 1 tỷ bạc mua mỗi lần",
                               description="Trang bị › Cửa hiệu, ô cuối; mỗi rương 1.000.000.000 bạc")
    auto_spin: bool = Field(True, title="Hết Quân lệnh thì mở rương Tinh Tú")
    buy_chests: bool = Field(False, title="Hết rương Tinh Tú thì mua thêm (tốn Vàng)")
    chest_quantity: int = Field(100, ge=1, le=10_000, title="Số rương Tinh Tú mua mỗi lần",
                                description=f"{VANG_PER_CHEST} Vàng mỗi rương")
    max_refills: int = Field(0, ge=0, le=100_000, title="Số lần mở rương tối đa (0 = không giới hạn)")
    chest_batch: int = Field(BATCH, ge=1, le=500, title="Số rương Tinh Tú mỗi đợt mở nhanh")
    chest_click_ms: int = Field(CLICK_MS, ge=0, le=1000, title="Khoảng cách giữa các click khi mở rương (ms)")
    max_minutes: int = Field(0, ge=0, le=24 * 60, title="Thời gian chạy tối đa (phút)",
                             description="0 = không giới hạn")


def _n(v: int | None) -> str:
    return "?" if v is None else f"{v:,}"


def _fighting(hp: float | None) -> bool:
    """The boss is losing HP: the player joined (an empty bar is a loading arena or a finished round)."""
    return hp is not None and EMPTY_HP < hp < FULL_HP


def _sig(snap: Snapshot, r: Rect) -> np.ndarray:
    """A small grey copy of a region, to tell whether a press changed it."""
    img = snap.senses.canvas[r.y:r.bottom, r.x:r.right]
    return cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (r.w // 2, r.h // 2),
                      interpolation=cv2.INTER_AREA).astype(np.int16)


def _differs(a: np.ndarray | None, b: np.ndarray, cells: int = 60) -> bool:
    """Cells that changed by more than 30 grey levels. A shop scroll changes ~10000 of the grid (the
    same grid 0-19: hover glow); another boss 44-256 of the name line (the same boss with another
    cost: 9)."""
    return a is None or int((np.abs(a - b) > 30).sum()) >= cells


@register
class VoKhoiThap(Feature):
    id = "vo_khoi_thap"
    title = "Võ Khôi Tháp"
    description = (
        "Đánh boss Võ Khôi Tháp liên tục (bấm vòng phép để vào trận, thua thì đánh lại). Bạc vượt 2 tỷ thì mua "
        "rương 1 tỷ bạc; hết Quân lệnh thì mở rương Tinh Tú. Bắt đầu từ bất kỳ màn hình nào"
    )
    order = 85
    Params = VoKhoiThapParams
    entry = WORLD  # the loop's home: a round in progress can only be left to the world map
    answers = frozenset({GOLD})

    params: VoKhoiThapParams

    class Memory(BaseModel):
        phase: str = "loop"  # loop | shop | refill
        rounds: int = 0
        wins: int = 0
        losses: int = 0
        cost: int | None = None  # Quân lệnh of the next round: read in the lobby every round (+1 per
        # round, back to 1 each day); between the result and the next lobby read it is the last cost + 1
        ql: int | None = None  # last HUD Quân lệnh
        silver: int | None = None  # last HUD silver
        boss_step: str = "first"  # first (‹ to the first boss) | next (› to boss N) | done
        boss_presses: int = 0
        reward: int | None = None  # silver of the last win (HUD after − HUD before the round; Mộc 20e6, Hoả 90e6)
        silver_round: int | None = None  # HUD silver when the round started
        reward_due: bool = False  # a win was counted: read its silver on the world map
        alert: bool = False  # the 2-billion alert was seen (and answered Hủy bỏ)
        team_fails: int = 0  # Lập đội presses in a row that made no team
        circle: int = 0  # next magic circle to click
        circle_clicks: int = 0  # in this round
        joined: bool = False  # the boss lost HP in this round
        joined_at: float | None = None  # "Ngài gia nhập thành công" seen after a circle click
        expired_since: float | None = None
        leaving: bool = False  # Thoát pressed in the arena
        counted: bool = False  # the result box on screen was counted
        result_at: float | None = None
        ok_clicks: int = 0
        # silver chests
        silver_events: int = 0
        skip_logged: bool = False
        bought: int = 0  # this time
        bought_total: int = 0
        at_bottom: bool = False
        scrolls: int = 0
        buy_tries: int = 0
        silver_before: int | None = None
        # Tinh Tú
        refills: int = 0
        chest: ChestState = ChestState()
        chests_first: int | None = None  # chest count when this refill opened the wheel
        opened_total: int = 0
        bought_chests_total: int = 0
        ql_before: int | None = None  # Quân lệnh before the refill (checked when the HUD shows again)
        check_gain: bool = False
        kept: int = 0
        end: str = ""

    m: Memory

    def __init__(self, params: Any = None) -> None:
        super().__init__(params)
        self.job = ChestJob(self, lambda: self.m.chest, lambda: 0, self._chests_to_buy,
                            active=lambda: self.m.phase == "refill",
                            speed=lambda: (self.params.chest_batch, self.params.chest_click_ms))
        self._boss_sig: np.ndarray | None = None
        self._grid_sig: np.ndarray | None = None
        self._last_box = ""

    def begin(self) -> None:
        self.m = self.Memory(boss_step="first" if self.params.boss else "done")
        self.entry = WORLD

    def max_run_s(self) -> float:
        return self.params.max_minutes * 60.0

    # --- phases ------------------------------------------------------------------------------------
    def _phase(self, phase: str, why: str) -> None:
        self.m.phase = phase
        self.entry = {"loop": WORLD, "shop": SHOP, "refill": WHEEL}[phase]
        self.actor.log(why)

    def _calm(self, snap: Snapshot) -> bool:
        """Between rounds: nothing of a round is on screen."""
        return not any(snap.has(s) for s in (TEAMS, ROOM, ARENA, RESULT))

    def goal(self, snap: Snapshot) -> str | None:
        p, m = self.params, self.m
        self._read_hud(snap)
        self._keep_boxes(snap)
        if m.phase == "refill":
            self._refill_tick(snap)
        if m.end and self._calm(snap) and snap.top not in BOXES:
            return f"{self.summary()}: {m.end}"
        if m.phase != "loop" or not self._calm(snap):
            return None
        if p.rounds and m.rounds >= p.rounds:
            return f"Đã đánh {m.rounds} lượt. {self.summary()}"
        if snap.hud is not None and m.silver is not None and (m.alert or m.silver + (m.reward or 0) >= SILVER_CAP):
            self._silver_full()
        elif m.ql is not None and m.cost is not None and m.ql < m.cost and not m.end:
            self._out_of_tokens()
        return None

    def _read_hud(self, snap: Snapshot) -> None:
        if snap.top == LOBBY:  # full screen: no HUD, but this round's cost (authoritative)
            cost = snap.value(LOBBY, "cost")
            if cost is not None:
                self.m.cost = cost
        hud = snap.hud
        if hud is None or snap.top == WHEEL:  # the Tinh Tú dialog covers Quân lệnh
            return
        ql, silver = hud.quan_lenh, hud.silver
        if ql is not None:
            if self.m.check_gain:
                self.m.check_gain = False
                before = self.m.ql_before or 0
                self.actor.log(f"Mở rương Tinh Tú: Quân lệnh {before} → {ql[0]}")
                if ql[0] <= before:
                    self.m.end = "mở rương Tinh Tú không thêm Quân lệnh"
            self.m.ql = ql[0]
        if silver is not None:
            self.m.silver = silver
            if self.m.reward_due and self.m.silver_round is not None and snap.top == WORLD:
                self.m.reward_due = False
                gain = silver - self.m.silver_round
                if gain > 0:
                    if self.m.reward is None or abs(gain - self.m.reward) > self.m.reward // 10:
                        self.actor.log(f"Bạc mỗi lượt thắng: {gain:,}")
                    self.m.reward = gain

    def _keep_boxes(self, snap: Snapshot) -> None:
        """Save the first frame of every box that shows up outside the bot's own flows (the silver
        alert has not been seen yet: this is how it gets captured)."""
        top = snap.top
        ours = (top == CONFIRM and (ARENA in snap.stack)) or (top == GOLD and self.m.phase == "refill")
        name = top if top in BOXES or top == UNKNOWN else ""  # a box, or a dialog no rule knows
        if not name or ours:
            self._last_box = ""
            return
        if name == self._last_box or self.m.kept >= KEEP_MAX:
            return
        self._last_box = name
        self.m.kept += 1
        silver = self.m.silver
        self.actor.keep(snap, f"{name}-bac-{silver if silver is not None else 'na'}")

    def _silver_full(self) -> None:
        p, m = self.params, self.m
        if p.on_full_silver is SilverPolicy.SKIP:
            if not m.skip_logged:
                m.skip_logged = True
                self.actor.log(f"Bạc {m.silver:,} sẽ vượt 2 tỷ (phần vượt bị mất): bỏ qua theo cài đặt", "warning")
            m.alert = False
            return
        m.silver_events += 1
        if p.on_full_silver is SilverPolicy.STOP:
            m.end = f"bạc sắp vượt 2 tỷ ({m.silver:,})"
            return
        m.bought, m.buy_tries, m.at_bottom, m.scrolls = 0, 0, False, 0
        m.alert = False
        self._grid_sig = None
        self._phase("shop", f"Bạc {m.silver:,} (+{m.reward or 0:,} mỗi lượt) sẽ vượt 2 tỷ: mua {p.silver_chests} "
                            "rương 1 tỷ bạc")

    def _out_of_tokens(self) -> None:
        p, m = self.params, self.m
        if not p.auto_spin:
            m.end = f"hết Quân lệnh ({m.ql} < {m.cost})"
            return
        if p.max_refills and m.refills >= p.max_refills:
            m.end = f"hết Quân lệnh ({m.ql} < {m.cost}) và đã mở rương {m.refills} lần"
            return
        m.chest = ChestState()
        m.chests_first = None
        m.ql_before = m.ql
        self._phase("refill", f"Hết Quân lệnh ({m.ql} < {m.cost} mỗi lượt): mở rương Tinh Tú")

    def _chests_to_buy(self) -> int:
        p = self.params
        return p.chest_quantity if p.buy_chests and self.m.chests_first == 0 else 0

    def _refill_tick(self, snap: Snapshot) -> None:
        m = self.m
        n = self.job.track(snap)
        if snap.top != WHEEL or n is None:
            return
        if m.chests_first is None:
            m.chests_first = n
            if n == 0 and not self.params.buy_chests:
                m.end = "hết Quân lệnh và hết rương Tinh Tú (không mua thêm theo cài đặt)"
                self._phase("loop", "Hết rương Tinh Tú")
                return
        if self.job.finished(snap) and not (n > 0):
            st = m.chest
            m.refills += 1
            m.opened_total += st.opened
            m.bought_chests_total += st.bought
            m.check_gain = True
            m.ql = None  # unknown until the HUD shows again (the wheel covers it): never act on the old value
            self.actor.count("refills")
            self._phase("loop", f"Mở rương Tinh Tú xong (lần {m.refills}): mở {st.opened}"
                                + (f", mua {st.bought}" if st.bought else ""))

    def summary(self) -> str:
        m = self.m
        s = f"{m.rounds} lượt (thắng {m.wins}, thua {m.losses})"
        if m.bought_total:
            s += f", mua {m.bought_total} rương 1 tỷ bạc"
        if m.refills:
            s += f", mở rương Tinh Tú {m.refills} lần ({m.opened_total} rương"
            s += f", mua {m.bought_chests_total})" if m.bought_chests_total else ")"
        return s

    # --- rules -------------------------------------------------------------------------------------
    def rules(self) -> list[Rule]:
        loop = lambda: self.m.phase == "loop" and not self.m.end  # noqa: E731
        shop = lambda: self.m.phase == "shop"  # noqa: E731
        return [
            # a round (the world-map tower opens the lobby, or the arena of a round still running)
            Rule("vào tháp", on=WORLD, when=lambda s: loop(),
                 do=lambda s: self._round_start() or Click(TOWER, label=f"toà Võ Khôi Tháp (lượt {self.m.rounds + 1})", within=15,
                                    expect=lambda n: n.base in (LOBBY, ARENA))),
            Rule("chọn boss", on=LOBBY, when=lambda s: loop() and self.m.boss_step != "done", do=self._boss),
            Rule("tấn công", on=LOBBY, when=lambda s: loop(),
                 do=lambda s: Click(s.attack, label=f"Tấn công (lượt {self.m.rounds + 1})", within=8, expect=TEAMS)),
            Rule("lập đội", on=TEAMS, when=lambda s: loop(),
                 do=lambda s: Click(s.create, label=f"Lập đội ({self.m.team_fails + 1})", within=6,
                                    expect=or_after(lambda n: n.has(ROOM), 4.0), then=self._team_made)),
            Rule("khai chiến", on=ROOM,
                 do=lambda s: Click(s.start, label="Khai chiến", within=20, expect=ARENA, then=self._entered)),
            Rule("vòng phép", on=ARENA, do=self._arena),
            Rule("rời đấu trường", on=CONFIRM, when=lambda s: ARENA in self.snap.stack,
                 do=lambda s: Click(s.ok if self.m.leaving else s.cancel, within=10,
                                    label="rời Võ Khôi Tháp" if self.m.leaving else "ở lại",
                                    expect=lambda n: not n.has(CONFIRM))),
            Rule("báo bạc đầy", on=ALERT, do=self._alert),
            Rule("kết quả", on=RESULT, do=self._result),
            # silver chests
            Rule("tab Cửa hiệu", on=SHOP, when=lambda s: shop() and s.tab != SHOP_TAB_NAME,
                 do=lambda s: Click(SHOP_TAB, label="tab Cửa hiệu", within=3,
                                    expect=lambda n: n.value(SHOP, "tab") == SHOP_TAB_NAME, then=self._shop_reset)),
            Rule("cuộn xuống", on=SHOP, when=lambda s: shop() and not self.m.at_bottom, do=self._scroll),
            Rule("mua rương bạc", on=SHOP, when=lambda s: shop(), do=self._buy),
            Rule("xác nhận mua", on=BOXES, when=lambda s: shop() and SHOP in self.snap.stack and self.m.silver_before is not None,
                 do=lambda s: Click(s.ok, label="确定 mua rương bạc", within=6,
                                    expect=or_after(self._silver_dropped, 5.0), then=self._bought)),
            # Tinh Tú
            *self.job.rules(),
        ]

    # --- lobby -------------------------------------------------------------------------------------
    def _boss(self, s: ScreenState) -> Action:
        m, want = self.m, self.params.boss
        self._boss_sig = _sig(self.snap, BOSS_TEXT)
        if m.boss_step == "first":
            if m.boss_presses >= BOSS_PRESSES:
                self.actor.log(f"Bấm ‹ {BOSS_PRESSES} lần mà danh sách boss vẫn đổi (vòng tròn?): giữ boss đang hiện",
                               "warning")
                m.boss_step = "done"
                return Wait(0.05, label="giữ boss")
            m.boss_presses += 1
            return Click(BOSS_PREV, label=f"‹ boss ({m.boss_presses})", within=BOSS_WAIT_S + 1,
                         expect=or_after(lambda n: _differs(self._boss_sig, _sig(n, BOSS_TEXT), BOSS_CELLS), BOSS_WAIT_S),
                         then=self._boss_prev)
        if m.boss_presses >= want - 1:
            m.boss_step = "done"
            self.actor.log(f"Đã chọn boss thứ {want}")
            return Wait(0.3, label="đọc Quân lệnh mỗi lượt")
        m.boss_presses += 1
        return Click(BOSS_NEXT, label=f"› boss ({m.boss_presses + 1})", within=BOSS_WAIT_S + 1,
                     expect=or_after(lambda n: _differs(self._boss_sig, _sig(n, BOSS_TEXT), BOSS_CELLS), BOSS_WAIT_S),
                     then=self._boss_next)

    def _boss_prev(self) -> None:
        if self.snap is None or self.snap.top != LOBBY:
            return
        if not _differs(self._boss_sig, _sig(self.snap, BOSS_TEXT), BOSS_CELLS):  # no change: this is the first boss
            self.m.boss_step, self.m.boss_presses = "next", 0

    def _boss_next(self) -> None:
        if self.snap is None or self.snap.top != LOBBY:
            return
        if not _differs(self._boss_sig, _sig(self.snap, BOSS_TEXT), BOSS_CELLS):
            self.m.end = f"không có boss thứ {self.params.boss} (chỉ có {self.m.boss_presses} boss)"

    # --- arena -------------------------------------------------------------------------------------
    def _entered(self) -> None:
        m = self.m
        m.circle_clicks, m.joined, m.expired_since, m.leaving, m.counted = 0, False, None, False, False
        m.joined_at = None

    def _arena(self, s: ScreenState) -> Action:
        m = self.m
        hp = s.boss_hp
        now = time.monotonic()
        if _fighting(hp):
            if not m.joined:
                m.joined = True
                self.actor.log(f"Đã vào trận (vòng phép {m.circle % 3 + 1}, máu boss {hp:.0%})")
            return Wait(0.5, label="đang đánh boss", patience=240)
        if m.leaving:
            return Click(s.exit, label="Thoát (lại)", within=6, expect=lambda n: n.has(CONFIRM) or not n.has(ARENA))
        if m.joined_at is not None:  # joined: the player walks to the boss; a new click would restart the walk
            if now - m.joined_at < JOIN_FIGHT_S:
                return Wait(0.5, label="đã gia nhập, chờ đánh boss", patience=JOIN_FIGHT_S + 10)
            self.actor.log(f"Gia nhập {JOIN_FIGHT_S:g}s mà chưa đánh boss: thử vòng phép khác", "warning")
            m.joined_at = None
            m.circle += 1
        if s.counting and not (hp is not None and hp <= EMPTY_HP):
            m.expired_since = None
            circles = sorted(s.circles, key=lambda p: (p.x, p.y))
            if not circles:
                return Wait(0.3, label="tìm vòng phép", patience=30)
            i = m.circle % len(circles)
            m.circle_clicks += 1
            return Click(circles[i], label=f"vòng phép {i + 1} (lần {m.circle_clicks})", within=CIRCLE_WAIT_S + 2,
                         expect=or_after(self._joined, CIRCLE_WAIT_S), then=self._circle_answered)
        if m.expired_since is None:  # loading (HP bar empty), or the countdown is gone
            m.expired_since = now
        if now - m.expired_since < EXPIRED_S:
            return Wait(0.5, label="đếm ngược đã tắt?", patience=EXPIRED_S + 30)
        m.leaving = True
        self.actor.log("Hết giờ đếm ngược mà chưa vào trận: Thoát (thua lượt này)", "warning")
        return Click(s.exit, label="Thoát (hết giờ)", within=6, expect=lambda n: n.has(CONFIRM) or not n.has(ARENA))

    def _team_made(self) -> None:
        """Lập đội that does nothing shows a green "Hiện không có Quân Lệnh." line: out of tokens even
        if the HUD read said otherwise."""
        m = self.m
        if self.snap is not None and self.snap.has(ROOM):
            m.team_fails = 0
            return
        m.team_fails += 1
        if m.team_fails >= TEAM_TRIES:
            m.team_fails = 0
            self.actor.log(f"Lập đội {TEAM_TRIES} lần không có tác dụng (\"Hiện không có Quân Lệnh\"; HUD đọc "
                           f"{_n(m.ql)}, cần {_n(m.cost)})", "warning")
            m.ql = 0
            self._out_of_tokens()

    def _round_start(self) -> None:
        """Remember the silver before the round (its win's reward is read from the HUD afterwards)."""
        self.m.silver_round = self.m.silver
        return None

    def _alert(self, s: ScreenState) -> Action:
        m = self.m
        if not m.alert:
            m.alert = True
            self.actor.count("silver_alerts")
            self.actor.keep(self.snap, f"silver-alert-bac-{m.silver}")
            self.actor.log(f"Game báo bạc đã đầy (2 tỷ), phần vượt bị mất: Hủy bỏ rồi mua rương 1 tỷ bạc "
                           f"(bạc trước lượt {_n(m.silver_round or m.silver)})", "warning")
        return Click(s.cancel or ALERT_CANCEL, label="Hủy bỏ (báo bạc đầy)", within=4,
                     expect=lambda n: not n.has(ALERT))

    def _joined(self, snap: Snapshot) -> bool:
        return (snap.has(RESULT) or _fighting(snap.value(ARENA, "boss_hp"))
                or bool(snap.value(ARENA, "joined")))

    def _circle_answered(self) -> None:
        if self.snap is not None and self._joined(self.snap):
            self.m.joined_at = time.monotonic()
            self.actor.log(f"Vòng phép {self.m.circle % 3 + 1}: đã gia nhập")
            return
        self.actor.log(f"Vòng phép {self.m.circle % 3 + 1}: chưa gia nhập, thử vòng khác", "warning")
        self.m.circle += 1

    def _result(self, s: ScreenState) -> Action:
        m = self.m
        if not m.counted and s.result is not None:
            m.counted = True
            m.result_at = time.monotonic()
            m.rounds += 1
            self.actor.count("bosses")
            self.actor.progress(m.rounds, self.params.rounds or None)
            if m.cost is not None:
                m.cost += 1  # the next round costs one more (the lobby reads it again)
            if s.result == "win":
                m.wins += 1
                m.reward_due = True
                self.actor.log(f"Lượt {m.rounds}: 胜利")
            else:
                m.losses += 1
                self.actor.log(f"Lượt {m.rounds}: 失败 (lượt sau đánh lại)", "warning")
        if s.result is None:
            return Wait(0.3, label="đọc kết quả", patience=20)
        if s.ok is None and time.monotonic() - (m.result_at or 0) < RESULT_SETTLE_S:
            return Wait(0.3, label="chờ nút 确定", patience=RESULT_SETTLE_S + 10)  # the box is still sliding in
        m.ok_clicks += 1
        return Click(s.ok or RESULT_OK, label=f"确定 kết quả lượt {m.rounds} ({m.ok_clicks})", within=5,
                     expect=or_after(lambda n: not n.has(RESULT), 3.0), then=self._result_closed)

    def _result_closed(self) -> None:
        if self.snap is not None and self.snap.has(RESULT):
            return  # clicked while the box was still animating: click again
        self.m.counted = False
        self.m.leaving = False
        self.m.ok_clicks = 0
        self.m.joined, self.m.joined_at = False, None

    # --- silver chests -----------------------------------------------------------------------------
    def _shop_reset(self) -> None:
        self.m.at_bottom = False
        self._grid_sig = None

    def _scroll(self, s: ScreenState) -> Action:
        m = self.m
        if m.scrolls >= SCROLL_PRESSES:
            return Fail(f"bấm ▼ {SCROLL_PRESSES} lần mà lưới Cửa hiệu vẫn đổi")
        m.scrolls += 1
        self._grid_sig = _sig(self.snap, SHOP_GRID)
        return Click(SHOP_DOWN, label=f"▼ Cửa hiệu ({m.scrolls})", within=2,
                     expect=or_after(lambda n: _differs(self._grid_sig, _sig(n, SHOP_GRID)), 1.0),
                     then=self._scrolled)

    def _scrolled(self) -> None:
        if self.snap is None or self.snap.top != SHOP:
            return
        if not _differs(self._grid_sig, _sig(self.snap, SHOP_GRID)):
            self.m.at_bottom = True
            self.actor.log(f"Cửa hiệu đã cuộn xuống cuối ({self.m.scrolls} lần ▼)")

    def _buy(self, s: ScreenState) -> Action:
        m, p = self.m, self.params
        if m.bought >= p.silver_chests:
            self._phase("loop", f"Đã mua {m.bought} rương 1 tỷ bạc (bạc còn {_n(m.silver)})")
            return Wait(0.05, label="xong mua rương bạc")
        chest = next((c for c in s.silver_chests if abs(c.x - CHEST_X) <= 15), None)
        if chest is None:
            return Fail("không thấy rương 1 tỷ bạc (ô cuối hàng dưới) trong Cửa hiệu")
        if s.price != SILVER_CHEST:
            return Click(chest, label="rương 1 tỷ bạc", within=3,
                         expect=lambda n: n.value(SHOP, "price") == SILVER_CHEST)
        silver = self.snap.hud.silver if self.snap.hud is not None else None
        if silver is None:  # red while it is being spent
            return Wait(0.3, label="đọc bạc", patience=15)
        if m.silver_before is not None:  # the last Mua is not settled yet: never buy twice by mistake
            if silver <= m.silver_before - SILVER_CHEST:
                self._count_chest(silver)
                return Wait(0.05, label="đã mua")
            self.actor.log(f"Mua chưa trừ bạc ({m.silver_before:,} → {silver:,}); bấm lại", "warning")
            m.silver_before = None
        if silver < SILVER_CHEST:
            self._phase("loop", f"Bạc {silver:,} không đủ mua rương 1 tỷ; tiếp tục")
            return Wait(0.05, label="không đủ bạc")
        if m.buy_tries >= BUY_TRIES:
            return Fail(f"bấm Mua {BUY_TRIES} lần mà bạc không giảm 1 tỷ")
        m.buy_tries += 1
        m.silver_before = silver
        return Click(s.buy, label=f"Mua rương 1 tỷ bạc ({m.bought + 1}/{p.silver_chests}, bạc {silver:,})",
                     within=7, expect=or_after(lambda n: self._silver_dropped(n) or n.top in BOXES, 5.0),
                     then=self._bought)

    def _silver_dropped(self, snap: Snapshot) -> bool:
        hud = snap.hud
        silver = hud.silver if hud is not None else None
        before = self.m.silver_before
        return silver is not None and before is not None and silver <= before - SILVER_CHEST

    def _bought(self) -> None:
        snap = self.snap
        if snap is None or snap.top in BOXES:
            if snap is not None:
                self.actor.log(f"Mua rương bạc: hộp xác nhận {snap.top}", "warning")
            return  # answered by "xác nhận mua"
        if self._silver_dropped(snap):
            self._count_chest(snap.hud.silver)  # type: ignore[union-attr]
        # else: settled by _buy on a frame where the silver reads again

    def _count_chest(self, now: int) -> None:
        m = self.m
        m.bought += 1
        m.bought_total += 1
        m.buy_tries = 0
        self.actor.count("silver_chests")
        self.actor.log(f"Đã mua rương 1 tỷ bạc ({m.bought}/{self.params.silver_chests}): bạc "
                       f"{m.silver_before:,} → {now:,}")
        m.silver = now
        m.silver_before = None

    @classmethod
    def import_legacy(cls, ini: Mapping[str, str]) -> dict[str, Any]:
        """``BOSS_ID`` (combo label "3. Bệ Ngạn ( lv 140 )": the N-th boss) and ``BOSS_SLMUA`` (chests
        to buy); docs/04. The other v1.8.0 values have no meaning on the decision loop."""
        out: dict[str, Any] = {}
        raw = ini.get("BOSS_ID", "").strip().split(".", 1)[0].strip()
        if raw.isdigit() and 1 <= int(raw) <= 20:
            out["boss"] = int(raw)
        qty = ini.get("BOSS_SLMUA", "").strip()
        if qty.isdigit() and int(qty) >= 1:
            out["chest_quantity"] = min(int(qty), 10_000)
        return out
