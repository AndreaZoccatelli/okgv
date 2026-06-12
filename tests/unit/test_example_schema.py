"""Tests for the example function-calling schema (example/config/schema.py)."""

import importlib.util
from pathlib import Path

import pytest

import okgv.core as core
from okgv.core import EntryError

SCHEMA_PATH = Path(__file__).parents[2] / "example" / "config" / "schema.py"

spec = importlib.util.spec_from_file_location("example_schema", SCHEMA_PATH)
assert spec is not None and spec.loader is not None
example_schema = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example_schema)

ToolCallEntry = example_schema.ToolCallEntry
ToolCallSchema = example_schema.ToolCallSchema
FunctionSpec = example_schema.FunctionSpec
FUNCTIONS = example_schema.FUNCTIONS


def valid_raw(**overrides) -> dict:
    raw = {
        "query": "What's the weather in Tokyo?",
        "function": "get_current_weather",
        "arguments": {"location": "Tokyo"},
        "difficulty": "easy",
    }
    raw.update(overrides)
    return raw


class TestToolCallEntry:
    def test_valid_entry_with_dict_arguments(self):
        entry = ToolCallEntry(valid_raw())
        assert entry.arguments == {"location": "Tokyo"}
        assert entry.difficulty == "easy"

    def test_string_arguments_rejected(self):
        with pytest.raises(ValueError, match="arguments: must be a JSON object"):
            ToolCallEntry(valid_raw(arguments='{"location": "Tokyo"}'))

    def test_missing_difficulty_raises_entry_error(self):
        raw = valid_raw()
        del raw["difficulty"]
        with pytest.raises(EntryError, match="difficulty"):
            core.build_entry(ToolCallSchema, raw)

    def test_invalid_difficulty_rejected(self):
        with pytest.raises(ValueError, match="difficulty"):
            ToolCallEntry(valid_raw(difficulty="extreme"))

    def test_empty_arguments_object_allowed(self):
        entry = ToolCallEntry(valid_raw(function="read_messages", arguments={}))
        assert entry.arguments == {}

    def test_invented_function_rejected_at_construction(self):
        with pytest.raises(ValueError, match="function: must be one of"):
            ToolCallEntry(valid_raw(function="launch_missiles"))


class TestValidateForTopic:
    def entry(self, **overrides) -> "ToolCallEntry":
        return ToolCallEntry(valid_raw(**overrides))

    def test_valid_entry_passes_for_its_topic(self):
        ToolCallSchema.validate_for_topic(self.entry(), "weather/current_conditions")

    def test_wrong_function_for_topic_rejected(self):
        with pytest.raises(ValueError, match="topic expects 'send_message'"):
            ToolCallSchema.validate_for_topic(self.entry(), "messaging/send_message")

    def test_missing_required_argument_rejected(self):
        e = self.entry(function="get_forecast", arguments={"location": "Tokyo"})
        with pytest.raises(ValueError, match=r"missing required keys \['days'\]"):
            ToolCallSchema.validate_for_topic(e, "weather/forecast")

    def test_unknown_argument_key_rejected(self):
        e = self.entry(arguments={"location": "Tokyo", "zoom": 3})
        with pytest.raises(ValueError, match=r"unknown keys \['zoom'\]"):
            ToolCallSchema.validate_for_topic(e, "weather/current_conditions")

    def test_optional_argument_accepted(self):
        e = self.entry(arguments={"location": "Tokyo", "units": "celsius"})
        ToolCallSchema.validate_for_topic(e, "weather/current_conditions")

    def test_invalid_optional_value_rejected(self):
        e = self.entry(arguments={"location": "Tokyo", "units": "kelvin"})
        with pytest.raises(ValueError, match="units: must be one of"):
            ToolCallSchema.validate_for_topic(e, "weather/current_conditions")

    def test_wrong_argument_type_rejected(self):
        e = self.entry(function="get_forecast", arguments={"location": "Tokyo", "days": "five"})
        with pytest.raises(ValueError, match="days: must be an integer"):
            ToolCallSchema.validate_for_topic(e, "weather/forecast")

    def test_topic_without_spec_rejected(self):
        with pytest.raises(ValueError, match="has no function spec"):
            ToolCallSchema.validate_for_topic(self.entry(), "weather")

    def test_number_argument_accepts_int_and_float(self):
        for value in (10, 2.5):
            e = self.entry(
                function="convert_units",
                arguments={"value": value, "from_unit": "km", "to_unit": "miles"},
            )
            ToolCallSchema.validate_for_topic(e, "math/unit_conversion")


class TestFunctionsRegistry:
    def test_registry_covers_every_structure_leaf(self):
        import json

        structure = json.loads((SCHEMA_PATH.parent / "structure.json").read_text())
        leaves = {f"{category}/{leaf}" for category, children in structure.items() for leaf in children}
        assert leaves == set(FUNCTIONS)

    def test_function_names_unique_across_topics(self):
        names = [spec.function for spec in FUNCTIONS.values()]
        assert len(names) == len(set(names))
