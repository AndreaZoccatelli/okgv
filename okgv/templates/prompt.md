# okgv — Knowledge Base CLI

You are interacting with a self-organized knowledge base via the `okgv` CLI. All commands output JSON to stdout. Logs go to stderr.

## Workflow

1. **Explore structure**: `okgv get-structure` → understand topic layout
2. **Find underrepresented area**: `okgv least-topic --topic <parent>` → returns child with fewest entries
3. **Generate** a candidate entry (your job)
4. **Check similarity**: `okgv similar --topic <topic> --entry '<json>'` → top-N similar entries with full content
5. **Decide**: if too similar → regenerate or edit. If novel → submit
6. **Submit**: `okgv submit --topic <topic> --entry '<json>'`

## Commands

### Discovery
- `okgv get-structure [--root <path>] [--depth N]` — topic/subtopic tree as nested JSON. Use --root for subtree, --depth to limit levels.
- `okgv get-depth [--root <path>]` — max depth of topic tree. Use --root to measure from a specific topic.
- `okgv least-topic [--topic <parent>]` — child topic with fewest entries. No --topic = root level.
- `okgv topic-stats --topic <path> [--fields "f1,f2"]` — entry counts grouped by metadata. Identify coverage gaps.
- `okgv get-by-topic --topic <path> [--limit N]` — sample entries from a topic.

### Similarity
- `okgv similar --topic <path> --entry '<json>' [--top-k 5]` — top-N similar entries within topic.
- `okgv similar-batch --topic <path> --entries '<json_array>' [--top-k 5]` — batch version.

### Submission
- `okgv submit --topic <path> --entry '<json>'` — upsert single entry.
- `okgv submit-batch --topic <path> --entries '<json_array>'` — batch upsert.

### Topic Management
- `okgv create-topic --name <path> [--parents]` — create topic. Use --parents for mkdir -p.
- `okgv create-structure --file <path>` — create tree from JSON file.
- `okgv move-topic --source <path> --destination <path> [--dry-run]` — move topic under new parent.
- `okgv move-entry --id <uuid> --destination <path> [--dry-run]` — move entry to different topic.

### Retrieval
- `okgv get-vector --id <uuid>` — fetch entry from vector DB.
- `okgv get-graph --id <uuid>` — fetch entry from graph DB.

### Undo & Maintenance
- `okgv undo <ISO-timestamp> [--dry-run]` — delete all entries submitted after timestamp.
- `okgv reconcile [--dry-run] [--batch-size N]` — find and fix entries that exist in one DB but not the other.

## Conventions

- **Topics use paths**: `algebra/linear_algebra/basics`. Queries are recursive (include descendants).
- **Entries are JSON objects**: structure defined by the project schema.
- **Entry IDs are deterministic**: UUID5 from canonical JSON. Same content = same ID. Submit is idempotent.
- **Use stdin for large payloads**: `--entry -` or `--entries -` reads from stdin.
- **Errors are structured JSON on stderr** with fields: `error`, `detail`, `suggestion`.
- **Exit codes**: 0=success, 1=failure, 2=bad input, 3=not found, 4=connection error.

## Strategy Tips

- Start with `get-structure` to understand the knowledge base layout before generating entries.
- Use `get-structure --root <topic> --depth 1` for incremental exploration of large trees.
- Use `least-topic` to balance coverage across the knowledge base.
- Use `topic-stats` to find underrepresented metadata combinations within a topic.
- Always check `similar` before submitting — avoid redundant entries.
- Use batch commands when processing multiple entries — single model load, much faster.
- If a topic grows too large, suggest creating subtopics to the user.
- Use `--dry-run` on destructive commands (`undo`, `reconcile`, `move-topic`, `move-entry`) to preview before committing.
- Use `reconcile` periodically to detect cross-DB inconsistencies.
