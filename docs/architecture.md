# Architecture & Internals

## Storage

Single storage layer:

- **SQLite** (`okgv.db`): topics, entries, vectors (via [sqlite-vec](https://github.com/asg017/sqlite-vec)), submission log, review state. All local, zero setup, fully portable single file.

Every entry is identified by a deterministic UUID5 (computed from canonical JSON of the entry content).

Use `okgv tree` to visualize the topic hierarchy in the terminal.

## Topic Structure

Topics form a tree with path-based identity:

```
algebra                          → path: "algebra"
├── linear_algebra               → path: "algebra/linear_algebra"
│   ├── basics                   → path: "algebra/linear_algebra/basics"
│   └── advanced                 → path: "algebra/linear_algebra/advanced"
└── abstract_algebra             → path: "algebra/abstract_algebra"
```

Entries can live at any level. Topic queries (counts, listings, stats) are recursive: querying `algebra` includes entries under all its descendants. Similarity search is the exception, it is scoped to the exact target topic only (see [Similarity Scoping](#similarity-scoping)).

### Tree TUI
```bash
# Terminal UI for tree structure (requires: pip install okgv[tui])
okgv tree -i
```
![Tree TUI](../resources/tree_tui.svg)

## Similarity Scoping

**Similarity search is scoped to the exact target topic.** When checking for duplicates before submitting to `topic1/sub_topic1`, only entries already in `topic1/sub_topic1` are compared. Entries in sibling topics like `topic1/sub_topic2` are not considered.

This is by design for performance (native sqlite-vec pre-filtering) and correctness (each topic has its own semantic scope). It means:

- **Same topic name, different parent = fine.** `dogs/legs` and `cats/legs` both contain "legs" entries but about different animals, no cross-dedup needed.
- **The full path determines semantic scope.** A well-structured topic tree naturally avoids ambiguity.
- **Avoid overlapping topics.** If `anatomy/limbs` and `dogs/legs` could contain similar entries, design the tree so each leaf has a clear, non-overlapping scope.

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

## Reliability

### Batch Operations

`submit-batch` and `similar-batch` load the embedding model once and process all entries with a single model load.

`undo` and `reconcile` also use batch deletes.

### Consistency

All data lives in a single SQLite database (`okgv.db`). Graph entries and vector entries share the same connection, so operations are atomic within a single command. Use `okgv reconcile` to detect and fix any inconsistencies between graph and vector tables.
