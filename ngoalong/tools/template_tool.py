"""Template capture tool (docs/06 "New features"): cut a template out of a capture, register it in
the profile, and test its match score on the current frame.

The "Mẫu ảnh" tab (``ngoalong/ui/tabs/templates.py``) drives it; tests drive it directly.

1. ``load_file(path)`` / ``grab(capturer)`` -> ``Capture`` (frame + canvas fix from ``CanvasLocator``).
2. The user drags a rectangle; ``canvas_rect`` turns it into canvas coordinates at 1000x600, the unit
   of every profile file.
3. ``score_selection`` says how the selection scores elsewhere on the frame (a good template has a
   low runner-up) and suggests a threshold.
4. ``save(capture, SaveRequest(...))`` writes, all or nothing:

   * ``templates/<name, dots as folders>.png`` at canvas scale 1.0;
   * a crop line in ``template_crops.yaml``, so ``tools/cut_templates.py --check`` reproduces the
     file. The crop source is the screenshot itself when it is a 1:1 file inside the project;
     otherwise the canvas is saved under ``captures/`` and that file becomes the source;
   * a scene entry in ``scenes/*.yaml``: an object of a scene, a signature item of a scene, or a new
     scene. The YAML files are edited as text (comments are kept) and reloaded to validate; any
     error restores every file.
5. ``score`` / ``score_references`` test a saved template on the frame: over the whole canvas, and
   at every place a scene uses it (with that place's ``at``, margin and threshold).

Command line (scores only, nothing is written)::

    python -m ngoalong.tools.template_tool "docs/game screenshots/tower-ui.png" tower.dialog.title
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from ngoalong.core.capture import Capturer, read_image, save_png, to_bgr
from ngoalong.core.detection import Detection
from ngoalong.core.errors import NgoaLongError
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.locator import DEFAULT_PROFILE_DIR, PROJECT_ROOT, CanvasFix, CanvasLocator, Confidence
from ngoalong.core.scenes import DEFAULT_MARGIN, SceneDef, SceneDetector, SceneKind, SceneMatch, load_scene_defs
from ngoalong.core.scenes import DEFAULT_THRESHOLD as DEFAULT_SCENE_THRESHOLD
from ngoalong.core.templates import Template, TemplateStore
from ngoalong.core.vision import DEFAULT_THRESHOLD, Matcher, frame_rect, score_map

CANVAS_W, CANVAS_H = 1000, 600
CANVAS = Rect(0, 0, CANVAS_W, CANVAS_H)
MIN_SIZE = 4  # px: smaller selections match almost anywhere
WHOLE_IMAGE = "whole image"  # CanvasFix detector name when the file is the canvas itself
SELECTION = "__selection__"
TOOL_TAG = "template tool"

NAME_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")  # "<area>.<name>": dots are folders
SCENE_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
OBJECT_RE = re.compile(r"^[a-z0-9_]+$")
SCENE_FILE_RE = re.compile(r"^[a-z0-9_]+\.yaml$")


class TemplateToolError(NgoaLongError):
    """Invalid request, or a profile file the tool cannot edit safely (nothing was changed)."""


class SaveMode(StrEnum):
    OBJECT = "object"  # an object of an existing scene (features look it up with find_object)
    SIGNATURE = "signature"  # one more signature item of an existing scene
    NEW_SCENE = "new_scene"  # a new scene whose signature is the template


# --- data ---------------------------------------------------------------------------------------
@dataclass(slots=True)
class Capture:
    image: np.ndarray  # BGR frame (a Coowon client capture, or a 1000x600 canvas)
    canvas: CanvasFix
    source: Path | None = None  # the file it was read from, if any
    label: str = ""
    saved_as: Path | None = None  # canvas file written under captures/ (reused by later saves)

    @property
    def usable(self) -> bool:
        """Templates may only be cut where the canvas position is certain."""
        return self.canvas.reliable or self.canvas.detectors == (WHOLE_IMAGE,)

    def canvas_image(self) -> np.ndarray:
        """The canvas at 1000x600 (resized when the game is zoomed)."""
        r = self.canvas.rect
        if r.intersect(frame_rect(self.image)) != r:
            raise TemplateToolError("Khung game nằm một phần ngoài ảnh chụp")
        region = self.image[r.y:r.bottom, r.x:r.right]
        if (r.w, r.h) == (CANVAS_W, CANVAS_H):
            return np.ascontiguousarray(region)
        interp = cv2.INTER_AREA if r.w > CANVAS_W else cv2.INTER_LINEAR
        return cv2.resize(region, (CANVAS_W, CANVAS_H), interpolation=interp)


@dataclass(frozen=True, slots=True)
class SaveRequest:
    name: str  # template name, e.g. "tham_bao.reward_x2" -> templates/tham_bao/reward_x2.png
    rect: Rect  # canvas coordinates at 1000x600
    scene: str
    mode: SaveMode = SaveMode.OBJECT
    object_name: str | None = None  # OBJECT: default = last part of the name; NEW_SCENE: optional
    threshold: float = 0.9
    margin: int = DEFAULT_MARGIN
    description: str = ""
    scene_file: str | None = None  # NEW_SCENE: file under scenes/ (default "<first part>.yaml")
    kind: SceneKind = "screen"  # NEW_SCENE
    overwrite: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", SaveMode(self.mode))  # accept plain strings (Qt item data)

    def object_key(self) -> str | None:
        if self.mode is SaveMode.OBJECT:
            return self.object_name or self.name.rsplit(".", 1)[-1]
        return self.object_name or None

    def scene_file_name(self) -> str:
        return self.scene_file or f"{self.name.split('.', 1)[0]}.yaml"


@dataclass(frozen=True, slots=True)
class SaveResult:
    name: str
    template_path: Path
    scene: str
    scene_file: Path
    entry: str  # the YAML line written into the scene file
    crop_source: str  # source alias in template_crops.yaml
    capture_file: Path | None  # canvas written under captures/ by this save (None: reused / file)

    def text(self) -> str:
        return f"Đã lưu mẫu {self.name} -> {self.template_path.name}; {self.scene_file.name}: {self.entry.strip()}"


@dataclass(frozen=True, slots=True)
class ScoreResult:
    name: str
    label: str  # where it was searched: "khung game", "<scene>.<object>", "<scene> (dấu hiệu)"
    score: float
    threshold: float
    found: bool
    rect: Rect | None  # best location, canvas coordinates
    runner_up: float | None = None  # best score away from that location
    suggested_threshold: float | None = None
    detection: Detection | None = None

    def text(self) -> str:
        verdict = "THẤY" if self.found else "không thấy"
        where = f" tại ({self.rect.x}, {self.rect.y})" if self.rect is not None else ""
        out = f"{self.label}: {verdict} {self.score:.3f} / ngưỡng {self.threshold:.2f}{where}"
        if self.runner_up is not None:
            out += f"; chỗ khác cao nhất {self.runner_up:.3f}"
        if self.suggested_threshold is not None:
            out += f"; ngưỡng gợi ý {self.suggested_threshold:.2f}"
        if self.runner_up is not None and self.runner_up >= self.threshold:
            out += "; CHÚ Ý: chỗ khác cũng vượt ngưỡng (mẫu không đặc trưng hoặc xuất hiện nhiều lần)"
        return out


# --- text helpers (YAML edited as text so comments survive) -------------------------------------
def flow(d: Mapping[str, Any]) -> str:
    """One-line YAML flow mapping, keys in the given order."""
    return yaml.safe_dump(dict(d), default_flow_style=True, sort_keys=False, allow_unicode=True,
                          width=1_000_000).strip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_content(line: str) -> bool:
    s = line.strip()
    return bool(s) and not s.startswith("#")


def _join(lines: Sequence[str]) -> str:
    return "\n".join(lines).rstrip("\n") + "\n"


def _scene_span(lines: Sequence[str], scene: str) -> tuple[int, int]:
    pat = re.compile(rf"^{re.escape(scene)}\s*:\s*(#.*)?$")
    start = next((i for i, line in enumerate(lines) if pat.match(line)), None)
    if start is None:
        raise TemplateToolError(f"Không tìm thấy khối màn hình {scene!r} trong tệp (hãy sửa tay)")
    end = next((i for i in range(start + 1, len(lines)) if _is_content(lines[i]) and _indent(lines[i]) == 0),
               len(lines))
    return start, end


def add_to_scene(text: str, scene: str, key: str, item: str, replace: re.Pattern[str] | None = None) -> str:
    """Add the one-line ``item`` to the ``key`` block (``objects`` / ``signature``) of ``scene``.

    When ``replace`` matches a one-line item of the block, that item is replaced instead.
    """
    lines = text.splitlines()
    start, end = _scene_span(lines, scene)
    ci = next((_indent(lines[i]) for i in range(start + 1, end) if _is_content(lines[i])), 2)
    key_re = re.compile(rf"^ {{{ci}}}{key}\s*:\s*(.*?)\s*$")
    k = next((i for i in range(start + 1, end) if key_re.match(lines[i])), None)
    if k is None:  # no such key yet: add it at the end of the scene block
        last = max([start, *(i for i in range(start + 1, end) if _is_content(lines[i]))])
        lines[last + 1:last + 1] = [" " * ci + f"{key}:", " " * (ci + 2) + item]
        return _join(lines)
    m = key_re.match(lines[k])
    assert m is not None
    value = m.group(1)
    if value.startswith("#"):
        value = ""
    if value in ("{}", "[]"):
        lines[k] = " " * ci + f"{key}:"
        value = ""
    if value:
        raise TemplateToolError(f"{scene}.{key} được viết trên một dòng; hãy sửa tệp bằng tay")
    last_child, item_indent = k, None
    for j in range(k + 1, end):
        line = lines[j]
        if not _is_content(line):
            continue
        ind = _indent(line)
        if not (ind > ci or (ind == ci and line.lstrip().startswith("- "))):
            break
        if item_indent is None:
            item_indent = ind
        if replace is not None and ind == item_indent and replace.search(line.strip()):
            if "{" not in line:
                raise TemplateToolError(f"{scene}.{key}: mục cũ viết trên nhiều dòng; hãy sửa tay")
            lines[j] = " " * ind + item
            return _join(lines)
        last_child = j
    lines.insert(last_child + 1, " " * (item_indent if item_indent is not None else ci + 2) + item)
    return _join(lines)


def new_scene_block(scene: str, kind: str, description: str, signature_item: str,
                    object_item: str | None = None) -> str:
    out = [f"{scene}:", f"  kind: {kind}"]
    if description:
        out.append(f"  description: {json.dumps(description, ensure_ascii=False)}")
    out += ["  signature:", f"    - {signature_item}"]
    if object_item:
        out += ["  objects:", f"    {object_item}"]
    return "\n".join(out) + "\n"


def add_source_line(text: str, line: str) -> str:
    """Insert ``line`` at the end of the ``sources:`` block of a crop list."""
    lines = text.splitlines()
    crops = next((i for i, l in enumerate(lines) if re.match(r"^crops\s*:", l)), None)
    if crops is None or not any(re.match(r"^sources\s*:\s*(#.*)?$", l) for l in lines[:crops]):
        raise TemplateToolError("template_crops.yaml cần khối 'sources:' rồi 'crops:'")
    j = crops
    while j > 0 and not (_is_content(lines[j - 1]) and _indent(lines[j - 1]) > 0) \
            and not re.match(r"^sources\s*:", lines[j - 1]):
        j -= 1
    lines.insert(j, line)
    return _join(lines)


def set_crop_line(text: str, name: str, line: str) -> str:
    """Replace the crop line of ``name`` or append ``line`` at the end of the crop list."""
    lines = text.splitlines()
    pat = re.compile(rf"^\s*-\s*\{{\s*name:\s*{re.escape(name)}\s*,")
    for i, l in enumerate(lines):
        if pat.match(l):
            lines[i] = line
            return _join(lines)
    header = f"  # --- added by the {TOOL_TAG} (ngoalong/tools/template_tool.py) ---"
    if header not in lines:
        lines += ["", header]
    lines.append(line)
    return _join(lines)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "capture"


# --- all-or-nothing file edits ------------------------------------------------------------------
class _Transaction:
    def __init__(self) -> None:
        self._saved: dict[Path, bytes | None] = {}
        self._dirs: list[Path] = []

    def _touch(self, p: Path) -> None:
        if p not in self._saved:
            self._saved[p] = p.read_bytes() if p.is_file() else None
        missing: list[Path] = []
        d = p.parent
        while not d.exists():
            missing.append(d)
            d = d.parent
        self._dirs.extend(missing)

    def write_text(self, p: Path, text: str) -> None:
        self._touch(p)
        old = self._saved[p]
        newline = "\r\n" if old is not None and b"\r\n" in old else "\n"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline=newline)

    def write_png(self, p: Path, image: np.ndarray) -> None:
        self._touch(p)
        save_png(p, image)

    def remove(self, p: Path) -> None:
        self._touch(p)
        p.unlink(missing_ok=True)

    def rollback(self) -> None:
        for p, data in reversed(list(self._saved.items())):
            try:
                if data is None:
                    p.unlink(missing_ok=True)
                else:
                    p.write_bytes(data)
            except OSError:
                pass
        for d in self._dirs:  # deepest first
            try:
                d.rmdir()
            except OSError:
                pass


# --- the tool -----------------------------------------------------------------------------------
class TemplateTool:
    def __init__(
        self,
        profile_dir: str | Path = DEFAULT_PROFILE_DIR,
        project_root: str | Path = PROJECT_ROOT,
        captures_dir: str | Path | None = None,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.project_root = Path(project_root)
        self.templates_dir = self.profile_dir / "templates"
        self.scenes_dir = self.profile_dir / "scenes"
        self.crops_path = self.profile_dir / "template_crops.yaml"
        self.captures_dir = Path(captures_dir) if captures_dir is not None else self.profile_dir / "captures"
        self.clock = clock
        self.store = TemplateStore(self.templates_dir)
        self.locator = CanvasLocator.from_profile(self.profile_dir, Matcher(self.store, history=64))

    # --- profile --------------------------------------------------------------------------------
    def template_path(self, name: str) -> Path:
        return self.templates_dir.joinpath(*name.split(".")).with_suffix(".png")

    def scene_defs(self) -> list[SceneDef]:
        return load_scene_defs(self.scenes_dir)

    def scene_names(self) -> list[str]:
        return sorted(d.name for d in self.scene_defs())

    def reload(self) -> None:
        """Forget cached template images (after files changed on disk)."""
        self.store.clear_cache()

    # --- captures -------------------------------------------------------------------------------
    def load_frame(self, frame: np.ndarray, source: str | Path | None = None, label: str = "") -> Capture:
        img = np.ascontiguousarray(to_bgr(frame))
        self.locator.reset()  # independent captures: never reuse the previous canvas
        fix = self.locator.locate(img)
        if not fix.reliable and img.shape[:2] == (CANVAS_H, CANVAS_W):
            fix = CanvasFix(CANVAS, 1.0, Confidence.LOW, (WHOLE_IMAGE,))
        src = Path(source) if source is not None else None
        return Capture(img, fix, src, label or (src.name if src else ""))

    def load_file(self, path: str | Path) -> Capture:
        p = Path(path)
        return self.load_frame(read_image(p), p, p.name)

    def grab(self, capturer: Capturer, crop: bool = True) -> Capture:
        """Live capture; cropped to the 1000x600 canvas when its position is certain (otherwise the
        whole window is kept, so the user can see why the canvas was not found)."""
        cap = self.load_frame(capturer.grab(), None, "ảnh chụp từ game")
        if not crop or not cap.canvas.reliable:
            return cap
        fix = cap.canvas
        canvas = CanvasFix(CANVAS, 1.0, fix.confidence, fix.detectors)
        return Capture(cap.canvas_image(), canvas, None, cap.label)

    def detect_scene(self, capture: Capture) -> SceneMatch:
        detector = SceneDetector(self.scene_defs(), Matcher(self.store, history=64))
        return detector.detect(capture.image, capture.canvas)

    # --- coordinates ----------------------------------------------------------------------------
    @staticmethod
    def canvas_rect(capture: Capture, frame_r: Rect) -> Rect:
        """Frame rectangle -> canvas rectangle at 1000x600, clipped to the canvas."""
        fix = capture.canvas
        x0, y0 = fix.to_canvas(Point(frame_r.x, frame_r.y))
        x1, y1 = fix.to_canvas(Point(frame_r.right, frame_r.bottom))
        return Rect.from_ltrb(round(x0), round(y0), round(x1), round(y1)).intersect(CANVAS)

    @staticmethod
    def frame_rect(capture: Capture, canvas_r: Rect) -> Rect:
        return capture.canvas.rect_to_frame(canvas_r)

    def cut(self, capture: Capture, rect: Rect) -> np.ndarray:
        if rect.intersect(CANVAS) != rect or rect.w < MIN_SIZE or rect.h < MIN_SIZE:
            raise TemplateToolError(
                f"Vùng chọn phải nằm trong khung game và lớn hơn {MIN_SIZE}×{MIN_SIZE} px "
                f"(đang là ({rect.x}, {rect.y}) {rect.w}×{rect.h})"
            )
        c = capture.canvas_image()
        return np.ascontiguousarray(c[rect.y:rect.bottom, rect.x:rect.right])

    # --- scoring --------------------------------------------------------------------------------
    def _canvas_roi(self, capture: Capture) -> Rect:
        return capture.canvas.rect.intersect(frame_rect(capture.image))

    def _to_canvas(self, capture: Capture, r: Rect | None) -> Rect | None:
        return None if r is None else self.canvas_rect(capture, r)

    @staticmethod
    def _runner_up(frame: np.ndarray, roi: Rect, t: Template, best: Rect) -> float | None:
        area = roi.intersect(frame_rect(frame))
        region = to_bgr(frame[area.y:area.bottom, area.x:area.right])
        res = score_map(np.ascontiguousarray(region), t.image, t.mask)
        if res is None:
            return None
        tw, th = t.size  # "elsewhere" = every position whose rectangle does not overlap the best one
        bx, by = best.x - area.x, best.y - area.y
        res[max(0, by - th + 1):by + th, max(0, bx - tw + 1):bx + tw] = -1.0
        top = float(res.max()) if res.size else -1.0
        return None if top <= -1.0 else min(top, 1.0)

    def _search(self, capture: Capture, matcher: Matcher, name: str, roi: Rect, threshold: float | None,
                label: str, runner_up: bool, scale: float | None = None) -> ScoreResult:
        scale = capture.canvas.scale if scale is None else scale
        det = matcher.search(name, roi, threshold, frame=capture.image, scale=scale)
        ru = None
        if runner_up and det.best_rect is not None:
            ru = self._runner_up(capture.image, roi, matcher.templates.get(name, scale), det.best_rect)
        return ScoreResult(name, label, det.score, det.threshold, det.found,
                           self._to_canvas(capture, det.best_rect), ru, None, det)

    def score_selection(self, capture: Capture, rect: Rect, threshold: float | None = None) -> ScoreResult:
        """Search the selection over the whole canvas: how distinctive is it?

        The frame pixels are used as they are (no resampling when the game is zoomed), so the
        selection scores 1.0 at its own place and the runner-up is what matters.
        """
        self.cut(capture, rect)  # validates the rectangle
        fr = self.frame_rect(capture, rect).intersect(frame_rect(capture.image))
        store = TemplateStore()
        store.register(SELECTION, np.ascontiguousarray(capture.image[fr.y:fr.bottom, fr.x:fr.right]))
        matcher = Matcher(store, default_threshold=threshold or 0.9)
        r = self._search(capture, matcher, SELECTION, self._canvas_roi(capture), threshold,
                         "vùng chọn", runner_up=True, scale=1.0)
        suggested = None
        if r.runner_up is not None and r.score - r.runner_up >= 0.05:
            suggested = round(min(0.98, max(0.8, (r.score + r.runner_up) / 2)), 2)
        elif r.runner_up is None:
            suggested = 0.9
        return ScoreResult(r.name, r.label, r.score, r.threshold, r.found, r.rect, r.runner_up,
                           suggested, r.detection)

    def score(self, capture: Capture, name: str, threshold: float | None = None) -> ScoreResult:
        """Search a saved template over the whole canvas of the capture."""
        if name not in self.store:
            raise TemplateToolError(f"Không có mẫu {name!r} trong {self.templates_dir}")
        matcher = Matcher(self.store, default_threshold=DEFAULT_THRESHOLD)
        return self._search(capture, matcher, name, self._canvas_roi(capture), threshold,
                            "khung game", runner_up=True)

    def score_references(self, capture: Capture, name: str) -> list[ScoreResult]:
        """Score the template at every place a scene uses it (its ``at``, margin and threshold)."""
        matcher = Matcher(self.store, default_threshold=DEFAULT_THRESHOLD)
        out: list[ScoreResult] = []
        fix = capture.canvas
        for sdef in self.scene_defs():
            d = sdef.name
            refs: list[tuple[str, Any]] = [(f"{d} (dấu hiệu)", r) for r in sdef.signature]
            refs += [(f"{d}.{k}", o) for k, o in sdef.objects.items()]
            for label, ref in refs:
                if ref.template != name:
                    continue
                t = self.store.get(name, fix.scale)
                tw, th = t.size
                m = round(ref.margin * fix.scale)
                tl = fix.to_frame(ref.at)
                roi = Rect(tl.x - m, tl.y - m, tw + 2 * m, th + 2 * m)
                thr = ref.threshold if ref.threshold is not None else DEFAULT_SCENE_THRESHOLD
                out.append(self._search(capture, matcher, name, roi, thr, label, runner_up=False))
        return out

    # --- saving ---------------------------------------------------------------------------------
    def _validate(self, capture: Capture, req: SaveRequest, defs: Mapping[str, SceneDef]) -> None:
        if not capture.usable:
            raise TemplateToolError("Không tìm thấy khung game trong ảnh này nên không cắt mẫu được")
        if not NAME_RE.match(req.name):
            raise TemplateToolError(
                f"Tên mẫu {req.name!r} không hợp lệ: dùng chữ thường không dấu, số, '_' và dấu chấm, "
                "vd: tham_bao.reward_x2"
            )
        if not SCENE_RE.match(req.scene):
            raise TemplateToolError(f"Tên màn hình {req.scene!r} không hợp lệ")
        if not 0 < req.threshold <= 1:
            raise TemplateToolError("Ngưỡng phải trong khoảng (0, 1]")
        if req.margin < 0:
            raise TemplateToolError("Lề tìm không được âm")
        key = req.object_key()
        if key is not None and not OBJECT_RE.match(key):
            raise TemplateToolError(f"Tên đối tượng {key!r} không hợp lệ")
        if req.mode is SaveMode.NEW_SCENE:
            if req.scene in defs:
                raise TemplateToolError(f"Màn hình {req.scene} đã có; chọn kiểu 'đối tượng' hoặc 'dấu hiệu'")
            if not SCENE_FILE_RE.match(req.scene_file_name()):
                raise TemplateToolError(f"Tên tệp {req.scene_file_name()!r} không hợp lệ (vd: tham_bao.yaml)")
            return
        sdef = defs.get(req.scene)
        if sdef is None:
            raise TemplateToolError(f"Không có màn hình {req.scene}; chọn kiểu 'màn hình mới' để tạo")
        if req.mode is SaveMode.OBJECT and key in sdef.objects and not req.overwrite:
            raise TemplateToolError(f"{req.scene} đã có đối tượng {key}; bật 'Ghi đè' để thay")
        if req.mode is SaveMode.SIGNATURE and any(r.template == req.name for r in sdef.signature) \
                and not req.overwrite:
            raise TemplateToolError(f"{req.name} đã là dấu hiệu của {req.scene}; bật 'Ghi đè' để thay")

    def _inside_project(self, p: Path) -> bool:
        return p.resolve().is_relative_to(self.project_root.resolve())

    def _rel(self, p: Path) -> str:
        """Path as written in template_crops.yaml: relative to the project root when inside it."""
        if self._inside_project(p):
            return p.resolve().relative_to(self.project_root.resolve()).as_posix()
        return p.resolve().as_posix()

    def _source_path(self, spec: Mapping[str, Any]) -> Path:
        p = Path(str(spec.get("path", "")))
        return (p if p.is_absolute() else self.project_root / p).resolve()

    def _crop_source(self, capture: Capture, sources: Mapping[str, Any], tx: _Transaction,
                     name: str) -> tuple[str, str | None, Path | None]:
        """(alias, new source line or None, canvas file written now or None)."""
        r = capture.canvas.rect
        written: Path | None = None
        src = capture.source
        if (src is not None and src.is_file() and self._inside_project(src)
                and capture.canvas.scale == 1.0 and r.intersect(frame_rect(capture.image)) == r):
            path, origin = src.resolve(), (r.x, r.y)
        elif capture.saved_as is not None and capture.saved_as.is_file():
            path, origin = capture.saved_as.resolve(), (0, 0)
        else:
            stamp = self.clock().strftime("%Y%m%d-%H%M%S")
            path = self.captures_dir / f"{stamp}-{_slug(name)}.png"
            n = 2
            while path.exists():
                path = self.captures_dir / f"{stamp}-{_slug(name)}-{n}.png"
                n += 1
            tx.write_png(path, capture.canvas_image())
            written, origin = path, (0, 0)
            path = path.resolve()
        for alias, spec in sources.items():
            if isinstance(spec, Mapping) and self._source_path(spec) == path \
                    and tuple(spec.get("origin") or ()) == origin:
                return str(alias), None, written
        base = "tool_" + _slug(path.stem)
        alias, n = base, 2
        while alias in sources:
            alias, n = f"{base}_{n}", n + 1
        line = f"  {alias}: {flow({'path': self._rel(path), 'origin': list(origin)})}  # {TOOL_TAG}"
        return alias, line, written

    def _scene_entry(self, req: SaveRequest) -> tuple[dict[str, Any], dict[str, Any]]:
        """(signature item, object item) for the request."""
        ref: dict[str, Any] = {"template": req.name, "at": [req.rect.x, req.rect.y],
                               "threshold": round(req.threshold, 3)}
        if req.margin != DEFAULT_MARGIN:
            ref["margin"] = req.margin
        obj = dict(ref)
        if req.description:
            obj["description"] = req.description
        return ref, obj

    def _write_scene(self, req: SaveRequest, defs: Mapping[str, SceneDef], tx: _Transaction) -> tuple[Path, str]:
        ref, obj = self._scene_entry(req)
        key = req.object_key()
        if req.mode is SaveMode.NEW_SCENE:
            path = self.scenes_dir / req.scene_file_name()
            old = path.read_text(encoding="utf-8") if path.is_file() else (
                f"# Scenes added by the {TOOL_TAG} (ngoalong/tools/template_tool.py). Canvas coordinates at 1000x600.\n"
            )
            block = new_scene_block(req.scene, req.kind, req.description, flow(ref),
                                    f"{key}: {flow(obj)}" if key else None)
            tx.write_text(path, old.rstrip("\n") + "\n\n" + block)
            return path, f"- {flow(ref)}"
        sdef = defs[req.scene]
        assert sdef.source is not None
        path = sdef.source
        text = path.read_text(encoding="utf-8")
        if req.mode is SaveMode.OBJECT:
            assert key is not None
            entry = f"{key}: {flow(obj)}"
            new = add_to_scene(text, req.scene, "objects", entry,
                               re.compile(rf"^{re.escape(key)}\s*:") if req.overwrite else None)
        else:
            entry = f"- {flow(ref)}"
            new = add_to_scene(text, req.scene, "signature", entry,
                               re.compile(rf"template:\s*{re.escape(req.name)}\s*[,}}]") if req.overwrite else None)
        tx.write_text(path, new)
        return path, entry

    def _verify(self, req: SaveRequest, tpl_path: Path) -> None:
        defs = {d.name: d for d in self.scene_defs()}  # raises on a broken scene file
        sdef = defs.get(req.scene)
        used = set() if sdef is None else {r.template for r in sdef.signature} | {
            o.template for o in sdef.objects.values() if o.template}
        if req.name not in used:
            raise TemplateToolError(f"Màn hình {req.scene} không dùng {req.name} sau khi lưu")
        data = yaml.safe_load(self.crops_path.read_text(encoding="utf-8")) or {}
        crops = [c for c in data.get("crops") or [] if c.get("name") == req.name]
        if len(crops) != 1:
            raise TemplateToolError(f"template_crops.yaml có {len(crops)} dòng cho {req.name}")
        crop = crops[0]
        spec = (data.get("sources") or {}).get(crop.get("source"))
        if not isinstance(spec, Mapping):
            raise TemplateToolError(f"template_crops.yaml: thiếu nguồn {crop.get('source')!r}")
        x, y, w, h = crop["rect"]
        ox, oy = spec["origin"]
        full = read_image(self._source_path(spec))
        again = full[oy + y:oy + y + h, ox + x:ox + x + w]
        if not np.array_equal(again, read_image(tpl_path)):
            raise TemplateToolError("Mẫu đã lưu không cắt lại được từ nguồn (template_crops.yaml)")
        TemplateStore(self.templates_dir).get(req.name)

    def save(self, capture: Capture, req: SaveRequest) -> SaveResult:
        """Write the template, its crop line and its scene entry; on any error nothing changes."""
        defs = {d.name: d for d in self.scene_defs()}
        self._validate(capture, req, defs)
        image = self.cut(capture, req.rect)
        tpl_path = self.template_path(req.name)
        crops_text = self.crops_path.read_text(encoding="utf-8")
        data = yaml.safe_load(crops_text) or {}
        known = any(isinstance(c, Mapping) and c.get("name") == req.name for c in data.get("crops") or [])
        if (tpl_path.exists() or known) and not req.overwrite:
            raise TemplateToolError(f"Mẫu {req.name} đã có; bật 'Ghi đè' để thay")
        tx = _Transaction()
        try:
            alias, source_line, written = self._crop_source(capture, data.get("sources") or {}, tx, req.name)
            tx.write_png(tpl_path, image)
            mask = tpl_path.with_name(f"{tpl_path.stem}.mask.png")
            if mask.exists():
                tx.remove(mask)  # the new crop has no mask
            if source_line is not None:
                crops_text = add_source_line(crops_text, source_line)
            crop_line = f"  - {flow({'name': req.name, 'source': alias, 'rect': list(req.rect.as_tuple())})}" \
                        f"  # {TOOL_TAG} {self.clock():%Y-%m-%d}"
            tx.write_text(self.crops_path, set_crop_line(crops_text, req.name, crop_line))
            scene_path, entry = self._write_scene(req, defs, tx)
            self._verify(req, tpl_path)
        except Exception as exc:
            tx.rollback()
            if isinstance(exc, TemplateToolError):
                raise
            raise TemplateToolError(f"Không lưu được mẫu (đã hoàn tác): {exc}") from exc
        if written is not None:
            capture.saved_as = written
        self.reload()
        return SaveResult(req.name, tpl_path, req.scene, scene_path, entry, alias, written)


# --- command line -------------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ngoalong.tools.template_tool",
                                 description="Score saved templates on a screenshot (nothing is written).")
    ap.add_argument("image", help="screenshot (Coowon client capture or a 1000x600 canvas)")
    ap.add_argument("templates", nargs="+", help="template names, e.g. tower.dialog.title")
    ap.add_argument("--profile", default=str(DEFAULT_PROFILE_DIR), help="profile folder")
    ap.add_argument("--threshold", type=float, default=None, help="threshold for the canvas-wide search")
    args = ap.parse_args(argv)
    tool = TemplateTool(args.profile)
    cap = tool.load_file(args.image)
    r = cap.canvas.rect
    print(f"{Path(args.image).name}: canvas ({r.x}, {r.y}) {r.w}x{r.h} x{cap.canvas.scale:.2f} "
          f"{cap.canvas.confidence.value}")
    missing = 0
    for name in args.templates:
        try:
            results = [tool.score(cap, name, args.threshold), *tool.score_references(cap, name)]
        except TemplateToolError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            missing += 1
            continue
        for res in results:
            print(f"{name} | {res.text()}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
