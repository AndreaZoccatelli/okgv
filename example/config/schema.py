"""Function calling schema for LLM tool-use training data.

Each entry is a user query paired with the correct function call.
Compact entries (~30-50 tokens) designed for fine-tuning tool-use capabilities.

Set OKGV_SCHEMA=config.schema:ToolCallSchema in .env.
"""

import json
from pathlib import Path

from okgv.protocols import PropertyDefinition
from okgv.specs import Spec, build_specs
from okgv.validators import IsType, NotEmpty, OneOf


class FunctionSpec:
    """One topic's effective contract: function name plus argument validators.

    Fed from the folded effective spec (okgv.specs.Spec), so a topic's contract
    is whatever its `_meta` blocks declare from root to leaf. ``required`` and
    ``optional`` map a parameter name to a *list* of validators (a conjunction
    may leave several stacked); ``forbidden`` keys must be absent.
    """

    def __init__(self, function: str, required: dict, optional: dict, forbidden: set):
        self.function = function
        self.required = required  # {param_name: [validators]}
        self.optional = optional  # {param_name: [validators]}
        self.forbidden = forbidden  # {param_name}

    @classmethod
    def from_effective(cls, spec: Spec) -> "FunctionSpec":
        return cls(spec.function, spec.required, spec.optional, spec.forbidden)

    def validate(self, function: str, arguments) -> dict:
        if function != self.function:
            raise ValueError(f"function: topic expects '{self.function}', got '{function}'")
        if not isinstance(arguments, dict):
            raise ValueError(f"arguments: must be a JSON object, got {type(arguments).__name__}")
        keys = set(arguments)
        missing = set(self.required) - keys
        forbidden_present = keys & self.forbidden
        unknown = keys - set(self.required) - set(self.optional) - self.forbidden
        if missing:
            raise ValueError(f"arguments: missing required keys {sorted(missing)}")
        if forbidden_present:
            raise ValueError(f"arguments: forbidden keys present {sorted(forbidden_present)}")
        if unknown:
            raise ValueError(f"arguments: unknown keys {sorted(unknown)}")
        for name, value in arguments.items():
            for validator in self.required.get(name) or self.optional.get(name) or []:
                validator.validate(value)
        return arguments


# Single source of truth: the per-topic contracts now live in structure.json
# `_meta` blocks (implementation_plan.md M7). They are parsed and folded along
# each root-to-leaf path once at import; a malformed validator or contradictory
# fold fails loudly here. Keyed by topic path, this mirrors what session.specs
# builds for the CLI.
_STRUCTURE_PATH = Path(__file__).with_name("structure.json")
SPECS: dict[str, Spec] = build_specs(json.loads(_STRUCTURE_PATH.read_text()))

query = NotEmpty("query")
function = OneOf("function", {spec.function for spec in SPECS.values() if spec.function})
arguments = IsType("arguments", dict)
difficulty = OneOf("difficulty", {"easy", "medium", "hard"})


class ToolCallEntry:
    def __init__(self, raw: dict):
        self.query = query.validate(raw["query"])
        self.function = function.validate(raw["function"])
        self.arguments = arguments.validate(raw["arguments"])
        self.difficulty = difficulty.validate(raw["difficulty"])


class ToolCallSchema:
    entry_class = ToolCallEntry
    validators = [query, function, arguments, difficulty]
    balance_fields = ["difficulty"]
    field_descriptions = {
        "query": "natural user request that implies a function call, 5-25 words",
        "function": "the function name to call, matching the topic's available functions",
        "arguments": "JSON object with the function arguments extracted from the query",
        "difficulty": (
            "how hard it is to identify the correct function and extract arguments",
            {
                "easy": "explicit keywords, all arguments stated directly",
                "medium": "requires inference or has optional arguments",
                "hard": "ambiguous phrasing, implicit arguments, or could map to multiple functions",
            },
        ),
    }

    @staticmethod
    def validate_for_topic(entry: ToolCallEntry, topic: str) -> None:
        # The dataset's whole purpose is verified query-to-function pairs, so a
        # topic with no `function` anywhere on its path (problem 1 alive again)
        # is refused at the last cheap gate rather than accepted as unverifiable
        # training data. Inheritance is automatic: a specless child of a spec'd
        # parent still demands the parent's folded function and arguments.
        spec = SPECS.get(topic)
        if spec is None or spec.function is None:
            raise ValueError(
                f"topic '{topic}' has no function spec on its path; "
                "add _meta to structure.json before submitting entries here"
            )
        FunctionSpec.from_effective(spec).validate(entry.function, entry.arguments)

    @staticmethod
    def metadata(entry: ToolCallEntry) -> dict:
        return {
            "function": entry.function,
            "difficulty": entry.difficulty,
        }

    @staticmethod
    def graph_properties(entry: ToolCallEntry) -> dict:
        return {"query": entry.query}

    @staticmethod
    def vector_properties(entry: ToolCallEntry) -> dict:
        return {
            "query": entry.query,
            "arguments": str(entry.arguments),
        }

    @staticmethod
    def embedding_text(entry: ToolCallEntry) -> str:
        return entry.query

    @staticmethod
    def vector_property_definitions() -> list[PropertyDefinition]:
        return [
            PropertyDefinition(name="function", data_type="text"),
            PropertyDefinition(name="difficulty", data_type="text"),
            PropertyDefinition(name="query", data_type="text"),
            PropertyDefinition(name="arguments", data_type="text"),
        ]
