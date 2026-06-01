"""Intent classification schema for LLM training data.

Each entry is a short user utterance with an intent label and difficulty.
Designed for fine-tuning intent classifiers — compact entries, ~20 tokens each.

Example entry:
    {
        "utterance": "I want my money back for order #4521",
        "intent": "request_refund",
        "difficulty": "easy"
    }
"""

from okgv.protocols import PropertyDefinition


class IntentEntry:
    VALID_DIFFICULTIES = {"easy", "medium", "hard"}

    def __init__(self, raw: dict):
        self.utterance = raw["utterance"]
        self.intent = raw["intent"]
        self.difficulty = raw.get("difficulty", "medium")
        if self.difficulty not in self.VALID_DIFFICULTIES:
            raise ValueError(
                f"difficulty must be one of {self.VALID_DIFFICULTIES}, got '{self.difficulty}'"
            )

    def char_length(self) -> int:
        return len(self.utterance)


class IntentSchema:
    entry_class = IntentEntry

    @staticmethod
    def metadata(entry: IntentEntry) -> dict:
        """Stored in both DBs. Used for grouping/filtering."""
        return {
            "intent": entry.intent,
            "difficulty": entry.difficulty,
            "char_length": entry.char_length(),
        }

    @staticmethod
    def graph_properties(entry: IntentEntry) -> dict:
        """Graph DB only. Full utterance for inspection."""
        return {"utterance": entry.utterance}

    @staticmethod
    def vector_properties(entry: IntentEntry) -> dict:
        """Vector DB only. Text for retrieval."""
        return {"utterance": entry.utterance}

    @staticmethod
    def embedding_text(entry: IntentEntry) -> str:
        """Embed the utterance for similarity search."""
        return entry.utterance

    @staticmethod
    def vector_property_definitions() -> list[PropertyDefinition]:
        return [
            PropertyDefinition(name="intent", data_type="text"),
            PropertyDefinition(name="difficulty", data_type="text"),
            PropertyDefinition(name="char_length", data_type="int"),
            PropertyDefinition(name="utterance", data_type="text"),
        ]
