"""tools/cut_templates.py: the committed templates are exactly what the crop list produces."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from conftest import PROFILE, ROOT

from ngoalong.core.capture import read_image


def _load() -> ModuleType:
    name = "cut_templates_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "cut_templates.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ct = _load()


def test_committed_templates_are_up_to_date() -> None:
    assert ct.check_all() == []


def test_sources_exist_and_crops_fit() -> None:
    sources, crops = ct.load_crops()
    assert crops
    for s in sources.values():
        assert s.path.is_file(), s.path
    for c in crops:
        img, mask = ct.cut(c, sources)
        assert img.shape[:2] == (c.rect[3], c.rect[2])
        if c.mask:
            assert mask is not None and mask.shape == img.shape[:2] and mask.any()


def test_write_all_is_reproducible(tmp_path: Path) -> None:
    written = ct.write_all(out_dir=tmp_path)
    assert ct.check_all(out_dir=tmp_path) == []
    rel = sorted(p.relative_to(tmp_path).as_posix() for p in written)
    committed = sorted(
        p.relative_to(PROFILE / "templates").as_posix() for p in (PROFILE / "templates").rglob("*.png")
    )
    assert rel == committed  # no stray, hand-made templates in the profile
    a = read_image(tmp_path / "anchor" / "kvuc_label.png")
    assert a.shape == (22, 40, 3)


def test_check_detects_drift(tmp_path: Path) -> None:
    ct.write_all(out_dir=tmp_path)
    (tmp_path / "anchor" / "chat_send.png").unlink()
    assert ct.check_all(out_dir=tmp_path) == ["anchor.chat_send"]
    assert ct.main(["--check", "--out", str(tmp_path)]) == 1


@pytest.mark.parametrize(
    "crops",
    [
        "  - {name: a, source: s, rect: [0, 0, 5, 5]}\n  - {name: a, source: s, rect: [0, 0, 5, 5]}\n",
        "  - {name: a, source: nope, rect: [0, 0, 5, 5]}\n",
        "  - {name: a, source: s, rect: [0, 0, 0, 5]}\n",
        "  - {name: a, source: s, rect: [0, 0, 5]}\n",
        "  - {name: a, source: s, rect: [0, 0, 5, 5], mask: star}\n",
    ],
)
def test_invalid_crop_lists(tmp_path: Path, crops: str) -> None:
    f = tmp_path / "crops.yaml"
    f.write_text(
        "sources:\n  s: {path: \"docs/game screenshots/main-game.png\", origin: [273, 109]}\ncrops:\n" + crops,
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        ct.load_crops(f)
