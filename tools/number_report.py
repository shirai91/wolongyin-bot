"""Read every number field (numbers.yaml) on every corpus frame where it is on screen (docs/08, R1).

A field is read only where its ``when`` part is found (parts.yaml). The report lists each value in
frame order, so a wrong read stands out against the session's known story (silver only changes when
spent or earned, Quân lệnh drops by 23 per Võ Khôi Tháp round, the chest count counts down...).

    python tools/number_report.py                 # every field
    python tools/number_report.py --field hud.silver
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from ngoalong.core.capture import read_image  # noqa: E402
from ngoalong.core.digits import GlyphSet  # noqa: E402
from ngoalong.core.templates import TemplateStore  # noqa: E402
from ngoalong.core.vision import match_template  # noqa: E402

PROFILE = ROOT / "profiles" / "ngoalongviet"
CORPUS = ROOT / "corpus" / "live"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--field", action="append", help="field name(s); default: all")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    fields = yaml.safe_load((PROFILE / "numbers.yaml").read_text(encoding="utf-8"))
    parts = yaml.safe_load((PROFILE / "parts.yaml").read_text(encoding="utf-8"))
    store = TemplateStore(PROFILE / "templates")
    glyphs = GlyphSet.load(PROFILE / "templates" / "digits" / "game")
    frames = [(p.name[:3], read_image(p)) for p in sorted(CORPUS.glob("[0-9][0-9][0-9]-*.png"))]

    failed = 0
    for name in args.field or list(fields):
        f = fields[name]
        gate = parts[f["when"]]
        t = store.get(gate["template"])
        gx, gy, gw, gh = gate["search"] if gate.get("search", "canvas") != "canvas" else (0, 0, 1000, 600)
        x, y, w, h = f["rect"]
        out, bad = [], []
        for fid, img in frames:
            m = match_template(img[gy:gy + gh, gx:gx + gw], t.image, t.mask)
            if m is None or m[0] < gate.get("threshold", 0.9):
                continue
            r = glyphs.read(img[y:y + h, x:x + w])
            v = r.parse(f["parse"])
            if v is None:
                bad.append(f"{fid}:{r.text!r}{'<' if r.clipped_left else ''}{'>' if r.clipped_right else ''}")
            else:
                out.append((fid, v))
        failed += len(bad)
        runs: list[str] = []
        for fid, v in out:  # compress repeats: "064-071 869174318"
            if runs and runs[-1].endswith(f" {v}"):
                runs[-1] = runs[-1].split("-")[0].split(" ")[0] + f"-{fid} {v}"
            else:
                runs.append(f"{fid} {v}")
        print(f"{name}: {len(out)} read, {len(bad)} unreadable")
        print("   " + " | ".join(runs))
        if bad:
            print("   UNREADABLE " + " ".join(bad))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
