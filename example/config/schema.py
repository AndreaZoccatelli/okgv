"""Function calling schema for LLM tool-use training data.

Each entry is a user query paired with the correct function call.
Compact entries (~30-50 tokens) designed for fine-tuning tool-use capabilities.

Set OKGV_SCHEMA=config.schema:ToolCallSchema in .env.
"""

from okgv.protocols import PropertyDefinition
from okgv.validators import NotEmpty, OneOf

query = NotEmpty("query")
function = NotEmpty("function")
arguments = NotEmpty("arguments")
difficulty = OneOf("difficulty", {"easy", "medium", "hard"})


class ToolCallEntry:
    def __init__(self, raw: dict):
        self.query = query.validate(raw["query"])
        self.function = function.validate(raw["function"])
        self.arguments = arguments.validate(raw["arguments"])
        self.difficulty = difficulty.validate(raw.get("difficulty", "medium"))


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
