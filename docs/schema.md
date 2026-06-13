<p align="center">
    <img src="../resources/okgv-logo-light.svg#gh-light-mode-only" alt="okgv logo" width="400">
    <img src="../resources/okgv-logo-dark.svg#gh-dark-mode-only" alt="okgv logo" width="400">
</p>

# Entry Schema & Configuration

## At a glance

A project is four pieces, wired by environment variables in `.env` (run `okgv init` to scaffold them):

| File | Env var | Holds |
|------|---------|-------|
| `config/schema.py` | `OKGV_SCHEMA` | The **entry class** (parse raw JSON → entry object, computed properties) and the **schema class** (global validators, which fields go to which store, what to embed, descriptions, balance fields, and the optional `validate_for_topic` hook). |
| `config/structure.json` | `OKGV_STRUCTURE` | The **topic tree**. Any node may carry an optional `_meta` block of per-topic constraints, folded along each root-to-leaf path. |
| `config/validators.py` | `OKGV_VALIDATORS` | Optional. **Custom validators** (`@register`'d) whose tags are referenced from `_meta` or the schema. |
| `.env` |, | Wires the above plus the embedding model and review mode. |

The unifying thread is the **validator vocabulary**: the same `OneOf`/`InRange`/… objects appear in the schema's global `validators` (Python) and in `structure.json` `_meta` (JSON, via serde). Global validators are the baseline every entry meets; `_meta` narrows them per topic. The rest of this document builds each piece in turn.

## Install

No external services required. Everything runs locally via SQLite and sqlite-vec.

```bash
pip install -e ".[embeddings]"    # with sentence-transformers (default embedding backend)
pip install -e .                  # core only, bring your own embedding backend
```

Optional extras:

| Extra | What it adds |
|-------|-------------|
| `embeddings` | `sentence-transformers`, local embedding via transformer models |
| `tui` | `textual`, interactive terminal UI for review and browsing |

Install multiple: `pip install -e ".[embeddings,tui]"`

## Configuration

All via environment variables. A `.env` file in the working directory is **auto-loaded** on every `okgv` command (via `python-dotenv`). Only the `.env` in the current directory is loaded, no parent directory traversal.

| Variable | Default | Purpose |
|----------|---------|---------|
| `OKGV_SCHEMA` | *required* | `module:ClassName` schema specifier |
| `OKGV_DB` | `./okgv.db` | Path to SQLite database (graph + vectors + log + review) |
| `OKGV_STRUCTURE` | `./config/structure.json` | Structure file folded into per-topic constraint specs (see [Topic constraints](#topic-constraints-_meta)) |
| `OKGV_VALIDATORS` | *(none)* | Comma-separated module paths to import so custom validators register before a fold (see [Validators](#validators)) |
| `EMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model (`backend/model-name`) |
| `EMBED_DIM` | auto-detect from model | Embedding dimension override |
| `OKGV_REVIEW` | `none` | Default review mode: `none` or `all` |

### Embedding Backends

okgv uses a pluggable embedding system. The `EMBED_MODEL` variable controls which backend loads:

```bash
# sentence-transformers (requires: pip install okgv[embeddings])
EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2

# Models without a recognized prefix default to sentence-transformers
EMBED_MODEL=all-MiniLM-L6-v2
```

New backends can be registered programmatically:

```python
from okgv.embedding import register_backend

def my_embedder_factory(model_name: str):
    # Load your model, return a callable: list[str] -> list[list[float]]
    ...

register_backend("my-backend", my_embedder_factory)
# Then use: EMBED_MODEL=my-backend/model-name
```

## Entry Schema

okgv does not assume a fixed entry structure. Define your own with two classes:

1. **Entry class**: field extraction from raw JSON + computed properties
2. **Schema class**: DB mapping (what goes where, what to embed)

Run `okgv init` to get a template, or write from scratch:

```python
from okgv.protocols import PropertyDefinition


class MyEntry:
    def __init__(self, raw: dict):
        self.text = raw["text"]
        self.label = raw["label"]

    def text_length(self) -> int:  # a method is fine for metadata(); to filter on
        return len(self.text)      # a value per topic, expose it as an attribute/@property


class MySchema:
    entry_class = MyEntry

    @staticmethod
    def metadata(entry: MyEntry) -> dict:
        """Stored in BOTH graph and vector tables."""
        return {"text_length": entry.text_length()}

    @staticmethod
    def graph_properties(entry: MyEntry) -> dict:
        """Graph DB only."""
        return {"label": entry.label}

    @staticmethod
    def vector_properties(entry: MyEntry) -> dict:
        """Vector DB only."""
        return {"text": entry.text}

    @staticmethod
    def embedding_text(entry: MyEntry) -> str:
        """Text used for vector embedding."""
        return entry.text

    @staticmethod
    def vector_property_definitions() -> list[PropertyDefinition]:
        """Vector DB collection schema. Must cover metadata() + vector_properties() keys."""
        return [
            PropertyDefinition(name="text_length", data_type="int"),
            PropertyDefinition(name="text", data_type="text"),
        ]
```

Set in `.env`:
```
OKGV_SCHEMA=schema:MySchema
```

Format: `module:ClassName`, module resolved relative to cwd.

## Validators

okgv provides validators that serve dual purpose: runtime enforcement and agent prompt generation. Define them once, use in `Entry.__init__` for validation, list in `Schema.validators` for prompt output.

```python
from okgv.validators import OneOf, InRange, NotEmpty, Matches, IsType, Items

difficulty = OneOf("difficulty", {"easy", "medium", "hard"})
score = InRange("score", 0, 100)
text = NotEmpty("text")

class MyEntry:
    def __init__(self, raw: dict):
        self.difficulty = difficulty.validate(raw["difficulty"])
        self.score = score.validate(raw["score"])
        self.text = text.validate(raw["text"])

class MySchema:
    entry_class = MyEntry
    validators = [text, difficulty, score]
    balance_fields = ["difficulty"]
    ...
```

Built-in validators:

| Validator | Purpose | Prompt output |
|-----------|---------|---------------|
| `OneOf(field, values)` | Value in allowed set | `field: must be one of [...]` |
| `InRange(field, lo, hi)` | Numeric range `[lo, hi]` | `field: number between lo and hi` |
| `NotEmpty(field)` | Non-empty string | `field: non-empty string` |
| `Matches(field, pattern)` | Regex match | `field: must match pattern '...'` |
| `IsType(field, type)` | Instance of a type or tuple of types (bool is not int) | `field: <type name>` |
| `Items(field, inner, [min_len], [max_len])` | List with a per-element validator and optional length bounds | `field: list ..., each: ...` |

**Referencing built-ins in `_meta`**, each validator's JSON `tag` and forms (`field` defaults to the key, so it is omitted):

| Validator | `tag` | Tagged form | Explicit form |
|-----------|-------|-------------|---------------|
| `OneOf` | `one_of` | `{"one_of": ["a", "b"]}` | `{"type": "one_of", "valid": ["a", "b"]}` |
| `InRange` | `in_range` | `{"in_range": [0, 1]}` | `{"type": "in_range", "lo": 0, "hi": 1}` |
| `NotEmpty` | `not_empty` | `"not_empty"` | `{"type": "not_empty"}` |
| `Matches` | `matches` | `{"matches": "^a+$"}` | `{"type": "matches", "pattern": "^a+$"}` |
| `IsType` | `is_type` | `{"is_type": ["int", "float"]}` | `{"type": "is_type", "expected": ["int", "float"]}` |
| `Items` | `items` | *(no shorthand)* | `{"type": "items", "inner": {...}, "min_len": 1}` |

`is_type` type names are `dict`, `list`, `str`, `int`, `float`, `bool` (a `bool` is not accepted as `int` unless `bool` is listed). `items` has no tagged shorthand, but its `inner` is written like any other validator (a bare tag, a `{tag: args}`, or explicit), e.g. `{"type": "items", "inner": "not_empty", "min_len": 1}` is a non-empty list of non-empty strings. Run `okgv validators` to see every available tag (built-in + custom) and the exact form to write for each.

Custom validators implement `validate(value)` and `prompt() -> str` with a `field` attribute. To participate in serialization (needed for use inside structure-file `_meta` blocks), add a unique `tag` class attribute plus `to_json()`/`from_json()` and apply the `@register` decorator from `okgv.validators`; `validator_from_json()` then rebuilds them and fails loudly on an unknown tag. Add an `args` tuple (the positional argument order) to opt into the tagged `{tag: args}` shorthand. A validator may also implement an optional `narrow(other)` returning the simplified conjunction of two validators on the same field, this powers contradiction detection, sibling-disjointness checks, and narrowed prompt rendering; validators without it are treated as opaque (enforcement is unaffected, only analysis degrades).

A complete custom validator (put it in `config/validators.py`):

```python
from okgv.validators import register


@register
class MultipleOf:
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
```

It is then usable in Python (`MultipleOf("count", 5)`) and in `_meta` as `"count": {"multiple_of": 5}` or `{"type": "multiple_of", "field": "count", "n": 5}`. (`okgv init` scaffolds `config/validators.py` with this example.)

A custom tag is only in the registry once the module holding its `@register` runs. The fold (`create-structure`, session start) does **not** import your schema module, so put custom validators in a module listed in `OKGV_VALIDATORS` (comma-separated, resolved relative to cwd, like `OKGV_SCHEMA`). okgv imports those modules before folding, so the tags resolve. `OKGV_VALIDATORS` is operator config: the structure file references tags, never code paths.

## Field Descriptions

Add `field_descriptions` to your schema to tell agents what each field means. These are included in `okgv entry-prompt` output alongside validator constraints.

```python
class MySchema:
    entry_class = MyEntry
    validators = [text, difficulty, score]
    field_descriptions = {
        "text": "the main content of the entry, 1-3 sentences",
        "score": "quality score based on clarity and correctness",
        "difficulty": (
            "cognitive difficulty for a graduate student",
            {
                "easy": "single concept, direct application",
                "medium": "requires combining 2-3 concepts",
                "hard": "multi-step reasoning, edge cases",
            },
        ),
    }
```

Simple string descriptions and tuple descriptions (with per-option details) can be mixed. Running `okgv entry-prompt` outputs:

```
# Entry Fields

Each entry in this knowledge base has the following fields:

- text: the main content of the entry, 1-3 sentences. Non-empty string
- score: quality score based on clarity and correctness. Number between 0 and 100
- difficulty: cognitive difficulty for a graduate student. Must be one of ['easy', 'hard', 'medium']
  - easy: single concept, direct application
  - medium: requires combining 2-3 concepts
  - hard: multi-step reasoning, edge cases
```

## Balance Fields

Add `balance_fields` to tell agents which fields the dataset should be balanced across. Not all metadata fields need balancing, computed fields like text length or fields derived from topic structure typically don't.

```python
class MySchema:
    balance_fields = ["difficulty", "category"]
```

`okgv entry-prompt` includes a balancing section when `balance_fields` is defined. `okgv topic-stats` defaults to these fields when `--fields` is not passed.

## Topic constraints (`_meta`)

The validators above are global: every entry must satisfy them regardless of topic. okgv also supports **per-topic** constraints, so a rule like "an entry under a function topic must call that function" can be enforced relationally.

For *when* to reach for `_meta` versus a global validator or the tree shape, and worked examples across several dataset types, see [Dataset Patterns](patterns.md). This section documents the mechanics.

### Default enforcement (no code needed)

For the common case, a topic narrows a scalar entry field via the `entry` namespace, okgv enforces it for you. On every submission (and move, and `revalidate`), the library runs each `entry`-namespace validator from the topic's folded spec against the entry, raising `EntryError` on a violation. A schema that only narrows entry fields per topic needs **no** hook at all.

The argument-object namespaces (`required`/`optional`/`forbidden`) are *not* auto-enforced, binding them to a compound entry field is dataset-specific.

**`entry` constraints resolve to entry attributes** (via `getattr`), so a field you constrain must be a stored attribute or a `@property` on your entry class. A value computed only inside `metadata()`/`graph_properties()`/`vector_properties()` is not visible here (you get "field … not present on the entry"), and a plain method is rejected with a clear error ("is a method, not a value …"). To filter a topic on a *computed* value, expose it as an attribute or `@property`:

```python
class MyEntry:
    def __init__(self, raw):
        self.text = raw["text"]
        self.length_bucket = "long" if len(self.text) > 200 else "short"  # now usable in _meta entry
```

Note this is distinct from `balance_fields`/`topic-stats`/`report`, which read `metadata()` output, so a derived metadata field can be balanced and reported on even when it is not an entry attribute, but it can only be used as a per-topic `entry` filter once exposed on the entry.

### The `validate_for_topic` hook (for the bespoke part)

Add an optional static method for anything the default cannot express (e.g. matching a function name and its argument signature). It is called after the default enforcement, with the built entry and its topic, before any DB write; raise `ValueError` to reject. It may take an optional third parameter to receive the topic's folded effective spec, so it need not re-read the structure file (`(entry, topic)` hooks still work).

```python
class MySchema:
    @staticmethod
    def validate_for_topic(entry, topic: str, spec=None) -> None:
        if spec is None or spec.function is None:   # spec passed in by okgv
            raise ValueError(f"topic '{topic}' has no function spec on its path")
        ...                                         # check entry against spec
```

The hook also runs for every moved entry (against the destination) and is what the `revalidate` command uses to find entries left invalid by a tightened spec.

### The effective `Spec`

What the hook (and `Session.effective_spec(topic)`) receives is the topic's folded spec, every ancestor's `_meta` combined down the path:

```python
@dataclass
class Spec:
    function: str | None              # set once on the path; the function identity
    required: dict[str, list]         # {param_name: [validators]}, argument keys that must be present
    optional: dict[str, list]         # {param_name: [validators]}, argument keys that may be present
    forbidden: set[str]               # argument keys that must be absent
    entry: dict[str, list]            # {entry_field: [validators]}, narrowed entry-schema fields
    similarity_scope: str | None      # "leaf" (default) or "subtree"
```

Each `{name: [validators]}` maps a name to a **list** of validators (a conjunction, all run; the fold may leave several stacked). Helpers: `spec.scope()` (resolved scope, default `"leaf"`), `spec.is_empty()`, and `spec.to_json()` (emit a `_meta` block, the inverse of authoring it in JSON, so you can build specs in Python and serialize them).

### Custom spec: enforcing an argument signature

The `entry` namespace is enforced for you, but `required`/`optional`/`forbidden` describe the shape of a **compound** field (an arguments object), which is dataset-specific, so you bind them to your entry in the hook. The pattern is a small combinator fed from the folded `Spec`:

```python
class FunctionSpec:
    """Check an entry's function name and arguments dict against a folded Spec."""

    def __init__(self, function, required, optional, forbidden):
        self.function = function
        self.required = required      # {param: [validators]}
        self.optional = optional
        self.forbidden = forbidden

    @classmethod
    def from_effective(cls, spec):
        return cls(spec.function, spec.required, spec.optional, spec.forbidden)

    def validate(self, function, arguments):
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


class MySchema:
    @staticmethod
    def validate_for_topic(entry, topic, spec=None):
        if spec is None or spec.function is None:
            raise ValueError(f"topic '{topic}' declares no function on its path")
        FunctionSpec.from_effective(spec).validate(entry.function, entry.arguments)
```

That is the whole mechanism behind the function-calling example: the `_meta` blocks declare the per-topic signature, the fold inherits and narrows it down each path, and this combinator enforces it against the entry's `function` + `arguments`. See `example/config/schema.py` for the full version.

### `_meta` blocks in `structure.json`

A topic node may carry a reserved `_meta` key declaring its constraints (any other key is a child topic). Blocks **compose along a path**, a topic's effective spec is the fold of every ancestor's `_meta` plus its own, and a child may only narrow, add, or `forbid`, never relax.

```json
{
  "weather": {
    "current_conditions": {
      "_meta": {
        "function": "get_current_weather",
        "required": {"location": "not_empty"},
        "optional": {"units": {"one_of": ["celsius", "fahrenheit"]}},
        "entry":    {"difficulty": {"one_of": ["easy", "medium", "hard"]}},
        "similarity_scope": "leaf"
      }
    }
  }
}
```

- `required` / `optional` / `forbidden` constrain a function's arguments; `entry` narrows global entry-schema fields; `function` is the function identity (set once per path); `similarity_scope` is `"leaf"` (default) or `"subtree"`.
- **Validator forms** (`field` always defaults to the key): a bare tag string for a zero-arg validator (`"location": "not_empty"`); the tagged `{tag: args}` form (`{"one_of": ["celsius", "fahrenheit"]}`, `{"in_range": [0, 1]}`, `{"is_type": ["int"]}`); or the explicit `{"type": tag, ...}` form. The tag decides how the args are read, a single-argument validator takes the value whole, `in_range` takes `[lo, hi]`, a dict value is named args. A **list is always a conjunction** (`["not_empty", {"matches": "^[A-Z]"}]`).
- Validators are parsed through the registry. A malformed validator, a contradictory fold, or a redeclared function fails at `create-structure`, before anything is written.
- **Python-first authoring:** build a `Spec` from validator objects and call `spec.to_json()` to emit a `_meta` block, instead of hand-writing JSON. `parse_meta(spec.to_json())` round-trips.
- `create-structure` warns about topics with no `_meta` on their path and about overlapping siblings with no explicit `similarity_scope`. Re-running over a populated DB suggests `revalidate`.
- The schema reads the folded specs from the structure file. `okgv entry-prompt --topic <path>` renders the fields narrowed to a topic plus its function name and argument signature; `Session.effective_spec(topic)` exposes the fold programmatically.

See the Structure Design Guide (`prompts/structure-prompt.md`, scaffolded by `okgv init`) for authoring guidance.

## Schema Validation

At runtime, okgv validates:
- No key collisions between `metadata()` and `graph_properties()`/`vector_properties()`
- `vector_property_definitions()` covers exactly the keys from `metadata()` + `vector_properties()`
- Each `_meta` block parses through the validator registry and folds without contradiction (at `create-structure`)
