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
from okgv.validators import OneOf, InRange, NotEmpty, Matches

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

Custom validators can be created by implementing `validate(value)` and `prompt() -> str` methods with a `field` attribute.

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

## Schema Validation

At runtime, okgv validates:
- No key collisions between `metadata()` and `graph_properties()`/`vector_properties()`
- `vector_property_definitions()` covers exactly the keys from `metadata()` + `vector_properties()`
