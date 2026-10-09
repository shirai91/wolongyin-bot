"""Learn digit glyphs from labelled number samples, then check every sample reads back (docs/08, R1).

The sample list is ``profiles/ngoalongviet/digit_samples.yaml``::

    font: game                  # -> templates/digits/game/<char>.png
    level: 150                  # ink threshold (ngoalong.core.digits)
    learn:                      # samples the glyphs are cut from (every instance; up to 3 variants)
      - {frame: 064, rect: [138, 31, 66, 13], text: "869174318"}
    check:                      # samples that must read back exactly with the learned glyphs
      - {frame: 266, rect: [366, 456, 80, 13], text: "1000000000"}

``frame`` is the 3-digit prefix of a capture in ``corpus/live/``; ``rect`` is canvas x, y, w, h.

    python tools/cut_digits.py            # learn, write glyphs, check every sample
    python tools/cut_digits.py --check    # only check with the glyphs on disk
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from ngoalong.core.capture import read_image, save_png  # noqa: E402
from ngoalong.core.digits import CHAR_FILES, GlyphSet, _score, ink_mask, segment  # noqa: E402

MAX_VARIANTS = 3  # per character
NEW_VARIANT_BELOW = 0.92  # an instance this unlike every kept variant becomes a new variant

SAMPLES = ROOT / "profiles" / "ngoalongviet" / "digit_samples.yaml"
TEMPLATES = ROOT / "profiles" / "ngoalongviet" / "templates" / "digits"
CORPUS = ROOT / "corpus" / "live"


def _region(frame: str, rect: list[int]):
    paths = sorted(CORPUS.glob(f"{frame}-*.png"))
    if not paths:
        raise SystemExit(f"no corpus frame {frame}")
    x, y, w, h = rect
    return read_image(paths[0])[y:y + h, x:x + w]


def learn(spec: dict, out: Path) -> list[str]:
    errors: list[str] = []
    seen: dict[str, list] = {}
    for old in out.glob("*.png"):
        old.unlink()
    for s in spec["learn"]:
        mask = ink_mask(_region(f"{s['frame']:03d}" if isinstance(s["frame"], int) else str(s["frame"]), s["rect"]),
                        spec.get("level", 150))
        boxes = segment(mask)
        text = s["text"]
        if len(boxes) != len(text):
            errors.append(f"learn {s['frame']} {text!r}: {len(boxes)} pieces for {len(text)} chars, "
                          f"widths {[b[2] for b in boxes]}")
            continue
        for ch, (x, y, w, h) in zip(text, boxes):
            piece = mask[y:y + h, x:x + w]
            kept = seen.setdefault(ch, [])
            if len(kept) >= MAX_VARIANTS or any(_score(piece, k) >= NEW_VARIANT_BELOW for k in kept):
                continue
            kept.append(piece)
            stem = CHAR_FILES.get(ch, ch) + ("" if len(kept) == 1 else f"_{len(kept)}")
            save_png(out / f"{stem}.png", piece)
    return errors


def check(spec: dict, glyphs: GlyphSet) -> list[str]:
    errors: list[str] = []
    for kind in ("learn", "check"):
        for s in spec.get(kind) or []:
            frame = f"{s['frame']:03d}" if isinstance(s["frame"], int) else str(s["frame"])
            r = glyphs.read(_region(frame, s["rect"]))
            good = r.text == s["text"] and r.ok
            status = "ok " if good else "BAD"
            print(f"  {status} {kind:5s} {frame} {s['text']!r:14s} read {r.text!r:14s} "
                  f"min score {min(r.scores, default=0):.2f}" + ("  CLIPPED" if r.clipped else ""))
            if not good:
                errors.append(f"{frame}: expected {s['text']!r}, read {r.text!r}")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="only check with the glyphs on disk")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    spec = yaml.safe_load(SAMPLES.read_text(encoding="utf-8"))
    out = TEMPLATES / spec.get("font", "game")
    errors = [] if args.check else learn(spec, out)
    for e in errors:
        print("  " + e)
    if errors:
        return 1
    glyphs = GlyphSet.load(out, spec.get("level", 150))
    print(f"glyphs {''.join(sorted(g.char for g in glyphs.glyphs))} from {out.relative_to(ROOT)}")
    errors = check(spec, glyphs)
    print(f"{len(errors)} sample(s) misread")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
