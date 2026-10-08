"""Quay rương: spin the Tinh Tú chest wheel (port of v1.8.0 ``QUAYRUONG``, docs/03).

v1.8.0 clicked ``per_spin`` (``coords/quay_ruong.yaml``: "Tự mở", a wheel item, "Tự mở") with the
user delay between clicks, ``count`` times, with no idea what was on screen. The port keeps the
same clicks and delays, and adds:

* the run starts only when the ``chest.wheel`` scene is shown (clear error otherwise);
* optionally the scene is checked again before every spin, so a closed wheel or an unexpected
  popup stops the run (with a failure screenshot) instead of clicking blind;
* a delay after the last click of a spin as well, so two spins never merge into a double click;
* Stop / Pause at any time; progress per spin.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field

from ngoalong.engine.context import RunContext
from ngoalong.engine.registry import register
from ngoalong.engine.task import Task, TaskParams

SCENE = "chest.wheel"
SPIN_KEY = "quay_ruong.per_spin"


class QuayRuongParams(TaskParams):
    spins: int = Field(200, ge=1, le=100_000, title="Số lượt quay")
    delay_ms: int = Field(500, ge=0, le=60_000, title="Thời gian chờ giữa các click (ms)")
    verify_each_spin: bool = Field(True, title="Kiểm tra màn hình Tinh Tú trước mỗi lượt")
    scene_timeout_s: float = Field(5.0, ge=0.5, le=120.0, title="Thời gian chờ màn hình (giây)")


@register
class QuayRuong(Task):
    id = "quay_ruong"
    title = "Quay rương"
    description = "Mở Tinh Tú trong game rồi bấm Bắt đầu: quay rương theo số lượt đã nhập"
    order = 50
    Params = QuayRuongParams
    start_scene = SCENE

    params: QuayRuongParams

    def run(self, ctx: RunContext) -> str:
        p = self.params
        keys = [f"{SPIN_KEY}.{i}" for i in range(len(ctx.coords.offsets(SPIN_KEY)))]
        ctx.step("kiểm tra Tinh Tú")
        ctx.expect_scene(SCENE, p.scene_timeout_s)
        for spin in range(p.spins):
            if spin and p.verify_each_spin:
                ctx.step(f"kiểm tra Tinh Tú (lượt {spin + 1})")
                ctx.expect_scene(SCENE, p.scene_timeout_s)
            ctx.step(f"lượt {spin + 1}/{p.spins}")
            for i, key in enumerate(keys):
                if i:
                    ctx.sleep(p.delay_ms)
                ctx.click(key)
            ctx.count("spins")
            ctx.progress(spin + 1, p.spins)
            if spin + 1 < p.spins:
                ctx.sleep(p.delay_ms)
        return f"Đã quay xong {p.spins} lượt"

    @classmethod
    def import_legacy(cls, ini: Mapping[str, str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, field in (("QR_SOLUONG", "spins"), ("QR_TOCDO", "delay_ms")):
            try:
                value = int(ini.get(key, "").strip())
            except ValueError:
                continue
            if field == "spins" and value < 1:
                continue
            out[field] = max(0, value)
        return out
