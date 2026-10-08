"""Validation errors shown on Start are Vietnamese only (no pydantic English text)."""

from __future__ import annotations

import re

import pytest
from pydantic import BaseModel, Field, ValidationError

from ngoalong.features.chua_cong import ChuaCongParams
from ngoalong.features.cong_trinh import CongTrinhParams, Mode
from ngoalong.features.thap_thi_luyen import ThapThiLuyenParams
from ngoalong.ui.forms import format_validation_error

ENGLISH = re.compile(r"Input should|Value error|valid integer|greater than|less than|Field required|\?:")


def _message(model: type[BaseModel], data: dict) -> str:
    with pytest.raises(ValidationError) as info:
        model.model_validate(data)
    text = format_validation_error(info.value, model)
    assert not ENGLISH.search(text), text
    return text


@pytest.mark.parametrize("bad", ["", "abc", "1.5x"])
def test_unparsable_number(bad: str) -> None:
    assert _message(ChuaCongParams, {"tokens": bad}) == "Số Thí luyện lệnh sử dụng: phải là số nguyên"


def test_bounds() -> None:
    assert _message(ChuaCongParams, {"floor": -5}) == "Tầng tháp: phải lớn hơn hoặc bằng 1"
    assert _message(ChuaCongParams, {"floor": 1000}) == "Tầng tháp: phải nhỏ hơn hoặc bằng 999"
    text = _message(ThapThiLuyenParams, {"battle_timeout_s": 1})
    assert text == "Thời gian tối đa mỗi trận (giây): phải lớn hơn hoặc bằng 10"
    assert _message(ThapThiLuyenParams, {"battle_timeout_s": "x"}).endswith(": phải là số")


def test_custom_validators_without_value_error_prefix() -> None:
    text = _message(CongTrinhParams, {"buildings": "abc"})
    assert text.startswith("Công trình: 'abc' không phải số")
    text = _message(ThapThiLuyenParams, {"start_floor": 8, "end_floor": 5})
    assert text == "Tầng kết thúc: phải lớn hơn hoặc bằng tầng bắt đầu (8)"


def test_model_level_validator_has_no_question_mark_label() -> None:
    text = _message(CongTrinhParams, {"mode": Mode.HAI_CANG, "buildings": "7"})
    assert text.startswith("Hải cảng chỉ có 6 công trình")


def test_several_errors_and_nested_items() -> None:
    class Item(BaseModel):
        x: int = Field(0, ge=0)

    class M(BaseModel):
        a: int = Field(1, ge=1, title="Số A")
        items: list[Item] = Field(default_factory=list, title="Điểm")

    text = _message(M, {"a": 0, "items": [{"x": 1}, {"x": -1}]})
    assert text == "Số A: phải lớn hơn hoặc bằng 1; Điểm (mục 2): phải lớn hơn hoặc bằng 0"
