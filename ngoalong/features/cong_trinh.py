"""Nâng cấp công trình: upgrade the buildings of the main city and / or the harbour (Hải Cảng)
(docs/08 "Feature flows" 2; rule-based since R3).

The buildings and their levels are read from the map, not from fixed points: every building
carries a white "lvN" label, found by its "lv" (``map.lv``, matched on its white ink) and read with
the digit glyphs (``city.labels`` / ``harbour.labels``). A level counts once two scans in a row
agree (an animation can cover a digit for a frame). The cap is the Chúa công level in the badge
under the avatar (``hud.level``), or the user's lower "Cấp tối đa".

* **Tidy**: the left notice list is collapsed and the top icon rows are hidden (they cover labels
  near the top); the icon rows are shown again at the end.
* **Pick**: the lowest building below the cap (top to bottom on a tie) that is not given up.
* **Visit**: click just above its label → the building panel; "Tăng cấp" upgrades one level at once.
  Each press must change "Tốn bạc" (or the silver): a press that changes nothing means the building
  is at its limit (the city's Tháp thí luyện at lv140 did that), so it is given up for this run. At
  most ``upgrades`` presses per visit, then ✕ and the map is read again.
* **Gold**: when no builder is free ("Kiến Trúc Nhàn *0") a press asks "Bỏ qua thời gian chờ tốn N
  Vàng" (unless the player ticked "don't remind", which a browser reload resets). Answered by the
  setting "Dùng vàng bỏ qua thời gian chờ": on = 确定 and go on; off = 取消 and the run ends (every
  building would ask the same until a builder is free). Both "no box" and the box are normal.
* **Areas**: city, harbour or both (city first). The harbour is reached with "Vào Hải Cảng" and
  left with 主城 (``nav.yaml``); the area being worked on is the loop's entry screen.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from ngoalong.core.geometry import Point
from ngoalong.engine.actions import Action, Click, Wait, or_after
from ngoalong.engine.feature import Feature, Rule
from ngoalong.engine.registry import register
from ngoalong.engine.task import TaskParams
from ngoalong.perception import Snapshot
from ngoalong.perception.readers import ScreenState

CITY = "city"
HARBOUR = "harbour"
PANEL = "city.building_panel"  # same panel in the city and the harbour
GOLD = "confirm.gold"  # "Bỏ qua thời gian chờ tốn N Vàng" (shown again after a browser reload)
GOLD_POLICY = "spend_gold_skip_wait"
PANEL_CLOSE = Point(687, 202)

SAME_PLACE = 5  # px: a label is centred on its building, so its "lv" moves ~3 px left per added digit
CLICK_ABOVE = 12  # px above the label's centre: the building's roof (the label sits on its body)
OPEN_TRIES = 3  # clicks on a building (above, on its label, above) before it is given up for the run
PRESS_WAIT_S = 3.0  # a press that changed nothing after this long did nothing (the spend-Vàng box
# came 2-3 s late once)
LIMIT_MISSES = 2  # presses in a row that change nothing before a building counts as at its limit
MIN_SCANS = 3  # map reads in an area before it may be called finished
UNREAD_LIMIT = 8  # a label never read after this many scans is left out (covered, animated)


class Area(StrEnum):
    THANH_CHINH = "thanh_chinh"
    HAI_CANG = "hai_cang"
    CA_HAI = "ca_hai"

    @property
    def label(self) -> str:
        return {Area.THANH_CHINH: "Thành chính", Area.HAI_CANG: "Hải cảng",
                Area.CA_HAI: "Thành chính rồi Hải cảng"}[self]

    @property
    def screens(self) -> tuple[str, ...]:
        return {Area.THANH_CHINH: (CITY,), Area.HAI_CANG: (HARBOUR,), Area.CA_HAI: (CITY, HARBOUR)}[self]


AREA_NAMES = {CITY: "Thành chính", HARBOUR: "Hải cảng"}


class CongTrinhParams(TaskParams):
    mode: Area = Field(Area.THANH_CHINH, title="Khu vực")
    max_level: int = Field(0, ge=0, le=999, title="Cấp tối đa",
                           description="0 = đến cấp Chúa công (số trong vòng tròn dưới ảnh đại diện)")
    upgrades: int = Field(10, ge=1, le=1000, title="Số lần bấm Tăng cấp mỗi lần mở công trình",
                          description="Sau đó đóng bảng, đọc lại bản đồ và chọn công trình thấp nhất")
    max_minutes: int = Field(0, ge=0, le=24 * 60, title="Thời gian chạy tối đa (phút)",
                             description="0 = không giới hạn")


class Place(BaseModel):
    """One building, known by where its "lv" sits on its area's map."""

    area: str
    x: int
    y: int
    level: int | None = None  # confirmed (two reads agree), or counted from the presses
    seen: int | None = None  # last read
    first: int | None = None  # first confirmed level of the run
    unread: int = 0  # scans without a valid read
    tries: int = 0  # clicks that did not open its panel
    misses: int = 0  # presses in a row that changed nothing
    out: str = ""  # why it is left alone: limit (a press changed nothing) | no_panel

    @property
    def name(self) -> str:
        return f"lv{self.level} ({self.x},{self.y})"

    def point(self, attempt: int) -> Point:
        digits = len(str(self.level)) if self.level is not None else 3
        cx, cy = self.x + (9 + 6 * digits) // 2, self.y + 5  # centre of "lv" + its digits (~6 px each)
        return Point(cx, cy) if attempt % 2 else Point(cx, cy - CLICK_ABOVE)


@register
class CongTrinh(Feature):
    id = "cong_trinh"
    title = "Nâng cấp công trình"
    description = (
        "Đọc cấp từng công trình trên bản đồ (nhãn lvN) ở Thành chính và/hoặc Hải cảng, nâng cấp công "
        "trình thấp nhất trước, đến cấp Chúa công (hoặc cấp tối đa đã đặt). Bắt đầu từ bất kỳ màn hình nào"
    )
    order = 30
    Params = CongTrinhParams
    entry = CITY
    answers = frozenset({GOLD})

    params: CongTrinhParams

    class Memory(BaseModel):
        area: int = 0  # index into the mode's areas
        places: list[Place] = []
        scans: dict[str, int] = {}
        cap: int | None = None
        current: int | None = None  # index of the place whose panel is open
        presses: int = 0  # presses that worked in this visit
        cost_last: int | None = None  # last "Tốn bạc" read on the open panel: each new value is a level
        cost_before: int | None = None  # "Tốn bạc" when Tăng cấp was pressed
        gold_paid: bool = False  # the last press was answered with Vàng: no change yet is no verdict
        no_builder: bool = False  # the spend-Vàng box was declined: the run ends
        hid_icons: bool = False
        finished: bool = False
        levels: int = 0  # levels gained in the run

    m: Memory

    def begin(self) -> None:
        self.m = self.Memory()
        self.entry = self.params.mode.screens[0]

    def max_run_s(self) -> float:
        return self.params.max_minutes * 60.0

    # --- the map -------------------------------------------------------------------------------
    @property
    def area(self) -> str:
        return self.params.mode.screens[self.m.area]

    def _target(self) -> int | None:
        cap = self.m.cap
        if cap is None:
            return None
        return min(cap, self.params.max_level) if self.params.max_level else cap

    def _scan(self, snap: Snapshot) -> None:
        """Merge the labels of this frame into the memory (only on the bare map of the area)."""
        if snap.top != self.area:
            return
        hud = snap.hud
        level = hud.level if hud is not None else None
        if isinstance(level, int) and level > 0:
            self.m.cap = level
        area = self.area
        tags = snap[area].labels
        if not tags:  # a frame while the map loads or fades in: not a scan
            return
        self.m.scans[area] = self.m.scans.get(area, 0) + 1
        seen: set[int] = set()
        for tag in tags:
            x, y = tag.rect.x, tag.rect.y
            near = [(abs(p.x - x) + abs(p.y - y), k) for k, p in enumerate(self.m.places)
                    if k not in seen and p.area == area and abs(p.x - x) <= SAME_PLACE and abs(p.y - y) <= SAME_PLACE]
            i = min(near)[1] if near else None
            if i is None:
                self.m.places.append(Place(area=area, x=x, y=y))
                i = len(self.m.places) - 1
            seen.add(i)
            p = self.m.places[i]
            p.x, p.y = x, y  # follow the label as it widens
            n = tag.number
            if n is None:
                p.unread += 1
                continue
            if n == p.seen and n != p.level:
                p.level = n
                if p.first is None:
                    p.first = n
            p.seen, p.unread = n, 0
        for k, p in enumerate(self.m.places):  # a label that did not show on this frame
            if p.area == area and k not in seen:
                p.unread += 1

    def _todo(self) -> list[int]:
        """Places of the current area still to upgrade, lowest first."""
        target = self._target()
        if target is None:
            return []
        out = [k for k, p in enumerate(self.m.places)
               if p.area == self.area and not p.out and p.level is not None and p.level < target]
        return sorted(out, key=lambda k: (self.m.places[k].level, self.m.places[k].y, self.m.places[k].x))

    def _area_done(self) -> bool:
        if self.m.scans.get(self.area, 0) < MIN_SCANS or self._target() is None or self._todo():
            return False
        # labels still unconfirmed get a few more scans before the area counts as done
        return not any(p.area == self.area and p.level is None and p.unread < UNREAD_LIMIT for p in self.m.places)

    # --- goal ----------------------------------------------------------------------------------
    def _track_cost(self, snap: Snapshot) -> None:
        """Every new "Tốn bạc" on the open panel is one level gained, whatever answered the press
        (no box, the spend-Vàng box, a level that came with the box)."""
        if self.m.current is None or snap.top != PANEL:
            return
        cost = snap.value(PANEL, "cost")
        if cost is None:
            return
        if self.m.cost_last is not None and cost != self.m.cost_last:
            p = self.m.places[self.m.current]
            p.misses = 0
            self._gained(p)
        self.m.cost_last = cost

    def goal(self, snap: Snapshot) -> str | None:
        self._track_cost(snap)
        self._scan(snap)
        if snap.top != self.area:
            return None
        if self.m.current is not None:  # the panel went away without our ✕ (recovery, a stray click)
            self._closed()
        if self.m.no_builder:
            self.m.finished = True
        if not self.m.finished and self._area_done():
            if self.m.area + 1 < len(self.params.mode.screens):
                self.actor.log(f"Xong {AREA_NAMES[self.area]}; sang {AREA_NAMES[self.params.mode.screens[self.m.area + 1]]}")
                self.m.area += 1
                self.entry = self.area
                return None
            self.m.finished = True
        if self.m.finished and not self.m.hid_icons:
            return self.summary()
        return None

    def summary(self) -> str:
        target = self._target()
        ups = [p for p in self.m.places if p.first is not None and p.level is not None and p.level > p.first]
        s = f"Chúa công cấp {self.m.cap}" if self.m.cap else "Chưa đọc được cấp Chúa công"
        if self.params.max_level and target is not None and target < (self.m.cap or 0):
            s += f" (nâng đến cấp {target})"
        s += f": nâng {self.m.levels} cấp"
        if ups:
            by_area = []
            for area in self.params.mode.screens:
                items = [f"lv{p.first}→lv{p.level}" for p in ups if p.area == area]
                if items:
                    by_area.append(f"{AREA_NAMES[area]} " + ", ".join(items))
            s += " (" + "; ".join(by_area) + ")"
        if self.m.no_builder:
            s += ("; dừng vì không còn thợ xây rảnh và game hỏi dùng Vàng bỏ qua thời gian chờ "
                  "(\"Dùng vàng bỏ qua thời gian chờ\" đang tắt ở thẻ Cài đặt)")
        stuck = [p for p in self.m.places if p.out in ("limit", "no_panel")
                 and p.level is not None and target is not None and p.level < target]
        if stuck:
            why = {"limit": "bấm Tăng cấp không có tác dụng", "no_panel": "không mở được bảng"}
            s += "; chưa nâng được: " + ", ".join(f"{AREA_NAMES[p.area]} {p.name}: {why[p.out]}" for p in stuck)
        return s

    # --- rules ---------------------------------------------------------------------------------
    def rules(self) -> list[Rule]:
        maps = (CITY, HARBOUR)
        return [
            Rule("thu gọn thông báo", on=maps, when=lambda s: self._here() and s.notice,
                 do=lambda s: Click(self.snap.point("hud.notice_open"), label="thu gọn danh sách thông báo",
                                    expect=lambda n: n.value(self.area, "notice") is False, within=2)),
            Rule("ẩn biểu tượng", on=maps, when=lambda s: self._here() and s.icons_shown and not self.m.finished,
                 do=lambda s: Click(self.snap.point("hud.icons_shown"), label="ẩn hàng biểu tượng", within=2,
                                    expect=lambda n: n.value(self.area, "icons_shown") is False,
                                    then=self._hid_icons)),
            Rule("hiện lại biểu tượng", on=maps, when=lambda s: self._here() and self.m.finished and self.m.hid_icons,
                 do=self._show_icons),
            Rule("chọn công trình", on=maps, when=lambda s: self._here() and not self.m.finished, do=self._visit),
            Rule("tăng cấp", on=PANEL, when=lambda s: self.m.current is not None, do=self._press),
            Rule("hỏi Vàng", on=GOLD, when=lambda s: self.m.current is not None, do=self._gold),
        ]

    def _here(self) -> bool:
        return self.snap is not None and self.snap.top == self.area

    def _hid_icons(self) -> None:
        self.m.hid_icons = True

    def _show_icons(self, s: ScreenState) -> Action:
        p = self.snap.point("hud.icons_hidden")
        if p is None:  # shown already (by the user, or another click)
            self.m.hid_icons = False
            return Wait(0.1, label="hàng biểu tượng đã hiện")
        return Click(p, label="hiện lại hàng biểu tượng", within=2,
                     expect=lambda n: n.value(self.area, "icons_shown") is True, then=self._icons_back)

    def _icons_back(self) -> None:
        self.m.hid_icons = False

    def _visit(self, s: ScreenState) -> Action | None:
        todo = self._todo()
        if not todo:
            return Wait(0.3, label="đọc cấp công trình", patience=20)
        k = todo[0]
        p = self.m.places[k]
        target = self._target()
        return Click(p.point(p.tries), label=f"{AREA_NAMES[p.area]} {p.name} (đến lv{target})", within=4,
                     expect=or_after(lambda n: n.has(PANEL), 3.0), then=lambda: self._opened(k))

    def _opened(self, k: int) -> None:
        p = self.m.places[k]
        if self.snap is not None and self.snap.has(PANEL):
            self.m.current, self.m.presses, p.tries = k, 0, 0
            self.m.cost_last = None
            return
        p.tries += 1
        if p.tries >= OPEN_TRIES:
            p.out = "no_panel"
            self.actor.log(f"{AREA_NAMES[p.area]} {p.name}: bấm không mở được bảng nâng cấp; bỏ qua", "warning")

    # --- the panel -------------------------------------------------------------------------------
    def _press(self, s: ScreenState) -> Action:
        p = self.m.places[self.m.current]  # type: ignore[index]
        target = self._target()
        stop = p.out or self.m.no_builder or self.m.presses >= self.params.upgrades or (
            p.level is not None and target is not None and p.level >= target)
        if stop:
            return Click(PANEL_CLOSE, label=f"đóng bảng {p.name}", within=3,
                         expect=lambda n: not n.has(PANEL), then=self._closed)
        if s.cost is None:
            return Wait(0.2, label="đọc Tốn bạc", patience=5)
        self.m.cost_before = s.cost
        return Click(s.upgrade, label=f"Tăng cấp {p.name}→lv{(p.level or 0) + 1}", within=PRESS_WAIT_S + 2,
                     expect=or_after(self._press_seen, PRESS_WAIT_S), then=self._pressed)

    def _press_seen(self, snap: Snapshot) -> bool:
        if snap.has(GOLD):
            return True
        cost = snap.value(PANEL, "cost")
        return cost is not None and self.m.cost_before is not None and cost != self.m.cost_before

    def _pressed(self) -> None:
        snap = self.snap
        if snap is None or snap.has(GOLD):
            return  # answered by the "hỏi Vàng" rule
        p = self.m.places[self.m.current]  # type: ignore[index]
        self._track_cost(snap)  # (goal() has not seen this frame yet)
        cost = self.m.cost_last
        paid, self.m.gold_paid = self.m.gold_paid, False
        if cost is not None and cost != self.m.cost_before:
            return  # a level: counted by _track_cost
        if paid:  # the Vàng may only have freed a builder: the next press tells
            self.actor.log(f"{AREA_NAMES[p.area]} {p.name}: đã dùng Vàng, Tốn bạc chưa đổi; bấm lại")
            return
        p.misses += 1
        if p.misses < LIMIT_MISSES:
            return
        p.out = "limit"
        self.actor.log(f"{AREA_NAMES[p.area]} {p.name}: bấm Tăng cấp không có tác dụng (cấp cao nhất?, Tốn bạc vẫn "
                       f"{cost}); bỏ qua công trình này", "warning")

    def _gained(self, p: Place) -> None:
        self.m.presses += 1
        self.m.levels += 1
        if p.level is not None:
            p.level += 1
            p.seen = None  # the label is read again after the panel closes
        self.actor.count("levels")
        self.actor.progress(self.m.levels)

    def _gold(self, s: ScreenState) -> Action:
        p = self.m.places[self.m.current]  # type: ignore[index]
        if self.actor.policy(GOLD_POLICY):
            self.m.gold_paid = True
            return Click(s.ok, label=f"dùng Vàng bỏ qua thời gian chờ ({p.name})", within=PRESS_WAIT_S + 2,
                         expect=or_after(lambda n: not n.has(GOLD) and self._press_seen(n), PRESS_WAIT_S),
                         then=self._pressed)
        self.m.no_builder = True
        self.actor.log(f"{AREA_NAMES[p.area]} {p.name}: không còn thợ xây rảnh, game hỏi dùng Vàng bỏ qua thời "
                       "gian chờ; \"Dùng vàng bỏ qua thời gian chờ\" đang tắt (thẻ Cài đặt) nên bấm Huỷ và dừng",
                       "warning")
        return Click(s.cancel, label="không dùng Vàng", within=3, expect=lambda n: not n.has(GOLD))

    def _closed(self) -> None:
        self.m.current = None
        self.m.presses = 0

    @classmethod
    def import_legacy(cls, ini: Mapping[str, str]) -> dict[str, Any]:
        """``CAPDO`` (upgrades per building); the mode radio was not saved."""
        try:
            value = int(ini.get("CAPDO", "").strip())
        except ValueError:
            return {}
        return {"upgrades": min(value, 1000)} if value >= 1 else {}

