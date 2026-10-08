"""``check_list`` editor: a ``list[SomeEnum]`` field as a grid of check boxes plus "Chọn hết".

Members are shown with their ``label`` (else the value) and grouped under their ``group`` (if the
enum has one), one row per group, wrapping after ``COLUMNS`` boxes. ``get()`` returns the ticked
members' values in enum order. Used by Tri Kỷ's purple / orange portrait lists (v1.8.0 had the
same check boxes and a "Chọn hết" box per list).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, get_args, get_origin

from pydantic.fields import FieldInfo
from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QVBoxLayout, QWidget

from ngoalong.ui.forms import FieldEditor, register_widget

COLUMNS = 3


def enum_of(annotation: Any) -> type[Enum]:
    """The enum of a ``list[E]`` / ``set[E]`` / ``tuple[E, ...]`` annotation."""
    if get_origin(annotation) in (list, set, frozenset, tuple):
        args = [a for a in get_args(annotation) if a is not Ellipsis]
        if len(args) == 1 and isinstance(args[0], type) and issubclass(args[0], Enum):
            return args[0]
    raise TypeError(f"check_list needs a list of an Enum, got {annotation!r}")


class CheckListEditor(FieldEditor):
    def __init__(self, name: str, info: FieldInfo, services: Any = None) -> None:
        super().__init__(name, info, services)
        self.enum = enum_of(info.annotation)
        self.boxes: dict[Enum, QCheckBox] = {}
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        self.all_box = QCheckBox("Chọn hết")
        self.all_box.toggled.connect(self._toggle_all)
        lay.addWidget(self.all_box)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(grid)
        row, col, group = -1, COLUMNS, object()
        for member in self.enum:
            g = getattr(member, "group", "")
            if g != group or col >= COLUMNS:
                row += 1
                col = 0
                if g != group:
                    grid.addWidget(QLabel(f"{g}:" if g else ""), row, 0)
                    group = g
            box = QCheckBox(str(getattr(member, "label", member.value)))
            box.toggled.connect(self._member_toggled)
            grid.addWidget(box, row, col + 1)
            self.boxes[member] = box
            col += 1
        self.widget = root

    # --- FieldEditor ----------------------------------------------------------------------------
    def get(self) -> list[Any]:
        return [m.value for m, box in self.boxes.items() if box.isChecked()]

    def set(self, value: Any) -> None:
        wanted = {v.value if isinstance(v, Enum) else v for v in (value or [])}
        for m, box in self.boxes.items():
            blocked = box.blockSignals(True)
            try:
                box.setChecked(m.value in wanted)
            finally:
                box.blockSignals(blocked)
        self._sync_all()

    # --- internals ------------------------------------------------------------------------------
    def _toggle_all(self, checked: bool) -> None:
        for box in self.boxes.values():
            blocked = box.blockSignals(True)
            try:
                box.setChecked(checked)
            finally:
                box.blockSignals(blocked)
        self.changed.emit()

    def _member_toggled(self, _checked: bool) -> None:
        self._sync_all()
        self.changed.emit()

    def _sync_all(self) -> None:
        blocked = self.all_box.blockSignals(True)
        try:
            self.all_box.setChecked(bool(self.boxes) and all(b.isChecked() for b in self.boxes.values()))
        finally:
            self.all_box.blockSignals(blocked)


@register_widget("check_list")
def _factory(name: str, info: FieldInfo, services: Any = None) -> FieldEditor:
    return CheckListEditor(name, info, services)
