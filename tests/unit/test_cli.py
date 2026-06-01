"""Tests for CLI commands via Click test runner."""

import json

import pytest
from click.testing import CliRunner

from okgv.main import cli
from okgv.session import Session
from tests.unit.conftest import MockGraphDB, MockVectorDB, SimpleSchema, fake_embedder


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
def mock_session(tmp_path):
    return Session(
        graph_db=MockGraphDB(),
        vector_db=MockVectorDB(),
        embedder=fake_embedder,
        schema=SimpleSchema(),
        log_db=tmp_path / "log.db",
    )


class TestSubmit:
    def test_submit_success(self, runner, mock_session):
        raw = json.dumps({"text": "hello"})
        result = runner.invoke(cli, ["submit", "--topic", "t", "--entry", raw], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["submitted"] is True
        assert len(mock_session.graph_db.entries) == 1
        assert len(mock_session.vector_db.entries) == 1

    def test_submit_duplicate_fails(self, runner, mock_session):
        raw = json.dumps({"text": "hello"})
        runner.invoke(cli, ["submit", "--topic", "t", "--entry", raw], obj=mock_session)
        result = runner.invoke(cli, ["submit", "--topic", "t", "--entry", raw], obj=mock_session)
        assert result.exit_code != 0

    def test_submit_duplicate_with_overwrite(self, runner, mock_session):
        raw = json.dumps({"text": "hello"})
        runner.invoke(cli, ["submit", "--topic", "t", "--entry", raw], obj=mock_session)
        result = runner.invoke(cli, ["submit", "--topic", "t", "--entry", raw, "--overwrite"], obj=mock_session)
        assert result.exit_code == 0

    def test_submit_invalid_json(self, runner, mock_session):
        result = runner.invoke(cli, ["submit", "--topic", "t", "--entry", "not json"], obj=mock_session)
        assert result.exit_code == 2


class TestSubmitBatch:
    def test_batch_submit(self, runner, mock_session):
        entries = json.dumps([{"text": "a"}, {"text": "b"}])
        result = runner.invoke(cli, ["submit-batch", "--topic", "t", "--entries", entries], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert len(data) == 2
        assert all(d["submitted"] for d in data)


    def test_batch_submit_partial_failure(self, runner, mock_session):
        """Bad entry in batch doesn't kill the whole batch."""
        entries = json.dumps([{"text": "good"}, {"wrong_key": "bad"}, {"text": "also good"}])
        result = runner.invoke(cli, ["submit-batch", "--topic", "t", "--entries", entries], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert len(data) == 3
        submitted = [d for d in data if d["submitted"]]
        failed = [d for d in data if not d["submitted"]]
        assert len(submitted) == 2
        assert len(failed) == 1
        assert "error" in failed[0]


class TestMoveTopic:
    def test_dry_run(self, runner, mock_session):
        result = runner.invoke(cli, ["move-topic", "--source", "a/b", "--destination", "c", "--dry-run"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["dry_run"] is True
        assert data["new_path"] == "c/b"

    def test_move_topic(self, runner, mock_session):
        graph = mock_session.graph_db
        graph.create_topic("root")
        graph.create_subtopic("root", "child")
        graph.create_topic("other")
        result = runner.invoke(cli, ["move-topic", "--source", "root/child", "--destination", "other"], obj=mock_session)
        assert result.exit_code == 0


class TestMoveEntry:
    def test_dry_run(self, runner, mock_session):
        result = runner.invoke(cli, ["move-entry", "--id", "abc", "--destination", "t", "--dry-run"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["dry_run"] is True


def _seed_log(log_db, timestamp, topic, entry_ids):
    """Insert test log entries into SQLite."""
    from okgv.core import _log_connect

    conn = _log_connect(log_db)
    conn.executemany(
        "INSERT INTO log (timestamp, topic, entry_id) VALUES (?, ?, ?)",
        [(timestamp, topic, eid) for eid in entry_ids],
    )
    conn.commit()
    conn.close()


class TestUndo:
    def test_dry_run(self, runner, mock_session):
        _seed_log(mock_session.log_db, "2026-06-01T00:00:00+00:00", "t", ["id1", "id2"])
        result = runner.invoke(cli, ["undo", "2026-05-30T00:00:00", "--dry-run"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["dry_run"] is True
        assert data["count"] == 2

    def test_undo_deletes_entries(self, runner, mock_session):
        graph = mock_session.graph_db
        vector = mock_session.vector_db
        graph.entries["id1"] = {"text": "a"}
        graph.entry_topics["id1"] = "t"
        vector.entries["id1"] = {"text": "a"}
        graph.entries["id2"] = {"text": "b"}
        graph.entry_topics["id2"] = "t"
        vector.entries["id2"] = {"text": "b"}

        _seed_log(mock_session.log_db, "2026-06-01T00:00:00+00:00", "t", ["id1", "id2"])
        result = runner.invoke(cli, ["undo", "2026-05-30T00:00:00"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["count"] == 2
        assert len(graph.entries) == 0
        assert len(vector.entries) == 0

    def test_undo_nothing_to_delete(self, runner, mock_session):
        _seed_log(mock_session.log_db, "2026-01-01T00:00:00+00:00", "t", ["id1"])
        result = runner.invoke(cli, ["undo", "2026-12-31T00:00:00"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["count"] == 0


class TestReconcile:
    def test_consistent(self, runner, mock_session):
        graph = mock_session.graph_db
        vector = mock_session.vector_db
        graph.entries["id1"] = {"text": "a"}
        graph.entry_topics["id1"] = "t"
        vector.entries["id1"] = {"text": "a"}
        result = runner.invoke(cli, ["reconcile"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["consistent"] is True
        assert data["orphans"] == 0

    def test_graph_only_orphan(self, runner, mock_session):
        graph = mock_session.graph_db
        graph.entries["ghost"] = {"text": "orphan"}
        graph.entry_topics["ghost"] = "t"
        result = runner.invoke(cli, ["reconcile"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert "ghost" in data["deleted_from_graph"]
        assert len(graph.entries) == 0

    def test_vector_only_orphan(self, runner, mock_session):
        vector = mock_session.vector_db
        vector.entries["ghost"] = {"text": "orphan"}
        result = runner.invoke(cli, ["reconcile"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert "ghost" in data["deleted_from_vector"]
        assert len(vector.entries) == 0

    def test_dry_run(self, runner, mock_session):
        graph = mock_session.graph_db
        graph.entries["ghost"] = {"text": "orphan"}
        graph.entry_topics["ghost"] = "t"
        result = runner.invoke(cli, ["reconcile", "--dry-run"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["dry_run"] is True
        assert len(graph.entries) == 1  # not deleted


class TestLeastTopic:
    def test_least_topic(self, runner, mock_session):
        graph = mock_session.graph_db
        graph.create_topic("a")
        graph.create_topic("b")
        graph.entries["e1"] = {}
        graph.entry_topics["e1"] = "a"
        result = runner.invoke(cli, ["least-topic"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["topic"] == "b"
        assert data["count"] == 0
