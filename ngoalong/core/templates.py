"""``TemplateStore``: resolves template names to images and caches them in memory.

Name resolution, tried in each root folder in order:

* ``"tower.open"``  -> ``tower/open.png`` (dots are folders), then ``tower.open.png``
* ``"Boss/VKT_CuaHang"`` -> ``Boss/VKT_CuaHang.png`` / ``.bmp``

A mask is taken from the template's alpha channel (transparent pixels are ignored) or from a
sibling ``<name>.mask.png`` (white = compare, black = ignore). Templates are stored at canvas scale
1.0 (1000x600) and resized on demand for other zoom levels; each scale is cached.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ngoalong.core.capture import read_image, to_bgr
from ngoalong.core.errors import CaptureError, TemplateError

TEMPLATE_SUFFIXES = (".png", ".bmp")


@dataclass(frozen=True, slots=True)
class Template:
    name: str
    image: np.ndarray  # BGR uint8
    mask: np.ndarray | None = None  # uint8, same shape as image, 0 = ignore, 255 = compare
    threshold: float | None = None
    path: Path | None = None

    @property
    def size(self) -> tuple[int, int]:
        return (int(self.image.shape[1]), int(self.image.shape[0]))


def normalize_mask(mask: np.ndarray, shape: tuple[int, ...]) -> np.ndarray:
    """Binary uint8 mask (0/255) with the same height, width and channel count as ``shape``."""
    m = mask
    if m.ndim == 3:
        m = m[:, :, 3] if m.shape[2] == 4 else cv2.cvtColor(m, cv2.COLOR_BGR2GRAY)
    if m.shape[:2] != tuple(shape[:2]):
        raise TemplateError(f"mask size {m.shape[1]}x{m.shape[0]} does not match the template")
    m = np.where(m > 127, 255, 0).astype(np.uint8)
    channels = shape[2] if len(shape) == 3 else 1
    return cv2.merge([m] * channels) if channels > 1 else m


def resize_template(t: Template, scale: float) -> Template:
    if abs(scale - 1.0) < 1e-6:
        return t
    h, w = t.image.shape[:2]
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    image = cv2.resize(t.image, (nw, nh), interpolation=interp)
    mask = None
    if t.mask is not None:
        mask = cv2.resize(t.mask, (nw, nh), interpolation=cv2.INTER_NEAREST)
        if mask.ndim == 2 and image.ndim == 3:
            mask = cv2.merge([mask] * image.shape[2])
    return Template(t.name, image, mask, t.threshold, t.path)


class TemplateStore:
    def __init__(self, roots: str | Path | Iterable[str | Path] = ()) -> None:
        if isinstance(roots, (str, Path)):
            roots = [roots]
        self.roots: list[Path] = [Path(r) for r in roots]
        self._base: dict[str, Template] = {}
        self._scaled: dict[tuple[str, float], Template] = {}
        self._lock = threading.Lock()
        self.load_count = 0  # files decoded from disk; tests use it to check the cache

    # --- registration ---------------------------------------------------------------------------
    def register(
        self,
        name: str,
        image: np.ndarray | str | Path,
        mask: np.ndarray | str | Path | None = None,
        threshold: float | None = None,
    ) -> Template:
        """Add or replace a template from an array or a file path."""
        path = None
        if not isinstance(image, np.ndarray):
            path = Path(image)
            image = self._read(path)
        t = self._build(name, image, mask, threshold, path)
        with self._lock:
            self._base[name] = t
            for key in [k for k in self._scaled if k[0] == name]:
                del self._scaled[key]
        return t

    def clear_cache(self) -> None:
        with self._lock:
            self._base.clear()
            self._scaled.clear()

    # --- lookup ---------------------------------------------------------------------------------
    def get(self, name: str, scale: float = 1.0) -> Template:
        key = (name, round(scale, 4))
        with self._lock:
            cached = self._scaled.get(key)
            if cached is not None:
                return cached
            base = self._base.get(name)
        if base is None:
            base = self._load(name)
            with self._lock:
                base = self._base.setdefault(name, base)
        scaled = resize_template(base, key[1])
        with self._lock:
            return self._scaled.setdefault(key, scaled)

    def resolve(self, name: str) -> Path:
        rel_names = [name.replace(".", "/"), name] if "." in name else [name]
        for root in self.roots:
            for rel in rel_names:
                for suffix in TEMPLATE_SUFFIXES:
                    p = root / f"{rel}{suffix}"
                    if p.is_file():
                        return p
        raise TemplateError(f"template {name!r} not found in {[str(r) for r in self.roots]}")

    def __contains__(self, name: str) -> bool:
        if name in self._base:
            return True
        try:
            self.resolve(name)
        except TemplateError:
            return False
        return True

    # --- internals ------------------------------------------------------------------------------
    def _read(self, path: Path) -> np.ndarray:
        try:
            img = read_image(path, cv2.IMREAD_UNCHANGED)
        except CaptureError as exc:
            raise TemplateError(str(exc)) from exc
        self.load_count += 1
        return img

    def _load(self, name: str) -> Template:
        path = self.resolve(name)
        image = self._read(path)
        mask_path = path.with_name(f"{path.stem}.mask.png")
        return self._build(name, image, mask_path if mask_path.is_file() else None, None, path)

    def _build(
        self,
        name: str,
        image: np.ndarray,
        mask: np.ndarray | str | Path | None,
        threshold: float | None,
        path: Path | None,
    ) -> Template:
        if image.dtype != np.uint8:
            raise TemplateError(f"template {name!r} must be 8-bit")
        alpha = image[:, :, 3] if image.ndim == 3 and image.shape[2] == 4 else None
        bgr = np.ascontiguousarray(to_bgr(image))
        mask_arr: np.ndarray | None = None
        if mask is not None:
            mask_arr = mask if isinstance(mask, np.ndarray) else self._read(Path(mask))
        elif alpha is not None and (alpha < 255).any():
            mask_arr = alpha
        norm = normalize_mask(mask_arr, bgr.shape) if mask_arr is not None else None
        if norm is not None and not norm.any():
            raise TemplateError(f"template {name!r} mask ignores every pixel")
        return Template(name, bgr, norm, threshold, path)
