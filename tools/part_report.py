"""Score every UI-part template over the live corpus (docs/08, phase R1).

For each part in ``parts.yaml``: its best match score inside the part's ``search`` region on every
canvas capture in ``corpus/live/`` (``--canvas`` searches the whole canvas instead), then how the
frames split into present (>= the part's own ``threshold``, or --hi), absent (< --lo, at most
threshold - 0.1) and ambiguous (between). A good part has no ambiguous frames and a wide gap
between the two groups.

    python tools/part_report.py                       # all parts in parts.yaml
    python tools/part_report.py --part close          # one part, list every frame
    python tools/part_report.py --csv build/parts.csv # also write all scores
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from ngoalong.core.capture import read_image  # noqa: E402
from ngoalong.core.templates import TemplateStore  # noqa: E402
from ngoalong.core.vision import match_template  # noqa: E402

PARTS = ROOT / "profiles" / "ngoalongviet" / "parts.yaml"
TEMPLATES = ROOT / "profiles" / "ngoalongviet" / "templates"
CORPUS = ROOT / "corpus" / "live"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--part", action="append", help="part name(s); default: all in parts.yaml")
    ap.add_argument("--hi", type=float, default=None, help="score counted as present (default: the part's threshold)")
    ap.add_argument("--lo", type=float, default=0.75, help="score counted as absent")
    ap.add_argument("--csv", type=Path, help="write frame x part scores")
    ap.add_argument("--canvas", action="store_true", help="ignore search regions")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    parts = yaml.safe_load(PARTS.read_text(encoding="utf-8"))
    names = args.part or list(parts)
    store = TemplateStore(TEMPLATES)
    frames = sorted(CORPUS.glob("[0-9][0-9][0-9]-*.png"))
    images = [read_image(p) for p in frames]

    rows: dict[str, list[float]] = {}
    t0 = time.perf_counter()
    for name in names:
        spec = parts[name]
        t = store.get(spec["template"])
        x, y, w, h = (0, 0, 1000, 600) if args.canvas or spec.get("search", "canvas") == "canvas" else spec["search"]
        rows[name] = [(match_template(img[y:y + h, x:x + w], t.image, t.mask) or (-1.0, (0, 0)))[0]
                      for img in images]
    ms = (time.perf_counter() - t0) * 1000 / max(1, len(names) * len(images))

    print(f"{len(frames)} frames x {len(names)} parts, {ms:.2f} ms per search")
    print(f"{'part':34s} {'present':>7s} {'ambig':>5s} {'absent':>6s}  {'min hit':>7s} {'max miss':>8s}  ambiguous frames")
    for name, scores in rows.items():
        hi = args.hi if args.hi is not None else float(parts[name].get("threshold", 0.90))
        lo = min(args.lo, hi - 0.1)
        hit = [s for s in scores if s >= hi]
        amb = [(f.name[:3], s) for f, s in zip(frames, scores) if lo <= s < hi]
        miss = [s for s in scores if s < lo]
        print(f"{name:34s} {len(hit):7d} {len(amb):5d} {len(miss):6d}  "
              f"{min(hit, default=float('nan')):7.2f} {max(miss, default=float('nan')):8.2f}  "
              + " ".join(f"{n}:{s:.2f}" for n, s in amb[:8]))
        if args.part:
            for f, s in zip(frames, scores):
                print(f"    {f.name:44s} {s:.3f}")
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["frame", *rows])
            for i, f in enumerate(frames):
                w.writerow([f.name, *(f"{rows[n][i]:.3f}" for n in rows)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
