"""Print and draw the perception Snapshot of screenshots or of the live game; benchmark it against
the labelled corpus (docs/08, phase R1, "Phases and gates").

    python tools/perceive.py corpus/live/234-tower-page-typed-3.png      # one frame (or a folder, or a glob)
    python tools/perceive.py corpus/live --draw build/perceive           # also write annotated copies
    python tools/perceive.py --live                                      # the game window, now
    python tools/perceive.py --live --save tower-dialog                  # ... and keep the frame in corpus/live
    python tools/perceive.py --bench                                     # compare with corpus/live/labels.yaml

The benchmark (``ngoalong/perception/bench.py``, also run by ``--selftest`` on the corpus bundled
into the build) compares each labelled frame's screen stack and the ``state`` values that a reader
of the same name exists for. It reports per-screen precision / recall, per-reader accuracy,
milliseconds per Snapshot and whether an OCR module was loaded, and exits 1 when the R1 gate is not
met (or when a reader value is wrong).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from ngoalong.core.capture import read_image, save_png  # noqa: E402
from ngoalong.perception import Perceiver, Snapshot  # noqa: E402
from ngoalong.perception import bench as benchmark  # noqa: E402

CORPUS = ROOT / "corpus" / "live"
LABELS = CORPUS / "labels.yaml"


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


def bench(p: Perceiver, verbose: bool) -> int:
    res = benchmark.run(p, LABELS)
    for line in benchmark.report(res, verbose):
        print(line)
    gate = res.gate() and res.wrong_readers == 0
    print(f"\nR1 gate (>= {benchmark.GATE_STACK:.0%} feature stacks, 0 OCR, parts <= {benchmark.GATE_PARTS_MS:.0f} ms, "
          f"Snapshot <= {benchmark.GATE_TOTAL_MS:.0f} ms, no unknown on a feature frame, no wrong reader value): "
          f"{'PASS' if gate else 'FAIL'}")
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
