"""Feature forms generated from a feature's pydantic ``Params`` model.

Field type -> editor: ``bool`` checkbox, ``int``/``float`` spin box (bounds from ``ge``/``le``/
``gt``/``lt``), ``str`` line edit, ``Enum``/``Literal`` combo box (an enum member's ``label`` is
shown when it has one). ``json_schema_extra={"widget": "<name>"}`` picks an editor registered with
``@register_widget("<name>")`` (e.g. AutoClick's ``canvas_points``). Anything else falls back to a
JSON line edit. Labels come from the field ``title``; ``description`` is shown under the editor.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any, Literal, get_args, get_origin

import annotated_types as at
from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout, QWidget,
)

INT_MIN, INT_MAX = -(2**31) + 1, 2**31 - 1


class FieldEditor(QObject):
    """One field's editor: ``widget``, ``get()``, ``set(value)``, ``changed`` signal."""

    changed = Signal()

    def __init__(self, name: str, info: FieldInfo, services: Any = None) -> None:
        super().__init__()
        self.name = name
        self.info = info
        self.services = services
        self.widget: QWidget = QWidget()
        self.form: ParamsForm | None = None  # set by the form holding this editor

    def get(self) -> Any:
        raise NotImplementedError

    def set(self, value: Any) -> None:
        raise NotImplementedError


EditorFactory = Callable[[str, FieldInfo, Any], FieldEditor]
_widgets: dict[str, EditorFactory] = {}


def register_widget(name: str) -> Callable[[EditorFactory], EditorFactory]:
    def deco(factory: EditorFactory) -> EditorFactory:
        _widgets[name] = factory
        return factory

    return deco


def _load_widget_modules() -> None:
    import ngoalong.ui.widgets as pkg

    for info in pkgutil.iter_modules(pkg.__path__, prefix="ngoalong.ui.widgets."):
        importlib.import_module(info.name)


def _bounds(info: FieldInfo, integer: bool) -> tuple[float | None, float | None]:
    lo: float | None = None
    hi: float | None = None
    step = 1 if integer else 0.0
    for m in info.metadata:
        if isinstance(m, at.Ge):
            lo = m.ge  # type: ignore[assignment]
        elif isinstance(m, at.Gt):
            lo = m.gt + step  # type: ignore[operator]
        elif isinstance(m, at.Le):
            hi = m.le  # type: ignore[assignment]
        elif isinstance(m, at.Lt):
            hi = m.lt - step  # type: ignore[operator]
    return lo, hi


class BoolEditor(FieldEditor):
    def __init__(self, name: str, info: FieldInfo, services: Any = None) -> None:
        super().__init__(name, info, services)
        self.widget = cb = QCheckBox()
        cb.toggled.connect(lambda _v: self.changed.emit())

    def get(self) -> bool:
        return self.widget.isChecked()  # type: ignore[attr-defined]

    def set(self, value: Any) -> None:
        self.widget.setChecked(bool(value))  # type: ignore[attr-defined]


class IntEditor(FieldEditor):
    def __init__(self, name: str, info: FieldInfo, services: Any = None) -> None:
        super().__init__(name, info, services)
        lo, hi = _bounds(info, integer=True)
        sb = QSpinBox()
        sb.setRange(int(max(INT_MIN, lo if lo is not None else INT_MIN)),
                    int(min(INT_MAX, hi if hi is not None else INT_MAX)))
        sb.setGroupSeparatorShown(True)
        sb.valueChanged.connect(lambda _v: self.changed.emit())
        self.widget = sb

    def get(self) -> int:
        return int(self.widget.value())  # type: ignore[attr-defined]

    def set(self, value: Any) -> None:
        self.widget.setValue(int(value))  # type: ignore[attr-defined]


class FloatEditor(FieldEditor):
    def __init__(self, name: str, info: FieldInfo, services: Any = None) -> None:
        super().__init__(name, info, services)
        lo, hi = _bounds(info, integer=False)
        sb = QDoubleSpinBox()
        sb.setDecimals(2)
        sb.setSingleStep(0.5)
        sb.setRange(lo if lo is not None else -1e9, hi if hi is not None else 1e9)
        sb.valueChanged.connect(lambda _v: self.changed.emit())
        self.widget = sb

    def get(self) -> float:
        return float(self.widget.value())  # type: ignore[attr-defined]

    def set(self, value: Any) -> None:
        self.widget.setValue(float(value))  # type: ignore[attr-defined]


class StrEditor(FieldEditor):
    def __init__(self, name: str, info: FieldInfo, services: Any = None) -> None:
        super().__init__(name, info, services)
        self.widget = le = QLineEdit()
        le.textChanged.connect(lambda _t: self.changed.emit())

    def get(self) -> str:
        return self.widget.text()  # type: ignore[attr-defined]

    def set(self, value: Any) -> None:
        self.widget.setText("" if value is None else str(value))  # type: ignore[attr-defined]


class ChoiceEditor(FieldEditor):
    """Combo box over ``(value, label)`` choices."""

    def __init__(self, name: str, info: FieldInfo, services: Any, choices: list[tuple[Any, str]]) -> None:
        super().__init__(name, info, services)
        self.choices = choices
        self.widget = combo = QComboBox()
        for _value, label in choices:
            combo.addItem(label)
        combo.currentIndexChanged.connect(lambda _i: self.changed.emit())

    def get(self) -> Any:
        i = self.widget.currentIndex()  # type: ignore[attr-defined]
        return self.choices[max(0, i)][0]

    def set(self, value: Any) -> None:
        for i, (v, _label) in enumerate(self.choices):
            if v == value or (isinstance(v, Enum) and v.value == value):
                self.widget.setCurrentIndex(i)  # type: ignore[attr-defined]
                return


class JsonEditor(FieldEditor):
    """Fallback for types without an editor: the value as JSON text."""

    def __init__(self, name: str, info: FieldInfo, services: Any = None) -> None:
        super().__init__(name, info, services)
        self.widget = le = QLineEdit()
        le.textChanged.connect(lambda _t: self.changed.emit())

    def get(self) -> Any:
        text = self.widget.text().strip()  # type: ignore[attr-defined]
        try:
            return json.loads(text) if text else None
        except json.JSONDecodeError:
            return text

    def set(self, value: Any) -> None:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        elif isinstance(value, list):
            value = [v.model_dump(mode="json") if isinstance(v, BaseModel) else v for v in value]
        self.widget.setText(json.dumps(value, ensure_ascii=False))  # type: ignore[attr-defined]


def editor_for(name: str, info: FieldInfo, services: Any = None) -> FieldEditor:
    extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
    hint = extra.get("widget")
    if isinstance(hint, str):
        if hint not in _widgets:
            _load_widget_modules()
        if hint in _widgets:
            return _widgets[hint](name, info, services)
    ann = info.annotation
    if ann is bool:
        return BoolEditor(name, info, services)
    if isinstance(ann, type) and issubclass(ann, Enum):
        return ChoiceEditor(name, info, services, [(m, str(getattr(m, "label", m.value))) for m in ann])
    if get_origin(ann) is Literal:
        return ChoiceEditor(name, info, services, [(v, str(v)) for v in get_args(ann)])
    if ann is int:
        return IntEditor(name, info, services)
    if ann is float:
        return FloatEditor(name, info, services)
    if ann is str:
        return StrEditor(name, info, services)
    return JsonEditor(name, info, services)


def _num(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


# pydantic error type -> Vietnamese message ({...} filled from the error's ``ctx``).
_MESSAGES_VI: dict[str, str] = {
    "missing": "chưa nhập",
    "int_parsing": "phải là số nguyên",
    "int_type": "phải là số nguyên",
    "int_from_float": "phải là số nguyên (không có phần thập phân)",
    "float_parsing": "phải là số",
    "float_type": "phải là số",
    "finite_number": "phải là số hữu hạn",
    "bool_parsing": "phải là Có hoặc Không",
    "bool_type": "phải là Có hoặc Không",
    "string_type": "phải là chữ",
    "greater_than_equal": "phải lớn hơn hoặc bằng {ge}",
    "greater_than": "phải lớn hơn {gt}",
    "less_than_equal": "phải nhỏ hơn hoặc bằng {le}",
    "less_than": "phải nhỏ hơn {lt}",
    "multiple_of": "phải là bội số của {multiple_of}",
    "string_too_short": "phải có ít nhất {min_length} ký tự",
    "string_too_long": "chỉ được tối đa {max_length} ký tự",
    "too_short": "phải có ít nhất {min_length} mục",
    "too_long": "chỉ được tối đa {max_length} mục",
    "literal_error": "phải là một trong: {expected}",
    "enum": "phải là một trong: {expected}",
    "list_type": "phải là danh sách",
    "dict_type": "sai định dạng",
    "model_type": "sai định dạng",
    "model_attributes_type": "sai định dạng",
    "json_invalid": "sai định dạng",
    "extra_forbidden": "thông số không được hỗ trợ",
}


def validation_message_vi(err: Mapping[str, Any]) -> str:
    """Vietnamese text for one pydantic error (``ValidationError.errors()`` item)."""
    kind = str(err.get("type", ""))
    ctx = err.get("ctx") or {}
    if kind in ("value_error", "assertion_error"):
        cause = ctx.get("error")
        text = str(cause) if cause is not None else str(err.get("msg", ""))
        for prefix in ("Value error, ", "Assertion failed, "):
            text = text.removeprefix(prefix)
        return text or "giá trị không hợp lệ"
    template = _MESSAGES_VI.get(kind)
    if template is None:
        return "giá trị không hợp lệ"
    try:
        return template.format(**{k: _num(v) for k, v in ctx.items()})
    except (KeyError, IndexError, ValueError):
        return template.split(" {")[0]


def format_validation_error(exc: ValidationError, model: type[BaseModel]) -> str:
    """All errors of ``exc`` in Vietnamese: ``"<field title>: <message>"`` joined by ``"; "``.

    A model-level validator (empty ``loc``) gives the message alone; a nested location adds the
    item number, e.g. ``"Điểm click (mục 2): ..."``.
    """
    parts = []
    for err in exc.errors():
        loc = tuple(err.get("loc") or ())
        msg = validation_message_vi(err)
        if not loc:
            parts.append(msg)
            continue
        field = model.model_fields.get(str(loc[0]))
        label = field.title if field and field.title else str(loc[0])
        items = [str(p + 1) for p in loc[1:] if isinstance(p, int)]
        if items:
            label += f" (mục {', '.join(items)})"
        parts.append(f"{label}: {msg}")
    return "; ".join(parts)


class ParamsForm(QWidget):
    """Editors for every field of ``model``; ``value()`` validates and returns a model instance."""

    changed = Signal()

    def __init__(self, model: type[BaseModel], value: BaseModel | None = None, services: Any = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self.editors: dict[str, FieldEditor] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self._layout = form
        self._rows: dict[str, QWidget] = {}
        outer.addLayout(form)
        outer.addStretch(1)
        for name, info in model.model_fields.items():
            ed = editor_for(name, info, services)
            ed.form = self
            self.editors[name] = ed
            ed.changed.connect(self.changed.emit)
            label = QLabel(info.title or name)
            if info.description:
                label.setToolTip(info.description)
                ed.widget.setToolTip(info.description)
                box = QWidget()
                lay = QVBoxLayout(box)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.addWidget(ed.widget)
                hint = QLabel(info.description)
                hint.setWordWrap(True)
                hint.setStyleSheet("color: gray; font-size: 11px;")
                lay.addWidget(hint)
                form.addRow(label, box)
                self._rows[name] = box
            else:
                form.addRow(label, ed.widget)
        self.set_value(value if value is not None else model())

    def set_value(self, value: BaseModel) -> None:
        for name, ed in self.editors.items():
            blocked = ed.blockSignals(True)
            try:
                ed.set(getattr(value, name))
            finally:
                ed.blockSignals(blocked)

    def set_others_enabled(self, keep: str, enabled: bool) -> None:
        """Enable or disable every editor except ``keep`` (an editor that gates the rest)."""
        for name, ed in self.editors.items():
            if name != keep:
                ed.widget.setEnabled(enabled)
                label = self._layout.labelForField(self._rows.get(name, ed.widget))
                if label is not None:
                    label.setEnabled(enabled)

    def raw(self) -> dict[str, Any]:
        return {name: ed.get() for name, ed in self.editors.items()}

    def value(self) -> BaseModel:
        """Validated params; raises ``pydantic.ValidationError``."""
        return self.model.model_validate(self.raw())
