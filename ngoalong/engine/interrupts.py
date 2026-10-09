"""Interrupts: rules the loop runs before a feature's own rules (docs/08 "Concepts" 5).

* **Pop-ups** (``popups.yaml``, ``engine/popups.py``): harmless notices the game shows on its own
  (reward "Chúc mừng ngài được", corner toasts) are closed; cost questions (``gold_skip_wait``)
  are answered by their user setting: accept, or decline and stop the run.
* **Panels** (``panels.yaml``, ``engine/panels.py``): the quest tracker, "Sắp mở" and the chat
  log are collapsed on a map with no dialog open, at most ``max_clicks`` times each per run (a
  panel that will not collapse is left alone; it never fails a run).

The YAML files and their templates are the ones the old engine used; only the place they run
changed: inside the loop, one click per tick, each with an expectation like any other action.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from ngoalong.core.geometry import Point, Rect
from ngoalong.core.locator import CanvasFix, Confidence
from ngoalong.core.vision import Matcher
from ngoalong.engine.actions import Action, Click
from ngoalong.engine.panels import PanelTidier
from ngoalong.engine.popups import PopupGuard
from ngoalong.perception import Snapshot

PANEL_MAPS = {"city", "harbour", "field", "world_map", "map"}


class Interrupts:
    def __init__(self, popups: PopupGuard | None, panels: PanelTidier | None, matcher: Matcher,
                 policy: Callable[[str], bool]) -> None:
        self.popups = popups
        self.panels = panels
        self.matcher = matcher
        self.policy = policy
        self._panel_clicks: dict[str, int] = {}

    def check(self, frame: np.ndarray, fix: CanvasFix, snap: Snapshot,
              answered: frozenset[str] = frozenset()) -> tuple[str, Action] | None:
        """(interrupt name, action) for the first interrupt that applies, else None. Cost questions
        are left alone when the top screen is one the running feature answers itself (``answered``:
        the spend-Vàng box after the feature's own "buy")."""
        if self.popups:
            hit = self.popups.find(frame, fix, self.matcher)
            if hit is not None:
                p = hit.popup
                if p.policy is not None and snap.top in answered:
                    return None
                if p.policy is None:
                    return f"popup {p.name}", Click(_canvas(fix, hit.point), label=f"đóng {p.description}",
                                                    within=2.0, expect=_gone(self, p.name))
                if self.policy(p.policy):
                    return f"popup {p.name}", Click(_canvas(fix, hit.point), label=f"{p.description}: đồng ý",
                                                    within=2.0, expect=_gone(self, p.name))
                no = hit.decline_point or hit.point
                return f"popup {p.name}", Click(_canvas(fix, no), label=f"{p.description}: huỷ",
                                                stop=p.stop_message or f"{p.description}: đã từ chối")
        if self.panels and snap.base in PANEL_MAPS and not snap.dialogs and not snap.unknown:
            for panel in self.panels.panels:
                if self._panel_clicks.get(panel.name, 0) >= self.panels.max_clicks:
                    continue
                spot = panel.open
                size = self.matcher.templates.get(spot.template).size
                det = self.matcher.search(spot.template, fix.rect_to_frame(spot.region(size)), spot.threshold,
                                          frame=frame, scale=fix.scale)
                if det.found:
                    self._panel_clicks[panel.name] = self._panel_clicks.get(panel.name, 0) + 1
                    return f"panel {panel.name}", Click(_canvas(fix, det.rect.center),
                                                        label=f"thu gọn {panel.description}", settle=0.5)
        return None


def _canvas(fix: CanvasFix, p: Point) -> Point:
    x, y = fix.to_canvas(p)
    return Point(round(x), round(y))


# A Snapshot keeps the canvas at 1000x600: searched as a frame, its canvas starts at 0,0, scale 1.
UNIT = CanvasFix(Rect(0, 0, 1000, 600), 1.0, Confidence.HIGH)


def _gone(ints: Interrupts, name: str) -> Callable[[Snapshot], bool]:
    """Expectation: the pop-up ``name`` is no longer shown."""

    def check(snap: Snapshot) -> bool:
        hit = ints.popups.find(snap.senses.canvas, UNIT, ints.matcher) if ints.popups else None
        return hit is None or hit.popup.name != name

    return check
