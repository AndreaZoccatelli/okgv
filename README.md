# okgv - organizing knowledge: graphs and vectors

[![Tests](https://github.com/AndreaZoccatelli/okgv/actions/workflows/tests.yml/badge.svg)](https://github.com/AndreaZoccatelli/okgv/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

LLMs are often used to generate synthetic text datasets for training other ML models. Two requirements make this hard at scale: the dataset has to stay balanced, and it has to avoid near-duplicate instances. Both get harder as the instance count grows.

The reason is context. Suppose you want questions about cats and dogs, each rated easy, medium, or hard, balanced both across categories (cats vs. dogs) and across difficulty levels. The naive approach, asking the LLM to "ensure diversity", forces it to hold every previously generated instance in context to know what is still missing. Deduplication hits the same wall: spotting a near-duplicate requires comparing the candidate against all prior instances, which again means keeping them in context. Past a few hundred instances this becomes infeasible.

okgv moves that state out of the prompt and into storage. It models a dataset as a tree: each topic is a node, its sub-topics are its children, and every instance is an entry attached to a single topic node. Each entry is also stored as a vector embedding. The agent never has to remember what it generated, it queries the store instead.

This makes the agent work one topic at a time. It checks which topics are underrepresented to pick what to generate next, and before adding a new entry it measures the candidate against the entries already under that same topic. The closest matches come back with their full content, so the agent can decide whether the candidate is too similar to keep. The dataset never lives in the prompt, and the result stays easy to inspect.

Handing an agent full ownership of generation requires a degree of trust that isn't always warranted. For that reason, okgv also supports a review stage: entries can be inspected and approved or discarded, interactively through a TUI by a human, or via CLI commands by an agent prompted to act as the reviewer.

## When to use okgv (and when not to)

okgv is not a vector database and not a large-scale curation pipeline. It is a thin, agent-native layer for building a dataset incrementally, where the generating agent makes the novelty and balance decisions in the loop. The design choices follow from that niche.

It is meant to be driven directly by a coding agent. You point the agent at the task; it reads `okgv cli-prompt`, then runs the generation loop itself through the CLI, finding gaps, checking novelty, submitting. You don't build an API-call pipeline, and the agent doesn't have to hold the growing dataset in its context to stay balanced and avoid duplicates, because that state lives in okgv and the agent queries it.

**Use okgv when:**

- An **agent drives generation** and you want it to decide, per candidate, whether a new entry is novel enough to keep, with the nearest existing entries surfaced as full-content context rather than reduced to a similarity score.
- The dataset is naturally **hierarchical** and must stay **balanced** across that hierarchy. The topic tree doubles as the balance stratum and the dedup scope.
- You want a **human or a second agent to review** generated entries before they ship.
- You want **zero infrastructure**: one portable SQLite file, no server, JSON in and out.
- You need the dataset to be an **auditable artifact**: inspectable, reviewable, traceable through the submission log, and reversible with `undo` and `reconcile`. Useful when you have to trust the data, such as eval sets or regulated domains.
- Each **leaf topic stays bounded** (roughly up to a few thousand entries). Similarity is scoped to the exact topic, so its cost tracks the per-topic count, not the total. The overall dataset can be large as long as individual leaf topics stay small. Where possible, group entries into finer sub-topics to keep each leaf small.

**Reach for something else when:**

- You need **reproducible, deterministic** dedup over a fixed corpus. okgv puts the keep/discard call in the agent's hands (see [below](#why-a-guide-not-a-filter)), which is non-deterministic and costs an LLM call per candidate. If you want a repeatable cosine cutoff instead, a vector store with a metadata filter does that without okgv.
- Individual **leaf topics grow very large** (tens of thousands of entries each). sqlite-vec scores vectors by brute force; the per-topic filter bounds how many it scores, but only down to the leaf-topic size, so a single huge topic wants a real ANN index. Splitting it into finer sub-topics is often enough to stay within okgv; if the entries genuinely can't be partitioned, reach for dedicated tooling.
- The data has **no meaningful hierarchy** and balance doesn't matter (for example, a flat set of diverse paraphrases). The tree collapses to a single node, the balance machinery does nothing, and okgv degrades to a dedup wrapper you don't need.
- You want a **full synthetic-data orchestration framework** with provided generation steps and integrations. okgv deliberately stays narrower than that.

In short: okgv trades determinism and per-topic scale for agent-driven, in-the-loop control and zero setup. If that trade matches your workflow, it fits.

### Why a guide, not a filter

A similarity threshold can answer one question: is this candidate too close to something we already have? It returns a number, and a number can reject but it cannot steer. The agent learns only that its attempt failed, not why, so the next attempt is a blind retry that may land in the same crowded region again.

okgv keeps the decision in the loop on purpose. Before a candidate is submitted, `similar` returns the nearest existing entries **with their full content**, not just a score. So "too similar" becomes "too similar *to this specific entry*," and the agent can generate deliberately away from it. A collision stops being a dead end and becomes direction for the next generation.

A threshold is cheaper and deterministic, and for filtering a fixed corpus it is the right tool. But when the goal is to *generate* a balanced, diverse dataset, what matters is filling the gaps, and that needs feedback the agent can act on. Showing it the nearest existing entry turns each near-miss into a more informed next attempt.

## Quickstart

```bash
pip install -e ".[embeddings]"
cd my-dataset-project
okgv init
# creates:
#   .env, generation-guide.md
#   config/schema.py, config/structure.json
#   prompts/schema-guide.md, prompts/reviewer-prompt.md, prompts/structure-prompt.md
# edit config/schema.py (or use prompts/schema-guide.md with an agent to generate it)
# edit config/structure.json, .env
okgv create-structure --file config/structure.json
```

See [`example/`](example/) for a complete worked project: schema, topic structure and generation guide.

## Architecture

Single storage layer:

- **SQLite** (`okgv.db`): topics, entries, vectors (via [sqlite-vec](https://github.com/asg017/sqlite-vec)), submission log, review state. All local, zero setup, fully portable single file.

Every entry is identified by a deterministic UUID5 (computed from canonical JSON of the entry content).

Use `okgv tree` to visualize the topic hierarchy in the terminal.

### Topic Structure

Topics form a tree with path-based identity:

```
algebra                          → path: "algebra"
├── linear_algebra               → path: "algebra/linear_algebra"
│   ├── basics                   → path: "algebra/linear_algebra/basics"
│   └── advanced                 → path: "algebra/linear_algebra/advanced"
└── abstract_algebra             → path: "algebra/abstract_algebra"
```

Entries can live at any level. Topic queries (counts, listings, stats) are recursive: querying `algebra` includes entries under all its descendants. Similarity search is the exception, it is scoped to the exact target topic only (see [Similarity Scoping](#similarity-scoping)).

## Agent Workflow

```
1. okgv cli-prompt + okgv entry-prompt
   → learn CLI usage and entry field requirements

2. okgv get-structure
   → understand topic layout

3. okgv least-topic --topic <parent>
   → pick child topic with fewest entries

4. Agent generates candidate entry (LLM call)

5. okgv similar --topic <topic> --entry '<json>'
   → top-N most similar entries WITH FULL CONTENT
   → agent decides: novel enough → submit, too similar → regenerate

6. okgv submit --topic <topic> --entry '<json>' [--review]
   → upserted into both tables, logged to okgv.db
   → optionally flagged for review
```

## Commands

All output is JSON to stdout. Logs go to stderr.

| Command | Purpose |
|---------|---------|
| `init` | Scaffold project: `.env`, `generation-guide.md`, `config/` (schema.py, structure.json), `prompts/` (schema-guide, reviewer-prompt, structure-prompt) |
| `cli-prompt` | Print agent instructions for using the CLI |
| `entry-prompt` | Print entry field descriptions and constraints for the agent |
| `get-structure` | Topic/subtopic tree as nested JSON. `--root`, `--depth` to scope |
| `get-depth` | Max depth of topic tree. `--root` to measure from specific topic |
| `create-topic` | Create topic by path. `--parents` for mkdir -p behavior |
| `create-structure` | Create topic tree from JSON file |
| `least-topic` | Child topic with fewest entries. `--topic` scopes to parent |
| `topic-stats` | Entry counts grouped by metadata fields |
| `similar` | Top-N similar entries within a topic |
| `similar-batch` | Batch similarity search (single model load) |
| `submit` | Upsert entry into both tables. `--review` to flag for review |
| `submit-batch` | Batch upsert (single model load). `--review` to flag for review |
| `move-topic` | Move topic/subtopic under different parent. `--dry-run` to preview |
| `move-entry` | Move entry to different topic. `--dry-run` to preview |
| `tree` | Visualize topic tree. `--counts`, `-i` interactive browser, `--export dot\|json` |
| `get-by-topic` | Fetch sample entries for a topic |
| `get-vector` | Fetch entry from the vector table by ID |
| `get-graph` | Fetch entry from the graph table by ID |
| `review` | Query review queue. `-i` for interactive UI, `--export`/`--import` for batch, `--purge-rejected`/`--recover-rejected` |
| `approve` | Mark entry as approved in review queue |
| `reject` | Mark entry as rejected in review queue |
| `log` | Query submission log. `--topic`, `--after`, `--before`, `--count` |
| `undo` | Delete entries submitted after a timestamp. `--dry-run` to preview |
| `reconcile` | Find and fix orphan entries across the graph and vector tables. `--dry-run` to preview |
| `export` | Export all entries to JSONL. `--fields`, `--exclude-in-review`, `--dry-run` |
| `purge` | **Hidden.** Delete everything (entries, topics, log). Requires `--confirm "delete all"` |

### Examples

```bash
# Explore topic structure
okgv get-structure
okgv get-structure --root algebra --depth 2
okgv get-depth

# Create topic tree
okgv create-topic --name algebra/linear_algebra/basics --parents

# Or from file
okgv create-structure --file config/structure.json

# Find underrepresented area
okgv least-topic --topic algebra
# {"topic": "algebra/linear_algebra/basics", "count": 3, "all_counts": {...}}

# Analyze coverage gaps
okgv topic-stats --topic algebra --fields "difficulty,category"

# Check similarity before submitting
okgv similar --topic algebra/linear_algebra --entry '{"text": "..."}' --top-k 5

# Submit (with optional review flag)
okgv submit --topic algebra/linear_algebra/basics --entry '{"text": "..."}' --review

# Batch operations (single model load)
okgv submit-batch --topic algebra --entries '[{"text": "..."}, {"text": "..."}]'

# Move a subtopic
okgv move-topic --source algebra/basics --destination geometry

# Review entries
okgv review -i --topic algebra          # interactive terminal UI
okgv review --topic algebra --count        # counts by status
okgv review --export review.json           # export for offline review
okgv review --import review.json           # import decisions
okgv approve --id <uuid>                   # approve single entry
okgv reject --id <uuid>                    # reject single entry
okgv review --purge-rejected --dry-run     # preview rejected cleanup
okgv review --purge-rejected               # delete rejected from all tables
okgv review --recover-rejected --dry-run   # preview recovery
okgv review --recover-rejected             # set rejected back to pending

# Export for training
okgv export --output dataset.jsonl
okgv export --output dataset.jsonl --fields "text,label" --exclude-in-review

# Query submission log
okgv log
okgv log --topic algebra --limit 50
okgv log --after 2025-01-15T00:00:00
okgv log --count

# Undo recent submissions
okgv undo 2025-01-15T12:00:00

# Find and fix cross-DB inconsistencies
okgv reconcile --dry-run
okgv reconcile
okgv reconcile --batch-size 500

# Nuclear option (hidden command)
okgv purge --confirm "delete all" --dry-run
okgv purge --confirm "delete all"
```

## Setup

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

### Configuration

All via environment variables. A `.env` file in the working directory is **auto-loaded** on every `okgv` command (via `python-dotenv`). Only the `.env` in the current directory is loaded, no parent directory traversal.

| Variable | Default | Purpose |
|----------|---------|---------|
| `OKGV_SCHEMA` | *required* | `module:ClassName` schema specifier |
| `OKGV_DB` | `./okgv.db` | Path to SQLite database (graph + vectors + log + review) |
| `EMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model (`backend/model-name`) |
| `EMBED_DIM` | auto-detect from model | Embedding dimension override |
| `OKGV_REVIEW` | `none` | Default review mode: `none` or `all` |

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

### Validators

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

### Field Descriptions

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

### Balance Fields

Add `balance_fields` to tell agents which fields the dataset should be balanced across. Not all metadata fields need balancing, computed fields like text length or fields derived from topic structure typically don't.

```python
class MySchema:
    balance_fields = ["difficulty", "category"]
```

`okgv entry-prompt` includes a balancing section when `balance_fields` is defined. `okgv topic-stats` defaults to these fields when `--fields` is not passed.

### Schema Validation

At runtime, okgv validates:
- No key collisions between `metadata()` and `graph_properties()`/`vector_properties()`
- `vector_property_definitions()` covers exactly the keys from `metadata()` + `vector_properties()`

## Review System

Entries can be flagged for review at submit time. Review is an external tracking layer, it does not block entry insertion. Entries always go into both tables immediately.

All review commands are CLI-based, so both humans and agents can drive review. This enables multi-agent pipelines: one agent generates entries, another reviews them for quality, consistency, or adherence to constraints.

### Review modes

- `OKGV_REVIEW=none` (default): entries skip review unless `--review` is passed
- `OKGV_REVIEW=all`: all entries flagged for review unless `--no-review` is passed

### Review workflow

**Via CLI** (agents or humans):
```bash
okgv review --topic algebra              # list pending entries
okgv review --topic algebra --count      # counts by status
okgv approve --id <uuid>
okgv reject --id <uuid>
okgv review --recover-rejected           # set rejected back to pending
okgv review --purge-rejected             # delete rejected from all tables
```

**Via interactive TUI** (humans only):
```bash
# Terminal UI with staged changes (requires: pip install okgv[tui])
okgv review -i --topic algebra

# Or export → edit → import
okgv review --export review.json --topic algebra
# edit status field in review.json
okgv review --import review.json
```

**TUI keyboard shortcuts:**

| Key | Action |
|-----|--------|
| `a` | Approve entry (toggle, press again to revert to pending) |
| `r` | Reject entry (toggle) |
| `u` | Undo mark (revert to pending) |
| `s` | Skip / next entry |
| `c` | Commit all staged decisions to DB |
| `p` | Purge rejected entries from all tables (press twice to confirm) |
| `v` | Recover rejected entries (set back to pending) |
| `q` | Quit and discard unsaved changes |

Decisions are staged locally, nothing is written until `c` is pressed. Entries stay visible in the table with colored status indicators. The status bar shows pending/approved/rejected counts and unsaved changes.

### Review states

| Status | Meaning |
|--------|---------|
| `pending` | Awaiting review |
| `approved` | Reviewed and kept |
| `rejected` | Reviewed and marked for deletion |

Rejected entries remain in DBs until `okgv review --purge-rejected` is run. Use `okgv review --recover-rejected` to set them back to pending instead. `undo` and `purge` also clean up review state.

## Error Handling

Errors go to stderr as structured JSON:

```json
{
  "error": "missing_field",
  "detail": "Entry JSON missing required key: 'text'",
  "suggestion": "Ensure entry has \"text\" field"
}
```

Exit codes:

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General failure |
| 2 | Usage/input error |
| 3 | Resource not found |
| 4 | Connection error |

## Session Logging

Every `submit` appends to `okgv.db` (SQLite with WAL mode). The same file stores the graph (topics + entries), vectors (embeddings via sqlite-vec), submission log, and review queue.

```
log table:
| id | timestamp                    | topic           | entry_id |
|----|------------------------------|-----------------|----------|
| 1  | 2025-01-15T12:00:00+00:00    | algebra/basics  | uuid1    |
| 2  | 2025-01-15T12:00:00+00:00    | algebra/basics  | uuid2    |

review table:
| entry_id | topic          | status   | created_at                  | reviewed_at                 |
|----------|----------------|----------|-----------------------------|-----------------------------|
| uuid1    | algebra/basics | approved | 2025-01-15T12:00:00+00:00   | 2025-01-15T14:00:00+00:00   |
| uuid2    | algebra/basics | pending  | 2025-01-15T12:00:00+00:00   |                             |
```

Query with `okgv log`. Timestamps are stored in UTC, displayed in local time. Used by `undo` to roll back submissions.

## Similarity Scoping

**Similarity search is scoped to the exact target topic.** When checking for duplicates before submitting to `topic1/sub_topic1`, only entries already in `topic1/sub_topic1` are compared. Entries in sibling topics like `topic1/sub_topic2` are not considered.

This is by design for performance (native sqlite-vec pre-filtering) and correctness (each topic has its own semantic scope). It means:

- **Same topic name, different parent = fine.** `dogs/legs` and `cats/legs` both contain "legs" entries but about different animals, no cross-dedup needed.
- **The full path determines semantic scope.** A well-structured topic tree naturally avoids ambiguity.
- **Avoid overlapping topics.** If `anatomy/limbs` and `dogs/legs` could contain similar entries, design the tree so each leaf has a clear, non-overlapping scope.

## Reliability

### Batch Operations

`submit-batch` and `similar-batch` load the embedding model once and process all entries with a single model load.

`undo` and `reconcile` also use batch deletes.

### Consistency

All data lives in a single SQLite database (`okgv.db`). Graph entries and vector entries share the same connection, so operations are atomic within a single command. Use `okgv reconcile` to detect and fix any inconsistencies between graph and vector tables.
