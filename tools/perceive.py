"""Print and draw the perception Snapshot of screenshots or of the live game; benchmark it against
the labelled corpus (docs/08, phase R1, "Phases and gates").

    python tools/perceive.py corpus/live/234-tower-page-typed-3.png      # one frame (or a folder, or a glob)
    python tools/perceive.py corpus/live --draw build/perceive           # also write annotated copies
    python tools/perceive.py --live                                      # the game window, now
    python tools/perceive.py --live --save tower-dialog                  # ... and keep the frame in corpus/live
    python tools/perceive.py --bench                                     # compare with corpus/live/labels.yaml

The benchmark compares each labelled frame's screen stack (``base``, ``dialogs``, declared
``overlay`` entries) and the ``state`` values that a reader of the same name exists for. Labels
name out-of-scope screens by what they are (tavern, hoat_dong_daily, ...); a base or dialog that no
screen file declares is expected as ``unknown``, a map with the HUD as ``map``. Frames marked
``out_of_scope`` are skipped. It reports per-screen precision / recall, per-reader accuracy,
milliseconds per Snapshot and OCR calls, and exits 1 when the R1 gate is not met.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import yaml  # noqa: E402

from ngoalong.core import ocr  # noqa: E402
from ngoalong.core.capture import read_image, save_png  # noqa: E402
from ngoalong.perception import Perceiver, Snapshot  # noqa: E402
from ngoalong.perception.screens import UNKNOWN  # noqa: E402

CORPUS = ROOT / "corpus" / "live"
LABELS = CORPUS / "labels.yaml"
HUD_MAPS = {"region", "resources", "event_dungeon"}  # labelled HUD maps that no feature uses -> "map"
FEATURE_BASES = {"city", "harbour", "field", "world_map", "battle", "tower.floor", "vkt.lobby", "vkt.arena"}
GATE_STACK = 0.98
GATE_PARTS_MS = 80.0
GATE_TOTAL_MS = 150.0


# --- OCR call counter (the gate is zero) ------------------------------------------------------------
OCR_CALLS = Counter()


def _count_ocr() -> None:
    def wrap(name: str, fn: Any) -> Any:
        def counted(*a: Any, **k: Any) -> Any:
            OCR_CALLS[name] += 1
            return fn(*a, **k)
        return counted

    ocr.RapidOcr.read = wrap("RapidOcr.read", ocr.RapidOcr.read)  # type: ignore[method-assign]
    ocr.default_ocr = wrap("default_ocr", ocr.default_ocr)  # type: ignore[assignment]


# --- drawing ------------------------------------------------------------------------------------------
def draw(snap: Snapshot, canvas: np.ndarray) -> np.ndarray:
    """The canvas with every searched part (green found), the unclaimed parts (red), the probes
    that have a rect (blue on, grey off) and the stack line on top."""
    img = canvas.copy()
    snap.read_all()
    for name, hits in snap.senses.scan.searched().items():
        for h in hits:
            r = h.rect
            cv2.rectangle(img, (r.x, r.y), (r.right, r.bottom), (0, 220, 0), 1)
            cv2.putText(img, name, (r.x, max(8, r.y - 2)), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (0, 255, 0), 1)
    for name, res in snap.senses._probe.items():
        r = snap.senses.probes[name].rect
        if r is None:
            continue
        colour = (255, 160, 0) if res.on else (140, 140, 140)
        cv2.rectangle(img, (r.x, r.y), (r.right, r.bottom), colour, 1)
        cv2.putText(img, str(res), (r.x, r.bottom + 9), cv2.FONT_HERSHEY_SIMPLEX, 0.3, colour, 1)
    for h in snap.unknown:
        r = h.rect
        cv2.rectangle(img, (r.x - 2, r.y - 2), (r.right + 2, r.bottom + 2), (0, 0, 255), 2)
    line = snap.describe(read=False)
    cv2.rectangle(img, (0, 0), (1000, 14), (0, 0, 0), -1)
    cv2.putText(img, line[:160], (3, 10), cv2.FONT_HERSHEY_SIMPLEX, 0.33, (0, 255, 255), 1)
    return img


# --- labels -------------------------------------------------------------------------------------------
def load_labels(path: Path = LABELS) -> dict[Path, dict[str, Any]]:
    """Label per frame file; keys are "NNN-name.png" or a range "NNN..MMM"."""
    raw: dict[str, dict[str, Any]] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    files = {int(p.name[:3]): p for p in path.parent.glob("[0-9][0-9][0-9]-*.png")}
    out: dict[Path, dict[str, Any]] = {}
    for key, v in raw.items():
        m = re.fullmatch(r"(\d{3})\.\.(\d{3})", str(key))
        nums = range(int(m[1]), int(m[2]) + 1) if m else [int(str(key)[:3])]
        for n in nums:
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


def _same(want: Any, got: Any) -> bool | None:
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


def _reader_for(snap: Snapshot, key: str) -> str | None:
    """The top-most screen on this Snapshot that has a reader called ``key``."""
    order = [*reversed(snap.dialogs), snap.base, *snap.overlays] + (["hud"] if snap.identity.hud else [])
    for n in order:
        if n != UNKNOWN and key in snap.book.by_name[n].read:
            return n
    return None


def bench(p: Perceiver, verbose: bool) -> int:
    labels = load_labels()
    rows = []
    screen_stats: dict[str, Counter] = defaultdict(Counter)
    reader_stats: dict[str, Counter] = defaultdict(Counter)
    reader_fail: dict[str, list[str]] = defaultdict(list)
    skipped = 0
    for path, label in sorted(labels.items()):
        if label.get("out_of_scope"):
            skipped += 1
            continue
        canvas = read_image(path)
        t0 = time.perf_counter()
        snap = p.perceive(canvas)
        t_id = (time.perf_counter() - t0) * 1000
        snap.read_all()
        t_all = (time.perf_counter() - t0) * 1000
        want_stack, want_over = expected(label, p)
        got_stack, got_over = snap.stack, snap.overlays
        ok = want_stack == got_stack and set(want_over) == set(got_over)
        feature = want_stack[0] in FEATURE_BASES and UNKNOWN not in want_stack
        rows.append((path.name, feature, ok, t_id, t_all, snap.senses.scan.ms, want_stack, want_over, snap))
        want_set, got_set = Counter([*want_stack, *want_over]), Counter([*got_stack, *got_over])
        for n in set(want_set) | set(got_set):
            tp = min(want_set[n], got_set[n])
            screen_stats[n]["tp"] += tp
            screen_stats[n]["fp"] += got_set[n] - tp
            screen_stats[n]["fn"] += want_set[n] - tp
        for key, want in (label.get("state") or {}).items():
            screen = _reader_for(snap, key)
            if screen is None or not ok:
                continue
            got = snap.value(screen, key)
            same = _same(want, got)
            if same is None:
                continue
            name = f"{screen}.{key}"
            reader_stats[name]["ok" if same else "wrong"] += 1
            if not same:
                reader_fail[name].append(f"{path.name[:3]}: want {want!r} got {got!r}")

    feat = [r for r in rows if r[1]]
    other = [r for r in rows if not r[1]]
    acc = lambda rs: sum(r[2] for r in rs) / max(1, len(rs))  # noqa: E731
    print(f"{len(rows)} labelled frames ({len(feat)} of the 6 features, {len(other)} other screens), "
          f"{skipped} out of scope skipped")
    print(f"screen stack correct: features {sum(r[2] for r in feat)}/{len(feat)} = {acc(feat):.1%}, "
          f"other {sum(r[2] for r in other)}/{len(other)} = {acc(other):.1%}")
    wrong = [r for r in rows if not r[2]]
    for name, feature, _ok, *_t, want_stack, want_over, snap in wrong:
        want = " > ".join(want_stack) + (" + " + " + ".join(want_over) if want_over else "")
        print(f"  WRONG {name[:36]:36s} {'F' if feature else ' '} want {want}\n{'':46s}got  {snap.describe(read=False)}")

    print("\nper screen (all frames)          tp   fp   fn  precision  recall")
    for n, c in sorted(screen_stats.items()):
        prec = c["tp"] / max(1, c["tp"] + c["fp"])
        rec = c["tp"] / max(1, c["tp"] + c["fn"])
        print(f"  {n:30s} {c['tp']:4d} {c['fp']:4d} {c['fn']:4d}  {prec:8.1%} {rec:7.1%}")

    print("\nper reader (label state values on correctly stacked frames)")
    for n, c in sorted(reader_stats.items()):
        print(f"  {n:34s} {c['ok']:4d} ok {c['wrong']:3d} wrong")
        for line in reader_fail[n][:6 if not verbose else None]:
            print(f"      {line}")

    ms_parts = [r[5] for r in rows]
    ms_id = [r[3] for r in rows]
    ms_all = [r[4] for r in rows]
    q = lambda xs, f: sorted(xs)[min(len(xs) - 1, int(len(xs) * f))]  # noqa: E731
    print(f"\ntiming per Snapshot: parts mean {statistics.mean(ms_parts):.1f} ms, p95 {q(ms_parts, 0.95):.1f}, "
          f"max {max(ms_parts):.1f}; identify mean {statistics.mean(ms_id):.1f} ms; "
          f"with every reader mean {statistics.mean(ms_all):.1f} ms, p95 {q(ms_all, 0.95):.1f}, max {max(ms_all):.1f}")
    ocr_calls = sum(OCR_CALLS.values())
    loaded = sorted(m for m in sys.modules if "rapidocr" in m)
    print(f"OCR calls: {ocr_calls}{' ' + str(dict(OCR_CALLS)) if ocr_calls else ''}; OCR modules loaded: {loaded or 'none'}")

    gate = (acc(feat) >= GATE_STACK and ocr_calls == 0 and max(ms_parts) <= GATE_PARTS_MS
            and max(ms_all) <= GATE_TOTAL_MS
            and not any(UNKNOWN in r[8].stack for r in feat))
    print(f"\nR1 gate (>= {GATE_STACK:.0%} feature stacks, 0 OCR, parts <= {GATE_PARTS_MS:.0f} ms, "
          f"Snapshot <= {GATE_TOTAL_MS:.0f} ms, no unknown on a feature frame): {'PASS' if gate else 'FAIL'}")
    return 0 if gate else 1


# --- printing frames ----------------------------------------------------------------------------------
def show(p: Perceiver, canvas: np.ndarray, title: str, draw_dir: Path | None) -> None:
    t0 = time.perf_counter()
    snap = p.perceive(canvas)
    line = snap.describe()
    ms = (time.perf_counter() - t0) * 1000
    print(f"{title}: {line}  [{ms:.0f} ms, parts {snap.ms_parts:.0f} ms]")
    if draw_dir is not None:
        out = save_png(draw_dir / f"{Path(title).stem}.png", draw(snap, canvas))
        print(f"    drawn: {out}")


def expand(sources: list[str]) -> list[Path]:
    out: list[Path] = []
    for s in sources:
        p = Path(s)
        if p.is_dir():
            out.extend(sorted(p.glob("*.png")))
        elif any(c in s for c in "*?["):
            out.extend(sorted(Path().glob(s)))
        else:
            out.append(p)
    return out


def live(p: Perceiver, save: str | None, draw_dir: Path | None, window: int) -> int:
    from ngoalong.core.locator import CanvasLocator
    from ngoalong.tools.explore import DEFAULT_OUT, _capture, _next_path, _window

    gw = _window(window)
    frame, fix = _capture(gw, CanvasLocator.from_profile())
    if not fix.reliable:
        print(f"canvas not found reliably ({fix.confidence.value}); nothing perceived")
        return 2
    canvas = p.canvas(frame, fix)
    title = "live"
    if save:
        path = save_png(_next_path(DEFAULT_OUT, save), canvas)
        title = path.name
    show(p, canvas, title, draw_dir)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sources", nargs="*", help="PNG files, folders or globs (canvas captures, 1000x600)")
    ap.add_argument("--live", action="store_true", help="capture the game window")
    ap.add_argument("--save", metavar="NAME", help="with --live: save the canvas as corpus/live/NNN-NAME.png")
    ap.add_argument("--window", type=int, default=0, help="index of the game window when several are open")
    ap.add_argument("--draw", type=Path, metavar="DIR", help="write annotated copies into DIR")
    ap.add_argument("--bench", action="store_true", help="benchmark against corpus/live/labels.yaml")
    ap.add_argument("-v", "--verbose", action="store_true", help="list every wrong reader value")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    _count_ocr()
    p = Perceiver.load()
    if args.bench:
        return bench(p, args.verbose)
    if args.live:
        return live(p, args.save, args.draw, args.window)
    if not args.sources:
        ap.error("give images, --live or --bench")
    for path in expand(args.sources):
        img = read_image(path)
        if img.shape[:2] != (600, 1000):
            print(f"{path.name}: not a 1000x600 canvas capture ({img.shape[1]}x{img.shape[0]}), skipped")
            continue
        show(p, img, path.name, args.draw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
