"""Tests for entry field validators."""

import pytest

from okgv.validators import IsType


class TestIsType:
    def test_matching_type_passes(self):
        v = IsType("arguments", dict)
        assert v.validate({"location": "Tokyo"}) == {"location": "Tokyo"}

    def test_wrong_type_rejected(self):
        v = IsType("arguments", dict)
        with pytest.raises(ValueError, match="arguments: must be a JSON object, got str"):
            v.validate('{"location": "Tokyo"}')

    def test_bool_not_accepted_as_int(self):
        v = IsType("days", int)
        assert v.validate(3) == 3
        with pytest.raises(ValueError, match="days: must be an integer, got bool"):
            v.validate(True)

    def test_prompt_uses_readable_type_name(self):
        assert IsType("arguments", dict).prompt() == "arguments: JSON object"
        assert IsType("days", int).prompt() == "days: integer"

    def test_tuple_of_types_accepts_any_member(self):
        v = IsType("value", (int, float))
        assert v.validate(10) == 10
        assert v.validate(2.5) == 2.5
        with pytest.raises(ValueError, match="value: must be an integer or number, got str"):
            v.validate("10")
        with pytest.raises(ValueError, match="got bool"):
            v.validate(True)

    def test_bool_accepted_when_explicitly_expected(self):
        v = IsType("unread_only", bool)
        assert v.validate(True) is True

    def test_unknown_type_falls_back_to_class_name(self):
        class Custom:
            pass

        v = IsType("x", Custom)
        assert v.prompt() == "x: Custom"
        with pytest.raises(ValueError, match="x: must be a Custom"):
            v.validate(1)
