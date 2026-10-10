"""The perception benchmark (docs/08, phase R1 gate): every labelled frame of a corpus is perceived
and compared with its label. Used by ``tools/perceive.py --bench`` (the full local corpus) and by
``--selftest`` (the subset bundled into the build).

A corpus is a folder of 1000x600 canvas captures ``NNN-name.png`` (or ``.webp``) with a ``labels.yaml`` next to
them. Keys are "NNN-name.png" or a range "NNN..MMM"; a label gives the expected screen stack
(``base``, ``dialogs``, declared ``overlay`` entries, or ``expect: "a > b + c"``) and ``state``
values that a reader of the same name must return. Labels name out-of-scope screens by what they
are (tavern, hoat_dong_daily, ...): a base or dialog that no screen file declares is expected as
``unknown``, a HUD map that no feature uses as ``map``. Frames marked ``out_of_scope`` are skipped.

The gate: >= 98% of the feature frames stacked right, no ``unknown`` on a feature frame, every
compared reader value right, no OCR module loaded, parts <= 80 ms and a whole Snapshot <= 150 ms.
"""

from __future__ import annotations

import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ngoalong.core.capture import read_image
from ngoalong.perception.screens import UNKNOWN
from ngoalong.perception.snapshot import Perceiver, Snapshot

HUD_MAPS = {"region", "resources", "event_dungeon"}  # labelled HUD maps that no feature uses -> "map"
FEATURE_BASES = {"city", "harbour", "field", "world_map", "battle", "tower.floor", "vkt.lobby", "vkt.arena"}
GATE_STACK = 0.98
GATE_PARTS_MS = 80.0
GATE_TOTAL_MS = 150.0
FRAME_SUFFIXES = {".png", ".webp"}  # the build bundles its frames as lossless WebP (40% smaller)


# --- labels -------------------------------------------------------------------------------------------
def frame_number(key: str) -> list[int]:
    """Frame numbers a label key stands for: "123-name.png" -> [123], "120..125" -> 120..125."""
    m = re.fullmatch(r"(\d{3})\.\.(\d{3})", str(key))
    return list(range(int(m[1]), int(m[2]) + 1)) if m else [int(str(key)[:3])]


def load_labels(path: Path) -> dict[Path, dict[str, Any]]:
    """Label per frame file of the corpus folder ``path.parent`` (frames that are not there are left out)."""
    raw: dict[str, dict[str, Any]] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    files = {int(p.name[:3]): p for p in path.parent.glob("[0-9][0-9][0-9]-*.*") if p.suffix in FRAME_SUFFIXES}
    out: dict[Path, dict[str, Any]] = {}
    for key, v in raw.items():
        for n in frame_number(key):
            if n in files:
                out[files[n]] = v
    return out


def expected(label: dict[str, Any], p: Perceiver) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(stack, overlays) the label asks for, in this profile's screen names. ``expect: "a > b + c"``
    overrides base / dialogs / overlay where the human labels can't say it (e.g. a full-screen
    location with its own ✕ is an unknown base under an unknown dialog)."""
    names = p.book.by_name
    if label.get("expect"):
        stack, *over = [part.strip() for part in str(label["expect"]).split("+")]
        return tuple(n.strip() for n in stack.split(">")), tuple(over)
    base = label.get("base", UNKNOWN)
    base = base if base in names else ("map" if base in HUD_MAPS else UNKNOWN)
    dialogs = tuple(d if d in names else UNKNOWN for d in label.get("dialogs", []))
    overlays = tuple(o for o in label.get("overlay", []) if o in names)
    return (base, *dialogs), overlays


def is_feature_frame(label: dict[str, Any], p: Perceiver) -> bool:
    """A frame of one of the six features: a feature base and no unknown screen in the stack."""
    stack, _over = expected(label, p)
    return stack[0] in FEATURE_BASES and UNKNOWN not in stack


def same(want: Any, got: Any) -> bool | None:
    """Compare a label value with a reader value; None when the label value is not comparable
    (a range such as "20→11", a free-text note)."""
    if want is None:
        return got is None
    if isinstance(want, str) and ("→" in want or "->" in want):
        return None
    if isinstance(got, tuple) and len(got) == 2 and all(isinstance(x, int) for x in got):  # fraction
        if isinstance(want, int):
            return got[0] == want
        if isinstance(want, str) and "/" in want:
            return f"{got[0]}/{got[1]}" == want.replace(" ", "")
        return None
    if isinstance(want, list) and isinstance(got, list):  # floors: [141] or ["131-140"]
        flat: list[int] = []
        for w in want:
            m = re.fullmatch(r"(\d+)-(\d+)", str(w))
            flat.extend(range(int(m[1]), int(m[2]) + 1) if m else [int(w)])
        return flat == got
    if isinstance(got, (list, tuple)) and isinstance(want, int):  # circles: 3
        return len(got) == want
    if isinstance(want, bool) or isinstance(got, bool):
        return bool(want) == bool(got) if isinstance(want, bool) and isinstance(got, bool) else None
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return abs(want - got) < 1e-6
    if isinstance(want, str) and isinstance(got, str):
        return want == got
    if got is None:
        return False
    return None


def reader_for(snap: Snapshot, key: str) -> str | None:
    """The top-most screen on this Snapshot that has a reader called ``key``."""
    order = [*reversed(snap.dialogs), snap.base, *snap.overlays] + (["hud"] if snap.identity.hud else [])
    for n in order:
        if n != UNKNOWN and key in snap.book.by_name[n].read:
            return n
    return None


# --- the benchmark ------------------------------------------------------------------------------------
@dataclass
class Row:
    name: str
    feature: bool
    ok: bool
    ms_identify: float
    ms_all: float
    ms_parts: float
    want_stack: tuple[str, ...]
    want_over: tuple[str, ...]
    snap: Snapshot


@dataclass
class BenchResult:
    rows: list[Row] = field(default_factory=list)
    skipped: int = 0
    screens: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    readers: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    reader_fail: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    @property
    def feature_rows(self) -> list[Row]:
        return [r for r in self.rows if r.feature]

    @property
    def other_rows(self) -> list[Row]:
        return [r for r in self.rows if not r.feature]

    @staticmethod
    def accuracy(rows: list[Row]) -> float:
        return sum(r.ok for r in rows) / max(1, len(rows))

    @property
    def wrong_readers(self) -> int:
        return sum(c["wrong"] for c in self.readers.values())

    @property
    def ocr_modules(self) -> list[str]:
        return sorted(m for m in sys.modules if "rapidocr" in m)

    def max_ms(self, attr: str) -> float:
        return max((getattr(r, attr) for r in self.rows), default=0.0)

    def gate(self, timing: bool = True) -> bool:
        feat = self.feature_rows
        ok = (bool(feat) and self.accuracy(feat) >= GATE_STACK and not self.ocr_modules
              and not any(UNKNOWN in r.snap.stack for r in feat))
        if timing:
            ok = ok and self.max_ms("ms_parts") <= GATE_PARTS_MS and self.max_ms("ms_all") <= GATE_TOTAL_MS
        return ok


def run(p: Perceiver, labels_path: Path, frames: set[Path] | None = None,
        progress: Callable[[int, int], None] | None = None) -> BenchResult:
    """Perceive every labelled frame (or only ``frames``) and compare it with its label."""
    labels = load_labels(labels_path)
    res = BenchResult()
    items = sorted((path, label) for path, label in labels.items() if frames is None or path in frames)
    for i, (path, label) in enumerate(items):
        if progress is not None:
            progress(i, len(items))
        if label.get("out_of_scope"):
            res.skipped += 1
            continue
        canvas = read_image(path)
        t0 = time.perf_counter()
        snap = p.perceive(canvas)
        t_id = (time.perf_counter() - t0) * 1000
        snap.read_all()
        t_all = (time.perf_counter() - t0) * 1000
        want_stack, want_over = expected(label, p)
        ok = want_stack == snap.stack and set(want_over) == set(snap.overlays)
        feature = want_stack[0] in FEATURE_BASES and UNKNOWN not in want_stack
        res.rows.append(Row(path.name, feature, ok, t_id, t_all, snap.senses.scan.ms, want_stack, want_over, snap))
        want_set, got_set = Counter([*want_stack, *want_over]), Counter([*snap.stack, *snap.overlays])
        for n in set(want_set) | set(got_set):
            tp = min(want_set[n], got_set[n])
            res.screens[n]["tp"] += tp
            res.screens[n]["fp"] += got_set[n] - tp
            res.screens[n]["fn"] += want_set[n] - tp
        for key, want in (label.get("state") or {}).items():
            screen = reader_for(snap, key)
            if screen is None or not ok:
                continue
            match = same(want, snap.value(screen, key))
            if match is None:
                continue
            name = f"{screen}.{key}"
            res.readers[name]["ok" if match else "wrong"] += 1
            if not match:
                res.reader_fail[name].append(f"{path.name[:3]}: want {want!r} got {snap.value(screen, key)!r}")
    return res


def report(res: BenchResult, verbose: bool = False, per_screen: bool = True) -> list[str]:
    """The benchmark report, one line per string."""
    out: list[str] = []
    feat, other = res.feature_rows, res.other_rows
    acc = res.accuracy
    out.append(f"{len(res.rows)} labelled frames ({len(feat)} of the 6 features, {len(other)} other screens), "
               f"{res.skipped} out of scope skipped")
    out.append(f"screen stack correct: features {sum(r.ok for r in feat)}/{len(feat)} = {acc(feat):.1%}, "
               f"other {sum(r.ok for r in other)}/{len(other)} = {acc(other):.1%}")
    for r in res.rows:
        if not r.ok:
            want = " > ".join(r.want_stack) + (" + " + " + ".join(r.want_over) if r.want_over else "")
            out.append(f"  WRONG {r.name[:36]:36s} {'F' if r.feature else ' '} want {want}")
            out.append(f"{'':46s}got  {r.snap.describe(read=False)}")
    if per_screen:
        out.append("")
        out.append("per screen (all frames)          tp   fp   fn  precision  recall")
        for n, c in sorted(res.screens.items()):
            prec = c["tp"] / max(1, c["tp"] + c["fp"])
            rec = c["tp"] / max(1, c["tp"] + c["fn"])
            out.append(f"  {n:30s} {c['tp']:4d} {c['fp']:4d} {c['fn']:4d}  {prec:8.1%} {rec:7.1%}")
        out.append("")
        out.append("per reader (label state values on correctly stacked frames)")
        for n, c in sorted(res.readers.items()):
            out.append(f"  {n:34s} {c['ok']:4d} ok {c['wrong']:3d} wrong")
            for line in res.reader_fail[n][:None if verbose else 6]:
                out.append(f"      {line}")
    else:
        ok = sum(c["ok"] for c in res.readers.values())
        out.append(f"reader values: {ok} ok, {res.wrong_readers} wrong ({len(res.readers)} readers)")
        for n in sorted(res.reader_fail):
            for line in res.reader_fail[n]:
                out.append(f"  WRONG {n}: {line}")
    if res.rows:
        ms_parts = [r.ms_parts for r in res.rows]
        ms_id = [r.ms_identify for r in res.rows]
        ms_all = [r.ms_all for r in res.rows]
        q = lambda xs, f: sorted(xs)[min(len(xs) - 1, int(len(xs) * f))]  # noqa: E731
        out.append(f"timing per Snapshot: parts mean {statistics.mean(ms_parts):.1f} ms, p95 {q(ms_parts, 0.95):.1f}, "
                   f"max {max(ms_parts):.1f}; identify mean {statistics.mean(ms_id):.1f} ms; "
                   f"with every reader mean {statistics.mean(ms_all):.1f} ms, p95 {q(ms_all, 0.95):.1f}, "
                   f"max {max(ms_all):.1f}")
    out.append(f"OCR modules loaded: {res.ocr_modules or 'none'}")
    return out
