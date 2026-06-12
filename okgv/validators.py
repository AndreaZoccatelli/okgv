"""Validators for entry schema fields.

Each validator serves dual purpose:
- .validate(value) enforces the constraint at runtime
- .prompt() returns a human-readable description for agent instructions

Usage in schema:

    from okgv.validators import OneOf, InRange, NotEmpty

    difficulty = OneOf("difficulty", {"easy", "medium", "hard"})
    score = InRange("score", 0, 100)

    class MyEntry:
        def __init__(self, raw: dict):
            self.difficulty = difficulty.validate(raw["difficulty"])
            self.score = score.validate(raw["score"])

    class MySchema:
        entry_class = MyEntry
        validators = [difficulty, score]

The entry-prompt command auto-includes .prompt() output in agent instructions.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable


@runtime_checkable
class Validator(Protocol):
    field: str

    def validate(self, value): ...

    def prompt(self) -> str: ...


class OneOf:
    """Check that value is in a set of allowed values."""

    def __init__(self, field: str, valid: set):
        self.field = field
        self.valid = valid

    def validate(self, value):
        if value not in self.valid:
            raise ValueError(f"{self.field}: must be one of {self.valid}, got '{value}'")
        return value

    def prompt(self) -> str:
        return f"{self.field}: must be one of {sorted(self.valid)}"


class InRange:
    """Check that a numeric value is within [lo, hi]."""

    def __init__(self, field: str, lo: float, hi: float):
        self.field = field
        self.lo = lo
        self.hi = hi

    def validate(self, value):
        if not (self.lo <= value <= self.hi):
            raise ValueError(f"{self.field}: must be between {self.lo} and {self.hi}, got {value}")
        return value

    def prompt(self) -> str:
        return f"{self.field}: number between {self.lo} and {self.hi}"


class NotEmpty:
    """Check that a string is non-empty after stripping whitespace."""

    def __init__(self, field: str):
        self.field = field

    def validate(self, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{self.field}: must be a non-empty string, got '{value}'")
        return value

    def prompt(self) -> str:
        return f"{self.field}: non-empty string"


class IsType:
    """Check that value is an instance of the expected type."""

    _NAMES = {
        dict: "JSON object",
        list: "list",
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
    }

    def __init__(self, field: str, expected: type):
        self.field = field
        self.expected = expected

    def _name(self) -> str:
        return self._NAMES.get(self.expected, self.expected.__name__)

    def validate(self, value):
        # bool is a subclass of int; an integer check should not accept True/False
        wrong_type = not isinstance(value, self.expected) or (self.expected is int and isinstance(value, bool))
        if wrong_type:
            name = self._name()
            article = "an" if name[0].lower() in "aeiou" else "a"
            raise ValueError(f"{self.field}: must be {article} {name}, got {type(value).__name__}")
        return value

    def prompt(self) -> str:
        return f"{self.field}: {self._name()}"


class Matches:
    """Check that a string matches a regex pattern."""

    def __init__(self, field: str, pattern: str):
        self.field = field
        self.pattern = pattern

    def validate(self, value):
        if not re.fullmatch(self.pattern, value):
            raise ValueError(f"{self.field}: must match pattern '{self.pattern}', got '{value}'")
        return value

    def prompt(self) -> str:
        return f"{self.field}: must match pattern '{self.pattern}'"
