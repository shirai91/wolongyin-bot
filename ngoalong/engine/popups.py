"""Popup guard: dismiss harmless pop-ups that can appear on top of any screen (user report 2026-10-06).

The game shows reward and notice pop-ups on its own, in the middle of whatever a feature is doing
(for example "Chúc mừng ngài được … M.C+4500" over a tower battle). They block the screen the
feature waits for, so the run stalled until its time limit. ``RunContext.observe`` asks the guard
about every frame it captures; when a known pop-up is shown, the guard tells it where to click to
close it, the context clicks, waits briefly and captures again. Features need no code for it.

Only HARMLESS pop-ups belong in ``profiles/<profile>/popups.yaml``: notices and rewards whose only
button closes them. Dialogs a feature expects (cost confirmations such as ``tower.confirm``, battle
results) must never be listed there: the guard would answer them behind the feature's back.

``popups.yaml``::

    reward:
      description: "Chúc mừng ngài được (reward notice)"
      signature: {template: popup.reward.title, at: [436, 229], margin: 40, threshold: 0.85}
      dismiss: {template: popup.reward.ok, at: [458, 341], margin: 30, threshold: 0.8}

``at`` is the template's canvas position (top-left, 1000x600) where it was cut; ``margin`` widens
the search region on every side (pop-ups are centred, but their height can vary). ``dismiss``
either names a button template (searched near its ``at`` shifted by the same amount the signature
moved) or ``{click: signature}`` to click the signature match itself (a close ✕).

Pop-ups that ASK something with a cost are answered by a user setting instead (``policy``)::

    gold_skip_wait:
      policy: spend_gold_skip_wait        # Settings.game.<policy>: True = accept, False = decline
      signature: {...}                    # what identifies the question
      dismiss: {...}                      # the ACCEPT button (确定, left)
      decline: {...}                      # the DECLINE button (取消, right)
      stop_message: "..."                 # why the task stops after declining

Accepting clicks ``dismiss`` and the run goes on; declining clicks ``decline`` and the run stops
(``TaskStopped``) with ``stop_message``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ngoalong.core.detection import Detection
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.locator import CanvasFix
from ngoalong.core.vision import Matcher

DEFAULT_MAX_ROUNDS = 3  # pop-ups closed one after another before the guard gives up on a frame
DEFAULT_SETTLE_S = 0.6  # wait after a dismiss click before capturing again (Flash closes the box)


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
    point: Point  # frame (client) point to click (the ACCEPT button for a policy pop-up)
    detections: tuple[Detection, ...]
    decline_point: Point | None = None  # policy pop-ups only


def _spot(raw: Mapping[str, Any], where: str) -> TemplateSpot:
    try:
        at = raw["at"]
        return TemplateSpot(
            template=str(raw["template"]),
            at=(int(at[0]), int(at[1])),
            margin=int(raw.get("margin", 20)),
            threshold=float(raw["threshold"]) if "threshold" in raw else None,
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise ValueError(f"popups.yaml: bad {where}: {raw!r} ({exc})") from exc


def parse_popups(data: Mapping[str, Any] | None) -> list[PopupDef]:
    defs: list[PopupDef] = []
    for name, raw in (data or {}).items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"popups.yaml: {name} must be a mapping")
        sig = _spot(raw.get("signature") or {}, f"{name}.signature")
        d = raw.get("dismiss") or {"click": "signature"}
        if not isinstance(d, Mapping):
            raise ValueError(f"popups.yaml: {name}.dismiss must be a mapping")
        dismiss = None if d.get("click") == "signature" else _spot(d, f"{name}.dismiss")
        policy = raw.get("policy")
        decline = _spot(raw["decline"], f"{name}.decline") if raw.get("decline") else None
        if policy is not None and (dismiss is None or decline is None):
            raise ValueError(f"popups.yaml: {name} has a policy, so it needs both dismiss (accept) and decline")
        defs.append(PopupDef(str(name), str(raw.get("description", name)), sig, dismiss,
                             decline, str(policy) if policy is not None else None,
                             str(raw.get("stop_message", ""))))
    return defs


class PopupGuard:
    """Finds known harmless pop-ups in a frame and says where to click to close them."""

    def __init__(self, popups: list[PopupDef], max_rounds: int = DEFAULT_MAX_ROUNDS,
                 settle_s: float = DEFAULT_SETTLE_S) -> None:
        self.popups = list(popups)
        self.max_rounds = max_rounds
        self.settle_s = settle_s

    @classmethod
    def load(cls, path: str | Path) -> PopupGuard:
        """Load ``popups.yaml``; a missing file means no pop-up is handled."""
        p = Path(path)
        if not p.is_file():
            return cls([])
        with p.open(encoding="utf-8") as f:
            return cls(parse_popups(yaml.safe_load(f)))

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
