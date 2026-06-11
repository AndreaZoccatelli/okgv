# Commands

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

## Examples

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

# Check similarity before submitting.
# --entry takes the complete candidate entry (the same JSON you would submit),
# so the check embeds exactly what submit would embed.
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
