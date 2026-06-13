# Example: Function Calling Dataset

A fully configured `okgv` knowledge base for building synthetic LLM tool-use data. Each entry pairs a natural user query with the correct function call, name and extracted arguments. Use this folder as a reference for how the pieces fit together.

![demo](media/demo.gif)

## What's in here

```
config/structure.json   topic hierarchy + per-topic function contracts (_meta)
config/schema.py        entry schema (ToolCallSchema)
.env                    wires schema, embedding model, review mode into okgv
prompts/                agent guides, one per workflow phase
media/                  demo recording
```

## How it's wired

`.env` is what connects this example to the `okgv` CLI:

```bash
OKGV_SCHEMA=config.schema:ToolCallSchema            # which schema class okgv loads
EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2  # embeddings for similarity / dedup
OKGV_REVIEW=all                                     # every submission enters a review queue
```

The database itself (`okgv.db`) is not checked in. To create it with the example's topic structure:

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

The global field validators above are the baseline every entry must satisfy. The *per-topic* rules (which `function` a topic expects, which arguments are required or allowed) are not hardcoded in the schema; they live in `structure.json` `_meta` blocks (below) and `ToolCallSchema.validate_for_topic` enforces the folded effective spec for whichever topic an entry is filed under.

## The topic hierarchy

Defined in `config/structure.json`. Each leaf carries a `_meta` block declaring its function contract, parsed and folded by okgv at load:

```
weather/          (current_conditions, forecast, alerts)
calendar/         (create_event, list_events, modify_event)
search/           (web_search, file_search, contact_lookup)
messaging/        (send_message, read_messages, manage_threads)
math/             (arithmetic, unit_conversion, statistics)
```

A `_meta` block names the function and its argument validators, for example `weather/current_conditions`:

```json
"_meta": {
  "function": "get_current_weather",
  "required": {"location": "not_empty"},
  "optional": {"units": ["celsius", "fahrenheit"]}
}
```

(`"not_empty"` is shorthand for a `NotEmpty` validator on that key; a list of strings is a `OneOf` over those values. The explicit `{"type": ..., "field": ...}` form works too.)

`_meta` blocks compose along a path: a child can narrow or add to what its parent declared but never relax it. `weather/current_conditions` is split two ways to show this — `metric` narrows `units` to `["celsius"]`, and `no_unit_stated` forbids `units` entirely. Both inherit the parent's `get_current_weather` function and required `location`, so an entry filed there is validated against the parent contract *and* the child's refinement. This is what makes targeted generation possible: "current conditions where the user never states a unit" becomes its own topic with its own quota and prompt.

15 leaf topics × 3 difficulty levels seed the coverage grid, each cell filled with diverse entries; refinement children like `metric` add further partitions where they earn their own quota.

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

## Explore the dataset

Once the database has been created and populated by a generation run:

```bash
okgv get-structure                                     # topic tree
okgv topic-stats --topic weather --fields "difficulty" # coverage per difficulty
okgv log --count                                       # entries per topic
okgv review --count                                    # review queue status
```
