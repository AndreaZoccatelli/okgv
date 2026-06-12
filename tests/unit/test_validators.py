"""Tests for entry field validators."""

import json

import pytest

from okgv.validators import (
    VALIDATOR_REGISTRY,
    InRange,
    IsType,
    Matches,
    NotEmpty,
    OneOf,
    register,
    validator_from_json,
)


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


ROUND_TRIP_SAMPLES = [
    OneOf("difficulty", {"easy", "medium", "hard"}),
    InRange("score", 0, 100),
    NotEmpty("query"),
    Matches("code", r"[A-Z]{3}-\d+"),
    IsType("arguments", dict),
    IsType("value", (int, float)),
]


class TestValidatorSerde:
    @pytest.mark.parametrize("v", ROUND_TRIP_SAMPLES, ids=lambda v: v.to_json()["type"])
    def test_round_trip(self, v):
        # through actual JSON text, not just the dict
        d = json.loads(json.dumps(v.to_json()))
        assert validator_from_json(d) == v

    def test_every_registered_tag_has_round_trip_sample(self):
        sampled = {type(v).tag for v in ROUND_TRIP_SAMPLES}
        assert set(VALIDATOR_REGISTRY) <= sampled, "new validator registered without a round-trip sample above"

    def test_unknown_tag_fails_loudly_naming_known_tags(self):
        with pytest.raises(ValueError, match="unknown validator type 'one_off'.*one_of"):
            validator_from_json({"type": "one_off", "field": "x", "valid": ["a"]})

    def test_tag_collision_raises(self):
        with pytest.raises(ValueError, match="tag 'one_of' already registered"):

            @register
            class Impostor:
                tag = "one_of"

    def test_reregistering_same_class_is_idempotent(self):
        assert register(OneOf) is OneOf

    def test_equality_is_by_value(self):
        assert OneOf("f", {"a", "b"}) == OneOf("f", {"b", "a"})
        assert OneOf("f", {"a"}) != OneOf("f", {"b"})
        assert NotEmpty("f") != Matches("f", ".*")

    def test_is_type_custom_type_not_serializable(self):
        class Custom:
            pass

        with pytest.raises(ValueError, match=r"cannot serialize custom type\(s\) \['Custom'\]"):
            IsType("x", Custom).to_json()

    def test_is_type_unknown_type_name_rejected(self):
        with pytest.raises(ValueError, match=r"unknown type name\(s\) \['number'\]"):
            validator_from_json({"type": "is_type", "field": "x", "expected": ["number"]})
