"""Tests for entry field validators."""

import json

import pytest

from okgv.validators import (
    NEVER,
    VALIDATOR_REGISTRY,
    InRange,
    IsType,
    Items,
    Matches,
    NotEmpty,
    OneOf,
    narrow,
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
    Items("attendees", NotEmpty("attendees"), min_len=1, max_len=5),
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


class TestItems:
    def test_valid_list_passes(self):
        v = Items("attendees", NotEmpty("attendees"))
        assert v.validate(["ada", "grace"]) == ["ada", "grace"]

    def test_non_list_rejected(self):
        v = Items("attendees", NotEmpty("attendees"))
        with pytest.raises(ValueError, match="attendees: must be a list, got str"):
            v.validate("ada")

    def test_bad_element_rejected_with_index(self):
        v = Items("data", InRange("data", 0, 100))
        with pytest.raises(ValueError, match=r"data\[1\]: data: must be between 0 and 100"):
            v.validate([50, 200])

    def test_length_bounds(self):
        v = Items("options", NotEmpty("options"), min_len=3, max_len=5)
        v.validate(["a", "b", "c"])
        with pytest.raises(ValueError, match="at least 3 items, got 2"):
            v.validate(["a", "b"])
        with pytest.raises(ValueError, match="at most 5 items, got 6"):
            v.validate(["a", "b", "c", "d", "e", "f"])

    def test_prompt_composes(self):
        v = Items("data", InRange("data", 0, 100), min_len=1, max_len=10)
        assert v.prompt() == "data: list of 1 to 10 items, each: number between 0 and 100"
        bare = Items("data", InRange("data", 0, 100))
        assert bare.prompt() == "data: list, each: number between 0 and 100"


class TestNarrow:
    def test_oneof_oneof_intersection(self):
        a = OneOf("d", {"easy", "medium", "hard"})
        b = OneOf("d", {"hard", "extreme"})
        assert narrow(a, b) == OneOf("d", {"hard"})

    def test_oneof_oneof_empty_intersection_is_never(self):
        assert narrow(OneOf("d", {"easy"}), OneOf("d", {"hard"})) is NEVER

    def test_oneof_filtered_by_inrange(self):
        assert narrow(OneOf("n", {1, 5, 50}), InRange("n", 0, 10)) == OneOf("n", {1, 5})

    def test_oneof_filtered_by_istype(self):
        assert narrow(OneOf("x", {1, "a"}), IsType("x", str)) == OneOf("x", {"a"})

    def test_inrange_inrange_tighter_bounds(self):
        assert narrow(InRange("s", 0, 100), InRange("s", 50, 200)) == InRange("s", 50, 100)

    def test_inrange_inrange_inverted_is_never(self):
        assert narrow(InRange("s", 0, 10), InRange("s", 20, 30)) is NEVER

    def test_notempty_notempty(self):
        assert narrow(NotEmpty("q"), NotEmpty("q")) == NotEmpty("q")

    def test_notempty_istype_str_is_notempty(self):
        assert narrow(NotEmpty("q"), IsType("q", str)) == NotEmpty("q")

    def test_notempty_istype_int_is_never(self):
        assert narrow(NotEmpty("q"), IsType("q", int)) is NEVER

    def test_notempty_inrange_is_never(self):
        assert narrow(NotEmpty("q"), InRange("q", 0, 1)) is NEVER

    def test_istype_istype_intersection(self):
        assert narrow(IsType("v", (int, float)), IsType("v", int)) == IsType("v", int)
        assert narrow(IsType("v", str), IsType("v", int)) is NEVER

    def test_matches_same_pattern_simplifies(self):
        assert narrow(Matches("c", r"\d+"), Matches("c", r"\d+")) == Matches("c", r"\d+")

    def test_matches_different_patterns_unknown(self):
        assert narrow(Matches("c", r"\d+"), Matches("c", r"[a-z]+")) is None

    def test_matches_notempty_unknown(self):
        assert narrow(Matches("c", r".*"), NotEmpty("c")) is None

    def test_field_mismatch_raises(self):
        with pytest.raises(ValueError, match="different fields: 'a' vs 'b'"):
            narrow(OneOf("a", {1}), OneOf("b", {1}))

    def test_opaque_validator_pair_is_unknown(self):
        class Opaque:
            field = "x"

            def validate(self, value):
                return value

        assert narrow(Opaque(), Opaque()) is None

    def test_oneof_simplifies_against_opaque_validator(self):
        class NoVowels:
            field = "w"

            def validate(self, value):
                if any(c in "aeiou" for c in value):
                    raise ValueError("vowel")
                return value

        assert narrow(OneOf("w", {"sky", "sea"}), NoVowels()) == OneOf("w", {"sky"})

    def test_items_conjunction_stays_stacked(self):
        a = Items("data", InRange("data", 0, 10))
        b = Items("data", InRange("data", 5, 20))
        assert narrow(a, b) is None
