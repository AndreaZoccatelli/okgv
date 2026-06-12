"""Function calling schema for LLM tool-use training data.

Each entry is a user query paired with the correct function call.
Compact entries (~30-50 tokens) designed for fine-tuning tool-use capabilities.

Set OKGV_SCHEMA=config.schema:ToolCallSchema in .env.
"""

from okgv.protocols import PropertyDefinition
from okgv.validators import IsType, NotEmpty, OneOf


class FunctionSpec:
    """One function's contract: name plus required/optional parameter validators."""

    def __init__(self, function: str, required: dict, optional: dict):
        self.function = function
        self.required = required  # {param_name: validator}
        self.optional = optional

    def validate(self, function: str, arguments) -> dict:
        if function != self.function:
            raise ValueError(f"function: topic expects '{self.function}', got '{function}'")
        if not isinstance(arguments, dict):
            raise ValueError(f"arguments: must be a JSON object, got {type(arguments).__name__}")
        keys = set(arguments)
        missing = set(self.required) - keys
        unknown = keys - set(self.required) - set(self.optional)
        if missing:
            raise ValueError(f"arguments: missing required keys {sorted(missing)}")
        if unknown:
            raise ValueError(f"arguments: unknown keys {sorted(unknown)}")
        for name, value in arguments.items():
            spec = self.required.get(name) or self.optional[name]
            spec.validate(value)
        return arguments


# Per-topic function contracts. Interim home: this registry duplicates what
# structure.json knows as topics; it moves into structure.json _meta blocks
# once node metadata lands (implementation_plan.md M6/M7).
FUNCTIONS: dict[str, FunctionSpec] = {
    "weather/current_conditions": FunctionSpec(
        "get_current_weather",
        required={"location": NotEmpty("location")},
        optional={"units": OneOf("units", {"celsius", "fahrenheit"})},
    ),
    "weather/forecast": FunctionSpec(
        "get_forecast",
        required={"location": NotEmpty("location"), "days": IsType("days", int)},
        optional={"units": OneOf("units", {"celsius", "fahrenheit"})},
    ),
    "weather/alerts": FunctionSpec(
        "get_weather_alerts",
        required={"location": NotEmpty("location")},
        optional={"severity": OneOf("severity", {"advisory", "watch", "warning"})},
    ),
    "calendar/create_event": FunctionSpec(
        "create_event",
        required={"title": NotEmpty("title"), "start_time": NotEmpty("start_time")},
        optional={
            "end_time": NotEmpty("end_time"),
            "location": NotEmpty("location"),
            "attendees": IsType("attendees", list),
        },
    ),
    "calendar/list_events": FunctionSpec(
        "list_events",
        required={"date": NotEmpty("date")},
        optional={"calendar": NotEmpty("calendar"), "query": NotEmpty("query")},
    ),
    "calendar/modify_event": FunctionSpec(
        "modify_event",
        required={"event_id": NotEmpty("event_id")},
        optional={
            "title": NotEmpty("title"),
            "start_time": NotEmpty("start_time"),
            "end_time": NotEmpty("end_time"),
            "location": NotEmpty("location"),
        },
    ),
    "search/web_search": FunctionSpec(
        "web_search",
        required={"query": NotEmpty("query")},
        optional={"num_results": IsType("num_results", int), "site": NotEmpty("site")},
    ),
    "search/file_search": FunctionSpec(
        "file_search",
        required={"query": NotEmpty("query")},
        optional={"path": NotEmpty("path"), "file_type": NotEmpty("file_type")},
    ),
    "search/contact_lookup": FunctionSpec(
        "contact_lookup",
        required={"name": NotEmpty("name")},
        optional={"field": OneOf("field", {"phone", "email", "address"})},
    ),
    "messaging/send_message": FunctionSpec(
        "send_message",
        required={"to": NotEmpty("to"), "body": NotEmpty("body")},
        optional={
            "subject": NotEmpty("subject"),
            "priority": OneOf("priority", {"low", "normal", "high"}),
        },
    ),
    "messaging/read_messages": FunctionSpec(
        "read_messages",
        required={},
        optional={
            "sender": NotEmpty("sender"),
            "unread_only": IsType("unread_only", bool),
            "limit": IsType("limit", int),
        },
    ),
    "messaging/manage_threads": FunctionSpec(
        "manage_thread",
        required={
            "thread_id": NotEmpty("thread_id"),
            "action": OneOf("action", {"archive", "label", "delete"}),
        },
        optional={"label": NotEmpty("label")},
    ),
    "math/arithmetic": FunctionSpec(
        "calculate",
        required={"expression": NotEmpty("expression")},
        optional={},
    ),
    "math/unit_conversion": FunctionSpec(
        "convert_units",
        required={
            "value": IsType("value", (int, float)),
            "from_unit": NotEmpty("from_unit"),
            "to_unit": NotEmpty("to_unit"),
        },
        optional={},
    ),
    "math/statistics": FunctionSpec(
        "compute_stats",
        required={"data": IsType("data", list)},
        optional={"operation": OneOf("operation", {"mean", "median", "std", "sum"})},
    ),
}

query = NotEmpty("query")
function = OneOf("function", {spec.function for spec in FUNCTIONS.values()})
arguments = IsType("arguments", dict)
difficulty = OneOf("difficulty", {"easy", "medium", "hard"})


class ToolCallEntry:
    def __init__(self, raw: dict):
        self.query = query.validate(raw["query"])
        self.function = function.validate(raw["function"])
        self.arguments = arguments.validate(raw["arguments"])
        self.difficulty = difficulty.validate(raw["difficulty"])


class ToolCallSchema:
    entry_class = ToolCallEntry
    validators = [query, function, arguments, difficulty]
    balance_fields = ["difficulty"]
    field_descriptions = {
        "query": "natural user request that implies a function call, 5-25 words",
        "function": "the function name to call, matching the topic's available functions",
        "arguments": "JSON object with the function arguments extracted from the query",
        "difficulty": (
            "how hard it is to identify the correct function and extract arguments",
            {
                "easy": "explicit keywords, all arguments stated directly",
                "medium": "requires inference or has optional arguments",
                "hard": "ambiguous phrasing, implicit arguments, or could map to multiple functions",
            },
        ),
    }

    @staticmethod
    def validate_for_topic(entry: ToolCallEntry, topic: str) -> None:
        spec = FUNCTIONS.get(topic)
        if spec is None:
            raise ValueError(
                f"topic '{topic}' has no function spec; entries can only be submitted to: {sorted(FUNCTIONS)}"
            )
        spec.validate(entry.function, entry.arguments)

    @staticmethod
    def metadata(entry: ToolCallEntry) -> dict:
        return {
            "function": entry.function,
            "difficulty": entry.difficulty,
        }

    @staticmethod
    def graph_properties(entry: ToolCallEntry) -> dict:
        return {"query": entry.query}

    @staticmethod
    def vector_properties(entry: ToolCallEntry) -> dict:
        return {
            "query": entry.query,
            "arguments": str(entry.arguments),
        }

    @staticmethod
    def embedding_text(entry: ToolCallEntry) -> str:
        return entry.query

    @staticmethod
    def vector_property_definitions() -> list[PropertyDefinition]:
        return [
            PropertyDefinition(name="function", data_type="text"),
            PropertyDefinition(name="difficulty", data_type="text"),
            PropertyDefinition(name="query", data_type="text"),
            PropertyDefinition(name="arguments", data_type="text"),
        ]
