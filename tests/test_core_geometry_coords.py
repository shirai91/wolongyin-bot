"""Geometry types and CoordMap (profiles/ngoalongviet/coords/*.yaml)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ngoalong.core.coords import DEFAULT_COORDS_DIR, CoordError, CoordMap
from ngoalong.core.geometry import Point, Rect


# --- geometry -------------------------------------------------------------------------------------
def test_rect_basics() -> None:
    r = Rect(10, 20, 30, 40)
    assert (r.right, r.bottom) == (40, 60)
    assert r.center == Point(25, 40)
    assert r.contains(Point(10, 20)) and not r.contains(Point(40, 20))
    assert r.offset(5, -5) == Rect(15, 15, 30, 40)
    assert r.scaled(2.0) == Rect(20, 40, 60, 80)


def test_rect_intersect() -> None:
    assert Rect(0, 0, 10, 10).intersect(Rect(5, 5, 10, 10)) == Rect(5, 5, 5, 5)
    assert Rect(0, 0, 10, 10).intersect(Rect(20, 20, 5, 5)).is_empty()
    with pytest.raises(ValueError):
        Rect(0, 0, -1, 5)


def test_point_arithmetic() -> None:
    assert Point(1, 2) + Point(3, 4) == Point(4, 6)
    assert Point(5, 5) - Point(1, 2) == Point(4, 3)


# --- CoordMap -------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def coords() -> CoordMap:
    return CoordMap.load(DEFAULT_COORDS_DIR)


def test_loads_every_feature_file(coords: CoordMap) -> None:
    files = sorted(p.stem for p in DEFAULT_COORDS_DIR.glob("*.yaml"))
    assert sorted(coords.features) == files
    assert coords.source_of("thap") == DEFAULT_COORDS_DIR / "thap.yaml"


def test_point_is_origin_plus_offset_times_scale(coords: CoordMap) -> None:
    assert coords.offset("thap.open_tower") == (525, 320)
    assert coords.point("thap.open_tower") == Point(525, 320)
    coords_z = CoordMap.load(DEFAULT_COORDS_DIR, origin=Point(180, 110), scale=1.25)
    assert coords_z.point("thap.open_tower") == Point(180 + 656, 110 + 400)
    # explicit origin/scale override the stored canvas
    assert coords.point("common.quantity_box", origin=Point(10, 20), scale=0.5) == Point(222, 164)


def test_set_canvas(coords: CoordMap) -> None:
    m = CoordMap(dict(a={"p": [100, 50]}))
    m.set_canvas(Point(270, 106), 1.0)
    assert m.point("a.p") == Point(370, 156)
    assert m.to_canvas(Point(370, 156)) == (100.0, 50.0)
    with pytest.raises(ValueError):
        m.set_canvas(Point(0, 0), 0)


def test_list_items_and_scalars(coords: CoordMap) -> None:
    assert coords.offset("nang_cap_cong_trinh.thanh_chinh_buildings.0") == (160, 300)
    assert len(coords.offsets("nang_cap_cong_trinh.thanh_chinh_buildings")) == 18
    assert coords.points("quay_ruong.per_spin")[1] == Point(440, 360)
    assert coords.value("thap.floor_list_y0") == 215
    assert coords.value("tri_ky.delay_ms") == 200
    assert "thap.open_tower" in coords
    assert "thap.nope" not in coords


def test_bad_keys_and_shapes(coords: CoordMap) -> None:
    with pytest.raises(CoordError, match="thap.nope"):
        coords.offset("thap.nope")
    with pytest.raises(CoordError):
        coords.offset("thap.floor_list_x")  # scalar, not a pair
    with pytest.raises(CoordError):
        coords.value("thap.open_tower")  # pair, not a scalar
    with pytest.raises(CoordError):
        coords.offset("nang_cap_cong_trinh.hai_cang_buildings.99")


def test_point_keys_cover_all_offsets(coords: CoordMap) -> None:
    keys = list(coords.point_keys("quay_ruong"))
    assert keys == ["quay_ruong.per_spin.0", "quay_ruong.per_spin.1", "quay_ruong.per_spin.2"]
    for k in coords.point_keys():
        coords.point(k)  # every listed key resolves


def test_duplicate_feature_key_rejected(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("feat:\n  x: [1, 2]\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("feat:\n  y: [3, 4]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="a.yaml.*b.yaml"):
        CoordMap.load(tmp_path)


def test_new_feature_file_is_picked_up(tmp_path: Path) -> None:
    (tmp_path / "one.yaml").write_text("one:\n  btn: [1, 2]\n", encoding="utf-8")
    (tmp_path / "two.yaml").write_text("two:\n  btn: [3, 4]   # Vietnamese ok: Đồng ý\n", encoding="utf-8")
    m = CoordMap.load(tmp_path)
    assert m.offset("two.btn") == (3, 4)


def test_non_mapping_file_rejected(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bad.yaml"):
        CoordMap.load(tmp_path)
