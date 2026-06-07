# Agent Guide — Function Calling Dataset

## Goal

Build a synthetic dataset for fine-tuning LLM tool-use / function calling capabilities. Each entry pairs a natural user query with the correct function call (name + arguments). The dataset should be diverse across function types, argument complexity, and phrasing difficulty.

## Instructions

You are generating entries for a synthetic knowledge base using the `okgv` CLI. Run `okgv cli-prompt` to learn how to use the CLI. Do NOT look at okgv source code. Use only the CLI.

## Function Signatures

Reference these when generating entries. Arguments in `[]` are optional.

### Weather
- `get_current_weather(location, [units])` — current conditions for a location
- `get_forecast(location, days, [units])` — multi-day forecast
- `get_weather_alerts(location, [severity])` — active weather alerts

### Calendar
- `create_event(title, start_time, [end_time], [location], [attendees])` — schedule new event
- `list_events(date, [calendar], [query])` — list events for a date/range
- `modify_event(event_id, [title], [start_time], [end_time], [location])` — update existing event

### Search
- `web_search(query, [num_results], [site])` — search the web
- `file_search(query, [path], [file_type])` — search local files
- `contact_lookup(name, [field])` — find contact info

### Messaging
- `send_message(to, body, [subject], [priority])` — send a message
- `read_messages([sender], [unread_only], [limit])` — read messages
- `manage_thread(thread_id, action, [label])` — archive/label/delete thread

### Math
- `calculate(expression)` — evaluate arithmetic expression
- `convert_units(value, from_unit, to_unit)` — unit conversion
- `compute_stats(data, [operation])` — mean/median/std/sum on a list

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

## Action

After you understood how to use the CLI ask me what I want to do.
