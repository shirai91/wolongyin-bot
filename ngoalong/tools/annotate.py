"""Run the canvas locator and scene detector on stored screenshots and write annotated copies.

Usage::

    python -m ngoalong.tools.annotate "docs/game screenshots/*.png" --out build/annotated
    python -m ngoalong.tools.annotate shot.png --objects --strict

Each input gets ``<out>/<stem>.png`` with the cyan canvas, green/red searches, scene objects and
the status line. ``--strict`` exits 1 when a canvas is not reliable or a scene is unknown.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ngoalong.core.capture import read_image, save_png, to_bgr
from ngoalong.core.drivers.file import expand_sources
from ngoalong.core.locator import DEFAULT_PROFILE_DIR, CanvasFix, CanvasLocator
from ngoalong.core.scenes import SceneDetector, SceneMatch
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher
from ngoalong.debug.model import DebugFrame, Marker
from ngoalong.debug.render import render


@dataclass(frozen=True, slots=True)
class Annotated:
    source: Path
    canvas: CanvasFix
    scene: SceneMatch
    frame: DebugFrame
    output: Path | None = None

    def summary(self) -> str:
        c = self.canvas
        sc = self.scene
        scene = f"{sc.name} {sc.score:.2f}"
        if not sc.found and sc.candidate:
            scene += f" (closest {sc.candidate})"
        if sc.overlays:
            scene += f" + {', '.join(sc.overlays)}"
        return (
            f"{self.source.name}: canvas ({c.rect.x}, {c.rect.y}) {c.rect.w}x{c.rect.h} "
            f"x{c.scale:.2f} {c.confidence.value} [{', '.join(c.detectors)}] | scene {scene}"
        )


class Annotator:
    def __init__(self, profile_dir: str | Path = DEFAULT_PROFILE_DIR) -> None:
        profile = Path(profile_dir)
        store = TemplateStore(profile / "templates")
        self.locator = CanvasLocator.from_profile(profile, Matcher(store, history=64))
        self.detector = SceneDetector.load(profile / "scenes", Matcher(store, history=256))

    def analyse(self, image: np.ndarray, source: Path, objects: bool = False) -> Annotated:
        self.locator.reset()  # independent screenshots: never fall back to a previous file's canvas
        canvas = self.locator.locate(image)
        scene = self.detector.detect(image, canvas)
        frame = DebugFrame(
            image=image, canvas=canvas, scene=scene.name, scene_score=scene.score,
            window=source.name, task="annotate", step=source.stem,
        )
        frame.add(canvas.detections).add(scene.detections)
        frame.add(d for d in scene.overlay_detections if d.found)
        if objects and scene.found:
            for obj in self.detector.find_objects(image, canvas, scene.name):
                if obj.detection is not None:
                    frame.detections.append(obj.detection)
                elif obj.point is not None:
                    frame.markers.append(Marker(obj.name, obj.point))
        if scene.overlays:
            frame.note = "overlays: " + ", ".join(scene.overlays)
        frame.failed = not canvas.reliable or not scene.found
        return Annotated(source, canvas, scene, frame)

    def annotate_file(self, path: str | Path, out_dir: str | Path | None, objects: bool = False) -> Annotated:
        p = Path(path)
        result = self.analyse(to_bgr(read_image(p)), p, objects)
        if out_dir is None:
            return result
        out = save_png(Path(out_dir) / f"{p.stem}.png", render(result.frame))
        return Annotated(result.source, result.canvas, result.scene, result.frame, out)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ngoalong.tools.annotate", description=__doc__.split("\n\n")[0])
    ap.add_argument("inputs", nargs="+", help="image files, folders or glob patterns")
    ap.add_argument("--out", default="build/annotated", help="output folder (default build/annotated)")
    ap.add_argument("--profile", default=str(DEFAULT_PROFILE_DIR), help="profile folder")
    ap.add_argument("--objects", action="store_true", help="also draw the scene's objects")
    ap.add_argument("--strict", action="store_true", help="exit 1 on an unreliable canvas or unknown scene")
    args = ap.parse_args(argv)

    files = [Path(f) for f in expand_sources(args.inputs) if not isinstance(f, np.ndarray)]
    if not files:
        print("no input images", file=sys.stderr)
        return 2
    annotator = Annotator(args.profile)
    bad = 0
    for f in files:
        r = annotator.annotate_file(f, args.out, args.objects)
        bad += int(r.frame.failed)
        print(r.summary())
    print(f"{len(files)} file(s) annotated into {args.out}; {bad} with problems")
    return 1 if (args.strict and bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
