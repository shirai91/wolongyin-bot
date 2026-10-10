"""AutoClick: background clicker on recorded canvas points (v1.8.0 ``ACP_RUN``, docs/03).

Differences from v1.8.0, on purpose:

* points are canvas-relative (1000x600), so they survive window moves and page zoom; recording a
  point never re-targets the game window (#4);
* the click type is honoured (#10);
* a run can be limited to a number of rounds; Stop / Pause work at any time (#2, #7).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ngoalong.core.geometry import Point
from ngoalong.core.input import MouseButton
from ngoalong.engine.actor import Actor
from ngoalong.engine.registry import register
from ngoalong.engine.task import Task, TaskParams

CANVAS_W, CANVAS_H = 1000, 600


class ClickType(StrEnum):
    """The six click types of v1.8.0's AutoClick combo (#10: now honoured)."""

    LEFT = "left"
    LEFT_DOUBLE = "left_double"
    MIDDLE = "middle"
    MIDDLE_DOUBLE = "middle_double"
    RIGHT = "right"
    RIGHT_DOUBLE = "right_double"

    @property
    def button(self) -> MouseButton:
        return self.value.split("_")[0]  # type: ignore[return-value]

    @property
    def double(self) -> bool:
        return self.value.endswith("_double")

    @property
    def label(self) -> str:
        return _CLICK_LABELS[self]


_CLICK_LABELS: dict[ClickType, str] = {
    ClickType.LEFT: "Chuột trái",
    ClickType.LEFT_DOUBLE: "Chuột trái (nhấp đúp)",
    ClickType.MIDDLE: "Chuột giữa",
    ClickType.MIDDLE_DOUBLE: "Chuột giữa (nhấp đúp)",
    ClickType.RIGHT: "Chuột phải",
    ClickType.RIGHT_DOUBLE: "Chuột phải (nhấp đúp)",
}


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

    def run(self, actor: Actor) -> str:
        """Click the points in turn. No screen recognition: the canvas is only located (again when
        it is a few seconds old or the window changed size) to turn canvas points into window points."""
        p = self.params
        if not p.points:
            raise ValueError("Chưa có tọa độ click: hãy ghi ít nhất một điểm")
        per_round = len(p.points)
        total = p.rounds * per_round if p.rounds else None
        done = 0
        rnd = 0
        while p.rounds == 0 or rnd < p.rounds:
            for pt in p.points:
                actor.canvas_fix()
                actor.click(pt.to_point(), p.click_type.double, p.click_type.button)
                done += 1
                actor.progress(done, total)
                if total is None or done < total:
                    actor.sleep(p.interval_ms / 1000)
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
