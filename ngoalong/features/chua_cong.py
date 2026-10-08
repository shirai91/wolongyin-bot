"""Cày cấp chúa công: lord XP farm in the trial tower (port of v1.8.0 ``CAYCAPCHUACONG``, docs/03).

v1.8.0 opened the tower dialog with two blind clicks, clicked "page back" 26 times, "page next"
``ceil(floor/10) - 1`` times (10 floors per page in v1.8.0), clicked the floor slot, then ``tokens // 3`` times: "Vượt Tháp",
"Đồng ý", "Thoát", "Đồng ý", with the user delay after every click and no idea of the screen.

The port keeps the floor paging, the slot offsets, the loop count and the delays, and:

* opens the dialog from the city through the tower building panel (the v1.8.0 click (525, 320)
  misses on this server, docs/07), or starts on an already open dialog;
* waits for each screen instead of sleeping blind: ``tower.dialog`` before "Vượt Tháp",
  ``tower.confirm`` before 确定 (a missing confirm box usually means no tokens left: the run stops
  with a clear message and a screenshot), ``tower.battle`` before "Thoát", and the dialog again
  after leaving (an optional confirm box is answered when it appears);
* pages by 10 floors per page as v1.8.0 (``thap.floors_per_page``), fixes the decompiled
  "+4294967295" paging for the last slot, checks each page turn, and refuses a floor whose button
  is not shown (the list only shows unlocked floors);
* validates the inputs (#15), Stop / Pause at any time (#2, #7), progress per entry.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field

from ngoalong.engine.context import RunContext
from ngoalong.engine.registry import register
from ngoalong.engine.task import Task, TaskParams
from ngoalong.features import _tower as tower


class ChuaCongParams(TaskParams):
    tokens: int = Field(
        1000, ge=tower.TOKENS_PER_ENTRY, le=1_000_000, title="Số Thí luyện lệnh sử dụng",
        description="Mỗi lần vào tháp mất 3 Thí luyện lệnh: số lần vào = số lệnh / 3",
    )
    floor: int = Field(6, ge=1, le=999, title="Tầng tháp")
    delay_ms: int = Field(500, ge=0, le=60_000, title="Thời gian chờ giữa các bước (ms)")
    open_from_city: bool = Field(True, title="Tự mở Tháp Thí Luyện từ Thành chính")
    scene_timeout_s: float = Field(10.0, ge=0.5, le=120.0, title="Thời gian chờ màn hình (giây)")


def _int(ini: Mapping[str, str], key: str) -> int | None:
    try:
        return int(ini.get(key, "").strip())
    except ValueError:
        return None


@register
class ChuaCong(Task):
    id = "chua_cong"
    title = "Cày cấp chúa công"
    description = (
        "Ở Thành chính (hoặc mở sẵn bảng Tháp Thí Luyện) rồi bấm Bắt đầu: chọn tầng, "
        "vào tháp rồi thoát ngay, lặp lại đến khi dùng hết số Thí luyện lệnh đã nhập"
    )
    order = 70
    Params = ChuaCongParams
    start_scene = tower.DIALOG

    params: ChuaCongParams

    @property
    def entries(self) -> int:
        """v1.8.0 ``SOLANFARM = INT(SOQUANLENH / 3)``."""
        return self.params.tokens // tower.TOKENS_PER_ENTRY

    def run(self, ctx: RunContext) -> str:
        p = self.params
        runs = self.entries
        tower.open_dialog(ctx, p.scene_timeout_s, p.delay_ms, p.open_from_city)
        tower.ensure_floor_list(ctx, p.scene_timeout_s)
        tower.first_page_by_back(ctx)
        pages, slot = tower.floor_position(p.floor, tower.per_page(ctx))
        tower.next_pages(ctx, pages, p.delay_ms)
        ctx.step(f"chọn tầng {p.floor}")
        tower.require_floor(ctx, p.floor, pages, slot)
        ctx.click(tower.slot_point(ctx, slot), label=f"tầng {p.floor}")
        for i in range(runs):
            ctx.step(f"lượt {i + 1}/{runs}: vượt tháp")
            ctx.expect_scene(tower.DIALOG, p.scene_timeout_s)
            ctx.sleep(p.delay_ms)
            ctx.click(tower.SWEEP)
            tower.confirm_entry(ctx, p.scene_timeout_s, p.delay_ms)
            tower.enter_battle(ctx, p.scene_timeout_s)
            ctx.sleep(p.delay_ms)
            tower.return_to_dialog(ctx, p.scene_timeout_s)
            ctx.progress(i + 1, runs)
        return f"Đã vào tháp tầng {p.floor} {runs} lần (dùng {runs * tower.TOKENS_PER_ENTRY} Thí luyện lệnh)"

    @classmethod
    def import_legacy(cls, ini: Mapping[str, str]) -> dict[str, Any]:
        """``CCCC_QUANLENH`` (tokens), ``CCCC_SLEEP`` (ms), ``CCCC_TANGTHAP`` (floor); docs/04."""
        out: dict[str, Any] = {}
        tokens = _int(ini, "CCCC_QUANLENH")
        if tokens is not None and tokens >= tower.TOKENS_PER_ENTRY:
            out["tokens"] = min(tokens, 1_000_000)
        delay = _int(ini, "CCCC_SLEEP")
        if delay is not None and delay >= 0:
            out["delay_ms"] = min(delay, 60_000)
        floor = _int(ini, "CCCC_TANGTHAP")
        if floor is not None and 1 <= floor <= 999:
            out["floor"] = floor
        return out
