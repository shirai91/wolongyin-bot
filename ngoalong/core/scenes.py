"""``SceneDetector``: names the screen the game is showing (docs/06 "Scene recognition").

Scenes are declared in ``profiles/ngoalongviet/scenes/*.yaml`` (one file per area; every file is
loaded, and a scene name defined twice is an error)::

    tower.dialog:
      kind: modal                 # modal | screen | overlay
      priority: 0                 # higher is checked first within its kind
      description: Tháp Thí Luyện dialog
      signature:                  # templates that must ALL match
        - {template: tower.dialog.title, at: [448, 101], threshold: 0.9}
      objects:                    # what the scene contains (for features and the debug overlay)
        close: {offset: [803, 118]}
        start: {template: chest.wheel.start, at: [398, 240], margin: 20}

``at`` is the template's top-left corner in canvas coordinates (1000x600); it is searched within
``margin`` px (default 10, scaled). Modal dialogs are checked first, then full screens; the first
scene whose signature matches wins. Overlays (toasts, transient banners) never win: they are
reported in ``SceneMatch.overlays`` next to the scene underneath.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ngoalong.core.detection import Detection
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.locator import CanvasFix
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher
from ngoalong.paths import APP_ROOT

PROJECT_ROOT = APP_ROOT  # the exe folder in the PyInstaller build (ngoalong/paths.py)
DEFAULT_PROFILE_DIR = PROJECT_ROOT / "profiles" / "ngoalongviet"
DEFAULT_SCENES_DIR = DEFAULT_PROFILE_DIR / "scenes"
DEFAULT_TEMPLATES_DIR = DEFAULT_PROFILE_DIR / "templates"

UNKNOWN = "unknown"
DEFAULT_THRESHOLD = 0.85
DEFAULT_MARGIN = 10
SceneKind = Literal["modal", "screen", "overlay"]
_KIND_ORDER: dict[str, int] = {"modal": 0, "screen": 1, "overlay": 2}


class SceneError(ValueError):
    """Invalid scene file, duplicate scene, or unknown scene/object name."""


# --- definitions --------------------------------------------------------------------------------
class TemplateRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    template: str
    at: tuple[int, int]
    threshold: float | None = Field(None, gt=0, le=1)
    margin: int = Field(DEFAULT_MARGIN, ge=0)


class SceneObject(BaseModel):
    """A fixed click offset, a template to look for near ``at``, or both."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    offset: tuple[int, int] | None = None
    template: str | None = None
    at: tuple[int, int] | None = None
    threshold: float | None = Field(None, gt=0, le=1)
    margin: int = Field(DEFAULT_MARGIN, ge=0)
    description: str = ""

    @model_validator(mode="after")
    def _check(self) -> SceneObject:
        if self.offset is None and self.template is None:
            raise ValueError("an object needs an offset or a template")
        if self.template is not None and self.at is None:
            raise ValueError("a template object needs 'at'")
        return self


class SceneDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    kind: SceneKind = "screen"
    priority: int = 0
    description: str = ""
    signature: tuple[TemplateRef, ...]
    objects: dict[str, SceneObject] = {}
    source: Path | None = None

    @model_validator(mode="after")
    def _check(self) -> SceneDef:
        if not self.signature:
            raise ValueError(f"scene {self.name!r} needs at least one signature template")
        return self


def load_scene_defs(directory: str | Path = DEFAULT_SCENES_DIR) -> list[SceneDef]:
    d = Path(directory)
    if not d.is_dir():
        raise FileNotFoundError(f"scenes directory not found: {d}")
    scenes: dict[str, SceneDef] = {}
    for path in sorted(d.glob("*.yaml")):
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
        if content is None:
            continue
        if not isinstance(content, Mapping):
            raise SceneError(f"{path.name}: top level must be a mapping of scene name -> definition")
        for name, spec in content.items():
            if name in scenes:
                src = scenes[name].source
                raise SceneError(f"scene {name!r} defined in both {src.name if src else '?'} and {path.name}")
            if not isinstance(spec, Mapping):
                raise SceneError(f"{path.name}: scene {name!r} must be a mapping")
            try:
                scenes[name] = SceneDef.model_validate({**spec, "name": name, "source": path})
            except ValueError as exc:
                raise SceneError(f"{path.name}: scene {name!r}: {exc}") from exc
    return list(scenes.values())


# --- results ------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SceneMatch:
    name: str  # scene name, or UNKNOWN
    score: float  # lowest signature score of the scene (best candidate when UNKNOWN)
    kind: str = ""
    detections: tuple[Detection, ...] = ()  # signature searches of the winner (or best candidate)
    overlays: tuple[str, ...] = ()
    overlay_detections: tuple[Detection, ...] = ()
    scores: Mapping[str, float] = field(default_factory=dict)  # every scene checked -> score
    candidate: str | None = None  # closest scene when UNKNOWN

    @property
    def found(self) -> bool:
        return self.name != UNKNOWN


@dataclass(frozen=True, slots=True)
class ObjectResult:
    """Where a scene object is: a template detection, a fixed offset, or both."""

    name: str
    point: Point | None  # frame point to click (match centre, else the offset)
    detection: Detection | None = None

    @property
    def found(self) -> bool:
        return self.point is not None


# --- detector -----------------------------------------------------------------------------------
class SceneDetector:
    def __init__(
        self,
        scenes: Iterable[SceneDef],
        matcher: Matcher,
        default_threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        self.matcher = matcher
        self.default_threshold = default_threshold
        self._scenes: dict[str, SceneDef] = {}
        for s in scenes:
            if s.name in self._scenes:
                raise SceneError(f"duplicate scene {s.name!r}")
            self._scenes[s.name] = s
        indexed = list(enumerate(self._scenes.values()))
        indexed.sort(key=lambda p: (_KIND_ORDER[p[1].kind], -p[1].priority, p[0]))
        self._order = [s for _, s in indexed if s.kind != "overlay"]
        self._overlays = [s for _, s in indexed if s.kind == "overlay"]

    @classmethod
    def load(
        cls,
        directory: str | Path = DEFAULT_SCENES_DIR,
        matcher: Matcher | None = None,
        templates_dir: str | Path = DEFAULT_TEMPLATES_DIR,
    ) -> SceneDetector:
        if matcher is None:
            matcher = Matcher(TemplateStore(templates_dir), history=256)
        return cls(load_scene_defs(directory), matcher)

    # --- lookup ---------------------------------------------------------------------------------
    @property
    def names(self) -> list[str]:
        return list(self._scenes)

    @property
    def check_order(self) -> list[str]:
        return [s.name for s in self._order]

    def get(self, name: str) -> SceneDef:
        try:
            return self._scenes[name]
        except KeyError:
            raise SceneError(f"unknown scene {name!r}") from None

    def templates(self) -> set[str]:
        """Every template name the scenes reference (signatures and objects)."""
        out: set[str] = set()
        for s in self._scenes.values():
            out.update(r.template for r in s.signature)
            out.update(o.template for o in s.objects.values() if o.template)
        return out

    # --- matching -------------------------------------------------------------------------------
    def _search(self, frame: np.ndarray, canvas: CanvasFix, ref: TemplateRef | SceneObject) -> Detection:
        assert ref.template is not None and ref.at is not None
        t = self.matcher.templates.get(ref.template, canvas.scale)
        tw, th = t.size
        m = round(ref.margin * canvas.scale)
        tl = canvas.to_frame(ref.at)
        roi = Rect(tl.x - m, tl.y - m, tw + 2 * m, th + 2 * m)
        thr = ref.threshold if ref.threshold is not None else self.default_threshold
        return self.matcher.search(ref.template, roi, thr, frame=frame, scale=canvas.scale)

    def _check(self, frame: np.ndarray, canvas: CanvasFix, scene: SceneDef) -> tuple[bool, float, list[Detection]]:
        dets = [self._search(frame, canvas, ref) for ref in scene.signature]
        ok = all(d.found for d in dets)
        return ok, min(d.score for d in dets), dets

    def check(self, frame: np.ndarray, canvas: CanvasFix, name: str) -> SceneMatch:
        """Test one scene only (what ``expect_scene`` polls)."""
        scene = self.get(name)
        ok, score, dets = self._check(frame, canvas, scene)
        return SceneMatch(
            scene.name if ok else UNKNOWN, score, scene.kind, tuple(dets),
            scores={scene.name: score}, candidate=None if ok else scene.name,
        )

    def detect(self, frame: np.ndarray, canvas: CanvasFix) -> SceneMatch:
        """Current scene: modal dialogs first, then full screens; overlays listed separately."""
        overlays: list[str] = []
        overlay_dets: list[Detection] = []
        for ov in self._overlays:
            ok, _score, dets = self._check(frame, canvas, ov)
            overlay_dets.extend(dets)
            if ok:
                overlays.append(ov.name)
        scores: dict[str, float] = {}
        best: tuple[float, SceneDef, list[Detection]] | None = None
        for scene in self._order:
            ok, score, dets = self._check(frame, canvas, scene)
            scores[scene.name] = score
            if ok:
                return SceneMatch(
                    scene.name, score, scene.kind, tuple(dets), tuple(overlays),
                    tuple(overlay_dets), scores,
                )
            if best is None or score > best[0]:
                best = (score, scene, dets)
        if best is None:
            return SceneMatch(UNKNOWN, 0.0, overlays=tuple(overlays),
                              overlay_detections=tuple(overlay_dets), scores=scores)
        return SceneMatch(
            UNKNOWN, best[0], "", tuple(best[2]), tuple(overlays), tuple(overlay_dets),
            scores, candidate=best[1].name,
        )

    # --- objects --------------------------------------------------------------------------------
    def find_object(self, frame: np.ndarray, canvas: CanvasFix, scene: str, obj: str) -> ObjectResult:
        sdef = self.get(scene)
        try:
            o = sdef.objects[obj]
        except KeyError:
            raise SceneError(f"scene {scene!r} has no object {obj!r}") from None
        if o.template is None:
            assert o.offset is not None
            return ObjectResult(obj, canvas.to_frame(o.offset))
        det = self._search(frame, canvas, o)
        if det.found:
            return ObjectResult(obj, det.rect.center, det)
        return ObjectResult(obj, canvas.to_frame(o.offset) if o.offset else None, det)

    def find_objects(self, frame: np.ndarray, canvas: CanvasFix, scene: str) -> list[ObjectResult]:
        return [self.find_object(frame, canvas, scene, name) for name in self.get(scene).objects]


def offsets_of(scene: SceneDef) -> Mapping[str, tuple[int, int]]:
    """Fixed click offsets of a scene's objects (canvas coordinates)."""
    return {k: o.offset for k, o in scene.objects.items() if o.offset is not None}
