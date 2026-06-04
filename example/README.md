# Example: Function Calling Dataset

Synthetic training data for fine-tuning LLM tool-use capabilities. Each entry pairs a natural user query with the correct function call — name and extracted arguments.

## Setup

```bash
cd example
pip install -e "..[embeddings]"
okgv create-structure --file config/structure.json
```

## Entry format

```json
{
  "query": "What's the weather in Tokyo?",
  "function": "get_current_weather",
  "arguments": {"location": "Tokyo"},
  "difficulty": "easy"
}
```

Fields:
- `query` — natural user request, 5-25 words
- `function` — function name matching the topic structure
- `arguments` — JSON object with extracted parameters
- `difficulty` — `easy` (explicit), `medium` (requires inference), `hard` (ambiguous/implicit)

## Topic hierarchy

```
weather/          (current_conditions, forecast, alerts)
calendar/         (create_event, list_events, modify_event)
search/           (web_search, file_search, contact_lookup)
messaging/        (send_message, read_messages, manage_threads)
math/             (arithmetic, unit_conversion, statistics)
```

15 leaf topics × 3 difficulty levels = 45 cells to fill with diverse entries.

## Generate

Point a coding agent (e.g. Claude Code) at `generation-guide.md` and let it work:

```bash
claude "read generation-guide.md and start generating"
```

The agent will use `okgv` commands to explore the structure, generate entries, check for duplicates, and submit — all autonomously.

## Explore the dataset

After generation:

```bash
okgv get-structure                                    # topic tree
okgv topic-stats --topic weather --fields "difficulty" # coverage per difficulty
okgv log --count                                       # entries per topic
okgv review --count                                    # review queue status
```
