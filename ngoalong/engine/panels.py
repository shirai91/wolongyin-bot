"""Panel tidier: collapse the HUD panels that cover the game before every run (user report 2026-10-06).

The city and the dungeon map keep panels open on top of the scene: the quest tracker on the right,
the "Sắp mở" box on the left and the chat log. They hide buildings and armies that features click
(Nâng cấp công trình clicks a building at (883, 243), under the tracker). The top icon rows stay
open: several features open their screen from them (Quay Tinh Tú, ...). ``execute`` (runner) calls ``RunContext.tidy_panels`` before
``Task.run``: each panel shown in its OPEN state is clicked on its own toggle, then checked again.

``profiles/<profile>/panels.yaml``::

    quest_tracker:
      description: "bảng nhiệm vụ bên phải"
      open: {template: field.hud.quest_collapse, at: [807, 165], margin: 8, threshold: 0.85}

``open`` is a template that shows only while the panel is open ("▶", "⌄", the "Sắp mở" title); the match itself is clicked (it is the panel's collapse button). A panel still open after
``max_clicks`` is reported and left alone: tidying never fails a run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ngoalong.core.errors import Cancelled, NgoaLongError, TaskStopped
from ngoalong.engine.popups import TemplateSpot

if TYPE_CHECKING:
    from ngoalong.engine.context import RunContext

log = logging.getLogger(__name__)

DEFAULT_SETTLE_S = 0.5  # the panel slides away before the next check
DEFAULT_MAX_CLICKS = 2


@dataclass(frozen=True, slots=True)
class PanelDef:
    name: str
    description: str
    open: TemplateSpot


class PanelTidier:
    def __init__(self, panels: list[PanelDef], settle_s: float = DEFAULT_SETTLE_S,
                 max_clicks: int = DEFAULT_MAX_CLICKS) -> None:
        self.panels = panels
        self.settle_s = settle_s
        self.max_clicks = max_clicks

    @classmethod
    def load(cls, path: str | Path) -> PanelTidier:
        """Panels of ``panels.yaml``; a missing file means no panels."""
        p = Path(path)
        if not p.is_file():
            return cls([])
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{p.name}: top level must be a mapping of panel name -> definition")
        settings = data.pop("settings", {}) or {}
        panels = [_panel(name, spec, p.name) for name, spec in data.items()]
        return cls(panels, float(settings.get("settle_s", DEFAULT_SETTLE_S)),
                   int(settings.get("max_clicks", DEFAULT_MAX_CLICKS)))

    def tidy(self, ctx: RunContext) -> list[str]:
        """Collapse every open panel; returns the descriptions of those collapsed."""
        closed: list[str] = []
        for panel in self.panels:
            try:
                if self._collapse(ctx, panel):
                    closed.append(panel.description)
            except (Cancelled, TaskStopped):
                raise
            except Exception as exc:  # a panel the bot cannot handle must not stop the feature
                log.warning("panel %s: %s", panel.name, exc)
                ctx.warn(f"Không thu gọn được {panel.description}: {exc}")
        return closed

    def _find(self, ctx: RunContext, panel: PanelDef) -> Any:
        spot = panel.open
        size = ctx.matcher.templates.get(spot.template).size
        return ctx.find(spot.template, region=spot.region(size), threshold=spot.threshold)

    def _collapse(self, ctx: RunContext, panel: PanelDef) -> bool:
        m = self._find(ctx, panel)
        if m is None:
            return False
        for _ in range(self.max_clicks):
            ctx.click(m, label=f"thu gọn {panel.description}")
            ctx.sleep_s(self.settle_s)
            m = self._find(ctx, panel)
            if m is None:
                return True
        ctx.warn(f"{panel.description} vẫn mở sau {self.max_clicks} lần bấm; bỏ qua")
        return False


def _panel(name: str, spec: Any, source: str) -> PanelDef:
    if not isinstance(spec, dict) or not isinstance(spec.get("open"), dict):
        raise ValueError(f"{source}: panel {name!r} needs an 'open' template spot")
    o = spec["open"]
    try:
        spot = TemplateSpot(str(o["template"]), (int(o["at"][0]), int(o["at"][1])), int(o.get("margin", 8)),
                            float(o["threshold"]) if o.get("threshold") is not None else None)
    except (KeyError, TypeError, IndexError, ValueError) as exc:
        raise NgoaLongError(f"{source}: panel {name!r}: bad 'open' spot ({exc})") from exc
    return PanelDef(name, str(spec.get("description", name)), spot)
