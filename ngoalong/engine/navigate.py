"""Navigation over the screen graph (docs/08 "Concepts" 5): ``Navigator.step(snap, "tower.dialog")``
gives the next action that brings the game closer to a screen, from whatever the Snapshot shows.

``profiles/<profile>/nav.yaml``::

    edges:
      city:                                   # from this screen (it must be the top of the stack) ...
        chest.wheel: {click: icon.tinh_tu, reveal: hud.icons_hidden}   # ... to this one: click a part
        city.building_panel: {at: [460, 320], label: "toà Tháp"}      # a fixed map spot
      confirm.ok_cancel:
        tower.dialog: {click: box.ok, over: tower.floor}             # only when tower.floor is under it
      tower.dialog:
        city: {close: true}                   # closing it shows this screen
    close:                                    # dialogs closed by something other than their ✕
      confirm.ok_cancel: {click: box.cancel}

An edge is ``click`` (a part, found on the frame), ``at`` (a canvas point) or ``key: esc``;
``reveal`` names a part to click first when the ``click`` part is not shown (the hidden top icon
rows); ``over`` limits it to stacks holding that screen; ``within`` is how long the next screen may
take (first opens show a loading bar). A dialog on top that is not on the way is closed: its
``close`` entry, else its ✕ (declared ``close`` point, or the ✕ of an unknown dialog), else ESC.

The plan: for the top of the stack and every screen under it, the cost is the dialogs to close
plus the edges of the shortest path (breadth-first) from there; the cheapest wins. Only the first
edge from the top is checked against the frame (its part must be shown); later edges are assumed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ngoalong.core.geometry import Point
from ngoalong.core.input import VK_ESCAPE
from ngoalong.engine.actions import Action, Click, Key
from ngoalong.engine.actor import close_point
from ngoalong.perception import Snapshot

DEFAULT_WITHIN_S = 8.0
CLOSE_WITHIN_S = 3.0
KEYS = {"esc": VK_ESCAPE}


@dataclass(frozen=True, slots=True)
class Edge:
    src: str
    dst: str
    click: str | None = None  # part to click
    at: Point | None = None  # or a canvas point
    key: str | None = None  # or a key
    close: bool = False  # or close the top dialog
    reveal: str | None = None  # part to click first when ``click`` is not shown
    over: str | None = None  # only when this screen is in the stack
    within: float = DEFAULT_WITHIN_S
    label: str = ""

    @property
    def name(self) -> str:
        return self.label or self.click or self.key or ("close" if self.close else "spot")


def _edge(src: str, dst: str, v: dict[str, Any], where: str) -> Edge:
    unknown = set(v) - {"click", "at", "key", "close", "reveal", "over", "within", "label"}
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
    if sum(k in v for k in ("click", "at", "key", "close")) != 1:
        raise ValueError(f"{where}: give exactly one of click / at / key / close")
    if "key" in v and v["key"] not in KEYS:
        raise ValueError(f"{where}: key must be one of {sorted(KEYS)}")
    return Edge(src, dst, v.get("click"), Point(*v["at"]) if "at" in v else None, v.get("key"),
                bool(v.get("close", False)), v.get("reveal"), v.get("over"),
                float(v.get("within", DEFAULT_WITHIN_S)), str(v.get("label", "")))


class Navigator:
    def __init__(self, edges: list[Edge], closers: dict[str, Edge]) -> None:
        self.edges = edges
        self.closers = closers
        self.out: dict[str, list[Edge]] = {}
        for e in edges:
            self.out.setdefault(e.src, []).append(e)

    @classmethod
    def load(cls, path: str | Path) -> Navigator:
        p = Path(path)
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {} if p.is_file() else {}
        edges = [_edge(src, dst, v or {}, f"{p.name}: {src} -> {dst}")
                 for src, dsts in (raw.get("edges") or {}).items() for dst, v in (dsts or {}).items()]
        closers = {name: _edge(name, "", v, f"{p.name}: close {name}") for name, v in (raw.get("close") or {}).items()}
        return cls(edges, closers)

    def check(self, screens: set[str], parts: set[str]) -> list[str]:
        """Names in nav.yaml that the profile does not know (empty when consistent)."""
        bad = []
        for e in [*self.edges, *self.closers.values()]:
            for n in (e.src, e.dst, e.over):
                if n and n not in screens:
                    bad.append(f"screen {n}")
            for n in (e.click, e.reveal):
                if n and n not in parts:
                    bad.append(f"part {n}")
        return sorted(set(bad))

    # --- planning -------------------------------------------------------------------------------
    def _usable(self, e: Edge, snap: Snapshot) -> bool:
        if e.over and e.over not in snap.stack:
            return False
        if e.click and not snap.parts(e.click):
            return bool(e.reveal and snap.parts(e.reveal))
        return True

    def _path(self, start: str, target: str, snap: Snapshot | None) -> list[Edge] | None:
        """Shortest edge path; with ``snap`` the first edge must be usable on that frame."""
        seen = {start}
        todo: deque[tuple[str, list[Edge]]] = deque([(start, [])])
        while todo:
            node, path = todo.popleft()
            for e in self.out.get(node, []):
                if e.dst in seen or (not path and snap is not None and not self._usable(e, snap)):
                    continue
                if e.dst == target:
                    return [*path, e]
                seen.add(e.dst)
                todo.append((e.dst, [*path, e]))
        return None

    def plan(self, snap: Snapshot, target: str) -> tuple[int, list[Edge]] | None:
        """(dialogs to close first, edges after that), cheapest first; None when there is no way."""
        stack = snap.stack
        best: tuple[int, list[Edge]] | None = None
        for i in range(len(stack) - 1, -1, -1):
            closes = len(stack) - 1 - i
            if stack[i] == target:
                path: list[Edge] | None = []
            else:
                path = self._path(stack[i], target, snap if closes == 0 else None)
            if path is not None and (best is None or closes + len(path) < best[0] + len(best[1])):
                best = (closes, path)
        return best

    def step(self, snap: Snapshot, target: str) -> Action | None:
        """The next action towards ``target``; None when it is on top already or there is no way."""
        if snap.top == target:
            return None
        plan = self.plan(snap, target)
        if plan is None:
            return None
        closes, path = plan
        if closes:
            return self.close(snap)
        return self._act(path[0], snap)

    def _act(self, e: Edge, snap: Snapshot) -> Action:
        before = snap.stack
        moved = lambda s: s.stack != before  # noqa: E731 - the next screen, or a box in between
        label = f"{e.src} → {e.dst}: {e.name}"
        if e.close:
            return self.close(snap)
        if e.click:
            hit = snap.part(e.click)
            if hit is None:  # usable, so the reveal part is there
                part = e.reveal or ""
                return Click(snap.point(part), label=f"{label} (hiện {part})", within=CLOSE_WITHIN_S,  # type: ignore[arg-type]
                             expect=lambda s, n=e.click: bool(s.parts(n)))
            return Click(hit.center, label=label, expect=moved, within=e.within)
        if e.at is not None:
            return Click(e.at, label=label, expect=moved, within=e.within)
        return Key(KEYS[e.key or "esc"], label=label, expect=moved, within=e.within)

    def close(self, snap: Snapshot) -> Action:
        """Close the top dialog: its ``close`` entry, else its ✕, else ESC."""
        top = snap.top
        before = snap.stack
        moved = lambda s: s.stack != before  # noqa: E731
        e = self.closers.get(top)
        if e is not None and self._usable(e, snap):
            p = snap.point(e.click) if e.click else e.at
            if p is not None:
                return Click(p, label=f"đóng {top}: {e.name}", expect=moved, within=CLOSE_WITHIN_S)
            if e.key:
                return Key(KEYS[e.key], label=f"đóng {top}: {e.key}", expect=moved, within=CLOSE_WITHIN_S)
        p = close_point(snap)
        if p is not None:
            return Click(p, label=f"đóng {top}: ✕", expect=moved, within=CLOSE_WITHIN_S)
        return Key(VK_ESCAPE, label=f"đóng {top}: esc", expect=moved, within=CLOSE_WITHIN_S)
