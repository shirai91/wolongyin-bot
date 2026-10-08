"""Tháp Thí Luyện: climb the trial tower floor by floor (port of v1.8.0 ``THAPTHILUYEN``, docs/03).

v1.8.0 opened the tower dialog with two blind clicks, typed page "1" into the page box blind (on
this server the box did not take it from page 11 on, user report 2026-10-06), paged to
the start floor, then for every floor: clicked the slot, "Vượt Tháp", "Đồng ý", waited up to 3 s
for its own unit (``TTL_Linh``) and clicked it, then waited **forever** for "Thắng Trận" /
"Bại Trận" (``TTL_Thang*`` / ``TTL_Thua*``), clicked OK (538, 407) on a win and moved to the next
slot (``page next`` after slot 10). A loss ended the run with a message box.

The port keeps that order, the offsets and the templates (cut whole from the v1.8.0 images by
``template_crops.yaml``: ``tower.result.*``, ``tower.battle.soldier``), and:

* opens the dialog from the city through the tower building panel (docs/07), or starts on it;
* reaches the start floor's page with a chosen ``page_strategy``: ◀ until the page box shows 1
  then ▶ (clicks only, each checked), or typing the page number (keys only while the page box
  visibly reacts; the box must keep the number after Enter). Nothing is clicked or typed until the
  "Tháp" tab's floor list is confirmed on screen;
* pages after slot 10 as v1.8.0 (``thap.floors_per_page``), checking that the page really turned;
* checks that each floor's button is shown before clicking it: the list only shows unlocked
  floors, and a click on an empty slot left Tầng 1 selected, so the run climbed from floor 1;
* waits for ``tower.dialog`` / ``tower.confirm`` / ``tower.battle`` instead of blind sleeps;
* the win / loss wait has a time limit (``battle_timeout_s``) and honours Stop (#2);
* after a win, leaves the battle screen if it is still shown and waits for the dialog again;
* a loss ends the run with a summary and a screenshot (no message box); validated inputs (#15).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field, ValidationInfo, field_validator

from ngoalong.core.errors import WaitTimeout
from ngoalong.engine.context import RunContext
from ngoalong.engine.registry import register
from ngoalong.engine.task import Task, TaskParams
from ngoalong.features import _tower as tower

WIN = ("tower.result.win", "tower.result.win_zh")  # "Thắng Trận", 战胜
LOSE = ("tower.result.lose", "tower.result.lose_zh")  # "Bại Trận", 战败
RESULT_THRESHOLD = 0.8  # legacy images; best score on the 47 stored non-result screens is 0.44
SOLDIER = "tower.battle.soldier"  # TTL_Linh: the player's own unit on the tower path
SOLDIER_THRESHOLD = 0.95  # 11x6 px template: demand a near-exact match (1.00 live, 0.65 elsewhere)
SOLDIER_WAIT_S = 3.0  # v1.8.0: IMAGEWAITSEARCHEX(..., CHODOI = 3)
RESULT_OK = "common.close_result_ok"  # (538, 409); v1.8.0 THAPTHILUYEN clicked (538, 407)
MAX_FLOOR = 999


class ThapThiLuyenParams(TaskParams):
    start_floor: int = Field(1, ge=1, le=MAX_FLOOR, title="Tầng bắt đầu")
    end_floor: int = Field(5, ge=1, le=MAX_FLOOR, title="Tầng kết thúc")
    delay_ms: int = Field(500, ge=0, le=60_000, title="Thời gian chờ giữa các bước (ms)")
    click_soldier: bool = Field(
        True, title="Bấm vào quân mình khi vào tháp",
        description="Như bản cũ: chờ tối đa 3 giây, thấy quân mình thì bấm để bắt đầu đánh",
    )
    battle_timeout_s: float = Field(
        300.0, ge=10.0, le=3600.0, title="Thời gian tối đa mỗi trận (giây)",
        description="Không thấy Thắng/Bại sau thời gian này thì dừng và chụp màn hình",
    )
    open_from_city: bool = Field(True, title="Tự mở Tháp Thí Luyện từ Thành chính")
    scene_timeout_s: float = Field(10.0, ge=0.5, le=120.0, title="Thời gian chờ màn hình (giây)")
    page_strategy: tower.PageStrategy = Field(
        tower.PageStrategy.BACK_THEN_NEXT, title="Cách tới trang của tầng bắt đầu",
        description=(
            "Về trang 1 rồi bấm sang trang: chỉ bấm chuột, chậm hơn khi đang ở trang xa. "
            "Gõ số trang: nhấp đúp ô trang, xoá, gõ số, Enter, rồi kiểm tra ô trang giữ đúng số"
        ),
    )

    @field_validator("end_floor")
    @classmethod
    def _not_below_start(cls, v: int, info: ValidationInfo) -> int:
        start = info.data.get("start_floor")
        if start is not None and v < start:
            raise ValueError(f"phải lớn hơn hoặc bằng tầng bắt đầu ({start})")
        return v


@register
class ThapThiLuyen(Task):
    id = "thap_thi_luyen"
    title = "Tháp Thí Luyện"
    description = (
        "Ở Thành chính (hoặc mở sẵn bảng Tháp Thí Luyện) rồi bấm Bắt đầu: vượt lần lượt "
        "từ tầng bắt đầu đến tầng kết thúc, dừng khi thua"
    )
    order = 75
    Params = ThapThiLuyenParams
    start_scene = tower.DIALOG

    params: ThapThiLuyenParams

    def run(self, ctx: RunContext) -> str:
        p = self.params
        if p.end_floor < p.start_floor:  # possible after field-by-field assignment
            raise ValueError(f"Tầng kết thúc ({p.end_floor}) nhỏ hơn tầng bắt đầu ({p.start_floor})")
        total = p.end_floor - p.start_floor + 1
        tower.open_dialog(ctx, p.scene_timeout_s, p.delay_ms, p.open_from_city)
        ctx.sleep(p.delay_ms)
        per_page = tower.per_page(ctx)
        page, slot = tower.floor_position(p.start_floor, per_page)
        tower.goto_page(ctx, page, p.page_strategy, p.delay_ms, p.scene_timeout_s)
        for n, floor in enumerate(range(p.start_floor, p.end_floor + 1)):
            ctx.step(f"tầng {floor}")
            ctx.expect_scene(tower.DIALOG, p.scene_timeout_s)
            ctx.sleep(p.delay_ms)
            tower.require_floor(ctx, floor, page, slot)
            ctx.click(tower.slot_point(ctx, slot), label=f"tầng {floor}")
            ctx.sleep(p.delay_ms)
            ctx.click(tower.CHALLENGE)
            tower.confirm_entry(ctx, p.scene_timeout_s, p.delay_ms)
            tower.enter_battle(ctx, p.scene_timeout_s)
            if p.click_soldier:
                self._click_soldier(ctx)
            if not self._wait_result(ctx, floor):
                shot = ctx.save_failure(f"thua ở tầng {floor}")
                ctx.warn(f"Vượt tháp thất bại ở tầng {floor}" + (f" (ảnh: {shot})" if shot else ""))
                return (f"Vượt tháp thất bại ở tầng {floor}! Hãy xếp lại trận pháp. "
                        f"Đã thắng {n}/{total} tầng")
            ctx.sleep(p.delay_ms)
            ctx.click(RESULT_OK, label="OK kết quả")
            ctx.count("floors")
            ctx.progress(n + 1, total)
            tower.return_to_dialog(ctx, p.scene_timeout_s)
            if floor < p.end_floor:
                if slot == per_page:
                    slot = 1
                    ctx.sleep(p.delay_ms)
                    tower.next_pages(ctx, 1, p.delay_ms, from_page=page)
                    page += 1
                else:
                    slot += 1
        return f"Đã vượt xong từ tầng {p.start_floor} đến tầng {p.end_floor}"

    def _click_soldier(self, ctx: RunContext) -> None:
        ctx.step("tìm quân mình")
        try:
            m = ctx.wait_for(SOLDIER, SOLDIER_WAIT_S, threshold=SOLDIER_THRESHOLD)
        except WaitTimeout:
            ctx.info("Không thấy quân mình trong tháp; chờ kết quả trận")
            return
        ctx.click(m, label="quân mình")

    def _wait_result(self, ctx: RunContext, floor: int) -> bool:
        """True on a win, False on a loss; raises ``WaitTimeout`` after ``battle_timeout_s``."""
        ctx.step(f"chờ kết quả tầng {floor}")
        try:
            m = ctx.wait_for(WIN + LOSE, self.params.battle_timeout_s, threshold=RESULT_THRESHOLD)
        except WaitTimeout as exc:
            raise WaitTimeout(
                f"không thấy kết quả trận (Thắng/Bại) ở tầng {floor} sau "
                f"{self.params.battle_timeout_s:.0f}s: {exc}"
            ) from exc
        won = m.name in WIN
        ctx.info(f"Tầng {floor}: {'thắng' if won else 'thua'} ({m.score:.2f})")
        return won

    @classmethod
    def import_legacy(cls, ini: Mapping[str, str]) -> dict[str, Any]:
        """``TTL_INPUT_BATDAU`` / ``TTL_INPUT_KETTHUC`` (start / end floor); docs/04."""
        out: dict[str, Any] = {}
        for key, field in (("TTL_INPUT_BATDAU", "start_floor"), ("TTL_INPUT_KETTHUC", "end_floor")):
            try:
                v = int(ini.get(key, "").strip())
            except ValueError:
                continue
            if 1 <= v <= MAX_FLOOR:
                out[field] = v
        defaults = ThapThiLuyenParams.model_fields
        start = out.get("start_floor", defaults["start_floor"].default)
        end = out.get("end_floor", defaults["end_floor"].default)
        if end < start:  # keep the pair valid: climb at least the start floor
            out["end_floor"] = start
        return out
