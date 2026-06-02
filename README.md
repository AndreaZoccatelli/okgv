# okgv — organizing knowledge: graphs and vectors

CLI for AI agents to build self-organized synthetic knowledge bases.

Coding agents generate entries, okgv handles deduplication (via vector similarity) and organization (via graph structure). The agent owns the decision loop — okgv provides the tools.

## Quickstart

```bash
pip install -e .
cd my-dataset-project
okgv init
# edit .env, schema.py, topics.json
okgv create-structure --file topics.json
```

## Architecture

Two storage layers:

- **SQLite** — topics, sub-topics, entries, submission log, review state. All local, zero setup.
- **Weaviate** — vectors: entry embeddings, similarity search.

Every entry lives in both stores, linked by a deterministic UUID5 (computed from canonical JSON of the entry content).

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

Entries can live at any level. Queries on a topic are recursive — include all descendant entries.

## Agent Workflow

```
1. okgv get-structure
   → understand topic layout

2. okgv least-topic --topic <parent>
   → pick child topic with fewest entries

3. Agent generates candidate entry (LLM call)

4. okgv similar --topic <topic> --entry '<json>'
   → top-N most similar entries WITH FULL CONTENT
   → agent decides: novel enough → submit, too similar → regenerate

5. okgv submit --topic <topic> --entry '<json>' [--review]
   → upserted into both DBs, logged to okgv.db
   → optionally flagged for review
```

## Commands

All output is JSON to stdout. Logs go to stderr.

| Command | Purpose |
|---------|---------|
| `init` | Scaffold project files (.env, schema.py, topics.json) |
| `master-prompt` | Print agent instructions for using the CLI |
| `get-structure` | Topic/subtopic tree as nested JSON. `--root`, `--depth` to scope |
| `get-depth` | Max depth of topic tree. `--root` to measure from specific topic |
| `create-topic` | Create topic by path. `--parents` for mkdir -p behavior |
| `create-structure` | Create topic tree from JSON file |
| `least-topic` | Child topic with fewest entries. `--topic` scopes to parent |
| `topic-stats` | Entry counts grouped by metadata fields |
| `similar` | Top-N similar entries within a topic |
| `similar-batch` | Batch similarity search (single model load) |
| `submit` | Upsert entry into both DBs. `--review` to flag for review |
| `submit-batch` | Batch upsert (single model load). `--review` to flag for review |
| `move-topic` | Move topic/subtopic under different parent. `--dry-run` to preview |
| `move-entry` | Move entry to different topic. `--dry-run` to preview |
| `tree` | Visualize topic tree. `--counts`, `-i` interactive browser, `--export dot\|json` |
| `get-by-topic` | Fetch sample entries for a topic |
| `get-vector` | Fetch entry from vector DB by ID |
| `get-graph` | Fetch entry from graph DB by ID |
| `review` | Query review queue. `--tui` for interactive UI, `--export`/`--import` for batch, `--purge-rejected`/`--recover-rejected` |
| `approve` | Mark entry as approved in review queue |
| `reject` | Mark entry as rejected in review queue |
| `log` | Query submission log. `--topic`, `--after`, `--before`, `--count` |
| `undo` | Delete entries submitted after a timestamp. `--dry-run` to preview |
| `reconcile` | Find and fix orphan entries across DBs. `--dry-run` to preview |
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
okgv create-structure --file topics.json

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
okgv review --tui --topic algebra          # interactive terminal UI
okgv review --topic algebra --count        # counts by status
okgv review --export review.json           # export for offline review
okgv review --import review.json           # import decisions
okgv approve --id <uuid>                   # approve single entry
okgv reject --id <uuid>                    # reject single entry
okgv review --purge-rejected --dry-run     # preview rejected cleanup
okgv review --purge-rejected               # delete rejected from all DBs
okgv review --recover-rejected --dry-run   # preview recovery
okgv review --recover-rejected             # set rejected back to pending

# Query submission log
okgv log
okgv log --topic algebra --limit 50
okgv log --after 2026-05-30T00:00:00
okgv log --count

# Undo recent submissions
okgv undo 2026-05-30T12:00:00

# Find and fix cross-DB inconsistencies
okgv reconcile --dry-run
okgv reconcile
okgv reconcile --batch-size 500

# Nuclear option (hidden command)
okgv purge --confirm "delete all" --dry-run
okgv purge --confirm "delete all"
```

## Setup

### Weaviate

Follow the [official installation guide](https://weaviate.io/developers/weaviate/installation). Docker recommended for local development.

The Weaviate collection is created automatically on first access if it doesn't exist.

### Configuration

All via environment variables. A `.env` file in the working directory is **auto-loaded** on every `okgv` command (via `python-dotenv`). Only the `.env` in the current directory is loaded — no parent directory traversal.

| Variable | Default | Purpose |
|----------|---------|---------|
| `OKGV_SCHEMA` | built-in QA schema | `module:ClassName` schema specifier |
| `OKGV_DB` | `./okgv.db` | Path to SQLite database (graph + log + review) |
| `WEAVIATE_HOST` | `localhost` | |
| `WEAVIATE_PORT` | `8080` | HTTP port |
| `WEAVIATE_GRPC_PORT` | `50051` | gRPC port |
| `WEAVIATE_COLLECTION` | `knowledge_base` | Collection name for entries |
| `WEAVIATE_API_KEY` | (none) | Optional API key |
| `EMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model |
| `OKGV_REVIEW` | `none` | Default review mode: `none` or `all` |

## Entry Schema

okgv does not assume a fixed entry structure. Define your own with two classes:

1. **Entry class** — field extraction from raw JSON + computed properties
2. **Schema class** — DB mapping (what goes where, what to embed)

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
        """Stored in BOTH graph and vector DBs."""
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

Format: `module:ClassName` — module resolved relative to cwd.

### Schema Validation

At runtime, okgv validates:
- No key collisions between `metadata()` and `graph_properties()`/`vector_properties()`
- `vector_property_definitions()` covers exactly the keys from `metadata()` + `vector_properties()`

## Review System

Entries can be flagged for review at submit time. Review is an external tracking layer — it does not block entry insertion. Entries always go into both DBs immediately.

### Review modes

- `OKGV_REVIEW=none` (default) — entries skip review unless `--review` is passed
- `OKGV_REVIEW=all` — all entries flagged for review unless `--no-review` is passed

### Review workflow

**For agents** — CLI commands:
```bash
okgv review --topic algebra              # list pending entries
okgv approve --id <uuid>
okgv reject --id <uuid>
okgv review --purge-rejected             # delete rejected from all DBs
okgv review --recover-rejected           # set rejected back to pending
```

**For humans** — interactive TUI or export/import:
```bash
# Terminal UI with staged changes
okgv review --tui --topic algebra

# Or export → edit → import
okgv review --export review.json --topic algebra
# edit status field in review.json
okgv review --import review.json
```

**TUI keyboard shortcuts:**

| Key | Action |
|-----|--------|
| `a` | Approve entry (toggle — press again to revert to pending) |
| `r` | Reject entry (toggle) |
| `u` | Undo mark (revert to pending) |
| `s` | Skip / next entry |
| `c` | Commit all staged decisions to DB |
| `p` | Purge rejected entries from all DBs (press twice to confirm) |
| `v` | Recover rejected entries (set back to pending) |
| `q` | Quit and discard unsaved changes |

Decisions are staged locally — nothing is written until `c` is pressed. Entries stay visible in the table with colored status indicators. The status bar shows pending/approved/rejected counts and unsaved changes.

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

Every `submit` appends to `okgv.db` (SQLite with WAL mode). The same file stores the graph (topics + entries), submission log, and review queue.

```
log table:
| id | timestamp                    | topic           | entry_id |
|----|------------------------------|-----------------|----------|
| 1  | 2026-05-30T12:00:00+00:00    | algebra/basics  | uuid1    |
| 2  | 2026-05-30T12:00:00+00:00    | algebra/basics  | uuid2    |

review table:
| entry_id | topic          | status   | created_at                  | reviewed_at                 |
|----------|----------------|----------|-----------------------------|-----------------------------|
| uuid1    | algebra/basics | approved | 2026-05-30T12:00:00+00:00   | 2026-05-30T14:00:00+00:00   |
| uuid2    | algebra/basics | pending  | 2026-05-30T12:00:00+00:00   |                             |
```

Query with `okgv log`. Timestamps are stored in UTC, displayed in local time. Used by `undo` to roll back submissions.

## Reliability

### Batch Operations

`submit-batch` and `similar-batch` load the embedding model once and use native Weaviate batch APIs (`insert_many`, `delete_many`) instead of per-entry round trips.

`undo` and `reconcile` also use batch deletes.

### Connection Retry

Both DB connection factories retry up to 3 times with exponential backoff on transient failures.

Per-operation retries (up to 2 retries with backoff) are applied to all read queries and idempotent writes (deletes, MERGE operations). Non-idempotent writes are not retried to avoid double-insertion.

### Cross-DB Consistency

Every entry lives in both SQLite and Weaviate. The write order ensures safe recovery:

| Operation | Strategy |
|-----------|----------|
| **Single upsert** | Graph first → vector. If vector fails, graph entry is rolled back |
| **Batch upsert** | Graph individually → vector batch. Failed vector entries rolled back from graph |
| **Undo** | Vector deleted first → graph → log → review. If vector fails, nothing changed, safe to retry |
| **Reconcile** | Chunked iteration with batch existence checks. Memory-efficient at scale. `--batch-size` controls chunk size |
| **Purge rejected** | Vector → graph → log → review. Same safe ordering as undo |
