"""Nâng cấp công trình: upgrade city or harbour buildings (port of v1.8.0 ``NCCT_THANHCHINH`` /
``NCCT_HAICANG``, docs/03).

**Thành chính** (rewritten 2026-10-06; v1.8.0's 18 fixed points no longer matched this server's city)
reads the screen instead of clicking fixed points:

1. every panel that covers the city is collapsed: the usual ones (quest tracker, "Sắp mở", chat:
   ``panels.yaml``, done by the runner) plus the HUD icon rows (">>") and the left notice list
   ("<<"). The icon rows are opened again at the end (other features start from them);
2. the city level is read with OCR from the round badge under the avatar (``city_level_region``);
3. every building label "lvN" on the city is read with OCR (one pass over the whole canvas);
4. the lowest building whose level is below the city level is opened (click near its label,
   ``label_click_offsets``) and "Tăng cấp" is clicked while its panel stays open, then the panel is
   closed and the city read again;
5. a building whose level did not rise after a visit (upgrade timer running, not enough silver, no
   free builder) or whose panel did not open is left alone for the rest of the run;
6. the run ends by itself when no building below the city level is left.

With "Dùng vàng bỏ qua thời gian chờ" off, "Tăng cấp" is clicked once per visit: a second click only
asks to spend Vàng, which the pop-up guard declines and stops the run.

**Hải cảng** (screen not catalogued yet) keeps v1.8.0's 6 points: click the building, click "Tăng cấp"
``upgrades`` times, click ✕, with the user delay ``d`` around every click (``d/100`` before and
``d/10`` after each "Tăng cấp"). With ``verify_screens`` on (default) a stray dialog is closed first,
"Tăng cấp" is only clicked while the building panel is shown and ✕ only while it is still open; off
is v1.8.0's blind click sequence.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from ngoalong.core.geometry import Point, Rect
from ngoalong.core.ocr import OcrLine, number
from ngoalong.core.scenes import SceneMatch
from ngoalong.engine.context import RunContext, SceneTimeout
from ngoalong.engine.panels import PanelDef, PanelTidier
from ngoalong.engine.popups import TemplateSpot
from ngoalong.engine.registry import register
from ngoalong.engine.task import Task, TaskParams

COORDS = "nang_cap_cong_trinh"
UPGRADE_KEY = f"{COORDS}.upgrade"
CLOSE_KEY = f"{COORDS}.close"
CITY_LEVEL_KEY = f"{COORDS}.city_level_region"
CLICK_OFFSETS_KEY = f"{COORDS}.label_click_offsets"

CITY_SCENE = "city"
PANEL_SCENE = "city.building_panel"
TOAST_SCENE = "overlay.toast"
GOLD_POLICY = "spend_gold_skip_wait"

CANVAS = Rect(0, 0, 1000, 600)
# A stray dialog is closed at most this many times while waiting for a ready screen.
MAX_RECOVERY_CLOSES = 3
# Two labels read in different passes are the same building within this distance (label centres;
# "lv9" -> "lv10" grows the label by one digit).
SAME_X, SAME_Y = 20, 10
# Safety net against an endless loop on misread labels (each visit already needs a higher level).
MAX_VISITS = 1000

# Panels the runner's tidier leaves open (panels.yaml) but that hide parts of the city.
HUD_ICONS = PanelDef("hud_icons", "hàng biểu tượng trên cùng",
                     TemplateSpot("field.hud.icons_toggle", (822, 70), 8, 0.85))
NOTICE_LIST = PanelDef("notice_list", "danh sách thông báo bên trái",
                       TemplateSpot("hud.notice_collapse", (2, 129), 6, 0.95))


class Mode(StrEnum):
    THANH_CHINH = "thanh_chinh"
    HAI_CANG = "hai_cang"

    @property
    def label(self) -> str:
        return _MODE_LABELS[self]

    @property
    def buildings_key(self) -> str:
        return f"{COORDS}.{self.value}_buildings"


_MODE_LABELS: dict[Mode, str] = {Mode.THANH_CHINH: "Thành chính", Mode.HAI_CANG: "Hải cảng"}
HARBOUR_COUNT = 6
HARBOUR_PRE_UPGRADE_DIVISOR = 100  # v1.8.0 waited d/100 before each harbour "Tăng cấp"


def parse_building_list(text: str) -> list[int]:
    """``"1-3, 7,9"`` -> ``[1, 2, 3, 7, 9]`` (sorted, unique); ``""`` -> ``[]`` (= all)."""
    out: set[int] = set()
    for raw in text.replace(";", ",").split(","):
        part = raw.strip()
        if not part:
            continue
        lo_s, sep, hi_s = part.partition("-")
        try:
            lo = int(lo_s.strip())
            hi = int(hi_s.strip()) if sep else lo
        except ValueError:
            raise ValueError(f"'{part}' không phải số hoặc khoảng số (ví dụ 1-5,9)") from None
        if lo < 1 or hi < 1:
            raise ValueError(f"'{part}': số thứ tự công trình bắt đầu từ 1")
        if hi < lo:
            raise ValueError(f"'{part}': khoảng phải viết từ nhỏ đến lớn")
        out.update(range(lo, hi + 1))
    return sorted(out)


def format_building_list(numbers: Sequence[int]) -> str:
    """Inverse of ``parse_building_list``: ``[1, 2, 3, 7]`` -> ``"1-3,7"``."""
    parts: list[str] = []
    nums = sorted(set(numbers))
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ",".join(parts)


# --- reading the city (pure functions of OCR lines: unit-tested) -----------------------------------
# "lv80", "Iv1", "IV1", "'lv60" as RapidOCR reads the white building labels; a line may hold two
# labels side by side ("lv80 lv60").
_LABEL = re.compile(r"[lI1|i!][vV]\s*(\d{1,3})")
_LABEL_LINE = re.compile(r"(?:[lI1|i!][vV]\s*\d{1,3}\s*)+")
_STRIP = " '\"`.,:;-_"


@dataclass(frozen=True, slots=True)
class BuildingLabel:
    level: int
    rect: Rect  # canvas

    @property
    def center(self) -> Point:
        return self.rect.center

    def same_place(self, other: BuildingLabel) -> bool:
        a, b = self.center, other.center
        return abs(a.x - b.x) <= SAME_X and abs(a.y - b.y) <= SAME_Y


def parse_labels(lines: Sequence[OcrLine]) -> list[BuildingLabel]:
    """Building labels "lvN" among OCR lines (canvas rectangles); any other text is ignored."""
    out: list[BuildingLabel] = []
    for ln in lines:
        text = ln.text.strip(_STRIP)
        if not text or not _LABEL_LINE.fullmatch(text):
            continue
        lead = len(ln.text) - len(ln.text.lstrip(_STRIP))
        n = max(1, len(ln.text))
        for m in _LABEL.finditer(text):
            # split a joined line by character position
            x0 = ln.rect.x + ln.rect.w * (lead + m.start()) // n
            x1 = ln.rect.x + ln.rect.w * (lead + m.end()) // n
            out.append(BuildingLabel(int(m.group(1)), Rect(x0, ln.rect.y, max(1, x1 - x0), ln.rect.h)))
    return out


def parse_city_level(lines: Sequence[OcrLine]) -> int | None:
    """The city level from the OCR of the badge under the avatar ("80"), else ``None``."""
    value = number(" ".join(ln.text for ln in lines))
    return value if value is not None and 1 <= value <= 999 else None


class CongTrinhParams(TaskParams):
    mode: Mode = Field(Mode.THANH_CHINH, title="Khu vực")
    upgrades: int = Field(
        10, ge=1, le=10_000, title="Số lần bấm Tăng cấp mỗi công trình",
        description="Thành chính: mỗi lần mở công trình (chỉ 1 lần nếu không dùng Vàng bỏ qua thời gian chờ)",
    )
    delay_ms: int = Field(500, ge=0, le=60_000, title="Thời gian chờ giữa các click (ms)")
    buildings: str = Field(
        "", title="Công trình",
        description="Chỉ cho Hải cảng: để trống = tất cả (1-6). Ví dụ: 1-3,5. "
                    "Thành chính tự đọc cấp các công trình",
    )
    verify_screens: bool = Field(
        True, title="Kiểm tra màn hình trước khi click",
        description="Chỉ cho Hải cảng; tắt = click mù như v1.8.0. Thành chính luôn kiểm tra",
    )
    scene_timeout_s: float = Field(3.0, ge=0.5, le=120.0, title="Thời gian chờ màn hình (giây)")

    @field_validator("buildings")
    @classmethod
    def _normalize_buildings(cls, v: str) -> str:
        return format_building_list(parse_building_list(v))

    @model_validator(mode="after")
    def _check_buildings_fit_mode(self) -> CongTrinhParams:
        if self.mode is not Mode.HAI_CANG:
            return self  # the city list comes from the screen
        too_big = [n for n in parse_building_list(self.buildings) if n > HARBOUR_COUNT]
        if too_big:
            raise ValueError(
                f"{self.mode.label} chỉ có {HARBOUR_COUNT} công trình; không có số {format_building_list(too_big)}"
            )
        return self

    def targets(self) -> list[int]:
        """1-based harbour building numbers this run visits."""
        return parse_building_list(self.buildings) or list(range(1, HARBOUR_COUNT + 1))


@register
class CongTrinh(Task):
    id = "cong_trinh"
    title = "Nâng cấp công trình"
    description = (
        "Mở Thành chính (hoặc Hải cảng) rồi bấm Bắt đầu. Thành chính: tự đọc cấp thành và cấp từng "
        "công trình, nâng cấp các công trình thấp hơn cấp thành, hết thì tự dừng"
    )
    order = 30
    Params = CongTrinhParams
    start_scene = CITY_SCENE

    params: CongTrinhParams

    def run(self, ctx: RunContext) -> str:
        if self.params.mode is Mode.THANH_CHINH:
            return self._run_city(ctx)
        return self._run_harbour(ctx)

    # --- Thành chính: OCR ---------------------------------------------------------------------------
    def _run_city(self, ctx: RunContext) -> str:
        p = self.params
        self._wait_ready(ctx)
        icons_hidden = self._reveal_city(ctx)
        city_level = self._city_level(ctx)
        clicks = p.upgrades if ctx.policy(GOLD_POLICY) else 1

        visited: list[tuple[BuildingLabel, int]] = []  # (place, level when last visited)
        given_up: list[BuildingLabel] = []  # not opened, or level did not rise after a visit
        raised: dict[int, tuple[int, int]] = {}  # visited index -> (first level, last level seen)
        total = 0
        first = True
        for _ in range(MAX_VISITS):
            self._wait_ready(ctx)
            labels = self._scan(ctx)
            # record the levels reached since the last visits; a building that did not rise is done
            for i, (place, before) in enumerate(visited):
                now = next((b for b in labels if b.same_place(place)), None)
                if now is None:
                    continue
                if i in raised:
                    raised[i] = (raised[i][0], max(raised[i][1], now.level))
                if now.level <= before and not any(g.same_place(place) for g in given_up):
                    given_up.append(place)
            todo = [b for b in labels if b.level < city_level and not any(g.same_place(b) for g in given_up)]
            if first:
                ctx.info(f"Thành chính cấp {city_level}: đọc được {len(labels)} công trình, "
                         f"{len(todo)} công trình dưới cấp {city_level}")
                first = False
            total = max(total, len(todo) + len(given_up))
            ctx.progress(total - len(todo), total)
            if not todo:
                break
            b = min(todo, key=lambda x: (x.level, x.center.y, x.center.x))
            idx = next((i for i, (place, _) in enumerate(visited) if place.same_place(b)), None)
            if idx is None:
                visited.append((b, b.level))
                idx = len(visited) - 1
                raised[idx] = (b.level, b.level)
            else:
                visited[idx] = (b, b.level)
            if not self._visit(ctx, b, clicks):
                given_up.append(b)
        else:
            ctx.warn(f"dừng sau {MAX_VISITS} lần mở công trình")

        if icons_hidden:
            self._restore_icons(ctx)
        ups = sorted((a, z) for a, z in raised.values() if z > a)
        msg = f"Thành chính cấp {city_level}: "
        if not visited:
            return msg + f"không còn công trình nào dưới cấp {city_level}"
        msg += f"đã mở {len(visited)} công trình"
        if ups:
            msg += ", lên cấp " + ", ".join(f"lv{a}→lv{z}" for a, z in ups)
        if given_up:
            msg += (f"; {len(given_up)} công trình chưa lên cấp (đang chờ, thiếu tài nguyên hoặc không mở "
                    "được): " + ", ".join(f"lv{g.level}" for g in sorted(given_up, key=lambda g: g.level)))
        return msg

    def _reveal_city(self, ctx: RunContext) -> bool:
        """Collapse the HUD icon rows and the left notice list; True if the icon rows were hidden."""
        ctx.step("thu gọn biểu tượng và thông báo")
        closed = PanelTidier([HUD_ICONS, NOTICE_LIST], settle_s=max(0.3, self.params.delay_ms / 1000)).tidy(ctx)
        if closed:
            ctx.info("Đã thu gọn: " + ", ".join(closed))
        return HUD_ICONS.description in closed

    def _restore_icons(self, ctx: RunContext) -> None:
        try:
            self._wait_ready(ctx)
        except SceneTimeout:
            return
        ctx.step("mở lại hàng biểu tượng")
        ctx.click_object(CITY_SCENE, "hud_toggle")
        ctx.sleep(self.params.delay_ms)

    def _city_level(self, ctx: RunContext) -> int:
        region = _rect(ctx.coords.raw(CITY_LEVEL_KEY), CITY_LEVEL_KEY)
        deadline = time.monotonic() + self.params.scene_timeout_s
        ctx.step("đọc cấp Thành chính")
        while True:
            lines = ctx.read_text(region)
            level = parse_city_level(lines)
            if level is not None:
                return level
            if time.monotonic() >= deadline:
                seen = " ".join(ln.text for ln in lines) or "không thấy chữ"
                raise ValueError(f"không đọc được cấp Thành chính ở góc trên bên trái (OCR: {seen})")
            ctx.sleep_s(ctx.poll_interval_s)

    def _scan(self, ctx: RunContext) -> list[BuildingLabel]:
        ctx.step("đọc cấp các công trình")
        return parse_labels(ctx.read_text(CANVAS))

    def _visit(self, ctx: RunContext, b: BuildingLabel, clicks: int) -> bool:
        """Open the building of label ``b`` and upgrade it; False when no click opened its panel."""
        p = self.params
        name = f"công trình lv{b.level} ({b.center.x}, {b.center.y})"
        offsets = ctx.coords.offsets(CLICK_OFFSETS_KEY)
        for k, (dx, dy) in enumerate(offsets):
            if k:
                self._wait_ready(ctx)
            ctx.sleep(p.delay_ms)
            ctx.step(f"{name}: mở")
            ctx.click(Point(b.center.x + dx, b.center.y + dy), label=name)
            try:
                ctx.expect_scene(PANEL_SCENE, p.scene_timeout_s)
                break
            except SceneTimeout:
                continue
        else:
            ctx.warn(f"{name}: không mở được bảng nâng cấp; bỏ qua")
            return False
        self._upgrade_in_panel(ctx, name, clicks, pre_ms=p.delay_ms / 10, verify=True)
        return True

    # --- Hải cảng: v1.8.0 points --------------------------------------------------------------------
    def _run_harbour(self, ctx: RunContext) -> str:
        p = self.params
        offsets = ctx.coords.offsets(Mode.HAI_CANG.buildings_key)
        targets = p.targets()
        missing = [n for n in targets if n > len(offsets)]
        if missing:
            raise ValueError(
                f"tệp tọa độ chỉ có {len(offsets)} công trình Hải cảng; thiếu số {format_building_list(missing)}"
            )
        upgraded: list[int] = []
        skipped: list[int] = []
        for done, n in enumerate(targets, start=1):
            if self._harbour_building(ctx, n):
                upgraded.append(n)
            else:
                skipped.append(n)
            ctx.progress(done, len(targets))

        msg = f"Đã nâng cấp {len(upgraded)}/{len(targets)} công trình Hải cảng, mỗi công trình {p.upgrades} lần"
        if skipped:
            msg += f"; không mở được bảng nâng cấp: {format_building_list(skipped)}"
        return msg

    def _harbour_building(self, ctx: RunContext, n: int) -> bool:
        """Upgrade harbour building ``n`` (1-based); False when its panel did not open."""
        p = self.params
        d = p.delay_ms
        key = f"{Mode.HAI_CANG.buildings_key}.{n - 1}"
        if p.verify_screens:
            ctx.step(f"công trình {n}: kiểm tra màn hình")
            self._wait_ready(ctx)
        ctx.sleep(d)
        ctx.step(f"công trình {n}: mở")
        ctx.click(key, label=f"công trình {n}")
        if p.verify_screens:
            try:
                ctx.expect_scene(PANEL_SCENE, p.scene_timeout_s)
            except SceneTimeout as exc:
                ctx.warn(f"công trình {n}: không mở được bảng nâng cấp ({exc}); bỏ qua")
                return False
        else:
            ctx.sleep(d)
        self._upgrade_in_panel(ctx, f"công trình {n}", p.upgrades, pre_ms=d / HARBOUR_PRE_UPGRADE_DIVISOR,
                               verify=p.verify_screens)
        return True

    # --- the building panel -------------------------------------------------------------------------
    def _upgrade_in_panel(self, ctx: RunContext, name: str, clicks: int, pre_ms: float, verify: bool) -> None:
        """Click "Tăng cấp" ``clicks`` times while the panel is open, then close the panel."""
        d = self.params.delay_ms
        for j in range(clicks):
            ctx.sleep(pre_ms)
            if verify and not self._panel_shown(ctx):
                ctx.warn(f"{name}: bảng nâng cấp đã đóng sau {j} lần nâng cấp")
                break
            ctx.step(f"{name}: tăng cấp {j + 1}/{clicks}")
            ctx.click(UPGRADE_KEY, label="Tăng cấp")
            ctx.sleep(d / 10)

        ctx.sleep(d)
        if not verify or self._panel_shown(ctx):
            ctx.step(f"{name}: đóng bảng")
            ctx.click(CLOSE_KEY, label="đóng bảng")
        ctx.sleep(d)

    # --- screen checks --------------------------------------------------------------------------
    def _panel_shown(self, ctx: RunContext) -> bool:
        """The building panel is on screen now (single check, no wait)."""
        try:
            ctx.expect_scene(PANEL_SCENE, 0.0)
        except SceneTimeout:
            return False
        return True

    def _is_ready(self, m: SceneMatch) -> bool:
        if self.params.mode is Mode.THANH_CHINH:
            return m.name == CITY_SCENE
        # The harbour screen is not catalogued: accept any screen that is not a dialog.
        return m.kind != "modal"

    def _wait_ready(self, ctx: RunContext) -> SceneMatch:
        """Poll until the building screen is shown with no dialog; close toasts and stray dialogs."""
        p = self.params
        deadline = time.monotonic() + p.scene_timeout_s
        closes = 0
        while True:
            m = ctx.detect_scene()
            if TOAST_SCENE in m.overlays and closes < MAX_RECOVERY_CLOSES:
                ctx.info("đóng thông báo nổi")
                ctx.click_object(TOAST_SCENE, "close")
                closes += 1
                ctx.sleep_s(ctx.poll_interval_s)
                continue
            if self._is_ready(m):
                return m
            if m.found and m.kind == "modal" and closes < MAX_RECOVERY_CLOSES \
                    and "close" in ctx.scenes.get(m.name).objects:  # type: ignore[union-attr]
                ctx.info(f"đóng hộp thoại {m.name}")
                ctx.click_object(m.name, "close")
                closes += 1
                deadline = max(deadline, time.monotonic() + p.scene_timeout_s)
                ctx.sleep_s(ctx.poll_interval_s)
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                want = "Thành chính" if p.mode is Mode.THANH_CHINH else "màn hình không có hộp thoại"
                shown = m.name if m.found else f"không rõ (gần nhất {m.candidate})"
                raise SceneTimeout(
                    f"không thấy {want} sau {p.scene_timeout_s:.1f}s (đang hiển thị: {shown})",
                    CITY_SCENE, m,
                )
            ctx.sleep_s(min(ctx.poll_interval_s, remaining))

    @classmethod
    def required_templates(cls) -> set[str]:
        return {HUD_ICONS.open.template, NOTICE_LIST.open.template}

    @classmethod
    def import_legacy(cls, ini: Mapping[str, str]) -> dict[str, Any]:
        """``CAPDO`` (upgrades per building) and ``NCCT_SLEEP`` (ms); the mode radio was not saved."""
        out: dict[str, Any] = {}
        for key, field, lo, hi in (("CAPDO", "upgrades", 1, 10_000), ("NCCT_SLEEP", "delay_ms", 0, 60_000)):
            try:
                value = int(ini.get(key, "").strip())
            except ValueError:
                continue
            if value < lo:
                continue
            out[field] = min(value, hi)
        return out


def _rect(raw: Any, key: str) -> Rect:
    if not isinstance(raw, list) or len(raw) != 4:
        raise ValueError(f"{key} phải là [x, y, w, h]")
    x, y, w, h = (int(v) for v in raw)
    return Rect(x, y, w, h)
