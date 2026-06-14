"""Entry schema: short texts labeled by intent (classification dataset).

The intent label is the topic path (the leaf *is* the label), so it is not an
entry field, the tree carries it. Each entry holds the utterance text and the
`channel` it arrived on; the dataset is balanced across `channel`, while the
tree balances intent.

Set OKGV_SCHEMA=config.schema:UtteranceSchema in .env.
"""

from okgv.protocols import PropertyDefinition
from okgv.validators import NotEmpty, OneOf

# Define validators once, reuse in Entry.__init__ and Schema.validators.
text = NotEmpty("text")
channel = OneOf("channel", {"chat", "email", "voice"})


class UtteranceEntry:
    def __init__(self, raw: dict):
        self.text = text.validate(raw["text"])
        self.channel = channel.validate(raw["channel"])


class UtteranceSchema:
    entry_class = UtteranceEntry
    validators = [text, channel]
    balance_fields = ["channel"]
    field_descriptions = {
        "text": "the user utterance, one or two sentences of natural language",
        "channel": (
            "the channel the message arrived on",
            {
                "chat": "live chat widget",
                "email": "email support inbox",
                "voice": "transcribed phone call",
            },
        ),
    }

    @staticmethod
    def metadata(entry: UtteranceEntry) -> dict:
        # Stored in both stores. The intent label lives in the topic path, not here.
        return {"channel": entry.channel}

    @staticmethod
    def graph_properties(entry: UtteranceEntry) -> dict:
        return {"text": entry.text}

    @staticmethod
    def vector_properties(entry: UtteranceEntry) -> dict:
        return {"text": entry.text}

    @staticmethod
    def embedding_text(entry: UtteranceEntry) -> str:
        return entry.text

    @staticmethod
    def vector_property_definitions() -> list[PropertyDefinition]:
        return [
            PropertyDefinition(name="channel", data_type="text"),
            PropertyDefinition(name="text", data_type="text"),
        ]
