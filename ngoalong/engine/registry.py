"""Feature registry: ``@register`` on a ``Task`` subclass in ``ngoalong/features/<name>.py``.

``discover()`` imports every module of the ``ngoalong.features`` package, so adding a feature means
adding one file; no central list to edit.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import re
import threading

from ngoalong.engine.task import Task, TaskParams

log = logging.getLogger(__name__)

FEATURES_PACKAGE = "ngoalong.features"
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_registry: dict[str, type[Task]] = {}
_lock = threading.Lock()


class RegistryError(ValueError):
    pass


def register[T: type[Task]](cls: T) -> T:
    """Class decorator: add a feature to the registry (ids must be unique)."""
    fid = getattr(cls, "id", None)
    if not isinstance(fid, str) or not _ID_RE.match(fid):
        raise RegistryError(f"{cls.__name__}: id must be a snake_case string, got {fid!r}")
    if not getattr(cls, "title", None):
        raise RegistryError(f"{cls.__name__}: title is required")
    if not (isinstance(cls.Params, type) and issubclass(cls.Params, TaskParams)):
        raise RegistryError(f"{cls.__name__}: Params must subclass TaskParams")
    with _lock:
        existing = _registry.get(fid)
        if existing is not None and existing is not cls and existing.__qualname__ != cls.__qualname__:
            raise RegistryError(f"feature id {fid!r} used by {existing.__name__} and {cls.__name__}")
        _registry[fid] = cls
    return cls


def unregister(fid: str) -> None:
    with _lock:
        _registry.pop(fid, None)


def get_feature(fid: str) -> type[Task]:
    with _lock:
        try:
            return _registry[fid]
        except KeyError:
            raise RegistryError(f"unknown feature {fid!r}") from None


def all_features() -> list[type[Task]]:
    with _lock:
        return sorted(_registry.values(), key=lambda c: (c.order, c.title))


def discover(package: str = FEATURES_PACKAGE) -> list[type[Task]]:
    """Import every feature module (a broken module is logged and skipped), then list features."""
    pkg = importlib.import_module(package)
    for info in pkgutil.iter_modules(pkg.__path__, prefix=f"{package}."):
        if info.name.rsplit(".", 1)[-1].startswith("_"):
            continue
        try:
            importlib.import_module(info.name)
        except Exception:
            log.exception("feature module %s failed to load", info.name)
    return all_features()
