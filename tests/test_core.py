"""Tests for core logic: upsert, schema validation, graph/vector consistency."""

import pytest

import okgv.core as core
from okgv.protocols import PropertyDefinition, entry_id
from tests.conftest import MockGraphDB, MockVectorDB, SimpleSchema, fake_embedder


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

    def test_upsert_duplicate_raises_without_overwrite(self, graph_db, vector_db, schema):
        raw = {"text": "hello"}
        core.upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)

        with pytest.raises(ValueError, match="already exists"):
            core.upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)

    def test_upsert_duplicate_with_overwrite(self, graph_db, vector_db, schema):
        raw = {"text": "hello"}
        core.upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)
        # Should not raise
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
    def test_vector_failure_rolls_back_graph(self, graph_db, schema):
        vector_db = MockVectorDB(fail_on_upload=True)
        raw = {"text": "will fail"}

        with pytest.raises(ConnectionError):
            core.upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)

        eid = entry_id(raw)
        assert eid not in graph_db.entries
        assert eid in graph_db.deleted

    def test_vector_failure_does_not_leave_partial_state(self, graph_db, schema):
        vector_db = MockVectorDB(fail_on_upload=True)
        raw = {"text": "partial"}

        with pytest.raises(ConnectionError):
            core.upsert_entry(schema, graph_db, vector_db, "t", raw, fake_embedder)

        assert len(graph_db.entries) == 0
        assert len(vector_db.entries) == 0


class TestValidateSchema:
    def setup_method(self):
        core._schema_validated = False

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
        core._schema_validated = False

        class BadSchema:
            entry_class = None

            @staticmethod
            def vector_property_definitions():
                return []  # missing definitions

        with pytest.raises(SystemExit):
            core.validate_schema(BadSchema(), {"foo": 1}, {}, {})

    def test_extra_vector_property_definition_exits(self):
        core._schema_validated = False

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

    def test_validation_cached_after_first_pass(self, schema):
        core._schema_validated = False
        meta = {"text_length": 5}
        graph_props = {"text": "hi"}
        vector_props = {"text": "hi"}
        core.validate_schema(schema, meta, graph_props, vector_props)
        assert core._schema_validated is True

        # Second call with bad data should pass (cached)
        core.validate_schema(schema, {"collision": 1}, {"collision": 1}, {})


class TestBuildEntry:
    def test_build_entry_success(self, schema):
        entry = core.build_entry(schema, {"text": "hello"})
        assert entry.text == "hello"

    def test_build_entry_missing_field_exits(self, schema):
        with pytest.raises(SystemExit):
            core.build_entry(schema, {"wrong_key": "value"})


class TestLogSession:
    def test_log_creates_file(self, tmp_path, monkeypatch):
        log_file = tmp_path / "log.json"
        monkeypatch.setattr(core, "get_log_file", lambda: log_file)

        core.log_session("topic_a", ["id1", "id2"])

        import json
        data = json.loads(log_file.read_text())
        assert len(data) == 1
        ts = list(data.keys())[0]
        assert data[ts] == {"topic_a": ["id1", "id2"]}

    def test_log_appends_to_existing(self, tmp_path, monkeypatch):
        import json

        log_file = tmp_path / "log.json"
        log_file.write_text(json.dumps({"2026-01-01T00:00:00+00:00": {"old": ["x"]}}))
        monkeypatch.setattr(core, "get_log_file", lambda: log_file)

        core.log_session("new_topic", ["id1"])

        data = json.loads(log_file.read_text())
        assert len(data) == 2
        assert "2026-01-01T00:00:00+00:00" in data


class TestGetLogFile:
    def test_default_is_cwd(self, monkeypatch):
        monkeypatch.delenv("OKGV_LOG", raising=False)
        from pathlib import Path
        result = core.get_log_file()
        assert result == Path.cwd() / "log.json"

    def test_custom_file_path(self, monkeypatch):
        monkeypatch.setenv("OKGV_LOG", "/tmp/custom.json")
        from pathlib import Path
        result = core.get_log_file()
        assert result == Path("/tmp/custom.json")

    def test_custom_dir_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OKGV_LOG", str(tmp_path) + "/")
        result = core.get_log_file()
        assert result == tmp_path / "log.json"

    def test_custom_existing_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OKGV_LOG", str(tmp_path))
        result = core.get_log_file()
        assert result == tmp_path / "log.json"
