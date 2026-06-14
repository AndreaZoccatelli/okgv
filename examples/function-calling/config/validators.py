"""Custom validators for this project.

Define validators here that the built-ins don't cover (OneOf, InRange, NotEmpty,
Matches, IsType, Items). Each one:

  - has a `field` attribute plus `validate(value)` and `prompt() -> str`;
  - registers a unique `tag` via @register, with `to_json()` / `from_json()`,
    so it can be referenced inside structure.json `_meta` blocks;
  - optionally declares an `args` tuple (positional argument order) to opt into
    the `{tag: args}` shorthand, and `narrow(other)` to participate in
    contradiction / sibling-disjointness / narrowed-prompt analysis (validators
    without `narrow` are still enforced, just treated as opaque to analysis).

To make these tags resolve when structure.json is folded, set in .env:

    OKGV_VALIDATORS=config.validators

okgv imports this module before the fold (it does not import your schema module
for that), so the tags are registered in time. The structure file references
tags only — never code paths.

The example below is a template — edit it or delete it.
"""

from okgv.validators import register


@register
class MultipleOf:
    """Example custom validator: an integer that is a multiple of n."""

    tag = "multiple_of"
    args = ("n",)  # enables the shorthand {"multiple_of": 5}

    def __init__(self, field: str, n: int):
        self.field = field
        self.n = n

    def validate(self, value):
        if not isinstance(value, int) or value % self.n != 0:
            raise ValueError(f"{self.field}: must be a multiple of {self.n}, got {value}")
        return value

    def prompt(self) -> str:
        return f"{self.field}: multiple of {self.n}"

    def to_json(self) -> dict:
        return {"type": self.tag, "field": self.field, "n": self.n}

    @classmethod
    def from_json(cls, d: dict) -> "MultipleOf":
        return cls(d["field"], d["n"])
