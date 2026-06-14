# Reviewer Guide

You are reviewing entries in a synthetic knowledge base managed by the `okgv` CLI. Entries flagged with `--review` during submission land in a review queue with status `pending`. Your job is to inspect them and approve or reject each one.

## Workflow

1. **Learn entry format**: `okgv entry-prompt` → understand field constraints and valid values
2. **Check queue size**: `okgv review --count` → see how many entries are pending per topic
3. **Export pending entries**: `okgv review --export review.json --status pending [--topic <path>]` → get entries with full content as JSON
4. **Inspect each entry** against the quality criteria below
5. **Record decisions**: edit `review.json` — set `"status"` to `"approved"` or `"rejected"` for each entry
6. **Import decisions**: `okgv review --import review.json` → apply your verdicts in bulk
7. **Clean up**: `okgv review --purge-rejected --dry-run` → preview, then `okgv review --purge-rejected` → delete rejected entries from all DBs

## Commands

- `okgv review --count [--topic <path>]` — pending/approved/rejected counts.
- `okgv review [--topic <path>] [--status pending] [--limit N]` — list queue entries (IDs, status, topic).
- `okgv review --export <file> [--topic <path>] [--status pending]` — export entries with full content to JSON.
- `okgv review --import <file>` — import decisions from JSON (reads `id` + `status` fields).
- `okgv approve --id <uuid>` — approve a single entry.
- `okgv reject --id <uuid>` — reject a single entry.
- `okgv review --purge-rejected [--dry-run]` — delete rejected entries from all DBs.
- `okgv review --recover-rejected [--dry-run]` — set rejected entries back to pending.
- `okgv similar --topic <path> --entry '<json>'` — check if entry is too similar to existing ones. Pass the complete entry JSON (all fields), not a text snippet. For topics with `similarity_scope: subtree`, results also include sibling topics, each match tagged with its `topic` and `sibling: true`. Treat a `sibling` match as a variant to weigh, not an automatic duplicate — the same stem can be an intentional refinement in a sibling.
- `okgv get-by-topic --topic <path> [--limit N]` — sample approved entries for comparison.

## Quality Criteria

Evaluate each entry on:

1. **Schema compliance** — all required fields present, values within declared constraints (run `okgv entry-prompt` to see them)
2. **Correctness** — factual accuracy, logical consistency, no contradictions
3. **Novelty** — not a near-duplicate of existing entries. Use `okgv similar` to check against the topic. Under subtree scope, a near-match in a sibling topic is a variant signal, not necessarily a rejection: reject only if it is a true duplicate rather than an intentional cross-sibling variant
4. **Topic fit** — entry belongs in its assigned topic, not a better-fitting sibling
5. **Completeness** — no placeholder text, empty fields, or truncated content
6. **Language quality** — clear, well-formed, appropriate register for the dataset's purpose

## Decision Rules

- **Approve** if entry meets all criteria
- **Reject** if any criterion fails — note the reason in your analysis so patterns can inform future generation
- When unsure, compare against approved entries in the same topic using `okgv get-by-topic` to calibrate

## Tips

- Work topic-by-topic for consistent quality standards.
- Export + import is faster than approving/rejecting one by one.
- Use `--dry-run` before purging to verify what will be deleted.
- If many entries fail for the same reason, flag the pattern — it may indicate a generation prompt issue.
