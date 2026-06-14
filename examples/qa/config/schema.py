"""Entry schema: question-answer pairs graded by difficulty.

The subject lives in the topic tree (algebra/..., calculus/...). Each entry
holds a question, its answer, and a difficulty grade balanced via `difficulty`.
Some leaves narrow difficulty per topic through structure.json `_meta` (the
`entry` namespace), which okgv enforces automatically, no code here.

Set OKGV_SCHEMA=config.schema:QASchema in .env.
"""

from okgv.protocols import PropertyDefinition
from okgv.validators import NotEmpty, OneOf

question = NotEmpty("question")
answer = NotEmpty("answer")
difficulty = OneOf("difficulty", {"easy", "medium", "hard"})


class QAEntry:
    def __init__(self, raw: dict):
        self.question = question.validate(raw["question"])
        self.answer = answer.validate(raw["answer"])
        # A stored attribute (not a metadata-only computed value), so a topic's
        # `_meta` `entry` constraint can narrow it via getattr.
        self.difficulty = difficulty.validate(raw["difficulty"])


class QASchema:
    entry_class = QAEntry
    validators = [question, answer, difficulty]
    balance_fields = ["difficulty"]
    field_descriptions = {
        "question": "the problem statement, self-contained",
        "answer": "the full worked answer",
        "difficulty": (
            "cognitive load for a graduate student",
            {
                "easy": "single concept, direct application",
                "medium": "combine two or three concepts",
                "hard": "multi-step reasoning or edge cases",
            },
        ),
    }

    @staticmethod
    def metadata(entry: QAEntry) -> dict:
        # Stored in both stores so difficulty is balanceable and reportable.
        return {"difficulty": entry.difficulty}

    @staticmethod
    def graph_properties(entry: QAEntry) -> dict:
        return {"question": entry.question, "answer": entry.answer}

    @staticmethod
    def vector_properties(entry: QAEntry) -> dict:
        # Dedup is on the question (see embedding_text); keep the answer here too
        # so `similar` surfaces full content.
        return {"question": entry.question, "answer": entry.answer}

    @staticmethod
    def embedding_text(entry: QAEntry) -> str:
        return entry.question

    @staticmethod
    def vector_property_definitions() -> list[PropertyDefinition]:
        return [
            PropertyDefinition(name="difficulty", data_type="text"),
            PropertyDefinition(name="question", data_type="text"),
            PropertyDefinition(name="answer", data_type="text"),
        ]
