"""Readers: typed state values per screen, read lazily and cached per frame (docs/08 "Concepts" 3,
"Screen state"). No OCR: values come from parts, colour probes and the ``digits`` font reader.

A screen's ``read:`` block in ``screens/*.yaml`` maps a key to one reader::

    tab:      {tabs: [Tháp, Luyện, Điểm tướng]}   # the gold tab of the medium-dialog tab row
    page:     {digits: tower.page}                # numbers.yaml field (None when unreadable)
    auto:     {present: tower.auto_tick}          # part found / probe on -> bool
    hp:       {value: vkt.boss_hp}                # a probe's raw value (mean, count, fill)
    climb:    {point: btn.vuot_thap}              # centre of a part (or the first of a list) -> Point | None
    circles:  {find_all: vkt.circle}              # centres of every hit -> tuple[Point, ...]
    beams:    {blobs: field.beam}                 # rectangles of a blobs probe
    result:   {first_of: {win: result.win, lose: result.lose}}   # first key whose part is there
    rows:     {grid: {at: [x, y, w, h], dy: 25, n: 10, filled: row.filled, selected: row.selected,
                      number: [dx, dy, w, h], page: page, per_page: 10}}   # -> tuple[Slot, ...]
    floors:   {from: rows, pick: numbers}         # numbers | selected_number | selected_index | count
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from ngoalong.core.digits import GlyphSet
from ngoalong.core.geometry import Point, Rect
from ngoalong.perception.parts import Hit, PartLibrary, PartScan
from ngoalong.perception.probes import ProbeResult, ProbeSpec, evaluate
from ngoalong.perception.screens import ScreenSpec

# The tab row of the medium dialog frame (Tháp Thí Luyện, Phủ Sách Lược, Trang bị, ...): tab i is
# at x = 190 + 77 i; the rect is the tab's face without its frame (docs/08 "Measured on the 71 frames").
TAB_ROW = (190, 148, 60, 18)
TAB_DX = 77
TAB_PROBE = "tab.selected"


@dataclass(frozen=True, slots=True)
class NumberField:
    name: str
    rect: Rect
    when: str
    parse: str


@dataclass(frozen=True, slots=True)
class Slot:
    """One cell of a list or grid reader."""

    index: int
    rect: Rect
    selected: bool
    number: int | None = None

    @property
    def center(self) -> Point:
        return self.rect.center

    def __repr__(self) -> str:
        n = "" if self.number is None else f" {self.number}"
        return f"Slot({self.index}{n}{' *' if self.selected else ''})"


class Senses:
    """Everything a rule or reader may look at on one canvas: parts, probes, digit fields.
    Part searches and probes without an explicit rect are cached."""

    def __init__(self, canvas: np.ndarray, parts: PartLibrary, probes: dict[str, ProbeSpec],
                 numbers: dict[str, NumberField], glyphs: GlyphSet) -> None:
        self.canvas = canvas
        self.scan = PartScan(parts, canvas)
        self.probes = probes
        self.numbers = numbers
        self.glyphs = glyphs
        self._probe: dict[str, ProbeResult] = {}
        self._digits: dict[str, Any] = {}
        self.probe_ms = 0.0
        self.digit_ms = 0.0

    def is_part(self, name: str) -> bool:
        return name in self.scan.lib.specs

    def hits(self, name: str) -> tuple[Hit, ...]:
        return self.scan.find(name)

    def probe(self, name: str, rect: Rect | None = None) -> ProbeResult:
        if rect is None and name in self._probe:
            return self._probe[name]
        t0 = time.perf_counter()
        res = evaluate(self.probes[name], self.canvas, rect)
        self.probe_ms += (time.perf_counter() - t0) * 1000
        if rect is None:
            self._probe[name] = res
        return res

    def on(self, name: str) -> bool:
        """A part is found, or a probe is on."""
        if self.is_part(name):
            return bool(self.scan.find(name))
        if name in self.probes:
            return self.probe(name).on
        raise KeyError(f"{name!r} is neither a part (parts.yaml) nor a probe (probes.yaml)")

    def read_digits(self, rect: Rect, parse: str) -> Any:
        t0 = time.perf_counter()
        r = self.glyphs.read(self.canvas[rect.y:rect.bottom, rect.x:rect.right])
        self.digit_ms += (time.perf_counter() - t0) * 1000
        return r.parse(parse)

    def digits(self, field: str) -> Any:
        """A numbers.yaml field, or None when its ``when`` part is missing or the read is rejected."""
        if field not in self._digits:
            f = self.numbers[field]
            self._digits[field] = self.read_digits(f.rect, f.parse) if self.on(f.when) else None
        return self._digits[field]

    @property
    def ms(self) -> float:
        return self.scan.ms + self.probe_ms + self.digit_ms


def _grid(spec: dict[str, Any], state: ScreenState) -> tuple[Slot, ...]:
    s = state.senses
    x0, y0, w, h = spec["at"]
    dx, dy = float(spec.get("dx", 0)), float(spec.get("dy", 0))
    cols = int(spec.get("cols", 1))
    page = state.get(spec["page"]) if spec.get("page") else None
    per_page = int(spec.get("per_page", spec["n"]))
    out = []
    for i in range(int(spec["n"])):
        r = Rect(round(x0 + dx * (i % cols)), round(y0 + dy * (i // cols)), w, h)
        if spec.get("filled") and not s.probe(spec["filled"], r).on:
            continue
        sel = bool(spec.get("selected")) and s.probe(spec["selected"], r).on
        number = None
        if spec.get("number"):
            nx, ny, nw, nh = spec["number"]
            number = s.read_digits(Rect(r.x + nx, r.y + ny, nw, nh), spec.get("parse", "last_int"))
            if number is None and isinstance(page, int):  # e.g. a highlighted row: its glow touches the text
                number = per_page * (page - 1) + 1 + i
        out.append(Slot(i, r, sel, number))
    return tuple(out)


def _pick(rows: tuple[Slot, ...], pick: str) -> Any:
    if pick == "numbers":
        return [r.number for r in rows]
    if pick == "count":
        return len(rows)
    sel = next((r for r in rows if r.selected), None)
    if pick == "selected_number":
        return sel.number if sel else None
    if pick == "selected_index":
        return sel.index if sel else None
    raise ValueError(f"unknown pick {pick!r}")


def read(spec: dict[str, Any], state: ScreenState) -> Any:
    """Evaluate one reader spec (one key of a screen's ``read:``)."""
    s = state.senses
    (kind, arg), = ((k, v) for k, v in spec.items() if k not in ("pick", "at", "dx"))
    if kind == "tabs":
        x, y, w, h = spec.get("at", TAB_ROW)
        dx = int(spec.get("dx", TAB_DX))
        lit = [name for i, name in enumerate(arg) if s.probe(TAB_PROBE, Rect(x + dx * i, y, w, h)).on]
        return lit[0] if len(lit) == 1 else None
    if kind in ("present", "enabled"):
        return s.on(arg)
    if kind == "value":
        return s.probe(arg).value
    if kind == "digits":
        return s.digits(arg)
    if kind == "point":
        for name in [arg] if isinstance(arg, str) else arg:
            hs = s.hits(name)
            if hs:
                return hs[0].center
        return None
    if kind == "find_all":
        return tuple(sorted((h.center for h in s.hits(arg)), key=lambda p: (p.y, p.x)))
    if kind == "count":
        return len(s.hits(arg))
    if kind == "blobs":
        return s.probe(arg).value
    if kind == "first_of":
        return next((k for k, name in arg.items() if s.on(name)), None)
    if kind == "grid":
        return _grid(arg, state)
    if kind == "from":
        return _pick(state.get(arg), spec["pick"])
    raise ValueError(f"unknown reader kind {kind!r}")


class ScreenState:
    """The readers of one screen on one frame; each value is read on first use and cached.

    ``state.tab``, ``state["page"]`` or ``state.get("page")``; ``values()`` reads them all.
    """

    def __init__(self, screen: ScreenSpec, senses: Senses) -> None:
        self.screen = screen
        self.senses = senses
        self._cache: dict[str, Any] = {}

    def keys(self) -> list[str]:
        return list(self.screen.read)

    def get(self, key: str) -> Any:
        if key not in self._cache:
            if key not in self.screen.read:
                raise KeyError(f"{self.screen.name} has no reader {key!r} (has {self.keys()})")
            self._cache[key] = read(self.screen.read[key], self)
        return self._cache[key]

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __getattr__(self, key: str) -> Any:
        if key.startswith("_") or key in ("screen", "senses"):
            raise AttributeError(key)
        try:
            return self.get(key)
        except KeyError as exc:
            raise AttributeError(str(exc)) from exc

    def values(self) -> dict[str, Any]:
        return {k: self.get(k) for k in self.keys()}
