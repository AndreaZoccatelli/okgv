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

Two databases, each with a role:

- **Neo4j** — relationships: topics, sub-topics, entries. Visual exploration via Neo4j Desktop.
- **Weaviate** — vectors: entry embeddings, similarity search.

Every entry lives in both DBs, linked by a deterministic UUID5 (computed from canonical JSON of the entry content).

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
1. okgv least-topic --topic <parent>
   → pick child topic with fewest entries

2. Agent generates candidate entry (LLM call)

3. okgv similar --topic <topic> --entry '<json>'
   → top-N most similar entries WITH FULL CONTENT
   → agent decides: novel enough → submit, too similar → regenerate

4. okgv submit --topic <topic> --entry '<json>'
   → upserted into both DBs, logged to log.json
```

## Commands

All output is JSON to stdout. Logs go to stderr.

| Command | Purpose |
|---------|---------|
| `init` | Scaffold project files (.env, schema.py, topics.json) |
| `create-topic` | Create topic by path. `--parents` for mkdir -p behavior |
| `create-structure` | Create topic tree from JSON file |
| `least-topic` | Child topic with fewest entries. `--topic` scopes to parent |
| `topic-stats` | Entry counts grouped by metadata fields |
| `similar` | Top-N similar entries within a topic |
| `similar-batch` | Batch similarity search (single model load) |
| `submit` | Upsert entry into both DBs |
| `submit-batch` | Batch upsert (single model load) |
| `move-topic` | Move topic/subtopic under different parent |
| `move-entry` | Move entry to different topic |
| `get-by-topic` | Fetch sample entries for a topic |
| `get-vector` | Fetch entry from vector DB by ID |
| `get-graph` | Fetch entry from graph DB by ID |
| `undo` | Delete entries submitted after a timestamp |

### Examples

```bash
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

# Submit
okgv submit --topic algebra/linear_algebra/basics --entry '{"text": "..."}'

# Batch operations (single model load)
okgv submit-batch --topic algebra --entries '[{"text": "..."}, {"text": "..."}]'

# Move a subtopic
okgv move-topic --source algebra/basics --destination geometry

# Undo recent submissions
okgv undo 2026-05-30T12:00:00
```

## Setup

### Neo4j

Neo4j Desktop recommended — provides visual graph exploration.

1. Download [Neo4j Desktop](https://neo4j.com/download/)
2. Create a Project → add local DBMS (5.x recommended)
3. Set password, start DBMS
4. Default connection: `bolt://localhost:7687`, user `neo4j`

### Weaviate

Follow the [official installation guide](https://weaviate.io/developers/weaviate/installation). Docker recommended for local development.

### Configuration

All via environment variables (`.env` file or exported):

| Variable | Default | Purpose |
|----------|---------|---------|
| `OKGV_SCHEMA` | built-in QA schema | `module:ClassName` schema specifier |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection |
| `NEO4J_USER` | `neo4j` | |
| `NEO4J_PASSWORD` | `password` | |
| `NEO4J_DATABASE` | `neo4j` | Database name in Neo4j Desktop |
| `WEAVIATE_HOST` | `localhost` | |
| `WEAVIATE_PORT` | `8080` | HTTP port |
| `WEAVIATE_GRPC_PORT` | `50051` | gRPC port |
| `WEAVIATE_COLLECTION` | `knowledge_base` | Collection name for entries |
| `WEAVIATE_API_KEY` | (none) | Optional API key |
| `EMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model |

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

Every `submit` appends to `log.json`:

```json
{"2026-05-30T12:00:00+00:00": {"algebra/basics": ["uuid1", "uuid2"]}}
```

Used by `undo` to roll back submissions after a given timestamp.
