"""AutoClick: background clicker on recorded canvas points (v1.8.0 ``ACP_RUN``, docs/03).

Differences from v1.8.0, on purpose:

* points are canvas-relative (1000x600), so they survive window moves and page zoom; recording a
  point never re-targets the game window (#4);
* the click type is honoured (#10);
* a run can be limited to a number of rounds; Stop / Pause work at any time (#2, #7).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ngoalong.core.geometry import Point
from ngoalong.engine.context import ClickType, RunContext
from ngoalong.engine.registry import register
from ngoalong.engine.task import Task, TaskParams

CANVAS_W, CANVAS_H = 1000, 600


class CanvasPoint(BaseModel):
    """A point on the game canvas at 1000x600."""

    model_config = ConfigDict(frozen=True)

    x: int = Field(ge=0, lt=CANVAS_W)
    y: int = Field(ge=0, lt=CANVAS_H)

    def to_point(self) -> Point:
        return Point(self.x, self.y)


class AutoClickParams(TaskParams):
    points: list[CanvasPoint] = Field(
        default_factory=list,
        title="Danh sách tọa độ",
        description="Các điểm trên khung game (1000x600) được click lần lượt",
        json_schema_extra={"widget": "canvas_points"},
    )
    click_type: ClickType = Field(ClickType.LEFT, title="Kiểu click")
    interval_ms: int = Field(1000, ge=10, le=3_600_000, title="Thời gian giữa 2 lần click (ms)")
    rounds: int = Field(0, ge=0, le=1_000_000, title="Số vòng", description="0 = chạy đến khi bấm Dừng")


def _parse_xy(text: str) -> tuple[int, int] | None:
    parts = text.strip().lower().split("x")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


@register
class AutoClick(Task):
    id = "autoclick"
    title = "AutoClick"
    description = "Click lần lượt các điểm đã ghi trên khung game, chạy ngầm"
    order = 900
    Params = AutoClickParams

    params: AutoClickParams

    def run(self, ctx: RunContext) -> str:
        p = self.params
        if not p.points:
            raise ValueError("Chưa có tọa độ click: hãy ghi ít nhất một điểm")
        per_round = len(p.points)
        total = p.rounds * per_round if p.rounds else None
        done = 0
        rnd = 0
        while p.rounds == 0 or rnd < p.rounds:
            for i, pt in enumerate(p.points):
                ctx.step(f"vòng {rnd + 1} điểm {i + 1}")
                ctx.click(pt.to_point(), p.click_type, label=f"điểm {i + 1}")
                done += 1
                ctx.progress(done, total)
                if total is None or done < total:
                    ctx.sleep(p.interval_ms)
            rnd += 1
        return f"Đã click {done} lần"

    @classmethod
    def import_legacy(cls, ini: Mapping[str, str]) -> dict[str, Any]:
        """``ACP_*`` keys; v1.8.0 points were client pixels, made canvas-relative with TOADO_GOC."""
        out: dict[str, Any] = {}
        origin = _parse_xy(ini.get("TOADO_GOC", "")) or (0, 0)
        raw = [_parse_xy(s) for s in ini.get("ACP_LIST_TOADO", "").split("|") if s.strip()]
        if not any(raw):
            single = _parse_xy(f"{ini.get('ACP_LABEL_X', '')}x{ini.get('ACP_LABEL_Y', '')}")
            raw = [single] if single else []
        points = []
        for xy in raw:
            if xy is None:
                continue
            x, y = xy[0] - origin[0], xy[1] - origin[1]
            if 0 <= x < CANVAS_W and 0 <= y < CANVAS_H:
                points.append({"x": x, "y": y})
        if points:
            out["points"] = points
        try:
            s = int(ini.get("ACP_INPUT_S", "") or 0)
            ms = int(ini.get("ACP_INPUT_MS", "") or 0)
            if s or ms:
                out["interval_ms"] = max(10, s * 1000 + ms)
        except ValueError:
            pass
        return out
