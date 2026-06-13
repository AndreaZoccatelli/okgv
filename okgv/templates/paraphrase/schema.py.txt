"""Entry schema: a flat pool of diverse paraphrases / stylistic variants.

No hierarchy and nothing to balance; okgv is used purely for in-the-loop dedup
feedback (the nearest existing variant comes back with full content, so the
agent can steer away from it). Each entry is a single text, embedded directly.

This sits at the edge of okgv's niche (see the README's "reach for something
else"): if a deterministic cosine cutoff over a fixed corpus would satisfy you,
a plain vector store is the better tool. Use this only when you want generation
steered by surfaced near-misses.

Set OKGV_SCHEMA=config.schema:ParaphraseSchema in .env.
"""

from okgv.protocols import PropertyDefinition
from okgv.validators import NotEmpty

text = NotEmpty("text")


class ParaphraseEntry:
    def __init__(self, raw: dict):
        self.text = text.validate(raw["text"])


class ParaphraseSchema:
    entry_class = ParaphraseEntry
    validators = [text]
    field_descriptions = {
        "text": "one paraphrase or stylistic variant of the seed sentence",
    }

    @staticmethod
    def metadata(entry: ParaphraseEntry) -> dict:
        return {}

    @staticmethod
    def graph_properties(entry: ParaphraseEntry) -> dict:
        return {"text": entry.text}

    @staticmethod
    def vector_properties(entry: ParaphraseEntry) -> dict:
        return {"text": entry.text}

    @staticmethod
    def embedding_text(entry: ParaphraseEntry) -> str:
        return entry.text

    @staticmethod
    def vector_property_definitions() -> list[PropertyDefinition]:
        return [PropertyDefinition(name="text", data_type="text")]
