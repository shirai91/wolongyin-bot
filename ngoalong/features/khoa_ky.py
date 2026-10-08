"""Nâng cấp khoa kỹ: upgrade the research techs (port of v1.8.0 ``NANGCAPKHOAKY``, docs/03).

v1.8.0 clicked two city spots to open the research building, then walked the first 3 rows x 6
icons of the tech grid: for each icon it waited, clicked the icon, waited, and clicked "Tăng cấp"
``SOLANNC`` times (``SLEEP/10`` apart). The port keeps the grid walk, the click order, the counts
and the v1.8.0 offsets (``coords/nang_cap_khoa_ky.yaml``; docs/07 confirmed live that they still
land inside the icons and on "Tăng cấp"), and changes:

* opening: the v1.8.0 city clicks miss on this server (docs/07 live recon), so the run starts on
  the ``research`` dialog, or opens it with the bottom-bar "Khoa kỹ" button (scene object
  ``city.research``) from a map screen;
* the ``research`` scene is checked before the run and (optionally) before every tech, so a closed
  dialog or a popup stops the run with a failure screenshot instead of clicking blind;
* the number of upgrades is validated (>= 1, #15) instead of turning "" into 0;
* Stop / Pause at any time (#2, #7), progress per tech, no message box at the end.
"""

from __future__ import annotations

import time

from pydantic import Field

from ngoalong.core.errors import NgoaLongError
from ngoalong.core.geometry import Point
from ngoalong.core.scenes import SceneMatch
from ngoalong.engine.context import RunContext
from ngoalong.engine.registry import register
from ngoalong.engine.task import Task, TaskParams

SCENE = "research"
# Map screens whose bottom bar shows "Khoa kỹ" (docs/07 shared HUD).
OPEN_FROM: tuple[str, ...] = ("city", "world_map", "field")
# The bottom-bar "Khoa kỹ" button, a scene object (scenes/city.yaml) shared by the map screens.
OPEN_OBJECT = ("city", "research")

KEY = "nang_cap_khoa_ky"
ROWS_KEY = f"{KEY}.rows_first_item"
STEP_KEY = f"{KEY}.item_step_x"
UPGRADE_KEY = f"{KEY}.upgrade"
COLUMNS = 6  # v1.8.0: For J = 1 To 6


class KhoaKyParams(TaskParams):
    upgrades_per_tech: int = Field(
        1, ge=1, le=10_000, title="Số lần nâng cấp mỗi khoa kỹ",
        description="Số lần bấm 'Tăng cấp' cho mỗi ô khoa kỹ",
    )
    delay_ms: int = Field(500, ge=0, le=60_000, title="Thời gian chờ quanh mỗi ô khoa kỹ (ms)")
    upgrade_delay_ms: int = Field(50, ge=0, le=60_000, title="Thời gian chờ giữa 2 lần 'Tăng cấp' (ms)")
    verify_each_tech: bool = Field(True, title="Kiểm tra bảng Khoa kỹ trước mỗi ô")
    scene_timeout_s: float = Field(5.0, ge=0.5, le=120.0, title="Thời gian chờ màn hình (giây)")


def tech_grid(ctx: RunContext) -> list[Point]:
    """Canvas points of the techs, row by row (v1.8.0 order: 3 rows x 6 icons)."""
    rows = ctx.coords.offsets(ROWS_KEY)
    step = int(ctx.coords.value(STEP_KEY))
    return [Point(x + col * step, y) for x, y in rows for col in range(COLUMNS)]


def _shown(m: SceneMatch) -> str:
    return m.name if m.found else f"không rõ (gần nhất {m.candidate})"


@register
class KhoaKy(Task):
    id = "khoa_ky"
    title = "Nâng cấp khoa kỹ"
    description = (
        "Ở Thành chính (hoặc mở sẵn bảng Khoa kỹ) rồi bấm Bắt đầu: nâng cấp lần lượt "
        "các ô khoa kỹ của 3 hàng đầu"
    )
    order = 60
    Params = KhoaKyParams
    start_scene = SCENE

    params: KhoaKyParams

    def _open(self, ctx: RunContext) -> None:
        """Make sure the research dialog is shown, opening it from a map screen if needed."""
        p = self.params
        ctx.step("kiểm tra màn hình")
        deadline = time.monotonic() + p.scene_timeout_s
        while True:
            m = ctx.detect_scene()
            if m.name == SCENE:
                return
            if m.name in OPEN_FROM:
                ctx.step("mở Khoa kỹ")
                ctx.click_object(*OPEN_OBJECT)
                ctx.step("chờ bảng Khoa kỹ")
                ctx.expect_scene(SCENE, p.scene_timeout_s)
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NgoaLongError(
                    "cần ở Thành chính hoặc mở bảng Khoa kỹ trước khi chạy "
                    f"(đang hiển thị: {_shown(m)})"
                )
            ctx.sleep_s(min(ctx.poll_interval_s, remaining))

    def run(self, ctx: RunContext) -> str:
        p = self.params
        techs = tech_grid(ctx)
        self._open(ctx)
        total = len(techs)
        for i, tech in enumerate(techs):
            if i and p.verify_each_tech:
                ctx.step(f"kiểm tra Khoa kỹ (ô {i + 1})")
                ctx.expect_scene(SCENE, p.scene_timeout_s)
            ctx.step(f"ô khoa kỹ {i + 1}/{total}")
            ctx.sleep(p.delay_ms)
            ctx.click(tech, label=f"khoa kỹ {i + 1}")
            ctx.sleep(p.delay_ms)
            for k in range(p.upgrades_per_tech):
                ctx.step(f"ô {i + 1}/{total}: tăng cấp {k + 1}/{p.upgrades_per_tech}")
                ctx.click(UPGRADE_KEY, label="Tăng cấp")
                ctx.sleep(p.upgrade_delay_ms)
            ctx.progress(i + 1, total)
        return f"Đã nâng cấp xong {total} ô khoa kỹ ({p.upgrades_per_tech} lần mỗi ô)"
