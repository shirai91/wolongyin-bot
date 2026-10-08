"""Tab registry: extra main-window tabs, one module per tab under ``ngoalong/ui/tabs/``.

A tab module decorates a factory ``(services) -> QWidget`` with ``@register_tab(id, title, order)``.
``discover_tabs()`` imports every module of this package, so adding a tab means adding one file.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

TabFactory = Callable[[Any], QWidget]


@dataclass(frozen=True, slots=True)
class TabSpec:
    id: str
    title: str
    order: int
    factory: TabFactory


_tabs: dict[str, TabSpec] = {}


def register_tab(tab_id: str, title: str, order: int = 100) -> Callable[[TabFactory], TabFactory]:
    def deco(factory: TabFactory) -> TabFactory:
        _tabs[tab_id] = TabSpec(tab_id, title, order, factory)
        return factory

    return deco


def all_tabs() -> list[TabSpec]:
    return sorted(_tabs.values(), key=lambda t: (t.order, t.title))


def discover_tabs() -> list[TabSpec]:
    for info in pkgutil.iter_modules(__path__, prefix=f"{__name__}."):
        if info.name.rsplit(".", 1)[-1].startswith("_"):
            continue
        try:
            importlib.import_module(info.name)
        except Exception:
            log.exception("tab module %s failed to load", info.name)
    return all_tabs()
