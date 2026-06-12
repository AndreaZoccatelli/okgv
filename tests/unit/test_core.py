"""Tests for core logic: upsert, schema validation, graph/vector consistency."""

from datetime import UTC

import pytest

import okgv.core as core
from okgv.core import EntryError
from okgv.protocols import PropertyDefinition, entry_id
from tests.unit.conftest import MockVectorDB, fake_embedder


class TestUpsertEntry:
    def test_upsert_writes_to_both_dbs(self, graph_db, vector_db, schema):
        raw = {"text": "hello world"}
        eid = core.upsert_entry(schema, graph_db, vector_db, "topic_a", raw, fake_embedder)

        assert eid == entry_id(raw)
        assert eid in graph_db.entries
        assert eid in vector_db.entries
        assert graph_db.entry_topics[eid] == "topic_a"

    def test_upsert_graph_properties_correct(self, graph_db, vector_db, schema):
        raw = {"text": "hello"}
        eid = core.upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)

        props = graph_db.entries[eid]
        assert props["text"] == "hello"
        assert props["text_length"] == 5

    def test_upsert_vector_properties_correct(self, graph_db, vector_db, schema):
        raw = {"text": "hello"}
        eid = core.upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)

        props = vector_db.entries[eid]
        assert props["text"] == "hello"
        assert props["text_length"] == 5
        assert vector_db.vectors[eid] == [0.1, 0.2, 0.3]

    def test_embedding_failure_writes_nothing(self, graph_db, vector_db, schema):
        """Embedding happens before any DB write, so a failure leaves no orphan."""

        def bad_embedder(texts):
            raise RuntimeError("model failed to load")

        with pytest.raises(RuntimeError):
            core.upsert_entry(schema, graph_db, vector_db, "t", {"text": "x"}, bad_embedder)
        assert graph_db.entries == {}
        assert vector_db.entries == {}

    def test_upsert_duplicate_raises_without_overwrite(self, graph_db, vector_db, schema):
        raw = {"text": "hello"}
        core.upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)

        with pytest.raises(ValueError, match="already exists"):
            core.upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)

    def test_upsert_duplicate_with_overwrite(self, graph_db, vector_db, schema):
        raw = {"text": "hello"}
        core.upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)
        eid = core.upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder, overwrite=True)
        assert eid in graph_db.entries

    def test_upsert_deterministic_id(self, graph_db, vector_db, schema):
        raw = {"text": "deterministic"}
        eid1 = core.upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)
        expected = entry_id(raw)
        assert eid1 == expected

    def test_upsert_different_content_different_id(self, graph_db, vector_db, schema):
        raw1 = {"text": "alpha"}
        raw2 = {"text": "beta"}
        eid1 = core.upsert_entry(schema, graph_db, vector_db, "t", raw1, fake_embedder)
        eid2 = core.upsert_entry(schema, graph_db, vector_db, "t", raw2, fake_embedder)
        assert eid1 != eid2


class TestGraphVectorConsistency:
    def test_vector_failure_raises(self, graph_db, schema):
        vector_db = MockVectorDB(fail_on_upload=True)
        raw = {"text": "will fail"}

        with pytest.raises(ConnectionError):
            core.upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)

    def test_vector_failure_leaves_graph_entry(self, graph_db, schema):
        """Graph entry persists on vector failure — reconcile handles cleanup."""
        vector_db = MockVectorDB(fail_on_upload=True)
        raw = {"text": "partial"}
        eid = entry_id(raw)

        with pytest.raises(ConnectionError):
            core.upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)

        assert eid in graph_db.entries
        assert len(vector_db.entries) == 0


class TestValidateSchema:
    def test_valid_schema_passes(self, schema):
        meta = {"text_length": 5}
        graph_props = {"text": "hi"}
        vector_props = {"text": "hi"}
        core.validate_schema(schema, meta, graph_props, vector_props)

    def test_graph_metadata_key_collision_exits(self, schema):
        meta = {"text": "collision"}
        graph_props = {"text": "same key"}
        vector_props = {}
        with pytest.raises(SystemExit):
            core.validate_schema(schema, meta, graph_props, vector_props)

    def test_vector_metadata_key_collision_exits(self, schema):
        meta = {"text": "collision"}
        graph_props = {}
        vector_props = {"text": "same key"}
        with pytest.raises(SystemExit):
            core.validate_schema(schema, meta, graph_props, vector_props)

    def test_missing_vector_property_definition_exits(self):
        class BadSchema:
            entry_class = None

            @staticmethod
            def vector_property_definitions():
                return []

        with pytest.raises(SystemExit):
            core.validate_schema(BadSchema(), {"foo": 1}, {}, {})

    def test_extra_vector_property_definition_exits(self):
        class ExtraSchema:
            entry_class = None

            @staticmethod
            def vector_property_definitions():
                return [
                    PropertyDefinition(name="foo", data_type="text"),
                    PropertyDefinition(name="extra", data_type="text"),
                ]

        with pytest.raises(SystemExit):
            core.validate_schema(ExtraSchema(), {"foo": 1}, {}, {})


class TestBuildEntry:
    def test_build_entry_success(self, schema):
        entry = core.build_entry(schema, {"text": "hello"})
        assert entry.text == "hello"

    def test_build_entry_missing_field_raises(self, schema):
        with pytest.raises(EntryError):
            core.build_entry(schema, {"wrong_key": "value"})


class TestValidateForTopic:
    def hooked_schema(self, schema, fail_for: str | None = None):
        """Wrap the fixture schema with a validate_for_topic hook that records calls."""
        calls = []

        class HookedSchema(type(schema)):
            @staticmethod
            def validate_for_topic(entry, topic):
                calls.append((entry, topic))
                if topic == fail_for:
                    raise ValueError(f"entry not allowed under '{topic}'")

        return HookedSchema(), calls

    def test_schema_without_hook_unaffected(self, graph_db, vector_db, schema):
        eid = core.upsert_entry(schema, graph_db, vector_db, "t", {"text": "x"}, fake_embedder)
        assert eid in graph_db.entries

    def test_hook_called_with_entry_and_topic(self, graph_db, vector_db, schema):
        hooked, calls = self.hooked_schema(schema)
        core.upsert_entry(hooked, graph_db, vector_db, "topic_a", {"text": "x"}, fake_embedder)

        assert len(calls) == 1
        entry, topic = calls[0]
        assert entry.text == "x"
        assert topic == "topic_a"

    def test_hook_rejection_raises_and_writes_nothing(self, graph_db, vector_db, schema):
        hooked, _ = self.hooked_schema(schema, fail_for="t")
        with pytest.raises(EntryError, match="Entry rejected for topic 't'"):
            core.upsert_entry(hooked, graph_db, vector_db, "t", {"text": "x"}, fake_embedder)

        assert graph_db.entries == {}
        assert vector_db.entries == {}

    def test_batch_hook_rejection_collected_as_failure(self, graph_db, vector_db, schema):
        hooked, _ = self.hooked_schema(schema, fail_for="bad")
        raws = [{"text": "a"}, {"text": "b"}]
        entries = [core.build_entry(hooked, raw) for raw in raws]
        vectors = [fake_embedder(["a"])[0], fake_embedder(["b"])[0]]

        inserted, failures = core.upsert_entries_batch(
            hooked, graph_db, vector_db, "bad", raws, entries=entries, vectors=vectors
        )

        assert inserted == []
        assert len(failures) == 2
        assert all("Entry rejected for topic 'bad'" in f["error"] for f in failures)
        assert graph_db.entries == {}

    def test_batch_hook_pass_inserts_all(self, graph_db, vector_db, schema):
        hooked, calls = self.hooked_schema(schema)
        raws = [{"text": "a"}, {"text": "b"}]
        entries = [core.build_entry(hooked, raw) for raw in raws]
        vectors = [fake_embedder(["a"])[0], fake_embedder(["b"])[0]]

        inserted, failures = core.upsert_entries_batch(
            hooked, graph_db, vector_db, "t", raws, entries=entries, vectors=vectors
        )

        assert len(inserted) == 2
        assert failures == []
        assert len(calls) == 2


class TestLogSession:
    def test_log_creates_db_and_inserts(self, tmp_path):
        db_path = tmp_path / "okgv.db"
        core.log_session(db_path, "topic_a", ["id1", "id2"])

        import sqlite3

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT topic, entry_id FROM log ORDER BY id").fetchall()
        conn.close()
        assert len(rows) == 2
        assert rows[0] == ("topic_a", "id1")
        assert rows[1] == ("topic_a", "id2")

    def test_log_appends_to_existing(self, tmp_path):
        db_path = tmp_path / "okgv.db"
        core.log_session(db_path, "old_topic", ["x"])
        core.log_session(db_path, "new_topic", ["id1"])

        import sqlite3

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT topic, entry_id FROM log ORDER BY id").fetchall()
        conn.close()
        assert len(rows) == 2
        assert rows[0] == ("old_topic", "x")
        assert rows[1] == ("new_topic", "id1")

    def test_get_entries_after(self, tmp_path):
        from datetime import datetime

        db_path = tmp_path / "okgv.db"
        core.log_session(db_path, "t", ["early"])
        # Insert a row with a known future timestamp
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO log (timestamp, topic, entry_id) VALUES (?, ?, ?)",
            ("2099-01-01T00:00:00+00:00", "t", "future"),
        )
        conn.commit()
        conn.close()

        cutoff = datetime(2098, 1, 1, tzinfo=UTC)
        result = core.log_get_entries_after(db_path, cutoff)
        assert result == ["future"]

    def test_remove_entries(self, tmp_path):
        db_path = tmp_path / "okgv.db"
        core.log_session(db_path, "t", ["id1", "id2", "id3"])
        core.log_remove_entries(db_path, ["id1", "id3"])

        import sqlite3

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT entry_id FROM log").fetchall()
        conn.close()
        assert [r[0] for r in rows] == ["id2"]
