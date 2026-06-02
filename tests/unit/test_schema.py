"""Tests for QAEntrySchema."""

import json

from okgv.schemas.qa import QAEntry, QAEntrySchema

SAMPLE_RAW = {
    "question": "What is 2+2?",
    "answer": "B",
    "dictionary": {"A": "3", "B": "4", "C": "5"},
}


class TestQAEntry:
    def test_fields_extracted(self):
        entry = QAEntry(SAMPLE_RAW)
        assert entry.question == "What is 2+2?"
        assert entry.answer == "B"
        assert entry.dictionary == {"A": "3", "B": "4", "C": "5"}

    def test_options(self):
        entry = QAEntry(SAMPLE_RAW)
        assert entry.options() == ["A", "B", "C"]

    def test_num_options(self):
        entry = QAEntry(SAMPLE_RAW)
        assert entry.num_options() == 3

    def test_missing_field_raises(self):
        import pytest

        with pytest.raises(KeyError):
            QAEntry({"question": "only question"})


class TestQAEntrySchema:
    def test_metadata(self):
        entry = QAEntry(SAMPLE_RAW)
        meta = QAEntrySchema.metadata(entry)
        assert meta == {"num_options": 3}

    def test_graph_properties(self):
        entry = QAEntry(SAMPLE_RAW)
        props = QAEntrySchema.graph_properties(entry)
        assert props["question"] == "What is 2+2?"
        assert props["answer"] == "B"
        assert props["options"] == ["A", "B", "C"]

    def test_vector_properties(self):
        entry = QAEntry(SAMPLE_RAW)
        props = QAEntrySchema.vector_properties(entry)
        assert props["question"] == "What is 2+2?"
        assert props["answer"] == "B"
        assert json.loads(props["options"]) == {"A": "3", "B": "4", "C": "5"}

    def test_embedding_text(self):
        entry = QAEntry(SAMPLE_RAW)
        text = QAEntrySchema.embedding_text(entry)
        assert text == "What is 2+2? B"

    def test_no_key_collision(self):
        entry = QAEntry(SAMPLE_RAW)
        meta_keys = set(QAEntrySchema.metadata(entry))
        graph_keys = set(QAEntrySchema.graph_properties(entry))
        vector_keys = set(QAEntrySchema.vector_properties(entry))
        assert meta_keys & graph_keys == set()
        assert meta_keys & vector_keys == set()

    def test_vector_definitions_cover_all_keys(self):
        entry = QAEntry(SAMPLE_RAW)
        defined = {pd.name for pd in QAEntrySchema.vector_property_definitions()}
        expected = set(QAEntrySchema.metadata(entry)) | set(QAEntrySchema.vector_properties(entry))
        assert defined == expected
