# okgv — Knowledge Base CLI

You are interacting with a self-organized knowledge base via the `okgv` CLI. All commands output JSON to stdout. Logs go to stderr.

## Workflow

1. **Learn entry format**: `okgv entry-prompt` → field descriptions, constraints, and valid values
2. **Explore structure**: `okgv get-structure` → understand topic layout
3. **Plan targets**: `okgv report` → counts for every leaf topic and balance-field value; pick an empty or low-count cell. For a quick single answer at one tree level, `okgv least-topic --topic <parent>` returns the child with fewest entries (raw counts, ignores balance fields)
4. **Generate** a candidate entry following the field constraints (your job)
5. **Check similarity**: `okgv similar --topic <topic> --entry '<json>'` → top-N similar entries with full content
6. **Decide**: if too similar (evaluate similarity score and compare the contents) → regenerate or edit. If novel → submit
7. **Submit**: `okgv submit --topic <topic> --entry '<json>'`
8. **Repeat** from step 4 until the target cell is filled, then re-run `okgv report` to pick the next one. Finish by verifying balance with a final `okgv report`

## Commands

### Entry Format
- `okgv entry-prompt` — field descriptions, constraints, and valid values for entries. Run this first to understand what fields to include.

### Discovery
- `okgv get-structure [--root <path>] [--depth N]` — topic/subtopic tree as nested JSON. Use --root for subtree, --depth to limit levels.
- `okgv get-depth [--root <path>]` — max depth of topic tree. Use --root to measure from a specific topic.
- `okgv least-topic [--topic <parent>]` — child topic with fewest entries. No --topic = root level.
- `okgv topic-stats --topic <path> [--fields "f1,f2"]` — entry counts grouped by metadata. Identify coverage gaps.
- `okgv report [--topic <path>] [--fields "f1,f2"]` — dataset-wide balance report: counts for every leaf topic x balance-field value, **including empty cells**. The fastest way to see all coverage gaps at once.
- `okgv get-by-topic --topic <path> [--limit N]` — sample entries from a topic.

### Similarity
- `okgv similar --topic <path> --entry '<json>' [--top-k 5]` — top-N similar entries. Scope follows the topic's `similarity_scope` metadata (default `leaf` = this topic only; `subtree` also searches sibling topics under the split). Output reports each match's `topic` and flags `sibling: true` for cross-topic hits.
- `okgv similar-batch --topic <path> --entries '<json_array>' [--top-k 5]` — batch version.

`--entry` takes the **complete candidate entry** (the same JSON you would submit), not a text snippet. Similarity is computed on the schema's embedding text, so the check only matches submit-time behavior when given the full entry; partial entries are rejected with `missing_field`.

### Submission
- `okgv submit --topic <path> --entry '<json>' [--review]` — upsert single entry. `--review` flags for review.
- `okgv submit-batch --topic <path> --entries '<json_array>' [--review]` — batch upsert.

### Topic Management
- `okgv create-topic --name <path> [--parents]` — create topic. Use --parents for mkdir -p.
- `okgv create-structure --file <path>` — create tree from JSON file.
- `okgv move-topic --source <path> --destination <path> [--dry-run]` — move topic under new parent.
- `okgv move-entry --id <uuid> --destination <path> [--dry-run]` — move entry to different topic.

### Retrieval
- `okgv get-vector --id <uuid>` — fetch entry from vector DB.
- `okgv get-graph --id <uuid>` — fetch entry from graph DB.

### Review
You can review entries submitted by yourself or other agents. Use `okgv review` to list pending entries, inspect their content, and approve or reject them.

- `okgv review [--topic <path>] [--status pending|approved|rejected] [--limit N]` — list review queue entries.
- `okgv review --count [--topic <path>]` — counts by status.
- `okgv review --export <file> [--topic <path>] [--status pending]` — export entries with content to JSON file.
- `okgv review --import <file>` — import review decisions from JSON file (reads `id` + `status` fields).
- `okgv review -i [--topic <path>]` — launch interactive terminal UI for review (humans only).
- `okgv approve --id <uuid>` — mark entry as approved.
- `okgv reject --id <uuid>` — mark entry as rejected.
- `okgv review --purge-rejected [--dry-run]` — delete rejected entries from all DBs.
- `okgv review --recover-rejected [--dry-run]` — set rejected entries back to pending.

### Log
- `okgv log [--limit N] [--offset N]` — list recent submissions (default: last 20).
- `okgv log --topic <path>` — filter by topic.
- `okgv log --after <ISO-timestamp> --before <ISO-timestamp>` — filter by time range.
- `okgv log --count` — total submissions grouped by topic. Add `--topic` for single topic count.

### Undo & Maintenance
- `okgv undo <ISO-timestamp> [--dry-run]` — delete all entries submitted after timestamp.
- `okgv reconcile [--dry-run] [--batch-size N]` — find and fix inconsistencies between graph and vector tables.

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
- Use `report` to plan at the start of a run, re-check after filling a target, and verify balance at the end. Its `empty_cells` list tells you exactly which leaf-topic/field-value combinations still need entries. On large trees, scope with `--topic <path>` to keep the output small.
- Use `least-topic` for a cheap mid-loop "where next": one answer, compares the direct children of one parent by raw recursive count. It ignores balance fields — a topic it skips may still be missing entries for specific field values, which only `report` (or `topic-stats`) reveals.
- Use `topic-stats` to drill into the metadata distribution of a single topic.
- Always check `similar` before submitting — avoid redundant entries. By default similarity only sees the target leaf; topics with `similarity_scope: subtree` also surface near-matches in sibling topics. Treat a `sibling: true` match as a **variant to note** (the same stem may be an intentional refinement in a sibling), not an automatic rejection — judge by the content, and let the review queue make the final call.
- Use batch commands when processing multiple entries — single model load, much faster.
- If a topic grows too large, suggest creating subtopics to the user.
- Use `--dry-run` on destructive commands (`undo`, `reconcile`, `move-topic`, `move-entry`) to preview before committing.
- Use `reconcile` periodically to detect internal inconsistencies.
