"""Screen identity: rules over parts and probes, layered base / dialogs / overlays (docs/08
"Concepts" 2, "Screen identity").

``screens/*.yaml`` declares each screen::

    tower.dialog:
      layer: dialog            # base | dialog | overlay | hud
      z: 1                     # dialogs only: stacking order (1 main dialog, 2 side panel, 3 box)
      over: [city, harbour]    # optional: only over these screens (base, or dialogs of a lower z)
      require: [title.tower, [bar.dinh_phu, bar.quoc_chinh]]   # all of; a nested list = one of
      forbid: [qty.max]        # none of
      close: [802, 119]        # its ✕: claims the ``close`` hit within CLOSE_TOL px
      claims: [box.ok]         # other generic parts it explains
      stays: map               # base only: see below
      read: {...}              # readers (readers.py)

A rule item is a part name (found), a probe name (on), or ``{part: close, near: [x, y]}``.
Items are checked in order and stop at the first failure, so a screen's later (dearer) parts are
searched only when its first ones are there: put the cheapest, most telling part first.

Base screens are tried in file order (``base.yaml`` first, most specific first) and the first that
holds wins; when none does the base is ``unknown``. A base with ``stays: <other base>`` is held:
later frames that read as that other base (or as ``unknown``, a dialog over the HUD) are still
this one, until a frame reads as any other base. The caller passes ``Identity.held`` of the last
frame back in (the city of a young account is only told apart by a banner shown on entering it). Every dialog and overlay that holds is kept;
dialogs are ordered by ``z``. A ✕ that no screen claims becomes an ``unknown`` dialog, and the
unclaimed hits are listed so recovery can still close it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ngoalong.core.geometry import Point
from ngoalong.perception.parts import Hit

LAYERS = ("base", "dialog", "overlay", "hud")
UNKNOWN = "unknown"
CLOSE = "close"  # the generic ✕ part
GENERIC = ("close", "box.ok", "box.cancel")  # parts that must be explained by some screen
CLOSE_TOL = 15


@dataclass(frozen=True, slots=True)
class Near:
    part: str
    at: Point
    tol: int = CLOSE_TOL


Item = str | Near | tuple[str | Near, ...]  # a tuple is "one of"


@dataclass(frozen=True, slots=True)
class ScreenSpec:
    name: str
    layer: str
    require: tuple[Item, ...] = ()
    forbid: tuple[Item, ...] = ()
    over: tuple[str, ...] = ()  # (not 'on': YAML reads a bare on as true)
    z: int = 1
    close: Point | None = None
    claims: tuple[str, ...] = ()
    stays: str = ""  # base only: a later frame read as this base is still this screen
    read: dict[str, Any] = field(default_factory=dict)
    source: str = ""


@dataclass(frozen=True, slots=True)
class Identity:
    """The layered answer for one frame."""

    base: str
    dialogs: tuple[str, ...]
    overlays: tuple[str, ...]
    hud: bool
    unknown: tuple[Hit, ...]  # generic parts no screen claimed (✕ of an unknown dialog, ...)
    held: str | None = None  # a base with ``stays`` seen last: pass it to the next frame's identify


def _item(v: Any, where: str) -> Item:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return tuple(_item(x, where) for x in v)
    if isinstance(v, dict) and "near" in v:
        return Near(v.get("part", CLOSE), Point(*v["near"]), int(v.get("tol", CLOSE_TOL)))
    raise ValueError(f"{where}: rule item must be a name, a list or {{part, near}}, not {v!r}")


def _names(item: Item) -> list[str]:
    if isinstance(item, str):
        return [item]
    if isinstance(item, Near):
        return [item.part]
    return [n for x in item for n in _names(x)]


class ScreenBook:
    """Every screen rule of a profile, in priority order."""

    def __init__(self, specs: list[ScreenSpec]) -> None:
        self.specs = specs
        self.by_name = {s.name: s for s in specs}
        self.bases = [s for s in specs if s.layer == "base"]
        self.dialogs = sorted((s for s in specs if s.layer == "dialog"), key=lambda s: s.z)
        self.overlays = [s for s in specs if s.layer == "overlay"]
        self.huds = [s for s in specs if s.layer == "hud"]

    @classmethod
    def load(cls, directory: str | Path) -> ScreenBook:
        d = Path(directory)
        files = sorted(d.glob("*.yaml"), key=lambda p: (p.name != "base.yaml", p.name))
        specs: list[ScreenSpec] = []
        for path in files:
            raw: dict[str, dict[str, Any]] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for name, v in raw.items():
                where = f"{path.name}:{name}"
                if True in v:
                    raise ValueError(f"{where}: use 'over:', not 'on:' (YAML reads on as true)")
                layer = v.get("layer")
                if layer not in LAYERS:
                    raise ValueError(f"{where}: layer must be one of {LAYERS}")
                specs.append(ScreenSpec(
                    name=name, layer=layer,
                    require=tuple(_item(x, where) for x in v.get("require", [])),
                    forbid=tuple(_item(x, where) for x in v.get("forbid", [])),
                    over=tuple(v.get("over", [])), z=int(v.get("z", 1)),
                    close=Point(*v["close"]) if v.get("close") else None,
                    claims=tuple(v.get("claims", [])), stays=str(v.get("stays", "")),
                    read=dict(v.get("read", {})), source=path.name,
                ))
        names = [s.name for s in specs]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            raise ValueError(f"screens declared twice: {sorted(dup)}")
        bases = {s.name for s in specs if s.layer == "base"}
        bad = [f"{s.source}:{s.name}" for s in specs if s.stays and (s.layer != "base" or s.stays not in bases)]
        if bad:
            raise ValueError(f"stays: only a base screen can stay, over another base: {bad}")
        return cls(specs)

    def signals(self) -> set[str]:
        """Every part / probe name the rules use (to check them against the profile)."""
        return {n for s in self.specs for item in (*s.require, *s.forbid) for n in _names(item)} | set(GENERIC)

    # --- identification -------------------------------------------------------------------------
    def identify(self, on: Callable[[str], bool], hits: Callable[[str], tuple[Hit, ...]],
                 held: str | None = None) -> Identity:
        """``on(name)``: is the part found / the probe on; ``hits(name)``: a part's hits;
        ``held``: ``Identity.held`` of the last frame."""

        def holds(item: Item) -> bool:
            if isinstance(item, str):
                return on(item)
            if isinstance(item, Near):
                return any(h.near(item.at, item.tol) for h in hits(item.part))
            return any(holds(x) for x in item)

        def matches(s: ScreenSpec, stack: list[str]) -> bool:
            if s.over and not any(n in stack for n in s.over):
                return False
            return all(holds(i) for i in s.require) and not any(holds(i) for i in s.forbid)

        base = next((s.name for s in self.bases if matches(s, [])), UNKNOWN)
        kept = self.by_name.get(held) if held else None
        if base != UNKNOWN and self.by_name[base].stays:
            held = base
        elif kept is not None and base == kept.stays:
            base = kept.name
        elif base != UNKNOWN:
            held = None
        stack = [base]
        found: list[ScreenSpec] = []
        for s in self.dialogs:  # sorted by z: a side panel can ask for its main dialog
            if matches(s, stack):
                found.append(s)
                stack.append(s.name)
        overlays = [s for s in self.overlays if matches(s, stack)]
        hud = any(matches(s, stack) for s in self.huds)

        # explain the generic parts; whatever is left is an unknown dialog
        claimed: set[tuple[str, int, int]] = set()
        for s in (*found, *overlays):
            for item in s.require:
                if isinstance(item, Near):
                    for h in hits(item.part):
                        if h.near(item.at, item.tol):
                            claimed.add((h.part, h.rect.x, h.rect.y))
            if s.close is not None:
                for h in hits(CLOSE):
                    if h.near(s.close, CLOSE_TOL):
                        claimed.add((h.part, h.rect.x, h.rect.y))
            for part in s.claims:
                claimed.update((h.part, h.rect.x, h.rect.y) for h in hits(part))
        unknown = tuple(h for part in GENERIC for h in hits(part)
                        if (h.part, h.rect.x, h.rect.y) not in claimed)
        dialogs = [s.name for s in found]
        n_unknown_dialogs = sum(1 for h in unknown if h.part == CLOSE)
        if n_unknown_dialogs:  # above the main dialogs, under side panels and boxes
            at = sum(1 for s in found if s.z <= 1)
            dialogs[at:at] = [UNKNOWN] * n_unknown_dialogs
        return Identity(base, tuple(dialogs), tuple(s.name for s in overlays), hud, unknown, held)
