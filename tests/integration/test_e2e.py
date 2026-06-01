"""End-to-end integration tests: full pipeline through both DBs."""

import pytest

from okgv.core import (
    log_get_entries_after,
    log_session,
    upsert_entries_batch,
    upsert_entry,
)
from okgv.protocols import PropertyDefinition, entry_id
from tests.integration.conftest import make_vector_unique


class SimpleEntry:
    def __init__(self, raw: dict):
        self.text = raw["text"]


class SimpleSchema:
    entry_class = SimpleEntry

    @staticmethod
    def metadata(entry: SimpleEntry) -> dict:
        return {"text_length": len(entry.text)}

    @staticmethod
    def graph_properties(entry: SimpleEntry) -> dict:
        return {"text": entry.text}

    @staticmethod
    def vector_properties(entry: SimpleEntry) -> dict:
        return {"text": entry.text}

    @staticmethod
    def embedding_text(entry: SimpleEntry) -> str:
        return entry.text

    @staticmethod
    def vector_property_definitions() -> list[PropertyDefinition]:
        return [
            PropertyDefinition(name="text_length", data_type="int"),
            PropertyDefinition(name="text", data_type="text"),
        ]


def fake_embedder(texts: list[str]) -> list[list[float]]:
    return [make_vector_unique(hash(t) % 10000) for t in texts]


@pytest.fixture
def schema():
    return SimpleSchema()


class TestUpsertE2E:
    def test_single_upsert_both_dbs(self, graph_db, vector_db, schema):
        graph_db.create_topic("t")
        raw = {"text": "integration test"}
        eid = upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)

        # Verify graph
        g_record = graph_db.get_by_id(eid)
        assert g_record is not None
        assert g_record.topic == "t"
        assert g_record.properties["text"] == "integration test"

        # Verify vector
        v_record = vector_db.get_by_id(eid)
        assert v_record is not None
        assert v_record.properties["text"] == "integration test"

    def test_deterministic_id(self, graph_db, vector_db, schema):
        graph_db.create_topic("t")
        raw = {"text": "deterministic"}
        eid = upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)
        assert eid == entry_id(raw)

    def test_duplicate_blocked(self, graph_db, vector_db, schema):
        graph_db.create_topic("t")
        raw = {"text": "once"}
        upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)
        with pytest.raises(ValueError, match="already exists"):
            upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)

    def test_overwrite(self, graph_db, vector_db, schema):
        graph_db.create_topic("t")
        raw = {"text": "original"}
        eid = upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)
        upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder, overwrite=True)
        assert graph_db.get_by_id(eid) is not None
        assert vector_db.get_by_id(eid) is not None


class TestBatchUpsertE2E:
    def test_batch_upsert(self, graph_db, vector_db, schema):
        graph_db.create_topic("t")
        raws = [{"text": "alpha"}, {"text": "beta"}, {"text": "gamma"}]
        vectors = [make_vector_unique(i) for i in range(3)]
        inserted, failures = upsert_entries_batch(
            schema, graph_db, vector_db, "t", raws, vectors=vectors
        )
        assert len(inserted) == 3
        assert len(failures) == 0

        for raw in raws:
            eid = entry_id(raw)
            assert graph_db.get_by_id(eid) is not None
            assert vector_db.get_by_id(eid) is not None

    def test_batch_all_in_same_topic(self, graph_db, vector_db, schema):
        graph_db.create_topic("batch_t")
        raws = [{"text": "a"}, {"text": "b"}]
        vectors = [make_vector_unique(i) for i in range(2)]
        inserted, _ = upsert_entries_batch(
            schema, graph_db, vector_db, "batch_t", raws, vectors=vectors
        )
        results = vector_db.get_by_topic("batch_t", limit=10)
        assert len(results) == 2


class TestSimilarityE2E:
    def test_similar_entries_found(self, graph_db, vector_db, schema):
        graph_db.create_topic("t")
        raw1 = {"text": "cat sat on mat"}
        raw2 = {"text": "dog ran in park"}
        upsert_entry(schema, graph_db, vector_db, "t", raw1, fake_embedder)
        upsert_entry(schema, graph_db, vector_db, "t", raw2, fake_embedder)

        query_vec = fake_embedder(["cat sat on mat"])[0]
        results = vector_db.get_top_n(query_vec, n=2, filter_topic="t")
        assert len(results) == 2
        ids = [uid for uid, _ in results]
        assert entry_id(raw1) in ids

    def test_topic_filter_works(self, graph_db, vector_db, schema):
        graph_db.create_topic("math")
        graph_db.create_topic("science")
        raw_math = {"text": "pythagorean theorem"}
        raw_sci = {"text": "cell mitosis"}
        upsert_entry(schema, graph_db, vector_db, "math", raw_math, fake_embedder)
        upsert_entry(schema, graph_db, vector_db, "science", raw_sci, fake_embedder)

        query_vec = fake_embedder(["pythagorean theorem"])[0]
        results = vector_db.get_top_n(query_vec, n=5, filter_topic="math")
        ids = [uid for uid, _ in results]
        assert entry_id(raw_math) in ids
        assert entry_id(raw_sci) not in ids


class TestCrossDBConsistency:
    def test_entries_exist_in_both(self, graph_db, vector_db, schema):
        graph_db.create_topic("t")
        raws = [{"text": f"entry_{i}"} for i in range(5)]
        vectors = [make_vector_unique(i) for i in range(5)]
        upsert_entries_batch(schema, graph_db, vector_db, "t", raws, vectors=vectors)

        graph_ids = set(graph_db.get_all_entry_ids())
        vector_ids = set(vector_db.get_all_entry_ids())
        assert graph_ids == vector_ids

    def test_delete_removes_from_both(self, graph_db, vector_db, schema):
        graph_db.create_topic("t")
        raw = {"text": "to delete"}
        eid = upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)

        graph_db.delete_entries([eid])
        vector_db.delete_by_id(eid)

        assert graph_db.get_by_id(eid) is None
        assert vector_db.get_by_id(eid) is None


class TestMoveE2E:
    def test_move_entry_both_dbs(self, graph_db, vector_db, schema):
        graph_db.create_topic("old")
        graph_db.create_topic("new")
        raw = {"text": "movable"}
        eid = upsert_entry(schema, graph_db, vector_db, "old", raw, fake_embedder)

        graph_db.move_entry(eid, "new")
        vector_db.update_entry_topic(eid, "new")

        g = graph_db.get_by_id(eid)
        assert g.topic == "new"
        assert len(vector_db.get_by_topic("old", limit=10)) == 0
        assert len(vector_db.get_by_topic("new", limit=10)) == 1

    def test_move_topic_both_dbs(self, graph_db, vector_db, schema):
        graph_db.create_topic("src")
        graph_db.create_subtopic("src", "child")
        graph_db.create_topic("dst")
        raw = {"text": "nested entry"}
        eid = upsert_entry(schema, graph_db, vector_db, "src/child", raw, fake_embedder)

        graph_db.move_topic("src/child", "dst")
        vector_db.update_topics("src/child", "dst/child")

        # Graph updated
        assert graph_db.topic_exists("dst/child")
        assert not graph_db.topic_exists("src/child")

        # Vector topic updated
        assert len(vector_db.get_by_topic("src/child", limit=10)) == 0
        assert len(vector_db.get_by_topic("dst/child", limit=10)) == 1


class TestLogE2E:
    def test_log_and_query(self, graph_db, vector_db, schema, tmp_path):
        from datetime import datetime, timezone

        log_db = tmp_path / "log.db"
        graph_db.create_topic("t")

        raw = {"text": "logged entry"}
        eid = upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)
        log_session(log_db, "t", [eid])

        # Query entries after epoch — should find our entry
        cutoff = datetime(2000, 1, 1, tzinfo=timezone.utc)
        ids = log_get_entries_after(log_db, cutoff)
        assert eid in ids
