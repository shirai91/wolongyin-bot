"""Chạy Thành Tựu: finish the achievements of one dungeon (dialog "Hoạt Động", tab T.Tựu).

The tab lists the dungeons ("Tên phó bản", a list with ▲ ▼) and, for the selected one:

* "Đánh giá": armies to beat once with rank S; a ✓ in front of a line means it is done;
* "Khiêu chiến": cards (points, portrait, name, ``have/need``): beat that army ``need`` times;
* "Điểm" (the score) and "Nhận thưởng" (claim) under the reward table.

Two steps, both runs of this task (so they use the runner, Stop, the log and the debug overlay):

1. **Đồng bộ** (``mode="sync"``, the button of the "Phó bản" field): open the tab, walk the whole
   list (scrolling with ▼) and read every dungeon's tasks into ``data/thanh_tuu.json``. The field
   then lists the dungeons with ✅ / ❌; a finished one cannot be started.
2. **Bắt đầu**: open the dungeon map, switch with « » until its name bar shows the chosen dungeon,
   then loop: open the tab, select the dungeon, read its tasks (and the card portraits), close the
   tab, find the army of the first unfinished task on the map (click each squad and read its dialog
   once; the names are kept for the run), and fight it: once for an S task, ``need - have`` wins for
   a card. The next loop checks the result: an S task that is still not ✓, or a card whose count did
   not move, stops the run with a screenshot (and a notification when they are on).

An army shows "Số lượng have/max": each fight uses one. At 0 only "Tiến công" (costs Vàng) still
fights it: allowed only with the "Tiến công" option, else the run stops.

Text is read with OCR (``ngoalong.core.ocr``, RapidOCR), so names are compared loosely (no marks,
dropped letters, names cut off by the card or the S badge); card armies are also recognised by their
portrait. Battles use Clear Phó Bản's battle handling (recruiting, result boxes, time limits).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

import cv2
import numpy as np
from pydantic import BaseModel, Field

from ngoalong.core.errors import NgoaLongError, WaitTimeout
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.ocr import (
    MATCH, OcrLine, army_name, fraction, normalize, number, prefix_similarity, similarity,
)
from ngoalong.engine.context import RunContext, SceneTimeout
from ngoalong.engine.registry import register
from ngoalong.engine.task import Task, TaskParams
from ngoalong.features import clear_map as cm
from ngoalong.paths import APP_ROOT

log = logging.getLogger(__name__)

# --- scenes and coords ------------------------------------------------------------------------------
DAILY = "daily"  # the "Hoạt Động" dialog (scenes/dialogs.yaml)
FIELD = cm.FIELD
ARMY = cm.ARMY
OPEN_FROM: tuple[str, ...] = (FIELD, "city", "world_map", "region", "resources")
KEY = "thanh_tuu"

NAME_MATCH = MATCH  # two names read by OCR are the same at 85 % (ngoalong.core.ocr.MATCH)
CARD_NAME_MATCH = MATCH  # a card name cut off by the card: compared with the start of the army name
PORTRAIT_MATCH = 0.3  # colour-histogram correlation (portrait_score)
PORTRAIT_WINDOW = 64  # px: about the size of the dialog's portrait
PORTRAIT_STEP = 6
PORTRAIT_BINS = (18, 16)  # hue, saturation
MAX_SCROLLS = 40
MAX_ROUNDS = 500  # tasks read + fights per run, a safety net on top of max_battles
DIALOG_SETTLE_MS = 400  # the tab redraws after a click on a dungeon
ARMY_DEDUPE_PX = 28
TENT_HSV = ((22, 150, 170), (34, 255, 255))
TENT_AREA = (15, 220)
RED_HSV = (((0, 110, 70), (8, 255, 230)), ((170, 110, 70), (180, 255, 230)))
RED_AREA = (250, 3000)


class ThanhTuuError(NgoaLongError):
    """The achievement run cannot go on (not synced, dungeon or army not found, rank not S...)."""


# --- achievement data ---------------------------------------------------------------------------------
class Kind(StrEnum):
    RANK = "rank"  # Đánh giá: beat once with rank S
    CHALLENGE = "challenge"  # Khiêu chiến: beat ``need`` times


class AchTask(BaseModel):
    kind: Kind
    name: str  # as read (OCR); compare with ``similarity``
    done: bool = False
    have: int = 0
    need: int = 0

    @property
    def label(self) -> str:
        if self.kind is Kind.RANK:
            return f"{self.name} (hạng S)"
        return f"{self.name} {self.have}/{self.need}"


class AchMap(BaseModel):
    name: str
    progress: int | None = None  # "Tiến độ: N%"
    tasks: list[AchTask] = []

    @property
    def done(self) -> bool:
        if self.tasks:
            return all(t.done for t in self.tasks)
        return self.progress == 100

    @property
    def summary(self) -> str:
        finished = sum(t.done for t in self.tasks)
        return f"{finished}/{len(self.tasks)} nhiệm vụ" if self.tasks else (
            f"{self.progress}%" if self.progress is not None else "chưa đọc được")

    def pending(self) -> list[AchTask]:
        """Unfinished tasks, S tasks first (one fight each)."""
        return sorted((t for t in self.tasks if not t.done), key=lambda t: t.kind is not Kind.RANK)


class AchState(BaseModel):
    synced_at: float | None = None
    score: int | None = None
    maps: list[AchMap] = []

    def find(self, name: str) -> AchMap | None:
        scored = sorted(((similarity(name, m.name), i) for i, m in enumerate(self.maps)), reverse=True)
        if scored and scored[0][0] >= NAME_MATCH:
            return self.maps[scored[0][1]]
        return None

    def put(self, m: AchMap) -> None:
        for i, old in enumerate(self.maps):
            if similarity(old.name, m.name) >= NAME_MATCH:
                self.maps[i] = m.model_copy(update={"name": old.name})
                return
        self.maps.append(m)


class AchievementStore:
    """The synced achievements (``data/thanh_tuu.json``); ``path=None`` keeps them in memory."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._state: AchState | None = None
        self._mtime: float | None = None

    def load(self) -> AchState:
        with self._lock:
            if self.path is not None and self.path.is_file():
                mtime = self.path.stat().st_mtime
                if self._state is None or mtime != self._mtime:
                    try:
                        self._state = AchState.model_validate_json(self.path.read_text(encoding="utf-8"))
                    except (OSError, ValueError) as exc:
                        log.warning("cannot read %s: %s", self.path, exc)
                        self._state = AchState()
                    self._mtime = mtime
            if self._state is None:
                self._state = AchState()
            return self._state.model_copy(deep=True)

    def save(self, state: AchState) -> None:
        with self._lock:
            self._state = state.model_copy(deep=True)
            if self.path is None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, self.path)
            self._mtime = self.path.stat().st_mtime


_default_store: AchievementStore | None = None


def default_store() -> AchievementStore:
    global _default_store
    if _default_store is None:
        _default_store = AchievementStore(APP_ROOT / "data" / "thanh_tuu.json")
    return _default_store


def store_of(services: Any) -> AchievementStore:
    return getattr(services, "thanh_tuu_store", None) or default_store()


# --- reading the T.Tựu tab (pure functions of OCR lines and pixels: unit-tested) -----------------------
def clean_rank_name(text: str) -> str:
    """A task name without the S badge OCR glued to its end ("Đoàn Viên Hạnh Phúc 1S" -> "... Phúc 1")."""
    words = text.split()
    if words and len(words[0]) == 1 and not words[0].isdigit():  # the ✓ box read as a letter
        words.pop(0)
    while words:
        last = normalize(words[-1])
        if last in ("", "s", "5"):  # the badge alone
            words.pop()
        elif len(last) == 2 and last[1] == "s":  # glued: "1S" -> "1", "ES" (a misread digit) -> drop
            if last[0].isdigit():
                words[-1] = last[0]
            else:
                words.pop()
            break
        else:
            break
    return " ".join(words)


def is_checked(canvas_img: np.ndarray, center: Point, half: int, ratio: float) -> bool:
    """The ✓ of a task line: share of bright yellow-orange pixels in the box around ``center``."""
    box = canvas_img[max(0, center.y - half):center.y + half + 1, max(0, center.x - half):center.x + half + 1]
    if box.size == 0:
        return False
    hsv = cv2.cvtColor(box, cv2.COLOR_BGR2HSV)
    bright = (hsv[..., 0] >= 10) & (hsv[..., 0] <= 40) & (hsv[..., 1] >= 90) & (hsv[..., 2] >= 150)
    return float(bright.mean()) >= ratio


def parse_rank_lines(lines: list[OcrLine]) -> list[tuple[str, OcrLine]]:
    """(name, line) of each "Đánh giá" line; lines that are only the badge are dropped."""
    out = []
    for ln in lines:
        name = clean_rank_name(ln.text)
        if len(normalize(name).replace(" ", "")) >= 2:
            out.append((name, ln))
    return out


_POINTS = re.compile(r"^\d+\s*d")  # "5Điểm" at the top of a card, however OCR spells "Điểm"


@dataclass
class Card:
    index: int
    name: str
    have: int
    need: int


def parse_cards(lines: list[OcrLine], first_x: int, pitch: int, width: int, count: int) -> list[Card]:
    """The "Khiêu chiến" cards: a card exists when its column holds a ``have/need`` count."""
    # OCR joins the names of neighbouring cards into one line ("Đẳng Đoàn Viên"): split such a line
    # into words placed by their share of the line's characters.
    pieces: list[tuple[str, int]] = []  # (text, centre x)
    for ln in lines:
        words = ln.text.split()
        if ln.rect.w <= width or len(words) < 2:
            pieces.append((ln.text, ln.rect.center.x))
            continue
        total, pos = len(ln.text), 0
        for w in words:
            start = ln.text.index(w, pos)
            pos = start + len(w)
            pieces.append((w, ln.rect.x + round(ln.rect.w * (start + len(w) / 2) / total)))
    cards = []
    for k in range(count):
        x0, x1 = first_x + k * pitch, first_x + k * pitch + width
        col = [text for text, cx in pieces if x0 - 4 <= cx <= x1 + 4]
        nums = [f for f in (fraction(text) for text in col) if f is not None]
        if not nums:
            continue
        have, need = nums[-1]
        names = [text for text in col if fraction(text) is None and not _POINTS.match(normalize(text))]
        cards.append(Card(k, " ".join(names).strip(), have, need))
    return cards


def parse_progress(lines: list[OcrLine]) -> int | None:
    for ln in lines:
        if "%" in ln.text:
            n = number(ln.text.split("%")[0][-4:])
            if n is not None and 0 <= n <= 100:
                return n
    return None


@dataclass
class ArmyInfo:
    title: str
    name: str  # normalised, without "(Đội-Tinh) Lv10"
    have: int | None
    need: int | None
    tien_cong: Point | None  # "Tiến công" (canvas), shown on armies with a count
    portrait: np.ndarray | None = None  # the dialog pixels, for portrait matching


def parse_army(lines: list[OcrLine]) -> tuple[str, tuple[int, int] | None, Point | None]:
    """(title, (have, max) of "Số lượng", "Tiến công" centre) from the army dialog lines."""
    title = ""
    titled = [ln for ln in lines if " lv" in f" {ln.norm}" or "(" in ln.text]
    if titled:
        title = titled[0].text
    elif lines:
        title = lines[0].text
    count = None
    for ln in lines:
        f = fraction(ln.text)
        if f is not None:
            count = f
            break
    tien = None  # "Tiến công"; OCR may drop the "ế" ("Tin công") but never reads "Tấn" as "Ti..."
    for ln in lines:
        words = ln.norm.split()
        if len(words) >= 2 and words[0] in ("tien", "tin", "tlen", "tlin") and words[1].startswith("c"):
            tien = ln.rect.center
            break
    return title, count, tien


def _hs_hist(img: np.ndarray) -> np.ndarray:
    h = cv2.calcHist([cv2.cvtColor(img, cv2.COLOR_BGR2HSV)], [0, 1], None, list(PORTRAIT_BINS), [0, 180, 0, 256])
    return cv2.normalize(h, h).flatten()


def portrait_score(card: np.ndarray | None, dialog: np.ndarray | None) -> float:
    """Colour likeness of a card portrait to the best square of the army dialog (-1..1).

    The card and the dialog frame the same picture differently (the card shows more of it, smaller),
    so pixel matching fails; hue/saturation histograms do not care. On the user's screenshots: the
    right army 0.42 / 0.66, the other one 0.08 / 0.09.
    """
    if card is None or dialog is None or card.size == 0 or dialog.size == 0:
        return 0.0
    win = PORTRAIT_WINDOW
    if dialog.shape[0] < win or dialog.shape[1] < win:
        return 0.0
    hc = _hs_hist(card)
    best = -1.0
    for y in range(0, dialog.shape[0] - win + 1, PORTRAIT_STEP):
        for x in range(0, dialog.shape[1] - win + 1, PORTRAIT_STEP):
            score = cv2.compareHist(hc, _hs_hist(dialog[y:y + win, x:x + win]), cv2.HISTCMP_CORREL)
            best = max(best, float(score))
    return best


def find_armies(canvas_img: np.ndarray, regions: list[Rect], flag: np.ndarray | None,
                tent_offset: tuple[int, int], flag_offset: tuple[int, int]) -> list[Point]:
    """Squads on the dungeon map (canvas points to click), top to bottom.

    Three cues, any one is enough: a red flag (``field.enemy_flag``), a bright yellow tent (squads
    already beaten), or a block of red soldiers.
    """
    hsv = cv2.cvtColor(canvas_img, cv2.COLOR_BGR2HSV)
    found: list[Point] = []

    def blobs(mask: np.ndarray, area: tuple[int, int], dx: int, dy: int) -> None:
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        n, _lab, st, _cen = cv2.connectedComponentsWithStats(mask)
        for i in range(1, n):
            x, y, w, h, a = (int(v) for v in st[i])
            if area[0] <= a <= area[1] and w <= 90 and h <= 80:
                found.append(Point(x + w // 2 + dx, y + h // 2 + dy))

    tent = cv2.inRange(hsv, np.array(TENT_HSV[0]), np.array(TENT_HSV[1]))
    blobs(tent, TENT_AREA, *tent_offset)
    red = np.zeros(hsv.shape[:2], np.uint8)
    for lo, hi in RED_HSV:
        red = cv2.bitwise_or(red, cv2.inRange(hsv, np.array(lo), np.array(hi)))
    blobs(red, RED_AREA, 0, 0)
    if flag is not None and flag.size:
        res = cv2.matchTemplate(canvas_img, flag, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= cm.ENEMY_THRESHOLD)
        fh, fw = flag.shape[:2]
        for x, y in zip(xs.tolist(), ys.tolist(), strict=True):
            found.append(Point(x + fw // 2 + flag_offset[0], y + fh // 2 + flag_offset[1]))
    inside = [p for p in found if any(r.contains(p) for r in regions)]
    out: list[Point] = []
    for p in sorted(inside, key=lambda p: (p.y, p.x)):
        if all(abs(p.x - q.x) > ARMY_DEDUPE_PX or abs(p.y - q.y) > ARMY_DEDUPE_PX for q in out):
            out.append(p)
    return out


# --- params --------------------------------------------------------------------------------------------
class ThanhTuuParams(TaskParams):
    map_name: str = Field(
        "", title="Phó bản",
        description="Bấm Đồng bộ để đọc thành tựu của mọi phó bản, rồi chọn một phó bản chưa xong (❌)",
        json_schema_extra={"widget": "thanh_tuu_maps"},
    )
    allow_gold: bool = Field(
        False, title="Cho phép 'Tiến công' (tốn Vàng)",
        description="Khi đạo quân hết số lượng (0/…), chỉ còn 'Tiến công' tốn Vàng; tắt thì dừng và báo",
    )
    claim_rewards: bool = Field(True, title="Bấm 'Nhận thưởng' khi xong phó bản")
    delay_ms: int = Field(300, ge=0, le=60_000, title="Thời gian chờ giữa các bước (ms)")
    wait_s: float = Field(3.0, ge=0.5, le=60.0, title="Thời gian chờ tìm hình (giây)")
    max_battles: int = Field(0, ge=0, le=100_000, title="Số trận tối đa (0 = không giới hạn)")
    max_recruits: int = Field(5, ge=0, le=50, title="Số lần chiêu binh tối đa mỗi trận")
    battle_timeout_s: float = Field(180.0, ge=10.0, le=3600.0, title="Thời gian tối đa mỗi trận (giây)")
    scene_timeout_s: float = Field(10.0, ge=0.5, le=120.0, title="Thời gian chờ màn hình (giây)")


@dataclass
class _Run:
    battles: int = 0
    wins: int = 0
    armies: dict[Point, ArmyInfo] = field(default_factory=dict)  # squads read on this map
    scanned: bool = False
    used_for_rank: set[Point] = field(default_factory=set)  # squads that gave an S already


def start_blocker(params: ThanhTuuParams, store: AchievementStore) -> str | None:
    """Why "Bắt đầu" is off for these params (``None`` = it may start)."""
    state = store.load()
    if state.synced_at is None:
        return "Chưa đồng bộ thành tựu: bấm 'Đồng bộ' trước"
    if not params.map_name:
        return "Chọn một phó bản"
    m = state.find(params.map_name)
    if m is None:
        return f"Không có phó bản '{params.map_name}' trong dữ liệu đã đồng bộ: bấm 'Đồng bộ' lại"
    if m.done:
        return f"Phó bản {m.name} đã hoàn thành thành tựu ✅: chọn phó bản khác"
    return None


@register
class ChayThanhTuu(Task):
    id = "thanh_tuu"
    title = "Chạy Thành Tựu"
    description = (
        "Bấm Đồng bộ để đọc thành tựu (Hoạt Động > T.Tựu) của mọi phó bản, chọn phó bản chưa xong "
        "rồi bấm Bắt đầu: bot vào phó bản đó, đánh hạng S và khiêu chiến đủ số lần"
    )
    order = 85
    Params = ThanhTuuParams
    start_scene = None

    MODES: ClassVar[tuple[str, ...]] = ("run", "sync")
    params: ThanhTuuParams

    def __init__(self, params: TaskParams | Mapping[str, Any] | None = None,
                 store: AchievementStore | None = None, mode: str = "run") -> None:
        super().__init__(params)
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {self.MODES}")
        self.mode = mode
        self.store = store or default_store()
        if mode == "sync":
            self.title = "Đồng bộ Thành Tựu"  # type: ignore[misc]  # shown in the log and history
        p = self.params
        self._cm = cm.ClearMap(cm.ClearMapParams(
            delay_ms=p.delay_ms, wait_s=p.wait_s, max_recruits=p.max_recruits,
            battle_timeout_s=p.battle_timeout_s, scene_timeout_s=p.scene_timeout_s, hide_hud=True,
        ))

    def bind_services(self, services: Any) -> ChayThanhTuu:
        self.store = store_of(services)
        return self

    @classmethod
    def start_blocker(cls, params: TaskParams, services: Any = None) -> str | None:
        assert isinstance(params, ThanhTuuParams)
        return start_blocker(params, store_of(services))

    # --- small helpers ------------------------------------------------------------------------------
    def _rect(self, ctx: RunContext, key: str) -> Rect:
        return cm._rect(ctx.coords.raw(f"{KEY}.{key}"), f"{KEY}.{key}")

    def _num(self, ctx: RunContext, key: str) -> float:
        return float(ctx.coords.value(f"{KEY}.{key}"))

    def _pause(self, ctx: RunContext, ms: float | None = None) -> None:
        ctx.sleep(self.params.delay_ms if ms is None else ms)

    def _canvas_image(self, ctx: RunContext) -> np.ndarray:
        """The whole canvas of a fresh frame, resized to 1000x600 (canvas coordinates = pixels)."""
        frame, fix = ctx.observe()
        r = fix.rect
        img = frame[max(0, r.y):r.bottom, max(0, r.x):r.right, :3]
        if img.shape[1] != 1000 or img.shape[0] != 600:
            img = cv2.resize(img, (1000, 600), interpolation=cv2.INTER_AREA)
        return img

    # --- the dialog ---------------------------------------------------------------------------------
    def _open_dialog(self, ctx: RunContext) -> None:
        p = self.params
        ctx.step("mở Thành tựu")
        m = ctx.detect_scene()
        if m.name == ARMY:
            ctx.click_object(ARMY, "close")
            m = ctx.expect_scene(FIELD, p.scene_timeout_s)
        if m.name != DAILY:
            if m.name not in OPEN_FROM:
                raise ThanhTuuError(
                    f"Hãy về Thành chính hoặc bản đồ Phó Bản rồi chạy lại (màn hình hiện tại: {cm._shown(m)})")
            ctx.click(f"{KEY}.open_button", label="T.Tựu")
            ctx.expect_scene(DAILY, p.scene_timeout_s)
        self._pause(ctx)
        ctx.click_object(DAILY, "tab_thanh_tuu")
        self._pause(ctx, max(p.delay_ms, DIALOG_SETTLE_MS))

    def _close_dialog(self, ctx: RunContext) -> None:
        ctx.step("đóng Thành tựu")
        ctx.click_object(DAILY, "close")
        deadline = time.monotonic() + self.params.scene_timeout_s
        while ctx.detect_scene().name == DAILY:
            if time.monotonic() >= deadline:
                raise ThanhTuuError("Không đóng được bảng Hoạt Động")
            ctx.sleep_s(ctx.poll_interval_s)

    def _visible_maps(self, ctx: RunContext) -> list[OcrLine]:
        return [ln for ln in ctx.read_text(self._rect(ctx, "map_list"))
                if len(ln.norm.replace(" ", "")) >= 3]

    def _scroll(self, ctx: RunContext, up: bool) -> None:
        ctx.click(f"{KEY}.map_scroll_up" if up else f"{KEY}.map_scroll_down", label="▲" if up else "▼")
        self._pause(ctx, min(self.params.delay_ms, 200))

    def _scroll_to_top(self, ctx: RunContext) -> list[OcrLine]:
        rows = self._visible_maps(ctx)
        for _ in range(MAX_SCROLLS):
            first = rows[0].text if rows else ""
            self._scroll(ctx, up=True)
            rows = self._visible_maps(ctx)
            if (rows[0].text if rows else "") == first:
                break
        return rows

    def _select_map(self, ctx: RunContext, name: str) -> None:
        ctx.step(f"chọn {name}")
        rows = self._scroll_to_top(ctx)
        for _ in range(MAX_SCROLLS):
            scored = sorted(((similarity(name, r.text), i) for i, r in enumerate(rows)), reverse=True)
            if scored and scored[0][0] >= NAME_MATCH:
                ctx.click(rows[scored[0][1]].rect.center, label=name)
                self._pause(ctx, max(self.params.delay_ms, DIALOG_SETTLE_MS))
                return
            last = [r.text for r in rows]
            self._scroll(ctx, up=False)
            rows = self._visible_maps(ctx)
            if [r.text for r in rows] == last:
                break
        raise ThanhTuuError(f"Không thấy phó bản {name} trong danh sách Thành tựu")

    def _read_map(self, ctx: RunContext, name: str) -> tuple[AchMap, dict[int, np.ndarray]]:
        """The selected dungeon's tasks, and the card portraits (card index -> pixels)."""
        ctx.step(f"đọc {name}")
        progress = parse_progress(ctx.read_text(self._rect(ctx, "progress")))
        img = self._canvas_image(ctx)
        half, ratio = int(self._num(ctx, "rank_checkbox_half")), self._num(ctx, "rank_checked_ratio")
        cx = int(self._num(ctx, "rank_checkbox_x"))
        tasks = [
            AchTask(kind=Kind.RANK, name=n, done=is_checked(img, Point(cx, ln.rect.center.y), half, ratio))
            for n, ln in parse_rank_lines(ctx.read_text(self._rect(ctx, "rank_tasks")))
        ]
        first, pitch = int(self._num(ctx, "card_first_x")), int(self._num(ctx, "card_pitch"))
        width, count = int(self._num(ctx, "card_width")), int(self._num(ctx, "max_cards"))
        area = self._rect(ctx, "challenge_cards")
        pr = self._rect(ctx, "card_portrait")
        portraits: dict[int, np.ndarray] = {}
        for c in parse_cards(ctx.read_text(area), first, pitch, width, count):
            tasks.append(AchTask(kind=Kind.CHALLENGE, name=c.name or f"thẻ {c.index + 1}",
                                 done=c.have >= c.need, have=c.have, need=c.need))
            x, y = first + c.index * pitch + pr.x, area.y + pr.y
            portraits[len(tasks) - 1] = img[y:y + pr.h, x:x + pr.w].copy()
        m = AchMap(name=name, progress=progress, tasks=tasks)
        ctx.info(f"{name}: {m.summary}" + "".join(f"\n  {'✅' if t.done else '❌'} {t.label}" for t in tasks))
        return m, portraits

    def _read_score(self, ctx: RunContext) -> int | None:
        lines = ctx.read_text(self._rect(ctx, "score"))
        return number(" ".join(ln.text for ln in lines))

    # --- sync -----------------------------------------------------------------------------------------
    def _sync(self, ctx: RunContext) -> str:
        self._open_dialog(ctx)
        state = AchState(synced_at=None, score=None, maps=[])
        ctx.step("đồng bộ")
        rows = self._scroll_to_top(ctx)
        seen: list[str] = []
        for _ in range(MAX_SCROLLS):
            new = [r for r in rows if all(similarity(r.text, s) < NAME_MATCH for s in seen)]
            for r in new:
                ctx.checkpoint()
                ctx.click(r.rect.center, label=r.text)
                self._pause(ctx, max(self.params.delay_ms, DIALOG_SETTLE_MS))
                m, _ = self._read_map(ctx, r.text.strip())
                state.maps.append(m)
                seen.append(r.text)
                ctx.progress(len(state.maps))
            last = [r.text for r in rows]
            self._scroll(ctx, up=False)
            rows = self._visible_maps(ctx)
            if [r.text for r in rows] == last:
                break
        if not state.maps:
            raise ThanhTuuError("Không đọc được danh sách phó bản trong Thành tựu (OCR không thấy chữ)")
        state.score = self._read_score(ctx)
        state.synced_at = time.time()
        self.store.save(state)
        self._close_dialog(ctx)
        done = sum(m.done for m in state.maps)
        return f"Đã đồng bộ {len(state.maps)} phó bản ({done} đã xong), điểm {state.score}"

    # --- dungeon map ------------------------------------------------------------------------------------
    def _dungeon_name(self, ctx: RunContext) -> str:
        return " ".join(ln.text for ln in ctx.read_text(self._rect(ctx, "dungeon_name")))

    def _goto_dungeon(self, ctx: RunContext, target: str, order: list[str]) -> None:
        p = self.params
        names = [normalize(n) for n in order]
        for _ in range(len(order) + 2):
            cur = self._dungeon_name(ctx)
            if similarity(cur, target) >= NAME_MATCH:
                return
            here = max(range(len(names)), key=lambda i: similarity(cur, names[i]), default=-1)
            there = max(range(len(names)), key=lambda i: similarity(target, names[i]), default=-1)
            forward = here < 0 or similarity(cur, names[here]) < NAME_MATCH or there > here
            ctx.step("chuyển phó bản")
            ctx.info(f"Đang ở phó bản '{cur}', chuyển {'»' if forward else '«'} tới {target}")
            ctx.click(f"{KEY}.next_dungeon" if forward else f"{KEY}.prev_dungeon", label="»" if forward else "«")
            self._pause(ctx)
            ctx.expect_scene(FIELD, p.scene_timeout_s)
            try:
                ctx.wait_gone(cm.SWITCH_BANNER, p.scene_timeout_s, region=cm.SWITCH_BANNER_REGION,
                              threshold=cm.SWITCH_BANNER_THRESHOLD)
            except WaitTimeout:
                ctx.warn("Băng rôn chuyển phó bản chưa tắt; tiếp tục")
        raise ThanhTuuError(f"Không chuyển được tới phó bản {target} bằng « »")

    def _army_points(self, ctx: RunContext, hud_hidden: bool) -> list[Point]:
        img = self._canvas_image(ctx)
        try:
            flag = ctx.matcher.templates.get(cm.ENEMY).image
        except Exception:  # a profile without the flag template: colours only
            flag = None
        tent_off = ctx.coords.offset(f"{KEY}.army_click_offset_from_tent")
        flag_off = ctx.coords.offset(cm.ENEMY_OFF)
        return find_armies(img, cm.search_regions(ctx, hud_hidden), flag, tent_off, flag_off)

    def _open_army(self, ctx: RunContext, at: Point) -> bool:
        ctx.click(at, label="đạo quân")
        try:
            ctx.expect_scene(ARMY, min(self.params.wait_s, 2.0))
        except SceneTimeout:
            return False
        self._pause(ctx)
        return True

    def _read_army(self, ctx: RunContext) -> ArmyInfo:
        region = self._rect(ctx, "army_dialog")
        title, count, tien = parse_army(ctx.read_text(region))
        area = self._rect(ctx, "army_portrait_area")
        crop = self._canvas_image(ctx)[area.y:area.bottom, area.x:area.right].copy()
        return ArmyInfo(title, army_name(title), count[0] if count else None, count[1] if count else None,
                        tien, crop)

    def _close_army(self, ctx: RunContext) -> None:
        ctx.click_object(ARMY, "close")
        try:
            ctx.expect_scene(FIELD, self.params.scene_timeout_s)
        except SceneTimeout:
            ctx.warn("Bảng đạo quân chưa đóng")

    def _score(self, task: AchTask, portrait: np.ndarray | None, info: ArmyInfo) -> float:
        if task.kind is Kind.RANK:
            name = normalize(task.name)
            return max(prefix_similarity(name, info.name), similarity(name, info.name))
        by_face = portrait_score(portrait, info.portrait)
        by_name = prefix_similarity(task.name, info.name) if len(normalize(task.name)) >= 4 else 0.0
        return max(by_face, by_name if by_name >= CARD_NAME_MATCH else 0.0)

    def _good(self, task: AchTask, score: float) -> bool:
        return score >= (NAME_MATCH if task.kind is Kind.RANK else PORTRAIT_MATCH)

    def _find_army(self, ctx: RunContext, run: _Run, task: AchTask, portrait: np.ndarray | None,
                   hud_hidden: bool) -> Point:
        """A squad for ``task``: from the squads read so far, else by opening the others one by one."""
        ctx.step(f"tìm {task.name}")
        skip = run.used_for_rank if task.kind is Kind.RANK else set()

        def best() -> Point | None:
            scored = [(self._score(task, portrait, info), p) for p, info in run.armies.items() if p not in skip]
            scored = [s for s in scored if self._good(task, s[0])]
            return max(scored, key=lambda s: s[0])[1] if scored else None

        hit = best()
        if hit is not None:
            return hit
        if not run.scanned:
            points = self._army_points(ctx, hud_hidden)
            ctx.info(f"Thấy {len(points)} chỗ có thể là đạo quân; mở từng chỗ để đọc tên")
            for at in points:
                if any(abs(at.x - q.x) <= ARMY_DEDUPE_PX and abs(at.y - q.y) <= ARMY_DEDUPE_PX for q in run.armies):
                    continue
                ctx.checkpoint()
                if not self._open_army(ctx, at):
                    continue
                info = self._read_army(ctx)
                run.armies[at] = info
                count = f" ({info.have}/{info.need})" if info.have is not None else ""
                ctx.info(f"Đạo quân tại {at.as_tuple()}: {info.title or '?'}{count}")
                self._close_army(ctx)
                if p := best():
                    return p
            run.scanned = True
            hit = best()
            if hit is not None:
                return hit
        raise ThanhTuuError(
            f"Không tìm thấy đạo quân '{task.name}' trên bản đồ (đã đọc {len(run.armies)} đạo quân)")

    # --- fighting --------------------------------------------------------------------------------------
    def _fight(self, ctx: RunContext, run: _Run, at: Point, stats: cm._Stats) -> cm.Outcome:
        p = self.params
        ctx.step("mở đạo quân")
        if not self._open_army(ctx, at):
            run.armies.pop(at, None)
            raise ThanhTuuError(f"Không mở được đạo quân tại {at.as_tuple()}")
        info = self._read_army(ctx)
        run.armies[at] = info
        if info.have == 0:
            if not p.allow_gold:
                raise ThanhTuuError(
                    f"Đạo quân {info.title} đã hết số lượng (0/{info.need}); chỉ còn 'Tiến công' tốn Vàng. "
                    "Bật 'Cho phép Tiến công' nếu muốn dùng Vàng")
            if info.tien_cong is None:
                raise ThanhTuuError(f"Đạo quân {info.title} hết số lượng nhưng không thấy nút 'Tiến công'")
            ctx.step("tiến công")
            ctx.warn(f"{info.title}: hết số lượng, dùng 'Tiến công' (tốn Vàng)")
            ctx.click(info.tien_cong, label="Tiến công")
            ctx.count("tien_cong")
        else:
            ctx.step("tấn công")
            ctx.click_object(ARMY, "attack")
        outcome = self._cm._resolve_battle(ctx, stats)
        if outcome is None:
            raise ThanhTuuError(f"Không đánh được {info.title} (bảng đạo quân đóng mà chưa vào trận)")
        stats.record(outcome)
        run.battles += 1
        ctx.count("battles")
        ctx.info(f"Trận {run.battles} với {info.title or 'đạo quân'}: {cm._OUTCOME_VI[outcome]}")
        if outcome is cm.Outcome.WIN:
            run.wins += 1
        self._cm._dismiss_popups(ctx, check_notice=False)
        return outcome

    def _limit_reached(self, run: _Run) -> bool:
        return bool(self.params.max_battles) and run.battles >= self.params.max_battles

    # --- run ------------------------------------------------------------------------------------------
    def _run(self, ctx: RunContext) -> str:
        p = self.params
        state = self.store.load()
        blocker = start_blocker(p, self.store)
        if blocker is not None:
            raise ThanhTuuError(blocker)
        target = state.find(p.map_name)
        assert target is not None
        order = [m.name for m in state.maps]
        self._cm._open_field(ctx)
        self._goto_dungeon(ctx, target.name, order)
        hud_hidden = self._cm._hide_hud(ctx)
        run, stats = _Run(), cm._Stats()
        last: AchTask | None = None  # the task fought in the previous round, checked on this one
        last_rank_done, last_have = 0, 0
        for _ in range(MAX_ROUNDS):
            ctx.checkpoint()
            self._open_dialog(ctx)
            self._select_map(ctx, target.name)
            m, portraits = self._read_map(ctx, target.name)
            m.name = target.name
            state.put(m)
            self.store.save(state)
            ctx.progress(sum(t.done for t in m.tasks), len(m.tasks) or None)
            if last is not None:
                self._check_progress(ctx, m, last, last_rank_done, last_have)
            if m.done:
                if p.claim_rewards:
                    ctx.step("nhận thưởng")
                    ctx.click(f"{KEY}.claim", label="Nhận thưởng")
                    self._pause(ctx, max(p.delay_ms, DIALOG_SETTLE_MS))
                    ctx.observe()  # the reward pop-up is closed by the pop-up guard
                state.score = self._read_score(ctx)
                self.store.save(state)
                self._close_dialog(ctx)
                return f"Phó bản {target.name} đã hoàn thành thành tựu ✅: {run.battles} trận, thắng {run.wins}"
            if self._limit_reached(run):
                self._close_dialog(ctx)
                return f"Đã đủ {p.max_battles} trận: {m.summary}"
            task = m.pending()[0]
            portrait = portraits.get(m.tasks.index(task))
            self._close_dialog(ctx)
            at = self._find_army(ctx, run, task, portrait, hud_hidden)
            last, last_have = task, task.have
            last_rank_done = sum(t.done for t in m.tasks if t.kind is Kind.RANK)
            if task.kind is Kind.RANK:
                ctx.info(f"Đánh {task.name} để đạt hạng S")
                if self._fight(ctx, run, at, stats) is cm.Outcome.LOSE:
                    shot = ctx.save_failure(f"thua {task.name}")
                    raise ThanhTuuError(f"Thua {task.name}: không thể đạt hạng S, dừng để kiểm tra đội hình"
                                        + (f" (ảnh: {shot})" if shot else ""))
                run.used_for_rank.add(at)
                continue
            wins_needed = task.need - task.have
            ctx.info(f"Khiêu chiến {task.name}: cần thắng thêm {wins_needed} trận")
            wins = losses = 0
            while wins < wins_needed and not self._limit_reached(run):
                outcome = self._fight(ctx, run, at, stats)
                if outcome is not cm.Outcome.LOSE:  # an unknown result counts; the next read checks it
                    wins += 1
                else:
                    losses += 1
                    if losses >= 3:
                        raise ThanhTuuError(f"Thua {task.name} {losses} lần: dừng để kiểm tra đội hình")
        raise ThanhTuuError(f"Đã chạy {MAX_ROUNDS} vòng mà phó bản {target.name} chưa xong")

    def _check_progress(self, ctx: RunContext, m: AchMap, last: AchTask, rank_done_before: int,
                        have_before: int) -> None:
        """Stop when the previous round's fight did not count (no S, or the card did not move)."""
        if last.kind is Kind.RANK:
            now = sum(t.done for t in m.tasks if t.kind is Kind.RANK)
            if now <= rank_done_before:
                shot = ctx.save_failure(f"{last.name}: chưa đạt hạng S")
                raise ThanhTuuError(
                    f"Đã thắng {last.name} nhưng thành tựu chưa được ✓: không đạt hạng S"
                    + (f" (ảnh: {shot})" if shot else ""))
            return
        same = [t for t in m.tasks if t.kind is Kind.CHALLENGE and similarity(t.name, last.name) >= NAME_MATCH
                and t.need == last.need]
        if same and not same[0].done and same[0].have <= have_before:
            shot = ctx.save_failure(f"{last.name}: số lần không tăng")
            raise ThanhTuuError(
                f"Đã đánh {last.name} nhưng số lần vẫn {same[0].have}/{same[0].need}"
                + (f" (ảnh: {shot})" if shot else ""))

    def run(self, ctx: RunContext) -> str:
        return self._sync(ctx) if self.mode == "sync" else self._run(ctx)
