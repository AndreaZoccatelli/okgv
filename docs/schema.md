<p align="center">
    <img src="../resources/okgv-logo-light.svg#gh-light-mode-only" alt="okgv logo" width="400">
    <img src="../resources/okgv-logo-dark.svg#gh-dark-mode-only" alt="okgv logo" width="400">
</p>

# Entry Schema & Configuration

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

    def text_length(self) -> int:
        return len(self.text)


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

Custom validators implement `validate(value)` and `prompt() -> str` with a `field` attribute. To participate in serialization (needed for use inside structure-file `_meta` blocks), add a unique `tag` class attribute plus `to_json()`/`from_json()` and apply the `@register` decorator from `okgv.validators`; `validator_from_json()` then rebuilds them and fails loudly on an unknown tag. A validator may also implement an optional `narrow(other)` returning the simplified conjunction of two validators on the same field — this powers contradiction detection, sibling-disjointness checks, and narrowed prompt rendering; validators without it are treated as opaque (enforcement is unaffected, only analysis degrades).

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

### The `validate_for_topic` hook

Add an optional static method to your schema. It is called on submission with the built entry and its destination topic, before any DB write; raise `ValueError` to reject. Schemas without the hook are unaffected.

```python
class MySchema:
    @staticmethod
    def validate_for_topic(entry, topic: str) -> None:
        spec = SPECS.get(topic)            # folded from structure.json _meta
        if spec is None or spec.function is None:
            raise ValueError(f"topic '{topic}' has no function spec on its path")
        ...                               # check entry against spec
```

The hook also runs for every moved entry (against the destination) and is what the `revalidate` command uses to find entries left invalid by a tightened spec.

### `_meta` blocks in `structure.json`

A topic node may carry a reserved `_meta` key declaring its constraints (any other key is a child topic). Blocks **compose along a path** — a topic's effective spec is the fold of every ancestor's `_meta` plus its own — and a child may only narrow, add, or `forbid`, never relax.

```json
{
  "weather": {
    "current_conditions": {
      "_meta": {
        "function": "get_current_weather",
        "required": {"location": {"type": "not_empty", "field": "location"}},
        "optional": {"units": {"type": "one_of", "field": "units", "valid": ["celsius", "fahrenheit"]}},
        "entry":    {"difficulty": {"type": "one_of", "field": "difficulty", "valid": ["easy", "medium", "hard"]}},
        "similarity_scope": "leaf"
      }
    }
  }
}
```

- `required` / `optional` / `forbidden` constrain a function's arguments; `entry` narrows global entry-schema fields; `function` is the function identity (set once per path); `similarity_scope` is `"leaf"` (default) or `"subtree"`.
- Validator values use the serializable form (`{"type": <tag>, "field": ..., ...}`), parsed through the validator registry. A malformed validator, a contradictory fold, or a redeclared function fails at `create-structure`, before anything is written.
- `create-structure` warns about topics with no `_meta` on their path and about overlapping siblings with no explicit `similarity_scope`. Re-running over a populated DB suggests `revalidate`.
- The schema reads the folded specs from the structure file. `okgv entry-prompt --topic <path>` renders the fields narrowed to a topic plus its function name and argument signature; `Session.effective_spec(topic)` exposes the fold programmatically.

See the Structure Design Guide (`prompts/structure-prompt.md`, scaffolded by `okgv init`) for authoring guidance.

## Schema Validation

At runtime, okgv validates:
- No key collisions between `metadata()` and `graph_properties()`/`vector_properties()`
- `vector_property_definitions()` covers exactly the keys from `metadata()` + `vector_properties()`
- Each `_meta` block parses through the validator registry and folds without contradiction (at `create-structure`)
