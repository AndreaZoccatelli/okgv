# Agent Guide — Function Calling Dataset

## Goal

Build a synthetic dataset for fine-tuning LLM tool-use / function calling capabilities. Each entry pairs a natural user query with the correct function call (name + arguments). The dataset should be diverse across function types, argument complexity, and phrasing difficulty.

## Instructions

You are generating entries for a synthetic knowledge base using the `okgv` CLI. Run `okgv cli-prompt` to learn how to use the CLI. Do NOT look at okgv source code. Use only the CLI.

## Function Signatures

Reference these when generating entries. Arguments in `[]` are optional. Signatures are enforced at submission: the function must match the target topic, required arguments must be present, unknown argument keys are rejected, and values must satisfy the constraints below. Arguments without a listed constraint are non-empty strings. The signatures live in `config/structure.json` `_meta` blocks; run `okgv entry-prompt --topic <path>` before generating for a topic to see its enforced function name and argument signature directly.

### Weather
- `get_current_weather(location, [units])` — current conditions for a location. units: `celsius` | `fahrenheit`
- `get_forecast(location, days, [units])` — multi-day forecast. days: integer; units: `celsius` | `fahrenheit`
- `get_weather_alerts(location, [severity])` — active weather alerts. severity: `advisory` | `watch` | `warning`

### Calendar
- `create_event(title, start_time, [end_time], [location], [attendees])` — schedule new event. attendees: list
- `list_events(date, [calendar], [query])` — list events for a date/range
- `modify_event(event_id, [title], [start_time], [end_time], [location])` — update existing event

### Search
- `web_search(query, [num_results], [site])` — search the web. num_results: integer
- `file_search(query, [path], [file_type])` — search local files
- `contact_lookup(name, [field])` — find contact info. field: `phone` | `email` | `address`

### Messaging
- `send_message(to, body, [subject], [priority])` — send a message. priority: `low` | `normal` | `high`
- `read_messages([sender], [unread_only], [limit])` — read messages. unread_only: boolean; limit: integer
- `manage_thread(thread_id, action, [label])` — archive/label/delete thread. action: `archive` | `label` | `delete`

### Math
- `calculate(expression)` — evaluate arithmetic expression
- `convert_units(value, from_unit, to_unit)` — unit conversion. value: number
- `compute_stats(data, [operation])` — mean/median/std/sum on a list. data: list; operation: `mean` | `median` | `std` | `sum`

## Entry Examples

Easy — explicit keywords, all arguments stated:
```json
{"query": "What's the weather in Tokyo?", "function": "get_current_weather", "arguments": {"location": "Tokyo"}, "difficulty": "easy"}
```

Medium — requires inference:
```json
{"query": "Block off tomorrow 2-4pm for dentist", "function": "create_event", "arguments": {"title": "Dentist", "start_time": "tomorrow 2pm", "end_time": "tomorrow 4pm"}, "difficulty": "medium"}
```

Hard — ambiguous, implicit:
```json
{"query": "Remind me about that thing with Sarah next week", "function": "list_events", "arguments": {"date": "next week", "query": "Sarah"}, "difficulty": "hard"}
```

Every entry must state `difficulty` explicitly; entries without it are rejected. When unsure between two levels, pick `medium`.

## Action

After you understood how to use the CLI ask me what I want to do.
