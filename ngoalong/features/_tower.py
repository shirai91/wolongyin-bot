"""Shared steps of the trial-tower features (``chua_cong`` and ``thap_thi_luyen``).

Both v1.8.0 routines (``CAYCAPCHUACONG``, ``THAPTHILUYEN``) open the Tháp Thí Luyện dialog, go to
page 1 of the floor list, page forward to the wanted floor and click its slot, then enter the tower
through "Vượt Tháp" and the confirm box. They used fixed offsets and blind ``Sleep`` calls; here
every step waits for the scene it expects (``scenes/tower.yaml``) with a time limit, and Stop /
Pause work everywhere (the waits are ``RunContext`` waits).

Offsets come from ``coords/thap.yaml`` (the v1.8.0 values, unchanged) plus the page size
``thap.floors_per_page`` (10, as v1.8.0). The floor list only shows the floors the player has
unlocked, so a page can hold fewer than 10 buttons; two live checks guard against that:
``require_floor`` stops when the wanted floor's slot is empty (not unlocked yet) and
``next_pages`` stops when "page next" does not change the page number. The module name starts
with ``_`` so the feature registry does not import it as a feature.
"""

from __future__ import annotations

import time
from enum import StrEnum
from itertools import takewhile

import numpy as np

from ngoalong.core.errors import NgoaLongError
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.input import VK_BACK, VK_RETURN
from ngoalong.core.scenes import SceneMatch
from ngoalong.engine.context import RunContext, SceneTimeout

# --- scenes (scenes/tower.yaml, scenes/city.yaml) -----------------------------------------------
DIALOG = "tower.dialog"
CONFIRM = "tower.confirm"
EXIT_CONFIRM = "tower.exit_confirm"
BATTLE = "tower.battle"
CITY = "city"
PANEL = "city.building_panel"

# --- coords (coords/thap.yaml) ------------------------------------------------------------------
KEY = "thap"
PAGE_BACK = f"{KEY}.page_back"
PAGE_NEXT = f"{KEY}.page_next"
FLOOR_X = f"{KEY}.floor_list_x"
FLOOR_Y0 = f"{KEY}.floor_list_y0"
SWEEP = f"{KEY}.sweep_button"
CHALLENGE = f"{KEY}.challenge_button"
SEND_BOX = f"{KEY}.ttl_send_box"
PER_PAGE_KEY = f"{KEY}.floors_per_page"
STEP_Y_KEY = f"{KEY}.floor_step_y"

# Floor slots per dialog page: 10, as v1.8.0 (TOADOVITRI_Y[1..10]). The list box has room for 10
# buttons (canvas y 209..452) but only shows UNLOCKED floors: with 6 floors unlocked it shows
# Tầng 1-6 and slots 7-10 are empty panel (live, 2026-10-06). An earlier port read "5 floors
# listed" as a 5-floor page, so floor 6 became "page next + slot 1" and, with no page 2 yet, the
# bot clicked Tầng 1. The value used at run time is ``thap.floors_per_page``; this is the fallback.
FLOORS_PER_PAGE = 10
# v1.8.0 constants (v1.8.0 app_logic_only.au3).
LEGACY_FLOORS_PER_PAGE = 10  # TOADOVITRI_Y[1..10]: v1.8.0's paging (same as FLOORS_PER_PAGE)
FLOOR_STEP_Y = 25  # TOADOVITRI_Y[n] = TOADOVITRI_Y[n-1] + 25
PAGE_BACK_CLICKS = 26  # CAYCAPCHUACONG: For K = 0 To 25
TOKENS_PER_ENTRY = 3  # "Mỗi lần Vượt tháp mất 3 Thí luyện lệnh"

PAGE_CLICK_GAP_MS = 80  # v1.8.0 clicked "page back" 26x with no pause; give Flash a moment
EXIT_RETRY_S = 3.0  # "Thoát" is clicked again when the battle screen is still shown after this

# --- live checks of the floor list (canvas pixels at 1000x600) ------------------------------------
# A floor button spans x 200..296 around floor_list_x 245; its centre is 3 px below v1.8.0's click
# row. It has a continuous bright border line 10 px above and 9 px below its centre (live: row mean
# ~69, 95/95 neighbouring pixels within 6 grey levels). Empty slots are flat dark panel (mean 22),
# and the chat overlay's text, which can run over the lower slots, never forms such full-width
# lines, so a slot counts as filled only when both border lines are found.
SLOT_BOX_DX, SLOT_BOX_DY, SLOT_BOX_W, SLOT_BOX_H = -45, -9, 96, 24  # canvas rows p.y-9 .. p.y+14
SLOT_EDGE_ROWS = 5  # top edge searched in the first 5 rows (p.y-9..-5), bottom in the last 5
SLOT_LINE_MIN_MEAN = 50.0
SLOT_LINE_MIN_SMOOTH = 0.88  # share of neighbouring pixel pairs differing by less than 6
# The page number box between the arrows (inner area x 220..276, y 468..484; "1" at x 245..249).
# Only its GREY pixels are read: the chat overlay can run over the box, but chat text is coloured
# (yellow / orange / green, chroma > 150), while the page number is pure grey (chroma 0; the "1"
# is an anti-aliased stem 47 / 161 / 71). Pixels under chat text are masked out of comparisons.
PAGE_DIGITS = Rect(224, 469, 50, 14)
# The same number is drawn grey (peak ~166) or bright white (peak ~255) depending on the box state
# (live, 2026-10-06), with the same shape: digit pixels are those above DIGIT_REL_LEVEL of the
# box's brightest grey pixel (and above DIGIT_FLOOR).
DIGIT_FLOOR = 40
DIGIT_REL_LEVEL = 0.4
DIGIT_MAX_CHROMA = 40  # max - min channel of a digit pixel (grey, not coloured)
COVER_MIN_LEVEL = 60  # a coloured pixel at least this bright is chat text covering the box
PAGE_TURN_TIMEOUT_S = 3.0
PAGE_ONE = "tower.pager.page1"  # template: the page box showing "1" (cut at canvas (236, 469))
PAGE_ONE_AT = (236, 469)
PAGER_NEXT = "tower.pager.next"  # ▶ of the floor list: shown only on the "Tháp" tab
PAGER_NEXT_REGION = Rect(279, 461, 25, 31)
TAB_THAP = Point(222, 159)  # the dialog's "Tháp" tab (docs/07)
MAX_BACK_CLICKS = 30  # 220 floors = 22 pages; v1.8.0 clicked "page back" 26 times


class PageStrategy(StrEnum):
    """How Tháp Thí Luyện reaches the page of its start floor (user choice, 2026-10-06)."""

    BACK_THEN_NEXT = "back_then_next"  # ◀ until the page box shows 1, then ▶ to the page
    TYPE_PAGE = "type_page"  # type the page number into the page box, Enter, check it stayed

    @property
    def label(self) -> str:
        return _STRATEGY_LABELS[self]


_STRATEGY_LABELS = {
    PageStrategy.BACK_THEN_NEXT: "Về trang 1 rồi bấm sang trang (chỉ bấm chuột)",
    PageStrategy.TYPE_PAGE: "Gõ số trang vào ô trang rồi Enter",
}


class TowerError(NgoaLongError):
    """The tower dialog could not be reached, or the game showed something unexpected."""


def shown(m: SceneMatch | None) -> str:
    """Vietnamese description of a scene result for error messages."""
    if m is None:
        return "không rõ"
    return m.name if m.found else f"không rõ (gần nhất {m.candidate})"


# --- floor arithmetic ---------------------------------------------------------------------------
def floor_position(floor: int, per_page: int = FLOORS_PER_PAGE) -> tuple[int, int]:
    """``(next_clicks, slot)`` for ``floor`` counted from page 1.

    ``page = ceil(floor/per_page) - 1`` "next" clicks, ``slot = floor - page*per_page``
    (1..per_page). Callers pass ``per_page(ctx)`` (10, as v1.8.0). The decompiled AutoIt added
    ``4294967295`` (a 32-bit -1) for multiples of the page size; this is the intended ``-1``, so the
    last floor of a page is its last slot.
    """
    if floor < 1:
        raise ValueError(f"floor must be >= 1, got {floor}")
    if per_page < 1:
        raise ValueError(f"per_page must be >= 1, got {per_page}")
    page = (floor - 1) // per_page  # == ceil(floor / per_page) - 1
    return page, floor - page * per_page


def per_page(ctx: RunContext) -> int:
    """Floors per dialog page: ``thap.floors_per_page`` from the profile (fallback 10)."""
    if PER_PAGE_KEY not in ctx.coords:
        return FLOORS_PER_PAGE
    n = int(ctx.coords.value(PER_PAGE_KEY))
    if n < 1:
        raise ValueError(f"{PER_PAGE_KEY} must be >= 1, got {n}")
    return n


def slot_point(ctx: RunContext, slot: int) -> Point:
    """Canvas point of floor slot ``slot`` (1..``per_page(ctx)``) in the dialog's floor list."""
    n = per_page(ctx)
    if not 1 <= slot <= n:
        raise ValueError(f"slot must be 1..{n}, got {slot}")
    x = int(ctx.coords.value(FLOOR_X))
    y0 = int(ctx.coords.value(FLOOR_Y0))
    step = int(ctx.coords.value(STEP_Y_KEY)) if STEP_Y_KEY in ctx.coords else FLOOR_STEP_Y
    return Point(x, y0 + step * (slot - 1))


# --- floor list checks --------------------------------------------------------------------------
def _grey(frame: np.ndarray, rect: Rect) -> np.ndarray:
    """Grey-level crop of ``rect`` (frame pixels), clipped to the frame."""
    h, w = frame.shape[:2]
    x0, y0 = max(0, rect.x), max(0, rect.y)
    x1, y1 = min(w, rect.x + rect.w), min(h, rect.y + rect.h)
    crop = frame[y0:y1, x0:x1]
    if crop.ndim == 3:
        crop = crop[..., :3].mean(axis=2)
    return crop.astype(np.float32)


def slot_rect(ctx: RunContext, slot: int) -> Rect:
    """Canvas rectangle of floor button ``slot`` in the floor list."""
    p = slot_point(ctx, slot)
    return Rect(p.x + SLOT_BOX_DX, p.y + SLOT_BOX_DY, SLOT_BOX_W, SLOT_BOX_H)


def _has_line(rows: np.ndarray) -> bool:
    """True when one of ``rows`` is a bright, continuous horizontal line (a button border)."""
    for row in rows:
        if row.size < 2:
            continue
        smooth = float((np.abs(np.diff(row)) < 6).mean())
        if float(row.mean()) >= SLOT_LINE_MIN_MEAN and smooth >= SLOT_LINE_MIN_SMOOTH:
            return True
    return False


def _slot_filled(crop: np.ndarray) -> bool:
    h = crop.shape[0]
    if h < 2 or crop.shape[1] < 2:
        return False
    k = max(1, round(h * SLOT_EDGE_ROWS / SLOT_BOX_H))
    return _has_line(crop[:k]) and _has_line(crop[h - k:])


def slot_states(ctx: RunContext) -> list[bool]:
    """For each slot of the current page (on one fresh frame): True when a floor button is shown.

    The list only shows unlocked floors, so the filled slots are the unlocked floors of the page.
    """
    frame, fix = ctx.observe()
    return [_slot_filled(_grey(frame, fix.rect_to_frame(slot_rect(ctx, s))))
            for s in range(1, per_page(ctx) + 1)]


def unlocked_slots(ctx: RunContext) -> int:
    """Number of floor buttons on the current page (filled slots from the top)."""
    return sum(1 for _ in takewhile(bool, slot_states(ctx)))


def require_floor(ctx: RunContext, floor: int, page: int, slot: int) -> None:
    """Stop with a clear message when ``floor`` (page ``page``, 0-based, slot ``slot``) is not shown.

    Clicking an empty slot does nothing in the game, so the next "Vượt Tháp" would enter whatever
    floor was selected before (Tầng 1 after the page box reset): never click blind.
    """
    states = slot_states(ctx)
    if states[slot - 1]:
        return
    n = per_page(ctx)
    top = page * n + sum(1 for _ in takewhile(bool, states))
    if top > page * n:
        hint = f"bảng Tháp Thí Luyện chỉ mở đến tầng {top}. Hãy chọn tầng từ 1 đến {top}"
    else:
        hint = f"trang {page + 1} của bảng Tháp Thí Luyện chưa có tầng nào"
    raise TowerError(f"Tầng {floor} chưa mở khoá: {hint}")


# --- the page number box -------------------------------------------------------------------------
def _dilate(mask: np.ndarray) -> np.ndarray:
    """Grow a boolean mask by 1 px in every direction (8-neighbourhood)."""
    out = mask.copy()
    h, w = mask.shape
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                src = mask[max(0, -dy):h - max(0, dy), max(0, -dx):w - max(0, dx)]
                out[max(0, dy):max(0, dy) + src.shape[0], max(0, dx):max(0, dx) + src.shape[1]] |= src
    return out


class PageView:
    """The page number box of one frame: grey digit pixels and the pixels covered by chat text."""

    __slots__ = ("cover", "digits")

    def __init__(self, image: np.ndarray) -> None:
        if image.ndim != 3 or image.size == 0:
            shape = image.shape[:2]
            self.digits = np.zeros(shape, dtype=bool)
            self.cover = np.zeros(shape, dtype=bool)
            return
        bgr = image[..., :3].astype(np.int16)
        hi, lo = bgr.max(axis=2), bgr.min(axis=2)
        self.cover = _dilate((hi >= COVER_MIN_LEVEL) & (hi - lo > DIGIT_MAX_CHROMA))
        grey = (hi >= DIGIT_FLOOR) & (hi - lo <= DIGIT_MAX_CHROMA) & ~self.cover
        if not grey.any():
            self.digits = grey
            return
        level = max(DIGIT_FLOOR, DIGIT_REL_LEVEL * float(hi[grey].max()))
        self.digits = grey & (hi >= level)

    @classmethod
    def of(cls, digits: np.ndarray) -> PageView:
        v = cls(np.zeros((0, 0), dtype=bool))
        v.digits, v.cover = digits.astype(bool), np.zeros(digits.shape, dtype=bool)
        return v


def digit_mask(image: np.ndarray) -> np.ndarray:
    """Boolean mask of the grey (digit) pixels of a BGR crop, chat-covered pixels excluded."""
    return PageView(image).digits


SAME_MAX_DIFF = 2  # pixels allowed to differ between two renderings of the same page number


def same_page(a: PageView, b: PageView) -> bool:
    """The two boxes show the same number at the same place, ignoring chat-covered pixels of either
    frame. Strict: the digits are ~4x9 px, so "2" / "3" or "11" / "12" differ by only a few pixels;
    the game draws the same number identically every frame. Both fully covered counts as "same"
    (nothing can be told apart)."""
    if a.digits.shape != b.digits.shape:
        return False
    hidden = a.cover | b.cover
    return int(((a.digits ^ b.digits) & ~hidden).sum()) <= SAME_MAX_DIFF


def _glyphs(mask: np.ndarray) -> np.ndarray:
    """The digits of a page-box mask, trimmed to their bounding box, without a text cursor.

    A cursor is a lone 1-px column (both neighbours empty); trimming makes the comparison
    independent of where the field draws its text while editing or not.
    """
    m = mask.copy()
    cols = m.sum(axis=0)
    for x in range(m.shape[1]):
        left = cols[x - 1] if x > 0 else 0
        right = cols[x + 1] if x + 1 < m.shape[1] else 0
        if cols[x] and not left and not right:
            m[:, x] = False
    ys, xs = np.nonzero(m)
    if xs.size == 0:
        return np.zeros((0, 0), dtype=bool)
    return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def glyphs_equal(a: np.ndarray, b: np.ndarray) -> bool:
    """True when two page-box masks show the same digits, wherever the field drew them.

    Both are trimmed to their digits; the shapes must match pixel for pixel (at most
    ``SAME_MAX_DIFF`` differences) at the best of the 1-px shifts.
    """
    ga, gb = _glyphs(a), _glyphs(b)
    if ga.size == 0 or gb.size == 0:
        return ga.size == gb.size
    if abs(ga.shape[0] - gb.shape[0]) > 1 or abs(ga.shape[1] - gb.shape[1]) > 1:
        return False
    h, w = max(ga.shape[0], gb.shape[0]) + 2, max(ga.shape[1], gb.shape[1]) + 2
    pa = np.zeros((h, w), dtype=bool)
    pa[1:1 + ga.shape[0], 1:1 + ga.shape[1]] = ga
    best = None
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            if dy + gb.shape[0] > h or dx + gb.shape[1] > w:
                continue
            pb = np.zeros((h, w), dtype=bool)
            pb[dy:dy + gb.shape[0], dx:dx + gb.shape[1]] = gb
            diff = int((pa ^ pb).sum())
            best = diff if best is None else min(best, diff)
    return best is not None and best <= SAME_MAX_DIFF


def page_box_empty(view: PageView) -> bool:
    return _glyphs(view.digits).size == 0


def text_kept(typed: PageView, after: PageView) -> bool:
    """After Enter the box still shows what was typed (same place, or same digits re-aligned)."""
    if same_page(typed, after):
        return True
    hidden = typed.cover | after.cover
    return glyphs_equal(typed.digits & ~hidden, after.digits & ~hidden)


def page_view(ctx: RunContext) -> PageView:
    """The page number box on a fresh frame."""
    frame, fix = ctx.observe()
    r = fix.rect_to_frame(PAGE_DIGITS)
    h, w = frame.shape[:2]
    return PageView(frame[max(0, r.y):min(h, r.y + r.h), max(0, r.x):min(w, r.x + r.w)])


def _page_one_mask(ctx: RunContext, shape: tuple[int, int]) -> np.ndarray:
    """The page box showing "1", as a digit mask of ``shape`` (from the live-cut template)."""
    t = ctx.matcher.templates.get(PAGE_ONE, 1.0).image
    ref = np.zeros((PAGE_DIGITS.h, PAGE_DIGITS.w), dtype=bool)
    dx, dy = PAGE_ONE_AT[0] - PAGE_DIGITS.x, PAGE_ONE_AT[1] - PAGE_DIGITS.y
    m = digit_mask(t)[: PAGE_DIGITS.h - dy, : PAGE_DIGITS.w - dx]
    ref[dy:dy + m.shape[0], dx:dx + m.shape[1]] = m
    if ref.shape != shape:  # another zoom level
        import cv2

        ref = cv2.resize(ref.astype(np.uint8), (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST).astype(bool)
    return ref


def is_page_one(ctx: RunContext) -> bool:
    """The page box shows "1" (shape compared wherever it sits: the canvas fix can be 1 px off)."""
    v = page_view(ctx)
    if page_box_empty(v):
        return False
    ref = _page_one_mask(ctx, v.digits.shape)
    return glyphs_equal(v.digits, ref & ~v.cover)


def _wait_page_change(ctx: RunContext, before: PageView) -> bool:
    """Poll the page box until its digits differ from ``before``; False after PAGE_TURN_TIMEOUT_S."""
    deadline = time.monotonic() + PAGE_TURN_TIMEOUT_S
    while same_page(before, page_view(ctx)):
        if time.monotonic() >= deadline:
            return False
        ctx.sleep_s(min(ctx.poll_interval_s, max(0.0, deadline - time.monotonic())))
    return True


# --- the floor list must be on screen before any click or key ------------------------------------
def floor_list_shown(ctx: RunContext) -> bool:
    """True when the "Tháp" tab's floor list is shown (its ▶ page arrow is visible).

    The dialog title is the same on the Luyện / Điểm tướng tabs, where the page box does not
    exist: keys sent there can press a focused button (Enter on "Điểm tướng" spends Hồn Tướng).
    """
    return ctx.find(PAGER_NEXT, region=PAGER_NEXT_REGION, threshold=0.85) is not None


def ensure_floor_list(ctx: RunContext, timeout_s: float) -> None:
    """Make sure the floor list is shown: click the "Tháp" tab once if another tab is open."""
    if floor_list_shown(ctx):
        return
    if ctx.detect_scene().name != DIALOG:
        raise TowerError(f"Không thấy bảng Tháp Thí Luyện (màn hình hiện tại: {shown(ctx.last_scene)})")
    ctx.step("chọn thẻ Tháp")
    ctx.click(TAB_THAP, label="thẻ Tháp")
    deadline = time.monotonic() + max(0.5, timeout_s)
    while not floor_list_shown(ctx):
        if time.monotonic() >= deadline:
            raise TowerError("Không mở được thẻ \"Tháp\" của bảng Tháp Thí Luyện (không thấy danh sách tầng)")
        ctx.sleep_s(ctx.poll_interval_s)


# --- input helpers ------------------------------------------------------------------------------
def type_text(ctx: RunContext, text: str) -> None:
    ctx.checkpoint()
    ctx.input.text(text)


def press_key(ctx: RunContext, vk: int) -> None:
    ctx.checkpoint()
    ctx.input.key(vk)


# --- steps --------------------------------------------------------------------------------------
def open_dialog(ctx: RunContext, timeout_s: float, delay_ms: int, from_city: bool) -> None:
    """Make sure the Tháp Thí Luyện dialog is shown.

    v1.8.0 clicked (525, 320) twice in the city; on this server the pagoda is at (460, 320) and the
    second click must hit the building panel's "Tháp Thí Luyện" button (docs/07). The port clicks
    the ``city.tower_building`` object, waits for the panel, clicks its ``action_1`` and waits for
    the dialog. A confirm box left open on the dialog is cancelled. Anything else stops the run.
    """
    ctx.step("mở Tháp Thí Luyện")
    m = ctx.detect_scene()
    if m.name == DIALOG:
        return
    if m.name == CONFIRM:
        ctx.click_object(m.name, "cancel")
        ctx.expect_scene(DIALOG, timeout_s)
        return
    if m.name in (BATTLE, EXIT_CONFIRM):  # started inside the tower (e.g. after a stopped run)
        ctx.info("Đang ở trong tháp: thoát ra bảng Tháp Thí Luyện trước")
        return_to_dialog(ctx, timeout_s)
        return
    if m.name == CITY and from_city:
        ctx.click_object(CITY, "tower_building")
        ctx.expect_scene(PANEL, timeout_s)
        ctx.sleep(delay_ms)
        ctx.click_object(PANEL, "action_1")
        ctx.expect_scene(DIALOG, timeout_s)
        return
    where = "mở bảng Tháp Thí Luyện" if not from_city else "về Thành chính hoặc mở bảng Tháp Thí Luyện"
    raise TowerError(f"Hãy {where} rồi bấm Bắt đầu (màn hình hiện tại: {shown(m)})")


def first_page_by_back(ctx: RunContext) -> None:
    """CAYCAPCHUACONG: click "page back" 26 times to reach page 1."""
    ctx.step("về trang 1")
    for i in range(PAGE_BACK_CLICKS):
        if i:
            ctx.sleep(PAGE_CLICK_GAP_MS)
        ctx.click(PAGE_BACK)


def first_page_by_back_checked(ctx: RunContext, delay_ms: int) -> None:
    """Strategy 1, step 1: click ◀ until the page box shows "1" (each click must change the box)."""
    ctx.step("về trang 1")
    for clicks in range(MAX_BACK_CLICKS + 1):
        if is_page_one(ctx):
            return
        if clicks == MAX_BACK_CLICKS:
            raise TowerError(f"Bấm \"trang trước\" {MAX_BACK_CLICKS} lần vẫn chưa về trang 1")
        before = page_view(ctx)
        ctx.click(PAGE_BACK)
        ctx.sleep(max(delay_ms, PAGE_CLICK_GAP_MS))
        if not _wait_page_change(ctx, before):
            raise TowerError("Nút \"trang trước\" không phản hồi: ô trang không đổi và chưa phải trang 1")


def type_page(ctx: RunContext, number: int, delay_ms: int) -> None:
    """Strategy 2: double-click the page box (select all), Backspace, type ``number``, Enter.

    Keys are sent only while the box visibly reacts: the box must be empty after Backspace and show
    digits after typing, otherwise the run stops WITHOUT pressing Enter (Enter on another focused
    control could press a button or send chat). After Enter the box must still show the typed
    digits; if the game rolled it back to another number (page not unlocked), the run stops.
    """
    ctx.step(f"gõ trang {number}")
    if number == 1 and is_page_one(ctx):
        return
    ctx.click(SEND_BOX, double=True, label="ô trang")
    ctx.sleep(delay_ms)
    press_key(ctx, VK_BACK)
    ctx.sleep(delay_ms)
    if not page_box_empty(page_view(ctx)):
        raise TowerError("Không xoá được ô trang (phím không vào ô trang); đã dừng, không bấm Enter")
    type_text(ctx, str(number))
    ctx.sleep(delay_ms)
    typed = page_view(ctx)
    if page_box_empty(typed):
        raise TowerError(f"Gõ \"{number}\" không hiện trong ô trang; đã dừng, không bấm Enter")
    press_key(ctx, VK_RETURN)
    ctx.sleep(max(delay_ms, PAGE_CLICK_GAP_MS))
    deadline = time.monotonic() + PAGE_TURN_TIMEOUT_S
    while not text_kept(typed, page_view(ctx)):
        if time.monotonic() >= deadline:
            raise TowerError(
                f"Ô trang không giữ trang {number}: game đổi sang số khác "
                f"(trang {number} chưa có tầng nào mở khoá?)"
            )
        ctx.sleep_s(ctx.poll_interval_s)


def goto_page(ctx: RunContext, page: int, strategy: PageStrategy, delay_ms: int, timeout_s: float) -> None:
    """Show page ``page`` (0-based) of the floor list with the chosen strategy."""
    ensure_floor_list(ctx, timeout_s)
    if PageStrategy(strategy) is PageStrategy.TYPE_PAGE:
        type_page(ctx, page + 1, delay_ms)
    else:
        first_page_by_back_checked(ctx, delay_ms)
        next_pages(ctx, page, delay_ms)


def next_pages(ctx: RunContext, count: int, delay_ms: int, from_page: int = 0) -> None:
    """Click "page next" ``count`` times from page ``from_page`` (0-based), checking each turn.

    The page number box must change after every click; if it does not (the next page has no
    unlocked floor yet), stop instead of carrying on with the floors of the old page.
    """
    for i in range(count):
        target = from_page + i + 2  # 1-based page number we are going to
        ctx.step(f"sang trang {target}")
        before = page_view(ctx)
        ctx.click(PAGE_NEXT)
        ctx.sleep(delay_ms)
        if not _wait_page_change(ctx, before):
            first = (target - 1) * per_page(ctx) + 1
            raise TowerError(
                f"Không sang được trang {target} của bảng Tháp Thí Luyện: "
                f"tầng {first} trở đi chưa mở khoá"
            )


def confirm_entry(ctx: RunContext, timeout_s: float, delay_ms: int) -> None:
    """Wait for "Mỗi lần Vượt tháp mất 3 Thí luyện lệnh" and press 确定."""
    ctx.step("xác nhận vượt tháp")
    try:
        ctx.expect_scene(CONFIRM, timeout_s)
    except SceneTimeout as exc:
        raise SceneTimeout(
            f"{exc} - không hiện hộp xác nhận vượt tháp (hết Thí luyện lệnh, hoặc chưa chọn tầng?)",
            exc.expected, exc.actual,
        ) from exc
    ctx.sleep(delay_ms)
    ctx.click_object(CONFIRM, "ok")


def enter_battle(ctx: RunContext, timeout_s: float) -> None:
    ctx.step("vào tháp")
    ctx.expect_scene(BATTLE, timeout_s)


def return_to_dialog(ctx: RunContext, timeout_s: float) -> None:
    """Leave the battle screen and wait for the tower dialog again.

    v1.8.0 clicked "Thoát" (960, 565) and then "Đồng ý" (455, 325) blind. Here: while the battle
    screen shows, click its "Thoát" (again every ``EXIT_RETRY_S`` if it is still there); when a
    confirm box shows (``tower.exit_confirm``), press its 确定; return once ``tower.dialog`` shows.
    """
    ctx.step("thoát tháp")
    deadline = time.monotonic() + max(0.0, timeout_s)
    last_exit = float("-inf")
    m: SceneMatch | None = None
    while True:
        m = ctx.detect_scene()
        now = time.monotonic()
        if m.name == DIALOG:
            return
        if m.name == BATTLE and now - last_exit >= EXIT_RETRY_S:
            ctx.click_object(BATTLE, "exit")
            last_exit = now
        elif m.name == EXIT_CONFIRM:
            ctx.click_object(EXIT_CONFIRM, "ok")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SceneTimeout(
                f"không quay lại được bảng Tháp Thí Luyện sau {timeout_s:.1f}s "
                f"(đang hiển thị: {shown(m)})",
                DIALOG, m,
            )
        ctx.sleep_s(min(ctx.poll_interval_s, remaining))
