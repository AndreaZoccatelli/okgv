"""Integration tests for Weaviate VectorDB client."""

import pytest

from tests.integration.conftest import make_uuid, make_vector, make_vector_unique

# Pre-generate deterministic UUIDs
E1 = make_uuid("e1")
E2 = make_uuid("e2")
E3 = make_uuid("e3")
B1 = make_uuid("b1")
B2 = make_uuid("b2")
B3 = make_uuid("b3")
NONEXISTENT = make_uuid("nonexistent")


class TestUploadAndRetrieve:
    def test_upload_and_get_by_id(self, vector_db):
        vec = make_vector()
        vector_db.upload_entry(E1, {"text": "hello", "text_length": 5}, vec, topic="t")
        record = vector_db.get_by_id(E1)
        assert record is not None
        assert record.properties["text"] == "hello"
        assert record.properties["text_length"] == 5

    def test_get_nonexistent(self, vector_db):
        assert vector_db.get_by_id(NONEXISTENT) is None

    def test_duplicate_raises(self, vector_db):
        vec = make_vector()
        vector_db.upload_entry(E1, {"text": "a", "text_length": 1}, vec, topic="t")
        with pytest.raises(ValueError, match="already exists"):
            vector_db.upload_entry(E1, {"text": "b", "text_length": 1}, vec, topic="t")

    def test_overwrite(self, vector_db):
        vec = make_vector()
        vector_db.upload_entry(E1, {"text": "old", "text_length": 3}, vec, topic="t")
        vector_db.upload_entry(E1, {"text": "new", "text_length": 3}, vec, topic="t", overwrite=True)
        record = vector_db.get_by_id(E1)
        assert record.properties["text"] == "new"

    def test_get_by_ids(self, vector_db):
        vec = make_vector()
        vector_db.upload_entry(E1, {"text": "a", "text_length": 1}, vec, topic="t")
        vector_db.upload_entry(E2, {"text": "b", "text_length": 1}, make_vector_unique(1), topic="t")
        records = vector_db.get_by_ids([E1, E2])
        assert len(records) == 2
        ids = {r.id for r in records}
        assert ids == {E1, E2}


class TestTopicFiltering:
    def test_get_by_topic(self, vector_db):
        vec = make_vector()
        vector_db.upload_entry(E1, {"text": "a", "text_length": 1}, vec, topic="math")
        vector_db.upload_entry(E2, {"text": "b", "text_length": 1}, make_vector_unique(1), topic="science")
        results = vector_db.get_by_topic("math", limit=10)
        assert len(results) == 1
        assert results[0].id == E1

    def test_get_by_topic_includes_subtopics(self, vector_db):
        vector_db.upload_entry(E1, {"text": "a", "text_length": 1}, make_vector(), topic="math")
        vector_db.upload_entry(E2, {"text": "b", "text_length": 1}, make_vector_unique(1), topic="math/algebra")
        results = vector_db.get_by_topic("math", limit=10)
        assert len(results) == 2

    def test_update_entry_topic(self, vector_db):
        vector_db.upload_entry(E1, {"text": "a", "text_length": 1}, make_vector(), topic="old")
        vector_db.update_entry_topic(E1, "new")
        assert len(vector_db.get_by_topic("old", limit=10)) == 0
        assert len(vector_db.get_by_topic("new", limit=10)) == 1

    def test_update_topics_batch(self, vector_db):
        vector_db.upload_entry(E1, {"text": "a", "text_length": 1}, make_vector(), topic="src")
        vector_db.upload_entry(E2, {"text": "b", "text_length": 1}, make_vector_unique(1), topic="src/child")
        vector_db.upload_entry(E3, {"text": "c", "text_length": 1}, make_vector_unique(2), topic="other")
        vector_db.update_topics("src", "dst")
        assert len(vector_db.get_by_topic("src", limit=10)) == 0
        assert len(vector_db.get_by_topic("dst", limit=10)) == 2
        # "other" untouched
        assert len(vector_db.get_by_topic("other", limit=10)) == 1


class TestSimilaritySearch:
    def test_get_top_n(self, vector_db):
        v1 = make_vector_unique(1)
        v2 = make_vector_unique(2)
        vector_db.upload_entry(E1, {"text": "a", "text_length": 1}, v1, topic="t")
        vector_db.upload_entry(E2, {"text": "b", "text_length": 1}, v2, topic="t")
        results = vector_db.get_top_n(v1, n=2, filter_topic="t")
        assert len(results) == 2
        ids = [uid for uid, _ in results]
        assert E1 in ids
        assert E2 in ids
        for _, certainty in results:
            assert 0.0 <= certainty <= 1.0

    def test_get_top_n_respects_topic_filter(self, vector_db):
        vec = make_vector()
        vector_db.upload_entry(E1, {"text": "a", "text_length": 1}, vec, topic="math")
        vector_db.upload_entry(E2, {"text": "b", "text_length": 1}, make_vector_unique(1), topic="science")
        results = vector_db.get_top_n(vec, n=5, filter_topic="math")
        ids = [uid for uid, _ in results]
        assert E1 in ids
        assert E2 not in ids

    def test_get_top_n_no_filter(self, vector_db):
        vector_db.upload_entry(E1, {"text": "a", "text_length": 1}, make_vector(), topic="t1")
        vector_db.upload_entry(E2, {"text": "b", "text_length": 1}, make_vector_unique(1), topic="t2")
        results = vector_db.get_top_n(make_vector(), n=10)
        assert len(results) == 2


class TestDelete:
    def test_delete_by_id(self, vector_db):
        vector_db.upload_entry(E1, {"text": "a", "text_length": 1}, make_vector(), topic="t")
        vector_db.delete_by_id(E1)
        assert vector_db.get_by_id(E1) is None

    def test_delete_nonexistent_no_error(self, vector_db):
        vector_db.delete_by_id(NONEXISTENT)  # should not raise

    def test_delete_by_ids_batch(self, vector_db):
        vector_db.upload_entry(E1, {"text": "a", "text_length": 1}, make_vector(), topic="t")
        vector_db.upload_entry(E2, {"text": "b", "text_length": 1}, make_vector_unique(1), topic="t")
        vector_db.upload_entry(E3, {"text": "c", "text_length": 1}, make_vector_unique(2), topic="t")
        vector_db.delete_by_ids([E1, E3])
        assert vector_db.get_by_id(E1) is None
        assert vector_db.get_by_id(E2) is not None
        assert vector_db.get_by_id(E3) is None

    def test_get_all_entry_ids(self, vector_db):
        vector_db.upload_entry(E1, {"text": "a", "text_length": 1}, make_vector(), topic="t")
        vector_db.upload_entry(E2, {"text": "b", "text_length": 1}, make_vector_unique(1), topic="t")
        ids = vector_db.get_all_entry_ids()
        assert sorted(ids) == sorted([E1, E2])


class TestBatchUpload:
    def test_upload_entries_batch(self, vector_db):
        entries = [
            {"text": "a", "text_length": 1},
            {"text": "b", "text_length": 1},
            {"text": "c", "text_length": 1},
        ]
        vectors = [make_vector_unique(i) for i in range(3)]
        eids = [B1, B2, B3]
        failed = vector_db.upload_entries_batch(entries, vectors, eids, topic="t")
        assert failed == []
        assert len(vector_db.get_all_entry_ids()) == 3
        for eid in eids:
            assert vector_db.get_by_id(eid) is not None

    def test_batch_sets_topic(self, vector_db):
        entries = [{"text": "a", "text_length": 1}]
        vectors = [make_vector()]
        failed = vector_db.upload_entries_batch(entries, vectors, [B1], topic="batch_topic")
        assert failed == []
        results = vector_db.get_by_topic("batch_topic", limit=10)
        assert len(results) == 1
