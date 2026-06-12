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

Validators also serialize: .to_json() emits a tagged dict, validator_from_json()
rebuilds it through VALIDATOR_REGISTRY. Custom validators participate via the
@register decorator (a unique `tag` class attribute, to_json, from_json);
unknown tags and tag collisions fail loudly.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

VALIDATOR_REGISTRY: dict[str, type] = {}


def register(cls):
    """Class decorator: register a validator type for deserialization by tag."""
    existing = VALIDATOR_REGISTRY.get(cls.tag)
    if existing is not None and existing is not cls:
        raise ValueError(f"validator tag '{cls.tag}' already registered by {existing.__name__}")
    VALIDATOR_REGISTRY[cls.tag] = cls
    return cls


def validator_from_json(d: dict):
    """Rebuild a validator from its to_json() dict. Unknown tags fail loudly."""
    try:
        cls = VALIDATOR_REGISTRY[d["type"]]
    except KeyError:
        raise ValueError(f"unknown validator type '{d.get('type')}', known: {sorted(VALIDATOR_REGISTRY)}") from None
    return cls.from_json(d)


def _validator_eq(self, other):
    return type(other) is type(self) and self.__dict__ == other.__dict__


@runtime_checkable
class Validator(Protocol):
    field: str

    def validate(self, value): ...

    def prompt(self) -> str: ...


@register
class OneOf:
    """Check that value is in a set of allowed values."""

    tag = "one_of"

    def __init__(self, field: str, valid: set):
        self.field = field
        self.valid = valid

    def validate(self, value):
        if value not in self.valid:
            raise ValueError(f"{self.field}: must be one of {self.valid}, got '{value}'")
        return value

    def prompt(self) -> str:
        return f"{self.field}: must be one of {sorted(self.valid)}"

    def to_json(self) -> dict:
        return {"type": self.tag, "field": self.field, "valid": sorted(self.valid)}

    @classmethod
    def from_json(cls, d: dict) -> OneOf:
        return cls(d["field"], set(d["valid"]))

    __eq__ = _validator_eq


@register
class InRange:
    """Check that a numeric value is within [lo, hi]."""

    tag = "in_range"

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

    def to_json(self) -> dict:
        return {"type": self.tag, "field": self.field, "lo": self.lo, "hi": self.hi}

    @classmethod
    def from_json(cls, d: dict) -> InRange:
        return cls(d["field"], d["lo"], d["hi"])

    __eq__ = _validator_eq


@register
class NotEmpty:
    """Check that a string is non-empty after stripping whitespace."""

    tag = "not_empty"

    def __init__(self, field: str):
        self.field = field

    def validate(self, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{self.field}: must be a non-empty string, got '{value}'")
        return value

    def prompt(self) -> str:
        return f"{self.field}: non-empty string"

    def to_json(self) -> dict:
        return {"type": self.tag, "field": self.field}

    @classmethod
    def from_json(cls, d: dict) -> NotEmpty:
        return cls(d["field"])

    __eq__ = _validator_eq


@register
class IsType:
    """Check that value is an instance of the expected type (or one of several)."""

    tag = "is_type"

    _NAMES = {
        dict: "JSON object",
        list: "list",
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
    }
    # JSON-serializable types; custom classes cannot round-trip through to_json
    _TYPE_TAGS = {t: t.__name__ for t in (dict, list, str, int, float, bool)}
    _TAG_TYPES = {name: t for t, name in _TYPE_TAGS.items()}

    def __init__(self, field: str, expected: type | tuple[type, ...]):
        self.field = field
        self.expected = expected if isinstance(expected, tuple) else (expected,)

    def _name(self) -> str:
        return " or ".join(self._NAMES.get(t, t.__name__) for t in self.expected)

    def validate(self, value):
        ok = isinstance(value, self.expected)
        # bool is a subclass of int; a numeric check should not accept True/False
        # unless bool itself was asked for
        if ok and isinstance(value, bool) and bool not in self.expected:
            ok = False
        if not ok:
            name = self._name()
            article = "an" if name[0].lower() in "aeiou" else "a"
            raise ValueError(f"{self.field}: must be {article} {name}, got {type(value).__name__}")
        return value

    def prompt(self) -> str:
        return f"{self.field}: {self._name()}"

    def to_json(self) -> dict:
        unknown = [t.__name__ for t in self.expected if t not in self._TYPE_TAGS]
        if unknown:
            raise ValueError(
                f"{self.field}: cannot serialize custom type(s) {unknown}; serializable: {sorted(self._TAG_TYPES)}"
            )
        return {
            "type": self.tag,
            "field": self.field,
            "expected": [self._TYPE_TAGS[t] for t in self.expected],
        }

    @classmethod
    def from_json(cls, d: dict) -> IsType:
        unknown = [n for n in d["expected"] if n not in cls._TAG_TYPES]
        if unknown:
            raise ValueError(f"unknown type name(s) {unknown} in is_type validator, known: {sorted(cls._TAG_TYPES)}")
        return cls(d["field"], tuple(cls._TAG_TYPES[n] for n in d["expected"]))

    __eq__ = _validator_eq


@register
class Matches:
    """Check that a string matches a regex pattern."""

    tag = "matches"

    def __init__(self, field: str, pattern: str):
        self.field = field
        self.pattern = pattern

    def validate(self, value):
        if not re.fullmatch(self.pattern, value):
            raise ValueError(f"{self.field}: must match pattern '{self.pattern}', got '{value}'")
        return value

    def prompt(self) -> str:
        return f"{self.field}: must match pattern '{self.pattern}'"

    def to_json(self) -> dict:
        return {"type": self.tag, "field": self.field, "pattern": self.pattern}

    @classmethod
    def from_json(cls, d: dict) -> Matches:
        return cls(d["field"], d["pattern"])

    __eq__ = _validator_eq
