"""
Example EntrySchema: multiple-choice QA entries.

Raw input format:
  {"question": "...", "answer": "...", "dictionary": {"A": "...", "B": "..."}}

Metadata (both DBs): num_options
Graph properties: question, answer, options (list of keys)
Vector properties: question, options (JSON string), answer
Embedding text: question + answer concatenated
"""

import json

from okgv.protocols import PropertyDefinition


class QAEntry:
    """Entry class for QA datasets with multiple-choice options."""

    def __init__(self, raw: dict):
        self.question = raw["question"]
        self.answer = raw["answer"]
        self.dictionary = raw["dictionary"]

    def options(self) -> list[str]:
        return list(self.dictionary.keys())

    def num_options(self) -> int:
        return len(self.dictionary)


class QAEntrySchema:
    entry_class = QAEntry

    @staticmethod
    def metadata(entry: QAEntry) -> dict:
        return {"num_options": entry.num_options()}

    @staticmethod
    def graph_properties(entry: QAEntry) -> dict:
        return {
            "question": entry.question,
            "answer": entry.answer,
            "options": entry.options(),
        }

    @staticmethod
    def vector_properties(entry: QAEntry) -> dict:
        return {
            "question": entry.question,
            "options": json.dumps(entry.dictionary),
            "answer": entry.answer,
        }

    @staticmethod
    def embedding_text(entry: QAEntry) -> str:
        return f"{entry.question} {entry.answer}"

    @staticmethod
    def vector_property_definitions() -> list[PropertyDefinition]:
        return [
            PropertyDefinition(name="num_options", data_type="int"),
            PropertyDefinition(name="question", data_type="text"),
            PropertyDefinition(name="options", data_type="text"),
            PropertyDefinition(name="answer", data_type="text"),
        ]
