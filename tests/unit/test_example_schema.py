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
        entry = ToolCallEntry(valid_raw(arguments={}))
        assert entry.arguments == {}
