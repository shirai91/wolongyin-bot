"""Tri Kỷ: look for chosen companions among the Tửu Quán visitors and talk to them (port of
v1.8.0 ``TRIKY``, docs/03).

v1.8.0: the user ticked portraits in two lists (19 purple, 21 orange; ``TriKy/Tim/<i>.bmp`` and
``TriKy/Cam/<i>.bmp`` = checkbox *i*) and entered "Số lần tìm". Each pass searched every ticked
portrait on screen, and for each one found clicked it, "Trò chuyện" (480, 480), the confirm
(600, 370), "Cáo từ" (735, 480) and Enter; then it clicked the refresh button (570, 475), or
(670, 475) when any orange portrait was ticked. The counter went down per companion talked to,
not per search, so with no match it refreshed forever (#17).

The port:

* attempts = number of searches (#17); the visitors are refreshed between two searches;
* on ngoalongviet.com a click on a visitor card talks at once and shows a result box whose only
  button is 确定 (docs/07): the port clicks the portrait, waits for ``tavern.talk_result``, presses
  its 确定 and waits for ``tavern`` again. The v1.8.0 talk / confirm / leave offsets
  (``coords/tri_ky.yaml``) no longer exist on this server and are not clicked;
* the refresh buttons moved (docs/07): the refresh is a ``tavern`` scene object. "Tự động" keeps
  the v1.8.0 rule (an orange portrait ticked: the second, faster invite; else the first);
* the visitors are searched again after each talk (a card may move or leave), each ticked portrait
  at most once per search, and a card already talked to is not clicked twice;
* portraits are matched with a score in the visitor row and clicked at their centre (#3);
* optional limit on the number of talks; Stop / Pause anywhere; progress per search.

The portraits are v1.8.0's images (``template_crops.yaml``): no companion was on screen during the
live recon, so their match on this server is still unverified.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import IntEnum
from typing import Any

from pydantic import Field, field_validator

from ngoalong.core.errors import NgoaLongError, WaitTimeout
from ngoalong.core.geometry import Rect
from ngoalong.engine.context import RunContext, SceneTimeout
from ngoalong.engine.registry import register
from ngoalong.engine.task import Task, TaskParams

TAVERN = "tavern"
TALK_RESULT = "tavern.talk_result"
# Canvas band of the visitor cards (cards at y 52..177, docs/07), left to right.
VISITOR_AREA = Rect(0, 40, 1000, 160)
DEFAULT_THRESHOLD = 0.88  # best score of any portrait on the 47 stored screens: 0.81
MAX_ATTEMPTS = 100_000


class TriKyError(NgoaLongError):
    """Nothing to look for, or the tavern did not react as expected."""


# (group, name) per checkbox index, from the v1.8.0 GUI (v1.8.0 app_logic_only.au3).
_PURPLE: tuple[tuple[str, str], ...] = (
    ("Vật công", "Hoa Man"), ("Vật công", "Quách Thị"), ("Vật công", "Từ Thị"),
    ("Chiến công", "Ngô Quốc Thái"), ("Chiến công", "Biện Thị"),
    ("Mưu công", "Tào Tiết"), ("Mưu công", "Vương Dị"), ("Mưu công", "Tôn Lỗ Ban"),
    ("Vật phòng", "Thái Thị"), ("Vật phòng", "Tào Tiện"), ("Vật phòng", "Tôn Lỗ Dục"),
    ("Vật phòng", "Trương Xuân Hoa"), ("Vật phòng", "Ngô Thị"),
    ("Chiến phòng", "Mi Thị"), ("Chiến phòng", "Tôn Thị"), ("Chiến phòng", "Hạ Hầu Thị"),
    ("Chiến phòng", "Cam Thị"),
    ("Mưu phòng", "Tào Hoa"), ("Mưu phòng", "Phàn Thị"),
)
_ORANGE: tuple[tuple[str, str], ...] = (
    ("Vật công", "Tông Thượng Hương"), ("Vật công", "Quan Ngân Bình"), ("Vật công", "Bào Tam Nương"),
    ("Chiến công", "Lữ Linh Khởi"), ("Chiến công", "Trương Oanh Oanh"), ("Chiến công", "Mã Vân Lộc"),
    ("Chiến công", "Chúc Dung"),
    ("Mưu công", "Hoàng Nguyệt Anh"), ("Mưu công", "Điêu Thuyền"), ("Mưu công", "Chân Cơ"),
    ("Vật phòng", "Châu Thị"), ("Vật phòng", "Tống Hiền Anh"),
    ("Chiến phòng", "Thái Văn Cơ"), ("Chiến phòng", "Đại Kiều"),
    ("Mưu phòng", "Vương Nguyệt Cơ"), ("Mưu phòng", "Tiểu Kiều"), ("Mưu phòng", "Chu Cát Quả"),
    ("Mod", "Chân*Thái Vân Cơ"), ("Mod", "Chân*Vương Nguyên Cơ"), ("Mod", "Chân*Chân Cơ"),
    ("Mod", "Phan Kim Liên"),
)


class Portrait(IntEnum):
    """Base of the two portrait lists; the value is the v1.8.0 checkbox (and file) index."""

    @property
    def color(self) -> str:
        return _COLOR[type(self)]

    @property
    def group(self) -> str:
        return _TABLE[type(self)][self.value][0]

    @property
    def label(self) -> str:
        return _TABLE[type(self)][self.value][1]

    @property
    def template(self) -> str:
        return f"tri_ky.{self.color}.{self.value}"


class TriKyTim(Portrait):
    """Purple companions (v1.8.0 ``TKT_CHECKBOX[0..18]``, ``TriKy/Tim/<i>.bmp``)."""

    HOA_MAN = 0
    QUACH_THI = 1
    TU_THI = 2
    NGO_QUOC_THAI = 3
    BIEN_THI = 4
    TAO_TIET = 5
    VUONG_DI = 6
    TON_LO_BAN = 7
    THAI_THI = 8
    TAO_TIEN = 9
    TON_LO_DUC = 10
    TRUONG_XUAN_HOA = 11
    NGO_THI = 12
    MI_THI = 13
    TON_THI = 14
    HA_HAU_THI = 15
    CAM_THI = 16
    TAO_HOA = 17
    PHAN_THI = 18


class TriKyCam(Portrait):
    """Orange companions (v1.8.0 ``TKC_CHECKBOX[0..20]``, ``TriKy/Cam/<i>.bmp``)."""

    TONG_THUONG_HUONG = 0
    QUAN_NGAN_BINH = 1
    BAO_TAM_NUONG = 2
    LU_LINH_KHOI = 3
    TRUONG_OANH_OANH = 4
    MA_VAN_LOC = 5
    CHUC_DUNG = 6
    HOANG_NGUYET_ANH = 7
    DIEU_THUYEN = 8
    CHAN_CO = 9
    CHAU_THI = 10
    TONG_HIEN_ANH = 11
    THAI_VAN_CO = 12
    DAI_KIEU = 13
    VUONG_NGUYET_CO = 14
    TIEU_KIEU = 15
    CHU_CAT_QUA = 16
    MOD_THAI_VAN_CO = 17
    MOD_VUONG_NGUYEN_CO = 18
    MOD_CHAN_CO = 19
    PHAN_KIM_LIEN = 20


_TABLE: dict[type[Portrait], tuple[tuple[str, str], ...]] = {TriKyTim: _PURPLE, TriKyCam: _ORANGE}
_COLOR: dict[type[Portrait], str] = {TriKyTim: "tim", TriKyCam: "cam"}


class RefreshButton(IntEnum):
    """Tửu Quán button that brings new visitors (objects of the ``tavern`` scene)."""

    AUTO = 0
    OTHER_CITY = 1
    INVITE_WINE = 2
    INVITE_FAST = 3

    @property
    def label(self) -> str:
        return _REFRESH_LABELS[self]


_REFRESH_LABELS: dict[RefreshButton, str] = {
    RefreshButton.AUTO: "Tự động (có tri kỷ cam: Mời nhanh, không: Mời rượu)",
    RefreshButton.OTHER_CITY: "Thành khác",
    RefreshButton.INVITE_WINE: "Mời rượu",
    RefreshButton.INVITE_FAST: "Mời nhanh",
}
_REFRESH_OBJECTS: dict[RefreshButton, str] = {
    RefreshButton.OTHER_CITY: "other_city",
    RefreshButton.INVITE_WINE: "invite_wine",
    RefreshButton.INVITE_FAST: "invite_fast",
}


def _unique_sorted[E: Portrait](values: Sequence[E]) -> list[E]:
    return sorted(set(values))


class TriKyParams(TaskParams):
    purple: list[TriKyTim] = Field(
        default_factory=list, title="Danh sách tri kỷ tím", json_schema_extra={"widget": "check_list"},
    )
    orange: list[TriKyCam] = Field(
        default_factory=list, title="Danh sách tri kỷ cam", json_schema_extra={"widget": "check_list"},
    )
    attempts: int = Field(
        50, ge=1, le=MAX_ATTEMPTS, title="Số lần tìm",
        description="Số lần tìm trong Tửu Quán; giữa hai lần tìm bot đổi khách",
    )
    refresh: RefreshButton = Field(RefreshButton.AUTO, title="Nút đổi khách")
    max_talks: int = Field(0, ge=0, le=MAX_ATTEMPTS, title="Dừng sau số lần trò chuyện (0 = không giới hạn)")
    delay_ms: int = Field(200, ge=0, le=60_000, title="Thời gian chờ giữa các bước (ms)")
    search_delay_ms: int = Field(1000, ge=0, le=60_000, title="Chờ khách mới hiện sau khi đổi (ms)")
    match_threshold: float = Field(DEFAULT_THRESHOLD, ge=0.6, le=0.99, title="Độ khớp ảnh chân dung")
    scene_timeout_s: float = Field(5.0, ge=0.5, le=120.0, title="Thời gian chờ màn hình (giây)")

    @field_validator("purple", "orange")
    @classmethod
    def _dedupe(cls, v: list[Portrait]) -> list[Portrait]:
        return _unique_sorted(v)

    def targets(self) -> list[Portrait]:
        return [*self.purple, *self.orange]

    def refresh_object(self) -> str:
        """``tavern`` object to click for new visitors."""
        button = self.refresh
        if button is RefreshButton.AUTO:
            button = RefreshButton.INVITE_FAST if self.orange else RefreshButton.INVITE_WINE
        return _REFRESH_OBJECTS[button]


def _overlaps(a: Rect, b: Rect) -> bool:
    return not a.intersect(b).is_empty()


@register
class TriKy(Task):
    id = "tri_ky"
    title = "Tri Kỷ"
    description = (
        "Mở Tửu Quán trong game, chọn các tri kỷ cần tìm rồi bấm Bắt đầu: bot tìm chân dung trong "
        "khách, trò chuyện với người tìm thấy và đổi khách giữa các lần tìm"
    )
    order = 85
    Params = TriKyParams
    start_scene = TAVERN

    params: TriKyParams

    @classmethod
    def required_templates(cls) -> set[str]:
        return {p.template for p in (*TriKyTim, *TriKyCam)}

    def run(self, ctx: RunContext) -> str:
        p = self.params
        targets = p.targets()
        if not targets:
            raise TriKyError("Chưa chọn tri kỷ nào: hãy đánh dấu ít nhất một tri kỷ tím hoặc cam")
        by_template = {t.template: t for t in targets}
        refresh = p.refresh_object()
        ctx.step("kiểm tra Tửu Quán")
        ctx.expect_scene(TAVERN, p.scene_timeout_s)
        talked: list[str] = []
        searches = 0
        for attempt in range(1, p.attempts + 1):
            ctx.step(f"tìm lần {attempt}/{p.attempts}")
            searches = attempt
            limit_reached = self._talk_to_visitors(ctx, by_template, talked)
            ctx.progress(attempt, p.attempts)
            if limit_reached:
                ctx.info(f"Đã trò chuyện đủ {p.max_talks} lần")
                break
            if attempt < p.attempts:
                self._refresh(ctx, refresh)
        summary = f"Đã tìm {searches} lần, trò chuyện với {len(talked)} tri kỷ"
        return summary + (f": {', '.join(talked)}" if talked else "")

    def _talk_to_visitors(self, ctx: RunContext, by_template: dict[str, Portrait], talked: list[str]) -> bool:
        """Talk to every ticked portrait on screen; True when ``max_talks`` is reached."""
        p = self.params
        remaining = list(by_template)
        done: list[Rect] = []
        while remaining:
            try:
                m = ctx.wait_for(remaining, 0.0, region=VISITOR_AREA, threshold=p.match_threshold)
            except WaitTimeout:
                return False
            remaining.remove(m.name)
            if any(_overlaps(m.rect, r) for r in done):
                continue  # another portrait on a card already talked to
            portrait = by_template[m.name]
            ctx.sleep(p.delay_ms)
            ctx.click(m, label=portrait.label)
            self._finish_talk(ctx, portrait)
            done.append(m.rect)
            talked.append(portrait.label)
            ctx.info(f"Đã trò chuyện với {portrait.label} ({m.score:.2f})")
            if p.max_talks and len(talked) >= p.max_talks:
                return True
        return False

    def _finish_talk(self, ctx: RunContext, portrait: Portrait) -> None:
        p = self.params
        try:
            ctx.expect_scene(TALK_RESULT, p.scene_timeout_s)
        except SceneTimeout as exc:
            raise SceneTimeout(
                f"{exc} - đã bấm vào {portrait.label} nhưng không hiện kết quả trò chuyện "
                "(hết lượt hội đàm?)",
                exc.expected, exc.actual,
            ) from exc
        ctx.sleep(p.delay_ms)
        ctx.click_object(TALK_RESULT, "ok")
        ctx.expect_scene(TAVERN, p.scene_timeout_s)

    def _refresh(self, ctx: RunContext, obj: str) -> None:
        p = self.params
        ctx.step("đổi khách")
        ctx.sleep(p.delay_ms)
        ctx.click_object(TAVERN, obj)
        ctx.sleep(p.search_delay_ms)
        try:
            ctx.expect_scene(TAVERN, p.scene_timeout_s)
        except SceneTimeout as exc:
            raise SceneTimeout(
                f"{exc} - sau khi bấm đổi khách Tửu Quán không hiện lại (hết lượt mời hoặc thiếu vàng?)",
                exc.expected, exc.actual,
            ) from exc

    @classmethod
    def import_legacy(cls, ini: Mapping[str, str]) -> dict[str, Any]:
        """v1.8.0 saved no Tri Kỷ value in ``[PRO5]`` (docs/04)."""
        return {}
