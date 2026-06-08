# Example: Function Calling Dataset

A fully configured `okgv` knowledge base for building synthetic LLM tool-use data. Each entry pairs a natural user query with the correct function call, name and extracted arguments. Use this folder as a reference for how the pieces fit together.

![demo](media/demo.gif)

## What's in here

```
config/structure.json   topic hierarchy (the 15 leaf topics)
config/schema.py        entry schema (ToolCallSchema)
.env                    wires schema, embedding model, review mode into okgv
prompts/                agent guides, one per workflow phase
okgv.db                 the populated knowledge base
media/                  demo recording
```

## How it's wired

`.env` is what connects this example to the `okgv` CLI:

```bash
OKGV_SCHEMA=config.schema:ToolCallSchema            # which schema class okgv loads
EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2  # embeddings for similarity / dedup
OKGV_REVIEW=all                                     # every submission enters a review queue
```

To reproduce the structure in a fresh DB:

```bash
cd example
pip install -e "..[embeddings]"
okgv create-structure --file config/structure.json
```

## The schema

Every entry has four fields, defined and validated by `ToolCallSchema` in `config/schema.py`:

```json
{
  "query": "What's the weather in Tokyo?",
  "function": "get_current_weather",
  "arguments": {"location": "Tokyo"},
  "difficulty": "easy"
}
```

- `query` — natural user request, 5-25 words
- `function` — function name matching the topic structure
- `arguments` — JSON object with extracted parameters
- `difficulty` — `easy` (explicit), `medium` (requires inference), `hard` (ambiguous/implicit)

The schema class also declares which fields are balanced (`difficulty`), what gets embedded for similarity (`query`), and how fields split across the graph and vector stores. Run `okgv entry-prompt` to see how it renders for a generating agent.

## The topic hierarchy

Defined in `config/structure.json`:

```
weather/          (current_conditions, forecast, alerts)
calendar/         (create_event, list_events, modify_event)
search/           (web_search, file_search, contact_lookup)
messaging/        (send_message, read_messages, manage_threads)
math/             (arithmetic, unit_conversion, statistics)
```

15 leaf topics × 3 difficulty levels = 45 cells, each filled with diverse entries.

## The prompts

Each file in `prompts/` is a guide handed to a coding agent for one phase of building the dataset. Together they trace the full workflow that produced this example:

- `schema-guide.md` — how the entry schema in `config/schema.py` was designed
- `structure-prompt.md` — how the topic tree in `config/structure.json` was designed
- `generation-guide.md` — how entries were generated and submitted (function signatures + examples live here)
- `reviewer-prompt.md` — how queued entries are approved or rejected

A generation run looks like:

```bash
claude "read generation-guide.md and start generating"
```

The agent uses `okgv` commands to explore the structure, generate entries, check for duplicates, and submit, all autonomously.

## Review

Because `.env` sets `OKGV_REVIEW=all`, submissions land in a `pending` queue instead of going live. An agent following `reviewer-prompt.md` triages them:

```bash
okgv review --count                                # pending/approved/rejected per topic
okgv review --export review.json --status pending  # export full content
okgv review --import review.json                   # apply approved/rejected decisions
okgv review --purge-rejected --dry-run             # preview cleanup, then drop --dry-run
```

## Explore the populated dataset

```bash
okgv get-structure                                     # topic tree
okgv topic-stats --topic weather --fields "difficulty" # coverage per difficulty
okgv log --count                                       # entries per topic
okgv review --count                                    # review queue status
```
