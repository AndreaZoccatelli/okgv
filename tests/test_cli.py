"""Tests for CLI commands via Click test runner."""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from okgv.main import cli
from tests.conftest import MockGraphDB, MockVectorDB, SimpleSchema, fake_embedder


@pytest.fixture
def runner():
    return CliRunner()


def parse_json_output(output: str):
    """Extract JSON from output that may contain log lines."""
    lines = output.strip().split("\n")
    for i in range(len(lines)):
        try:
            return json.loads("\n".join(lines[i:]))
        except json.JSONDecodeError:
            continue
    raise ValueError(f"No JSON found in output: {output!r}")


@pytest.fixture
def mock_dbs(tmp_path):
    graph = MockGraphDB()
    vector = MockVectorDB()
    schema = SimpleSchema()
    log_file = tmp_path / "log.json"

    with (
        patch("okgv.main.connect_graph_db", return_value=graph),
        patch("okgv.main.connect_vector_db", return_value=vector),
        patch("okgv.main.get_embedder", return_value=fake_embedder),
        patch("okgv.main.get_schema", return_value=schema),
        patch("okgv.core.get_log_file", return_value=log_file),
    ):
        yield graph, vector, schema


class TestSubmit:
    def test_submit_success(self, runner, mock_dbs):
        graph, vector, _ = mock_dbs
        raw = json.dumps({"text": "hello"})
        result = runner.invoke(cli, ["submit", "--topic", "t", "--entry", raw])
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["submitted"] is True
        assert len(graph.entries) == 1
        assert len(vector.entries) == 1

    def test_submit_duplicate_fails(self, runner, mock_dbs):
        graph, vector, _ = mock_dbs
        raw = json.dumps({"text": "hello"})
        runner.invoke(cli, ["submit", "--topic", "t", "--entry", raw])
        result = runner.invoke(cli, ["submit", "--topic", "t", "--entry", raw])
        assert result.exit_code != 0

    def test_submit_duplicate_with_overwrite(self, runner, mock_dbs):
        graph, vector, _ = mock_dbs
        raw = json.dumps({"text": "hello"})
        runner.invoke(cli, ["submit", "--topic", "t", "--entry", raw])
        result = runner.invoke(cli, ["submit", "--topic", "t", "--entry", raw, "--overwrite"])
        assert result.exit_code == 0

    def test_submit_invalid_json(self, runner, mock_dbs):
        result = runner.invoke(cli, ["submit", "--topic", "t", "--entry", "not json"])
        assert result.exit_code == 2


class TestSubmitBatch:
    def test_batch_submit(self, runner, mock_dbs):
        graph, vector, _ = mock_dbs
        entries = json.dumps([{"text": "a"}, {"text": "b"}])
        result = runner.invoke(cli, ["submit-batch", "--topic", "t", "--entries", entries])
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert len(data) == 2
        assert all(d["submitted"] for d in data)


class TestMoveTopic:
    def test_dry_run(self, runner, mock_dbs):
        result = runner.invoke(cli, ["move-topic", "--source", "a/b", "--destination", "c", "--dry-run"])
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["dry_run"] is True
        assert data["new_path"] == "c/b"

    def test_move_topic(self, runner, mock_dbs):
        graph, _, _ = mock_dbs
        graph.create_topic("root")
        graph.create_subtopic("root", "child")
        graph.create_topic("other")
        result = runner.invoke(cli, ["move-topic", "--source", "root/child", "--destination", "other"])
        assert result.exit_code == 0


class TestMoveEntry:
    def test_dry_run(self, runner, mock_dbs):
        result = runner.invoke(cli, ["move-entry", "--id", "abc", "--destination", "t", "--dry-run"])
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["dry_run"] is True


class TestUndo:
    def test_dry_run(self, runner, mock_dbs, tmp_path):
        log_file = tmp_path / "undo_log.json"
        log_file.write_text(json.dumps({
            "2026-06-01T00:00:00+00:00": {"t": ["id1", "id2"]},
        }))
        with patch("okgv.core.get_log_file", return_value=log_file):
            result = runner.invoke(cli, ["undo", "2026-05-30T00:00:00", "--dry-run"])
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["dry_run"] is True
        assert data["count"] == 2

    def test_undo_deletes_entries(self, runner, mock_dbs, tmp_path):
        graph, vector, schema = mock_dbs
        graph.entries["id1"] = {"text": "a"}
        graph.entry_topics["id1"] = "t"
        vector.entries["id1"] = {"text": "a"}
        graph.entries["id2"] = {"text": "b"}
        graph.entry_topics["id2"] = "t"
        vector.entries["id2"] = {"text": "b"}

        log_file = tmp_path / "undo_log.json"
        log_file.write_text(json.dumps({
            "2026-06-01T00:00:00+00:00": {"t": ["id1", "id2"]},
        }))
        with patch("okgv.core.get_log_file", return_value=log_file):
            result = runner.invoke(cli, ["undo", "2026-05-30T00:00:00"])
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["count"] == 2
        assert len(graph.entries) == 0
        assert len(vector.entries) == 0

    def test_undo_nothing_to_delete(self, runner, mock_dbs, tmp_path):
        log_file = tmp_path / "undo_log.json"
        log_file.write_text(json.dumps({
            "2026-01-01T00:00:00+00:00": {"t": ["id1"]},
        }))
        with patch("okgv.core.get_log_file", return_value=log_file):
            result = runner.invoke(cli, ["undo", "2026-12-31T00:00:00"])
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["count"] == 0


class TestLeastTopic:
    def test_least_topic(self, runner, mock_dbs):
        graph, _, _ = mock_dbs
        graph.create_topic("a")
        graph.create_topic("b")
        graph.entries["e1"] = {}
        graph.entry_topics["e1"] = "a"
        result = runner.invoke(cli, ["least-topic"])
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["topic"] == "b"
        assert data["count"] == 0
