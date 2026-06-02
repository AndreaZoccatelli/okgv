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
from okgv.validators import NotEmpty, OneOf

difficulty = OneOf("difficulty", {"easy", "medium", "hard"})
utterance = NotEmpty("utterance")
intent = NotEmpty("intent")


class IntentEntry:
    def __init__(self, raw: dict):
        self.utterance = utterance.validate(raw["utterance"])
        self.intent = intent.validate(raw["intent"])
        self.difficulty = difficulty.validate(raw.get("difficulty", "medium"))

    def char_length(self) -> int:
        return len(self.utterance)


class IntentSchema:
    entry_class = IntentEntry
    validators = [utterance, intent, difficulty]
    balance_fields = ["difficulty"]
    field_descriptions = {
        "utterance": "a realistic user message, 5-30 words, natural tone",
        "intent": "the user's intent category, matching the topic structure",
        "difficulty": (
            "how hard it is to classify the utterance correctly",
            {
                "easy": "clear keyword match, unambiguous intent",
                "medium": "requires context or mild ambiguity",
                "hard": "ambiguous phrasing, multiple possible intents, sarcasm or implicit meaning",
            },
        ),
    }

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
