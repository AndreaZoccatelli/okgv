# candidate.py — Agent CLI for the Knowledge Base

CLI designed for AI agents. Agent owns the decision loop. Built with Click following agent-friendly CLI practices.

## Principles

- **JSON to stdout, logs to stderr** — stdout is the API contract
- **Structured errors** — JSON error objects with `error`, `detail`, `suggestion`
- **Semantic exit codes** — 0=success, 1=failure, 2=bad input, 3=not found, 4=connection error
- **Idempotent** — `submit` is upsert, safe to retry
- **Stdin support** — `--entry -` reads from stdin for large payloads
- **Self-documenting** — `--help` on every command with examples

## Agent Workflow

```
1. python candidate.py least-topic
   → pick topic with fewest entries

2. Agent generates a candidate entry (LLM call)

3. python candidate.py similar --topic <topic> --entry '<json>'
   → agent receives top-5 most similar entries WITH FULL CONTENT
   → search restricted to entries belonging to <topic>

4. Agent reads similar entries, decides:
   - Novel enough → proceed to step 5
   - Too similar  → edit candidate, go back to step 3

5. python candidate.py submit --topic <topic> --entry '<json>'
   → upserted into both Neo4j + Weaviate, logged to log.json
```

## Commands

### `least-topic`

```bash
python candidate.py least-topic
```
```json
{"topic": "geometry", "count": 15}
```

### `similar`

```bash
python candidate.py similar --topic algebra --entry '{"question":"...","answer":"...","dictionary":{"A":"..."}}' --top-k 5
# or from stdin:
echo '{"question":"...","answer":"...","dictionary":{"A":"..."}}' | python candidate.py similar --topic algebra --entry -
```
```json
{
  "candidate_id": "uuid5-of-candidate",
  "similar": [
    {
      "id": "existing-uuid",
      "certainty": 0.89,
      "question": "What is...",
      "answer": "B",
      "options": {"A": "...", "B": "..."}
    }
  ]
}
```

Restricted to entries in the given topic. Returns full content so agent can reason about semantic overlap.

### `submit`

```bash
python candidate.py submit --topic algebra --entry '{"question":"...","answer":"...","dictionary":{"A":"..."}}'
```
```json
{"id": "uuid5", "submitted": true}
```

Idempotent upsert. Safe to retry on transient failures.

## Error Handling

Errors go to stderr as structured JSON:

```json
{
  "error": "missing_field",
  "detail": "Entry JSON missing required key: dictionary",
  "suggestion": "Ensure entry has \"dictionary\" field"
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

## Configuration

All via environment variables:

| Variable | Default |
|----------|---------|
| `NEO4J_URI` | `bolt://localhost:7687` |
| `NEO4J_USER` | `neo4j` |
| `NEO4J_PASSWORD` | `password` |
| `WEAVIATE_HOST` | `localhost` |
| `WEAVIATE_PORT` | `8080` |
| `WEAVIATE_GRPC_PORT` | `50051` |
| `WEAVIATE_API_KEY` | (none) |
| `EMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` |
| `COLLECTION_NAME` | `knowledge_base` |

## Session Logging

Every `submit` appends to `log.json`:

```json
{"2026-05-30T12:00:00+00:00": ["uuid1"]}
```
