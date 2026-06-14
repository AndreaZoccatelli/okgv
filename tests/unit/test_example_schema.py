"""Tests for the example function-calling schema (examples/function-calling/config/schema.py)."""

import importlib.util
from pathlib import Path

import pytest

import okgv.core as core
from okgv.core import EntryError

SCHEMA_PATH = Path(__file__).parents[2] / "examples" / "function-calling" / "config" / "schema.py"

spec = importlib.util.spec_from_file_location("example_schema", SCHEMA_PATH)
assert spec is not None and spec.loader is not None
example_schema = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example_schema)

ToolCallEntry = example_schema.ToolCallEntry
ToolCallSchema = example_schema.ToolCallSchema
FunctionSpec = example_schema.FunctionSpec
SPECS = example_schema.SPECS


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


class TestSpecsFromStructure:
    def test_every_structure_topic_has_a_folded_spec(self):
        import json

        from okgv.specs import topic_paths

        structure = json.loads((SCHEMA_PATH.parent / "structure.json").read_text())
        assert set(SPECS) == topic_paths(structure)

    def test_function_names_unique_across_declaring_topics(self):
        # Refinement children inherit their parent's function, so a name repeats
        # down a path; collect only the topics whose own _meta declares it and
        # assert those are distinct.
        import json

        structure = json.loads((SCHEMA_PATH.parent / "structure.json").read_text())
        declared: list[str] = []

        def walk(node: dict) -> None:
            for key, value in node.items():
                if key == "_meta" and isinstance(value, dict) and "function" in value:
                    declared.append(value["function"])
                elif not key.startswith("_") and isinstance(value, dict):
                    walk(value)

        walk(structure)
        assert len(declared) == len(set(declared))


class TestRefinementSplit:
    """The weather/current_conditions split is living documentation of the fold:
    children inherit the parent's function and required args, and narrow units."""

    def _entry(self, **arguments) -> "ToolCallEntry":
        return ToolCallEntry(valid_raw(arguments=arguments))

    def test_specless_inheritance_demands_parent_spec(self):
        # no_unit_stated declares only `forbidden`; function + required location
        # come entirely from the parent fold.
        with pytest.raises(ValueError, match="topic expects 'get_current_weather'"):
            ToolCallSchema.validate_for_topic(
                ToolCallEntry(valid_raw(function="send_message", arguments={"location": "Tokyo"})),
                "weather/current_conditions/no_unit_stated",
            )
        with pytest.raises(ValueError, match=r"missing required keys \['location'\]"):
            ToolCallSchema.validate_for_topic(self._entry(), "weather/current_conditions/no_unit_stated")

    def test_metric_narrows_units_to_celsius(self):
        ToolCallSchema.validate_for_topic(
            self._entry(location="Tokyo", units="celsius"), "weather/current_conditions/metric"
        )
        with pytest.raises(ValueError, match="units: must be one of"):
            ToolCallSchema.validate_for_topic(
                self._entry(location="Tokyo", units="fahrenheit"), "weather/current_conditions/metric"
            )

    def test_no_unit_stated_forbids_units(self):
        ToolCallSchema.validate_for_topic(self._entry(location="Tokyo"), "weather/current_conditions/no_unit_stated")
        with pytest.raises(ValueError, match=r"forbidden keys present \['units'\]"):
            ToolCallSchema.validate_for_topic(
                self._entry(location="Tokyo", units="celsius"), "weather/current_conditions/no_unit_stated"
            )
