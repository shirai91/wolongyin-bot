"""Cut anchor and object templates from the reference screenshots (reproducible).

The crop list lives in ``profiles/ngoalongviet/template_crops.yaml``::

    sources:                       # screenshot alias -> file + canvas origin in that file
      main: {path: "docs/game screenshots/main-game.png", origin: [273, 109]}
    crops:
      - name: anchor.kvuc_label    # -> templates/anchor/kvuc_label.png
        source: main
        rect: [2, 574, 40, 22]     # canvas x, y, w, h at 1000x600
        mask: ellipse              # optional: also write <stem>.mask.png

Template names use dots for folders, the same names ``TemplateStore`` resolves
(``"tower.dialog.title"`` -> ``tower/dialog/title.png``). Paths are relative to the project root.

Usage::

    python tools/cut_templates.py            # (re)write every template
    python tools/cut_templates.py --check    # exit 1 if a template on disk differs from its crop
    python tools/cut_templates.py --sheet build/template-sheet.png   # contact sheet for review
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # allow running as a plain script from anywhere
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import yaml  # noqa: E402

from ngoalong.core.capture import read_image, save_png  # noqa: E402

DEFAULT_CROPS = ROOT / "profiles" / "ngoalongviet" / "template_crops.yaml"
DEFAULT_OUT = ROOT / "profiles" / "ngoalongviet" / "templates"
MASK_KINDS = ("ellipse",)


@dataclass(frozen=True, slots=True)
class Source:
    alias: str
    path: Path
    origin: tuple[int, int]


@dataclass(frozen=True, slots=True)
class Crop:
    name: str
    source: str
    rect: tuple[int, int, int, int]  # canvas x, y, w, h
    mask: str | None = None

    def rel_path(self) -> Path:
        return Path(*self.name.split(".")).with_suffix(".png")


def _pair(v: Any, what: str) -> tuple[int, int]:
    if not (isinstance(v, Sequence) and len(v) == 2 and all(isinstance(n, int) for n in v)):
        raise ValueError(f"{what} must be [x, y] integers, got {v!r}")
    return (int(v[0]), int(v[1]))


def load_crops(path: str | Path = DEFAULT_CROPS, root: Path = ROOT) -> tuple[dict[str, Source], list[Crop]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, Mapping):
        raise ValueError(f"{path}: top level must be a mapping")
    sources: dict[str, Source] = {}
    for alias, spec in (data.get("sources") or {}).items():
        sources[alias] = Source(alias, root / spec["path"], _pair(spec["origin"], f"sources.{alias}.origin"))
    crops: list[Crop] = []
    seen: set[str] = set()
    for i, c in enumerate(data.get("crops") or []):
        name = c["name"]
        if name in seen:
            raise ValueError(f"crop {name!r} listed twice")
        seen.add(name)
        if c["source"] not in sources:
            raise ValueError(f"crop {name!r}: unknown source {c['source']!r}")
        r = c["rect"]
        if not (isinstance(r, Sequence) and len(r) == 4 and all(isinstance(n, int) for n in r)):
            raise ValueError(f"crop {name!r}: rect must be [x, y, w, h] integers")
        if r[2] <= 0 or r[3] <= 0:
            raise ValueError(f"crop {name!r}: empty rect")
        mask = c.get("mask")
        if mask is not None and mask not in MASK_KINDS:
            raise ValueError(f"crop {name!r}: mask must be one of {MASK_KINDS}")
        crops.append(Crop(name, c["source"], (r[0], r[1], r[2], r[3]), mask))
    return sources, crops


def make_mask(kind: str, w: int, h: int) -> np.ndarray:
    m = np.zeros((h, w), np.uint8)
    if kind == "ellipse":
        cv2.ellipse(m, (w // 2, h // 2), (max(1, w // 2), max(1, h // 2)), 0, 0, 360, 255, -1)
    return m


def cut(crop: Crop, sources: Mapping[str, Source], cache: dict[Path, np.ndarray] | None = None) -> tuple[np.ndarray, np.ndarray | None]:
    src = sources[crop.source]
    if cache is not None and src.path in cache:
        img = cache[src.path]
    else:
        img = read_image(src.path)
        if cache is not None:
            cache[src.path] = img
    x, y, w, h = crop.rect
    fx, fy = src.origin[0] + x, src.origin[1] + y
    if fx < 0 or fy < 0 or fx + w > img.shape[1] or fy + h > img.shape[0]:
        raise ValueError(f"crop {crop.name!r} falls outside {src.path.name}")
    image = np.ascontiguousarray(img[fy : fy + h, fx : fx + w])
    mask = make_mask(crop.mask, w, h) if crop.mask else None
    return image, mask


def write_all(crops_path: str | Path = DEFAULT_CROPS, out_dir: str | Path = DEFAULT_OUT) -> list[Path]:
    sources, crops = load_crops(crops_path)
    out = Path(out_dir)
    cache: dict[Path, np.ndarray] = {}
    written: list[Path] = []
    for crop in crops:
        image, mask = cut(crop, sources, cache)
        p = save_png(out / crop.rel_path(), image)
        written.append(p)
        if mask is not None:
            written.append(save_png(p.with_name(f"{p.stem}.mask.png"), mask))
    return written


def check_all(crops_path: str | Path = DEFAULT_CROPS, out_dir: str | Path = DEFAULT_OUT) -> list[str]:
    """Names whose template file is missing or differs from a fresh cut."""
    sources, crops = load_crops(crops_path)
    out = Path(out_dir)
    cache: dict[Path, np.ndarray] = {}
    bad: list[str] = []
    for crop in crops:
        image, mask = cut(crop, sources, cache)
        p = out / crop.rel_path()
        if not p.is_file() or not np.array_equal(read_image(p), image):
            bad.append(crop.name)
            continue
        if mask is not None:
            mp = p.with_name(f"{p.stem}.mask.png")
            if not mp.is_file() or not np.array_equal(read_image(mp, cv2.IMREAD_GRAYSCALE), mask):
                bad.append(crop.name)
    return bad


def contact_sheet(crops_path: str | Path = DEFAULT_CROPS, zoom: int = 2) -> np.ndarray:
    sources, crops = load_crops(crops_path)
    cache: dict[Path, np.ndarray] = {}
    rows: list[np.ndarray] = []
    for crop in crops:
        image, _ = cut(crop, sources, cache)
        z = cv2.resize(image, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST)
        label = np.zeros((16, max(z.shape[1], 420), 3), np.uint8)
        cv2.putText(label, f"{crop.name} {list(crop.rect)} <{crop.source}>", (2, 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
        pad = np.zeros((z.shape[0], label.shape[1] - z.shape[1], 3), np.uint8)
        rows.append(np.vstack([label, np.hstack([z, pad])]))
    width = max(r.shape[1] for r in rows)
    rows = [np.hstack([r, np.zeros((r.shape[0], width - r.shape[1], 3), np.uint8)]) for r in rows]
    return np.vstack(rows)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--crops", default=str(DEFAULT_CROPS), help="crop list YAML")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="templates folder")
    ap.add_argument("--check", action="store_true", help="only verify the templates on disk")
    ap.add_argument("--sheet", help="also write a contact sheet PNG of every crop")
    args = ap.parse_args(argv)
    if args.check:
        bad = check_all(args.crops, args.out)
        for name in bad:
            print(f"out of date: {name}")
        print("templates up to date" if not bad else f"{len(bad)} template(s) out of date")
        return 1 if bad else 0
    written = write_all(args.crops, args.out)
    print(f"wrote {len(written)} file(s) to {args.out}")
    if args.sheet:
        save_png(args.sheet, contact_sheet(args.crops))
        print(f"contact sheet: {args.sheet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
