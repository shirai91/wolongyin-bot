"""Mở thẻ bài: draw cards of one card type (port of v1.8.0 ``MOTHEBAI``, docs/03).

v1.8.0 sequence (offsets in ``coords/mo_the_bai.yaml``), kept click for click:

1. wait, "lùi" x3 (back to card 1), wait, "tiến" x (card - 1), wait, "chọn";
2. with "Mua thẻ": wait, "mua", wait, double-click the quantity box, wait, type the quantity,
   wait, double-click "rút" (confirms the buy dialog), wait, OK;
3. ``quantity`` times: "rút SLL", "rút", "rút SLL" (5 ms apart).

Changes, on purpose:

* the quantity is validated (>= 1, #15): the "Số lượng" placeholder can no longer become 0;
* the card type is a fixed choice instead of a parsed combo text;
* a small delay after the last click of a draw too, so two draws never merge into a double click;
* text is typed as characters (``InputDriver.text``) instead of key-downs only (#9);
* the card screen was not reached during the live recon, so it has no scene signature yet. The run
  refuses to start while another known dialog (Khoa kỹ, Tướng, ...) is open, and otherwise clicks
  the v1.8.0 offsets;
* Stop / Pause at any time (#2, #7), progress per draw, no message box at the end.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import IntEnum
from typing import Any

from pydantic import Field

from ngoalong.core.errors import NgoaLongError
from ngoalong.engine.context import RunContext
from ngoalong.engine.registry import register
from ngoalong.engine.task import Task, TaskParams

KEY = "mo_the_bai"
BACK_CLICKS = 3  # v1.8.0 pressed "lùi" three times to be sure card 1 is shown
MAX_QUANTITY = 100_000


class CardType(IntEnum):
    """The three card types of v1.8.0's combo; the value is how many cards from the first."""

    HAC_DIEU_THACH = 1
    HOA_DIEM_SON = 2
    BONG_LAI_TIEN_SON = 3

    @property
    def label(self) -> str:
        return _CARD_LABELS[self]


_CARD_LABELS: dict[CardType, str] = {
    CardType.HAC_DIEU_THACH: "1. Thẻ Bài Hắc Diệu Thạch",
    CardType.HOA_DIEM_SON: "2. Thẻ Bài Hỏa Diễm Sơn",
    CardType.BONG_LAI_TIEN_SON: "3. Bồng Lai Tiên Sơn",
}


class MoTheBaiParams(TaskParams):
    card: CardType = Field(CardType.HAC_DIEU_THACH, title="Loại thẻ bài")
    quantity: int = Field(
        100, ge=1, le=MAX_QUANTITY, title="Số lượng",
        description="Số lần rút thẻ (và số thẻ mua khi chọn 'Mua thẻ')",
    )
    buy_first: bool = Field(False, title="Mua thẻ trước khi mở")
    delay_ms: int = Field(500, ge=0, le=60_000, title="Thời gian chờ giữa các bước (ms)")
    draw_delay_ms: int = Field(5, ge=0, le=60_000, title="Thời gian chờ giữa các click khi rút (ms)")
    check_screen: bool = Field(
        True, title="Không chạy khi đang mở bảng khác",
        description="Dừng nếu bot nhận ra một bảng khác đang mở (Khoa kỹ, Tướng, Trang bị, ...)",
    )


@register
class MoTheBai(Task):
    id = "mo_the_bai"
    title = "Mở thẻ bài"
    description = "Mở màn hình thẻ bài trong game rồi bấm Bắt đầu: chọn loại thẻ, mua (tùy chọn) và rút thẻ"
    order = 55
    Params = MoTheBaiParams

    params: MoTheBaiParams

    def _check_screen(self, ctx: RunContext) -> None:
        if not self.params.check_screen or ctx.scenes is None:
            return
        ctx.step("kiểm tra màn hình")
        m = ctx.detect_scene()
        if m.found and m.kind == "modal":
            raise NgoaLongError(
                f"đang mở bảng '{m.name}', không phải màn hình thẻ bài: hãy đóng nó và mở "
                "màn hình thẻ bài trước khi chạy"
            )

    def _buy(self, ctx: RunContext) -> None:
        p = self.params
        ctx.step("mua thẻ")
        ctx.sleep(p.delay_ms)
        ctx.click(f"{KEY}.buy", label="mua thẻ")
        ctx.sleep(p.delay_ms)
        ctx.click(f"{KEY}.buy_quantity_box", double=True, label="ô số lượng")
        ctx.sleep(p.delay_ms)
        ctx.checkpoint()
        ctx.input.text(str(p.quantity))
        ctx.sleep(p.delay_ms)
        ctx.click(f"{KEY}.draw", double=True, label="xác nhận mua")
        ctx.sleep(p.delay_ms)
        ctx.click(f"{KEY}.buy_confirm", label="OK")

    def run(self, ctx: RunContext) -> str:
        p = self.params
        self._check_screen(ctx)
        ctx.info("Màn hình thẻ bài chưa có mẫu nhận diện: bot click theo tọa độ của v1.8.0")

        ctx.step("về thẻ đầu tiên")
        ctx.sleep(p.delay_ms)
        for _ in range(BACK_CLICKS):
            ctx.click(f"{KEY}.back", label="lùi")
        ctx.step(f"chọn {p.card.label}")
        ctx.sleep(p.delay_ms)
        for _ in range(int(p.card) - 1):
            ctx.click(f"{KEY}.next", label="tiến")
        ctx.sleep(p.delay_ms)
        ctx.click(f"{KEY}.select", label="chọn thẻ")

        if p.buy_first:
            self._buy(ctx)

        for i in range(p.quantity):
            ctx.step(f"rút {i + 1}/{p.quantity}")
            ctx.click(f"{KEY}.draw_toggle", label="rút SLL")
            ctx.sleep(p.draw_delay_ms)
            ctx.click(f"{KEY}.draw", label="rút")
            ctx.sleep(p.draw_delay_ms)
            ctx.click(f"{KEY}.draw_toggle", label="rút SLL")
            ctx.progress(i + 1, p.quantity)
            if i + 1 < p.quantity:
                ctx.sleep(p.draw_delay_ms)
        return f"Đã mở xong {p.quantity} lần ({p.card.label})"

    @classmethod
    def import_legacy(cls, ini: Mapping[str, str]) -> dict[str, Any]:
        """``MTB_SOLUONGTHE``; the placeholder "Số lượng", blanks and values < 1 are dropped (#15)."""
        try:
            quantity = int(ini.get("MTB_SOLUONGTHE", "").strip())
        except ValueError:
            return {}
        return {"quantity": min(quantity, MAX_QUANTITY)} if quantity >= 1 else {}
