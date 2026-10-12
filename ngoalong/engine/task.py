"""``Task``: base class of every feature (docs/06: "Each feature becomes a Task subclass with a pydantic
Params model, so the UI form, validation and saved settings all come from one definition").

A feature module (``ngoalong/features/<name>.py``) defines a ``Params`` model and a ``Task``
subclass decorated with ``@register``. Field ``title``/``description`` texts (Vietnamese) label the
generated UI form; ``json_schema_extra={"widget": "..."}`` selects a custom widget.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from ngoalong.engine.actor import Actor


class TaskParams(BaseModel):
    """Base for feature parameters: validated on assignment; unknown saved keys are ignored."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class NoParams(TaskParams):
    pass


class Task(ABC):
    id: ClassVar[str]  # feature key: settings section, coords file name, registry key
    title: ClassVar[str]  # Vietnamese name shown in the UI
    description: ClassVar[str] = ""
    order: ClassVar[int] = 100  # position in the UI list (lower first)
    Params: ClassVar[type[TaskParams]] = NoParams
    # Screen the feature starts from, shown on its page ("Màn hình bắt đầu"; a Feature's ``entry``).
    start_scene: ClassVar[str | None] = None

    def __init__(self, params: TaskParams | Mapping[str, Any] | None = None) -> None:
        if params is None:
            params = self.Params()
        elif not isinstance(params, TaskParams):
            params = self.Params.model_validate(dict(params))
        elif not isinstance(params, self.Params):
            params = self.Params.model_validate(params.model_dump())
        self.params = params

    @abstractmethod
    def run(self, actor: Actor) -> str | None:
        """Do the work; return a short Vietnamese summary for the log (or ``None``)."""

    @classmethod
    def start_blocker(cls, params: TaskParams, services: Any = None) -> str | None:
        """Why the feature cannot start with ``params`` now (Vietnamese, shown under the form and
        "Bắt đầu" is disabled), or ``None``. Checked by the "Tính năng" page on every change."""
        return None

    def bind_services(self, services: Any) -> Task:
        """Called by the app on every task it starts (UI, routines, multi-window), before the run:
        a feature that keeps app data (e.g. a store under ``data/``) picks it up here."""
        return self

    def on_stop(self, actor: Actor) -> None:
        """Called once after ``run`` ends for any reason (done, stopped or failed)."""
