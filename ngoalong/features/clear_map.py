"""Clear Phó Bản: attack every army of the dungeon map, then move to the next one (v1.8.0 ``CLEARMAP``).

v1.8.0 (v1.8.0 ``app_logic_only.au3``, docs/03) hid the HUD icon rows (``Button_AnChucNang``), the
quest tracker (``AnKhungThongBao``) and a "new dungeon" notice, then looped until F10:

* double-clicked (5, 1) twice; clicked a "Chúa công lên cấp" popup and pressed ESC twice;
* waited up to 3 s for an army (``DaoQuan_1..3``). On a miss: ESC x4 and ``FLAG + 1``; the 4th miss
  clicked "»" (580, 580), the 5th ended the run;
* clicked the army (+10 px), waited for "Tấn công" (``Button_TanCong``) and clicked it; when the
  "recruit" cue showed (``Button_ChieuBinh``) it recruited up to 5 times (548, 331), "Tối đa",
  confirm, ✕ (803, 120), attack again, and after 5 it stopped ("Binh lược suy yếu...");
* then waited **forever** for ``Button_KetQua`` (#2) and clicked it +(335, 480) and (538, 409).

The port keeps that order, the offsets (``coords/clear_map.yaml``) and the v1.8.0 images of the
popups no live capture shows yet (``scenes/clear_map.yaml``), and changes:

* the army to attack is the one under the game's light-blue beam (the "next army" marker): the beam
  is found by colour, then the red flag (``field.enemy_flag``, cut live; v1.8.0's ``DaoQuan_2/_3``
  match the same flags) just below it is clicked. Any other red flag is ignored, since several
  armies carry one but only the marked one can be attacked. Only map regions that avoid the HUD,
  chat and bottom bars are searched;
* the run starts on the field (or opens it from a map screen with "Phó Bản") and the army dialog is
  a scene (``field.army_dialog``): clicks go to its "Tấn công", not to a template corner (#3);
* the battle wait has a time limit (``battle_timeout_s``) and honours Stop. Live (2026-10-06) the
  result box is 战胜 / 战败 with "Xem lại" and "Thoát" (540, 408) = v1.8.0's (538, 409), and
  Button_KetQua matches the "第N回合" round label of the battle screen: the box decides win / loss,
  Button_KetQua keeps v1.8.0's clicks; the map showing again with no box also ends the battle;
* bounded everywhere: consecutive misses, recruits per battle, "Tấn công" clicks, losses in a row,
  the same army attacked again and again, maps visited (``max_maps``) and battles (``max_battles``);
* "»" is clicked only when it is shown (it is missing on the last dungeon); after a switch the
  first miss ends the run, as in v1.8.0, so empty dungeons are not walked forever;
* no message boxes: the run ends with a summary, failures with a screenshot.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import cv2
import numpy as np
from pydantic import Field

from ngoalong.core.errors import NgoaLongError, WaitTimeout
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.input import VK_ESCAPE
from ngoalong.core.scenes import ObjectResult, SceneMatch
from ngoalong.engine.context import RunContext, SceneTimeout
from ngoalong.engine.registry import register
from ngoalong.engine.task import Task, TaskParams

# --- scenes (scenes/field.yaml, scenes/clear_map.yaml, scenes/city.yaml) --------------------------
FIELD = "field"
ARMY = "field.army_dialog"
OBJECTS = "field.clear_map"  # object group of the field used by this feature
CITY = "city"
OPEN_FROM: tuple[str, ...] = ("city", "world_map", "region", "resources")  # show "Phó Bản" (899, 549)
ENEMY = "field.enemy_flag"
ENEMY_THRESHOLD = 0.88  # 0.99+ on the live flags, <= 0.75 on every other stored screen
# The light-blue beam over the next army: 8-17 x 25-39 px live, 150-250 px of colour; its lower
# tip is 25-45 px above that army's red flag (it bobs up and down).
BEAM_MIN_H = 20
BEAM_MIN_W = 4
BEAM_MIN_AREA = 60
BEAM_ASPECT = 1.8  # height / width at least
BEAM_FLAG_DX = 25  # the flag is searched within +-25 px of the beam's x...
BEAM_FLAG_DY = (-10, 75)  # ...and from 10 px above to 75 px below its tip
BEAM_ARMY_DY = 52  # no flag matched: click this far below the tip (flag centre + 10 px)
SWITCH_BANNER = "field.switch_banner.left"
SWITCH_BANNER_REGION = Rect(374, 274, 50, 52)  # its signature (384, 284) +- 10 px
SWITCH_BANNER_THRESHOLD = 0.8
WIN_OBJECTS = ("win", "win_zh")
LOSE_OBJECTS = ("lose", "lose_zh")
LEGACY_RESULT = "result"  # Button_KetQua

# --- coords (coords/clear_map.yaml, coords/common.yaml) -------------------------------------------
KEY = "clear_map"
FOCUS = f"{KEY}.focus_click"
RECRUIT = f"{KEY}.recruit"
RECRUIT_MAX_OFF = f"{KEY}.recruit_max_offset_from_match"
RECRUIT_CONFIRM_OFF = f"{KEY}.recruit_confirm_offset_from_match"
CLOSE_RECRUIT = f"{KEY}.close_recruit"
RESULT_CLOSE_OFF = f"{KEY}.result_close_offset_from_match"
ENEMY_OFF = f"{KEY}.enemy_click_offset_from_match"
REGIONS = f"{KEY}.enemy_search_regions"
REGION_HUD_HIDDEN = f"{KEY}.enemy_search_region_hud_hidden"
RESULT_OK = "common.close_result_ok"  # (538, 409)

# --- limits ---------------------------------------------------------------------------------------
ATTACK_RETRY_S = 3.0  # "Tấn công" is clicked again when the army dialog is still open after this
MAX_ATTACK_CLICKS = 3
SAME_TARGET_LIMIT = 3  # attacks on (nearly) the same army on one map before giving up
SAME_TARGET_PX = 12
SETTLE_MIN_S = 2.0  # the map must show this long (or wait_s) with no result box to end a battle
ESC_PRESSES = 2
CANVAS = Rect(0, 0, 1000, 600)
RECRUIT_FAILED = "Binh lược suy yếu, thiếu lúa hoặc không thể chiêu binh rồi Chủ Công!"


class ClearMapError(NgoaLongError):
    """The dungeon could not be cleared (wrong screen, recruit failed, an army that never falls...)."""


class Outcome(StrEnum):
    WIN = "win"
    LOSE = "lose"
    UNKNOWN = "unknown"  # the battle ended without a recognised result box


class ClearMapParams(TaskParams):
    delay_ms: int = Field(200, ge=0, le=60_000, title="Thời gian chờ giữa các bước (ms)")
    wait_s: float = Field(
        3.0, ge=0.5, le=60.0, title="Thời gian chờ tìm hình (giây)",
        description="Chờ tối đa bấy nhiêu giây để thấy đạo quân, bảng đạo quân, bảng chiêu binh",
    )
    hide_hud: bool = Field(True, title="Thu gọn biểu tượng và bảng nhiệm vụ trước khi đánh")
    next_map: bool = Field(True, title="Hết đạo quân thì sang phó bản kế tiếp (»)")
    max_maps: int = Field(0, ge=0, le=100, title="Số phó bản được chuyển sang tối đa (0 = không giới hạn)")
    max_battles: int = Field(0, ge=0, le=100_000, title="Số trận tối đa (0 = không giới hạn)")
    max_misses: int = Field(
        4, ge=1, le=50, title="Số lần không thấy đạo quân liên tiếp",
        description="Sau bấy nhiêu lần liên tiếp không thấy đạo quân thì sang phó bản kế tiếp hoặc dừng",
    )
    max_recruits: int = Field(5, ge=0, le=50, title="Số lần chiêu binh tối đa mỗi trận")
    max_losses: int = Field(3, ge=1, le=100, title="Dừng khi thua liên tiếp bấy nhiêu trận")
    battle_timeout_s: float = Field(
        180.0, ge=10.0, le=3600.0, title="Thời gian tối đa mỗi trận (giây)",
        description="Không thấy kết quả trận sau thời gian này thì dừng và chụp màn hình",
    )
    scene_timeout_s: float = Field(10.0, ge=0.5, le=120.0, title="Thời gian chờ màn hình (giây)")


@dataclass
class _Stats:
    battles: int = 0
    wins: int = 0
    losses: int = 0
    unknown: int = 0
    loss_streak: int = 0
    maps: int = 0
    recruits: int = 0

    def record(self, outcome: Outcome) -> None:
        self.battles += 1
        if outcome is Outcome.WIN:
            self.wins += 1
            self.loss_streak = 0
        elif outcome is Outcome.LOSE:
            self.losses += 1
            self.loss_streak += 1
        else:
            self.unknown += 1
            self.loss_streak = 0

    def summary(self) -> str:
        return (f"{self.battles} trận (thắng {self.wins}, thua {self.losses}, không rõ {self.unknown}), "
                f"chiêu binh {self.recruits} lần, sang {self.maps} phó bản")


def _shown(m: SceneMatch) -> str:
    return m.name if m.found else f"không rõ (gần nhất {m.candidate})"


def _rect(v: Any, key: str) -> Rect:
    if not (isinstance(v, (list, tuple)) and len(v) == 4 and all(isinstance(n, int) for n in v)):
        raise ClearMapError(f"{key} phải là [x, y, w, h], không phải {v!r}")
    return Rect(*v)


def _same_army(seen: Mapping[Point, int], p: Point) -> Point:
    """The key of an army already attacked within ``SAME_TARGET_PX`` of ``p``, else ``p``."""
    for k in seen:
        if abs(k.x - p.x) <= SAME_TARGET_PX and abs(k.y - p.y) <= SAME_TARGET_PX:
            return k
    return p


def find_beams(frame: np.ndarray, roi: Rect | None = None, scale: float = 1.0) -> list[Rect]:
    """Light-blue beams (the game's "next army" marker) in a BGR ``frame``, largest first.

    Rectangles are frame pixels; ``roi`` limits the search, ``scale`` is the canvas scale.
    """
    x0, y0 = 0, 0
    img = frame
    if roi is not None:
        x0, y0 = max(0, roi.x), max(0, roi.y)
        img = frame[y0:max(y0, roi.bottom), x0:max(x0, roi.right)]
    if img.size == 0:
        return []
    b, g, r = (img[..., i].astype(np.int16) for i in range(3))
    mask = ((b > 200) & (g > 170) & (r < 170) & (b - r > 70)).astype(np.uint8)
    n, _labels, st, _c = cv2.connectedComponentsWithStats(mask)
    out: list[tuple[int, Rect]] = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in st[i])
        if (h >= BEAM_MIN_H * scale and w >= BEAM_MIN_W * scale and area >= BEAM_MIN_AREA * scale * scale
                and h >= BEAM_ASPECT * w):
            out.append((area, Rect(x0 + x, y0 + y, w, h)))
    return [rect for _a, rect in sorted(out, key=lambda t: -t[0])]


def search_regions(ctx: RunContext, hud_hidden: bool) -> list[Rect]:
    """Canvas rectangles searched for red flags (``coords/clear_map.yaml``)."""
    raw = ctx.coords.raw(REGIONS)
    if not isinstance(raw, list) or not raw:
        raise ClearMapError(f"{REGIONS} phải là danh sách [x, y, w, h]")
    out = [_rect(r, REGIONS) for r in raw]
    if hud_hidden and REGION_HUD_HIDDEN in ctx.coords:
        out.append(_rect(ctx.coords.raw(REGION_HUD_HIDDEN), REGION_HUD_HIDDEN))
    return out


@register
class ClearMap(Task):
    id = "clear_map"
    title = "Clear Phó Bản"
    description = (
        "Mở bản đồ Phó Bản (hoặc ở Thành chính) rồi bấm Bắt đầu: đánh lần lượt đạo quân có tia sáng xanh, "
        "hết thì sang phó bản kế tiếp"
    )
    order = 80
    Params = ClearMapParams
    start_scene = FIELD

    params: ClearMapParams

    # --- helpers ----------------------------------------------------------------------------------
    def _seen(self, ctx: RunContext, obj: str) -> ObjectResult | None:
        r = ctx.find_object(OBJECTS, obj)
        return r if r.found else None

    def _canvas_point(self, ctx: RunContext, frame_point: Point, offset_key: str) -> Point:
        """Canvas point = a frame point (match centre or corner) + a canvas offset."""
        fix = ctx.last_canvas or ctx.reliable_canvas()
        cx, cy = fix.to_canvas(frame_point)
        dx, dy = ctx.coords.offset(offset_key)
        return Point(round(cx + dx), round(cy + dy))

    def _escape(self, ctx: RunContext, presses: int = ESC_PRESSES) -> None:
        """v1.8.0 double-clicked (5, 1) for keyboard focus, then sent ESC."""
        ctx.click(FOCUS, double=True, label="lấy tiêu điểm")
        for _ in range(presses):
            ctx.sleep(self.params.delay_ms)
            ctx.checkpoint()
            ctx.input.key(VK_ESCAPE)

    # --- start ------------------------------------------------------------------------------------
    def _open_field(self, ctx: RunContext) -> None:
        p = self.params
        ctx.step("kiểm tra màn hình")
        m = ctx.detect_scene()
        if m.name == FIELD:
            return
        if m.name == ARMY:
            ctx.click_object(ARMY, "close")
            ctx.expect_scene(FIELD, p.scene_timeout_s)
            return
        if m.name in OPEN_FROM:
            ctx.step("mở Phó Bản")
            ctx.click_object(CITY, "dungeon")
            ctx.expect_scene(FIELD, p.scene_timeout_s)
            return
        raise ClearMapError(f"Hãy mở bản đồ Phó Bản rồi bấm Bắt đầu (màn hình hiện tại: {_shown(m)})")

    def _hide_hud(self, ctx: RunContext) -> bool:
        """Collapse the HUD icon rows and the quest tracker (v1.8.0 did both); True if icons are hidden."""
        ctx.step("thu gọn HUD")
        for obj, label in (("hud_toggle", "thu gọn biểu tượng"), ("quest_collapse", "thu gọn nhiệm vụ")):
            r = self._seen(ctx, obj)
            if r is not None:
                ctx.click(r, label=label)
                ctx.sleep(self.params.delay_ms)
        return self._seen(ctx, "hud_toggle") is None

    # --- popups -----------------------------------------------------------------------------------
    def _dismiss_popups(self, ctx: RunContext, check_notice: bool) -> None:
        p = self.params
        r = self._seen(ctx, "level_up")
        if r is not None:
            ctx.step("đóng thông báo lên cấp")
            ctx.click(r, label="Chúa công lên cấp")
            ctx.sleep(p.delay_ms)
            self._escape(ctx)
        res = self._result_marker(ctx)
        if res is not None:  # a result box that showed up after the battle was counted
            self._close_result(ctx, *res)
        if check_notice:
            r = self._seen(ctx, "notice_close")
            if r is not None:
                ctx.step("đóng thông báo")
                ctx.click(r, label="✕ thông báo")
                ctx.sleep(p.delay_ms)

    def _result_marker(self, ctx: RunContext) -> tuple[str, ObjectResult] | None:
        for obj in (*WIN_OBJECTS, *LOSE_OBJECTS, LEGACY_RESULT):
            r = self._seen(ctx, obj)
            if r is not None:
                return obj, r
        return None

    def _close_result(self, ctx: RunContext, obj: str, r: ObjectResult) -> None:
        """v1.8.0: Button_KetQua +(335, 480), then OK (538, 409); the boxes: OK only."""
        ctx.step("đóng kết quả trận")
        ctx.sleep(self.params.delay_ms)
        if obj == LEGACY_RESULT and r.detection is not None:
            target = self._canvas_point(ctx, r.detection.rect.top_left, RESULT_CLOSE_OFF)
            if CANVAS.contains(target):
                ctx.click(target, label="đóng kết quả")
                ctx.sleep(self.params.delay_ms)
        ctx.click(RESULT_OK, label="OK kết quả")

    # --- enemies ----------------------------------------------------------------------------------
    def _find_enemy(self, ctx: RunContext, hud_hidden: bool) -> Point | None:
        """Canvas point of the army under the light-blue beam, waiting up to ``wait_s`` (v1.8.0: 3 s)."""
        ctx.step("tìm đạo quân")
        regions = search_regions(ctx, hud_hidden)
        deadline = time.monotonic() + self.params.wait_s
        while True:
            target = self._beam_target(ctx, regions)
            if target is not None:
                return target
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ctx.sleep_s(min(ctx.poll_interval_s, remaining))

    def _beam_target(self, ctx: RunContext, regions: Sequence[Rect]) -> Point | None:
        """The red flag right under a beam whose tip is in ``regions`` (+ the click offset); with no
        flag there, a point below the tip."""
        frame, fix = ctx.observe()
        for beam in find_beams(frame, fix.rect, fix.scale):
            bx, by = fix.to_canvas(Point(beam.x + beam.w // 2, beam.bottom))
            tip = Point(round(bx), round(by))
            if not any(r.contains(tip) for r in regions):
                continue
            area = Rect.from_ltrb(tip.x - BEAM_FLAG_DX, tip.y + BEAM_FLAG_DY[0],
                                  tip.x + BEAM_FLAG_DX, tip.y + BEAM_FLAG_DY[1]).intersect(CANVAS)
            flag = ctx.find(ENEMY, region=area, threshold=ENEMY_THRESHOLD) if not area.is_empty() else None
            if flag is not None:
                return self._canvas_point(ctx, flag.center, ENEMY_OFF)
            target = Point(tip.x, min(tip.y + BEAM_ARMY_DY, CANVAS.bottom - 1))
            ctx.info(f"Không thấy cờ dưới tia sáng xanh tại {tip.as_tuple()}: bấm {target.as_tuple()}")
            return target
        return None

    def _attack(self, ctx: RunContext, target: Point, stats: _Stats) -> Outcome | None:
        """Click the army at canvas ``target``, then "Tấn công"; ``None`` when its dialog did not open."""
        p = self.params
        ctx.step("mở đạo quân")
        ctx.sleep(p.delay_ms)
        ctx.click(target, label="đạo quân")
        try:
            ctx.expect_scene(ARMY, p.wait_s)
        except SceneTimeout as exc:
            ctx.info(f"Không mở được bảng đạo quân tại {target.as_tuple()}: {exc}")
            return None
        ctx.sleep(p.delay_ms)
        ctx.step("tấn công")
        ctx.click_object(ARMY, "attack")
        return self._resolve_battle(ctx, stats)

    def _recruit(self, ctx: RunContext) -> None:
        """v1.8.0: (548, 331), wait "− +", "Tối đa" +(100, 3), confirm +(215, 30), ✕ (803, 120)."""
        p = self.params
        ctx.step("chiêu binh")
        box = self._seen(ctx, "recruit_max")
        if box is None:
            ctx.sleep(p.delay_ms)
            ctx.click(RECRUIT, label="chiêu binh")
            deadline = time.monotonic() + p.wait_s
            while box is None:
                ctx.sleep_s(ctx.poll_interval_s)
                box = self._seen(ctx, "recruit_max")
                if time.monotonic() >= deadline:
                    break
        if box is not None and box.detection is not None:
            corner = box.detection.rect.top_left
            ctx.sleep(p.delay_ms)
            ctx.click(self._canvas_point(ctx, corner, RECRUIT_MAX_OFF), label="tối đa")
            ctx.sleep(p.delay_ms)
            ctx.click(self._canvas_point(ctx, corner, RECRUIT_CONFIRM_OFF), label="xác nhận chiêu binh")
        else:
            ctx.info("Không thấy bảng chiêu binh")
        ctx.sleep(p.delay_ms)
        ctx.click(CLOSE_RECRUIT, label="đóng chiêu binh")
        ctx.sleep(p.delay_ms)

    def _resolve_battle(self, ctx: RunContext, stats: _Stats) -> Outcome | None:
        """Wait (bounded) until the battle is over and its result box closed; fixes #2.

        ``None`` when the army dialog closed after recruiting without a new "Tấn công" (no battle).
        """
        p = self.params
        ctx.step("chờ kết quả trận")
        start = time.monotonic()
        deadline = start + p.battle_timeout_s
        settle_s = max(p.wait_s, SETTLE_MIN_S)
        attack_at, attack_clicks, recruits = start, 1, 0
        reattack = False  # recruited: the battle starts only after "Tấn công" is pressed again
        outcome: Outcome | None = None
        field_since: float | None = None
        while True:
            ctx.checkpoint()
            now = time.monotonic()
            res = self._result_marker(ctx)
            if res is not None:
                obj, r = res
                if obj in WIN_OBJECTS:
                    outcome = Outcome.WIN
                elif obj in LOSE_OBJECTS:
                    outcome = Outcome.LOSE
                elif outcome is None:
                    outcome = Outcome.UNKNOWN
                self._close_result(ctx, obj, r)
                field_since = None
            elif self._seen(ctx, "army_attack") is not None and outcome is None:
                field_since = None
                if now - attack_at >= ATTACK_RETRY_S:
                    if attack_clicks >= MAX_ATTACK_CLICKS:
                        raise ClearMapError(
                            f"đã bấm 'Tấn công' {attack_clicks} lần nhưng bảng đạo quân vẫn mở "
                            "(hết lệnh, thiếu lính hoặc đạo quân chưa mở?)"
                        )
                    ctx.click_object(ARMY, "attack")
                    attack_at, attack_clicks = time.monotonic(), attack_clicks + 1
                    reattack = False
            elif self._needs_recruit(ctx, now - attack_at <= max(p.wait_s, 1.0)):
                recruits += 1
                if recruits > p.max_recruits:
                    raise ClearMapError(RECRUIT_FAILED)
                stats.recruits += 1
                self._recruit(ctx)
                attack_at, attack_clicks = float("-inf"), 0  # attack again as soon as the dialog shows
                reattack = True
                field_since = None
            elif self._seen(ctx, "dungeon_bar") is not None:
                if outcome is not None:
                    return outcome
                if reattack:
                    ctx.info("Bảng đạo quân đã đóng sau khi chiêu binh: chưa đánh")
                    return None
                field_since = now if field_since is None else field_since
                if now - field_since >= settle_s:
                    ctx.info("Trận kết thúc nhưng không thấy bảng kết quả")
                    return Outcome.UNKNOWN
            else:
                field_since = None  # battle screen, or a box we do not know: keep waiting
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WaitTimeout(f"không thấy kết quả trận sau {p.battle_timeout_s:.0f}s")
            ctx.sleep_s(min(ctx.poll_interval_s, remaining))

    def _needs_recruit(self, ctx: RunContext, just_attacked: bool) -> bool:
        """The recruit dialog, or (right after "Tấn công", as in v1.8.0) the generic recruit cue."""
        if self._seen(ctx, "recruit_max") is not None:
            return True
        return just_attacked and self._seen(ctx, "recruit_prompt") is not None

    # --- next dungeon -----------------------------------------------------------------------------
    def _next_dungeon(self, ctx: RunContext) -> bool:
        p = self.params
        r = self._seen(ctx, "next_dungeon")
        if r is None:
            return False
        ctx.step("sang phó bản kế tiếp")
        ctx.click(r, label="phó bản kế tiếp »")
        ctx.sleep(p.delay_ms)
        ctx.expect_scene(FIELD, p.scene_timeout_s)
        try:  # the "<Tên> 100%" banner covers the map for about 2 s
            ctx.wait_gone(SWITCH_BANNER, p.scene_timeout_s, region=SWITCH_BANNER_REGION,
                          threshold=SWITCH_BANNER_THRESHOLD)
        except WaitTimeout:
            ctx.warn("Băng rôn chuyển phó bản chưa tắt; tiếp tục")
        return True

    # --- main loop --------------------------------------------------------------------------------
    def run(self, ctx: RunContext) -> str:
        p = self.params
        stats = _Stats()
        self._open_field(ctx)
        hud_hidden = self._hide_hud(ctx) if p.hide_hud else False
        misses = 0
        just_switched = False
        attempts: dict[Point, int] = {}  # attacks per army (flag position) on the current map
        while True:
            ctx.checkpoint()
            if p.max_battles and stats.battles >= p.max_battles:
                return f"Đã đủ {p.max_battles} trận: {stats.summary()}"
            self._dismiss_popups(ctx, check_notice=misses == 0)
            enemy = self._find_enemy(ctx, hud_hidden)
            outcome: Outcome | None = None
            if enemy is not None:
                key = _same_army(attempts, enemy)
                if attempts.get(key, 0) >= SAME_TARGET_LIMIT:
                    raise ClearMapError(
                        f"đã đánh cùng một đạo quân {SAME_TARGET_LIMIT} lần mà nó vẫn còn: {stats.summary()}"
                    )
                attempts[key] = attempts.get(key, 0) + 1
                outcome = self._attack(ctx, enemy, stats)
            if outcome is None:  # no army, or its dialog did not open
                misses += 1
                ctx.info(f"Không thấy đạo quân ({misses}/{p.max_misses})")
                if just_switched or misses >= p.max_misses:
                    if just_switched:  # v1.8.0: the first miss after "»" ends the run
                        return f"Phó bản mới không có đạo quân: {stats.summary()}"
                    if p.next_map and (not p.max_maps or stats.maps < p.max_maps) and self._next_dungeon(ctx):
                        stats.maps += 1
                        ctx.count("maps")
                        misses, just_switched = 0, True
                        attempts.clear()
                        continue
                    return f"Đã hết đạo quân: {stats.summary()}"
                self._escape(ctx)
                continue
            misses, just_switched = 0, False
            stats.record(outcome)
            ctx.count("battles")
            ctx.progress(stats.battles, p.max_battles or None)
            ctx.info(f"Trận {stats.battles}: {_OUTCOME_VI[outcome]}")
            if stats.loss_streak >= p.max_losses:
                shot = ctx.save_failure(f"thua {stats.loss_streak} trận liên tiếp")
                ctx.warn(f"Thua {stats.loss_streak} trận liên tiếp" + (f" (ảnh: {shot})" if shot else ""))
                return f"Dừng vì thua {stats.loss_streak} trận liên tiếp: {stats.summary()}"

    @classmethod
    def import_legacy(cls, ini: Mapping[str, str]) -> dict[str, Any]:
        """``CPB_INPUT_MS`` (delay, ms) and ``CPB_INPUT_S`` (template wait, s); docs/04."""
        out: dict[str, Any] = {}
        try:
            ms = int(ini.get("CPB_INPUT_MS", "").strip())
            if 0 <= ms <= 60_000:
                out["delay_ms"] = ms
        except ValueError:
            pass
        try:
            s = float(ini.get("CPB_INPUT_S", "").strip())
            if 0.5 <= s <= 60.0:
                out["wait_s"] = s
        except ValueError:
            pass
        return out


_OUTCOME_VI: dict[Outcome, str] = {Outcome.WIN: "thắng", Outcome.LOSE: "thua", Outcome.UNKNOWN: "không rõ kết quả"}
