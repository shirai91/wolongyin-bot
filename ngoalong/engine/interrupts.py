"""Interrupts: rules the loop runs before a feature's own rules (docs/08 "Concepts" 5).

* **Pop-ups** (``profiles/<profile>/popups.yaml``): harmless notices the game shows on its own
  (reward "Chúc mừng ngài được", corner toasts, the story box) are closed; cost questions
  (``gold_skip_wait``) are answered by their user setting: accept, or decline and stop the run.
* **Panels** (``panels.yaml``): the quest tracker, "Sắp mở" and the chat log are collapsed on a map
  with no dialog open, at most ``max_clicks`` times each per run (a panel that will not collapse is
  left alone; it never fails a run). Setting: Cài đặt > "Thu gọn các bảng che game".

One click per tick, each with an expectation like any other action.

``popups.yaml``::

    reward:
      description: "Chúc mừng ngài được (reward notice)"
      signature: {template: popup.reward.title, at: [436, 229], margin: 40, threshold: 0.85}
      dismiss: {template: popup.reward.ok, at: [458, 341], margin: 20, threshold: 0.8}
    gold_skip_wait:
      policy: spend_gold_skip_wait        # Settings.game.<policy>: True = accept, False = decline
      signature: {...}                    # what identifies the question
      dismiss: {...}                      # the ACCEPT button
      decline: {...}                      # the DECLINE button
      stop_message: "..."                 # why the run stops after declining

``at`` is the template's canvas top-left (1000x600) where it was cut; ``margin`` widens the search
region on every side. ``dismiss`` names a button template (searched near its ``at`` moved by as much
as the signature moved) or ``{click: signature}`` to click the signature match itself. Only
harmless notices are listed without a ``policy``; never list a box a feature answers itself.

``panels.yaml``::

    quest_tracker:
      description: "bảng nhiệm vụ bên phải"
      open: {template: field.hud.quest_collapse, at: [807, 165], margin: 8, threshold: 0.85}

``open`` is a template that shows only while the panel is open; the match is its collapse button.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ngoalong.core.detection import Detection
from ngoalong.core.errors import NgoaLongError
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.locator import CanvasFix, Confidence
from ngoalong.core.vision import Matcher
from ngoalong.engine.actions import Action, Click
from ngoalong.perception import Snapshot

PANEL_MAPS = {"city", "harbour", "field", "world_map", "map"}
DEFAULT_MAX_CLICKS = 2  # clicks per panel and run


# --- profile data ---------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TemplateSpot:
    template: str
    at: tuple[int, int]  # canvas top-left where the template was cut
    margin: int
    threshold: float | None

    def region(self, size: tuple[int, int], shift: tuple[int, int] = (0, 0)) -> Rect:
        """Canvas search region: the template's own rectangle grown by ``margin``, moved by ``shift``."""
        w, h = size
        return Rect(self.at[0] + shift[0] - self.margin, self.at[1] + shift[1] - self.margin,
                    w + 2 * self.margin, h + 2 * self.margin)


def _spot(raw: Any, where: str, margin: int = 20) -> TemplateSpot:
    try:
        at = raw["at"]
        return TemplateSpot(str(raw["template"]), (int(at[0]), int(at[1])), int(raw.get("margin", margin)),
                            float(raw["threshold"]) if raw.get("threshold") is not None else None)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        raise NgoaLongError(f"{where}: bad template spot {raw!r} ({exc})") from exc


@dataclass(frozen=True, slots=True)
class PopupDef:
    name: str
    description: str
    signature: TemplateSpot
    dismiss: TemplateSpot | None  # None: click the signature match itself; with a policy: ACCEPT
    decline: TemplateSpot | None = None  # with a policy: the DECLINE button
    policy: str | None = None  # Settings.game field that decides accept (True) / decline (False)
    stop_message: str = ""


@dataclass(frozen=True, slots=True)
class PopupHit:
    popup: PopupDef
    point: Point  # frame point to click (the ACCEPT button of a policy pop-up)
    detections: tuple[Detection, ...]
    decline_point: Point | None = None  # policy pop-ups only


def parse_popups(data: Mapping[str, Any] | None) -> list[PopupDef]:
    defs: list[PopupDef] = []
    for name, raw in (data or {}).items():
        if not isinstance(raw, Mapping):
            raise NgoaLongError(f"popups.yaml: {name} must be a mapping")
        sig = _spot(raw.get("signature") or {}, f"popups.yaml {name}.signature")
        d = raw.get("dismiss") or {"click": "signature"}
        dismiss = None if d.get("click") == "signature" else _spot(d, f"popups.yaml {name}.dismiss")
        policy = raw.get("policy")
        decline = _spot(raw["decline"], f"popups.yaml {name}.decline") if raw.get("decline") else None
        if policy is not None and (dismiss is None or decline is None):
            raise NgoaLongError(f"popups.yaml: {name} has a policy, so it needs both dismiss (accept) and decline")
        defs.append(PopupDef(str(name), str(raw.get("description", name)), sig, dismiss, decline,
                             str(policy) if policy is not None else None, str(raw.get("stop_message", ""))))
    return defs


class PopupGuard:
    """Finds the listed pop-ups in a frame and says where to click."""

    def __init__(self, popups: list[PopupDef]) -> None:
        self.popups = list(popups)

    @classmethod
    def load(cls, path: str | Path) -> PopupGuard:
        """``popups.yaml``; a missing file means no pop-up is handled."""
        p = Path(path)
        if not p.is_file():
            return cls([])
        return cls(parse_popups(yaml.safe_load(p.read_text(encoding="utf-8"))))

    def __bool__(self) -> bool:
        return bool(self.popups)

    def find(self, frame: np.ndarray, fix: CanvasFix, matcher: Matcher) -> PopupHit | None:
        """The first listed pop-up shown in ``frame``, with the point to click; None when clear."""
        for popup in self.popups:
            sig = popup.signature
            size = matcher.templates.get(sig.template, 1.0).size
            sig_det = matcher.search(sig.template, fix.rect_to_frame(sig.region(size)), sig.threshold,
                                     frame=frame, scale=fix.scale)
            if not sig_det.found:
                continue
            if popup.dismiss is None:
                return PopupHit(popup, sig_det.rect.center, (sig_det,))
            # The pop-up may sit a little off its usual place: move the button region with it.
            found_at = fix.to_canvas(Point(sig_det.rect.x, sig_det.rect.y))
            shift = (round(found_at[0]) - sig.at[0], round(found_at[1]) - sig.at[1])
            btn_det = self._button(popup.dismiss, shift, frame, fix, matcher)
            if not btn_det.found:
                continue
            if popup.decline is None:
                return PopupHit(popup, btn_det.rect.center, (sig_det, btn_det))
            no_det = self._button(popup.decline, shift, frame, fix, matcher)
            if no_det.found:  # a question is answered only when BOTH of its buttons are seen
                return PopupHit(popup, btn_det.rect.center, (sig_det, btn_det, no_det), no_det.rect.center)
        return None

    @staticmethod
    def _button(spot: TemplateSpot, shift: tuple[int, int], frame: np.ndarray, fix: CanvasFix,
                matcher: Matcher) -> Detection:
        size = matcher.templates.get(spot.template, 1.0).size
        return matcher.search(spot.template, fix.rect_to_frame(spot.region(size, shift)), spot.threshold,
                              frame=frame, scale=fix.scale)


@dataclass(frozen=True, slots=True)
class PanelDef:
    name: str
    description: str
    open: TemplateSpot


@dataclass
class Panels:
    panels: list[PanelDef]
    max_clicks: int = DEFAULT_MAX_CLICKS

    @classmethod
    def load(cls, path: str | Path) -> Panels:
        """``panels.yaml``; a missing file means no panels."""
        p = Path(path)
        if not p.is_file():
            return cls([])
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise NgoaLongError(f"{p.name}: top level must be a mapping of panel name -> definition")
        settings = data.pop("settings", {}) or {}
        panels = []
        for name, spec in data.items():
            if not isinstance(spec, dict) or not isinstance(spec.get("open"), dict):
                raise NgoaLongError(f"{p.name}: panel {name!r} needs an 'open' template spot")
            panels.append(PanelDef(str(name), str(spec.get("description", name)),
                                   _spot(spec["open"], f"{p.name} {name}.open", margin=8)))
        return cls(panels, int(settings.get("max_clicks", DEFAULT_MAX_CLICKS)))

    def __bool__(self) -> bool:
        return bool(self.panels)


def templates_used(popups: PopupGuard, panels: Panels) -> set[str]:
    """Every template the interrupts search (the self-test checks they exist)."""
    names = {s.template for p in popups.popups for s in (p.signature, p.dismiss, p.decline) if s is not None}
    return names | {p.open.template for p in panels.panels}


# --- the interrupts of one run --------------------------------------------------------------------
class Interrupts:
    def __init__(self, popups: PopupGuard | None, panels: Panels | None, matcher: Matcher,
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
