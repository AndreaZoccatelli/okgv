"""Tests for CLI commands via Click test runner."""

import json

import pytest
from click.testing import CliRunner

from okgv.core import review_add
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
        db_path=tmp_path / "okgv.db",
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
        assert result.exit_code == 2
        assert "duplicate_entry" in result.stderr
        assert "--overwrite" in result.stderr

    def test_submit_missing_field_structured_error(self, runner, mock_session):
        result = runner.invoke(cli, ["submit", "--topic", "t", "--entry", '{"wrong": 1}'], obj=mock_session)
        assert result.exit_code == 2
        assert "missing_field" in result.stderr

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
        graph = mock_session.graph_db
        graph.create_topic("a")
        graph.create_subtopic("a", "b")
        graph.create_topic("c")
        result = runner.invoke(
            cli,
            ["move-topic", "--source", "a/b", "--destination", "c", "--dry-run"],
            obj=mock_session,
        )
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["dry_run"] is True
        assert data["new_path"] == "c/b"

    def test_source_not_found(self, runner, mock_session):
        mock_session.graph_db.create_topic("c")
        result = runner.invoke(cli, ["move-topic", "--source", "nope", "--destination", "c"], obj=mock_session)
        assert result.exit_code == 3
        assert "not_found" in result.stderr

    def test_destination_not_found(self, runner, mock_session):
        mock_session.graph_db.create_topic("a")
        result = runner.invoke(cli, ["move-topic", "--source", "a", "--destination", "nope"], obj=mock_session)
        assert result.exit_code == 3
        assert "not_found" in result.stderr

    def test_move_topic(self, runner, mock_session):
        graph = mock_session.graph_db
        graph.create_topic("root")
        graph.create_subtopic("root", "child")
        graph.create_topic("other")
        result = runner.invoke(
            cli,
            ["move-topic", "--source", "root/child", "--destination", "other"],
            obj=mock_session,
        )
        assert result.exit_code == 0


class TestMoveEntry:
    def _seed(self, mock_session):
        graph = mock_session.graph_db
        graph.create_topic("t")
        graph.entries["abc"] = {"text": "hello"}
        graph.entry_topics["abc"] = "t"

    def test_dry_run(self, runner, mock_session):
        self._seed(mock_session)
        result = runner.invoke(cli, ["move-entry", "--id", "abc", "--destination", "t", "--dry-run"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["dry_run"] is True

    def test_entry_not_found(self, runner, mock_session):
        mock_session.graph_db.create_topic("t")
        result = runner.invoke(cli, ["move-entry", "--id", "ghost", "--destination", "t"], obj=mock_session)
        assert result.exit_code == 3
        assert "not_found" in result.stderr

    def test_destination_not_found(self, runner, mock_session):
        self._seed(mock_session)
        result = runner.invoke(cli, ["move-entry", "--id", "abc", "--destination", "nope"], obj=mock_session)
        assert result.exit_code == 3
        assert "not_found" in result.stderr


def _seed_log(db_path, timestamp, topic, entry_ids):
    """Insert test log entries into SQLite."""
    from okgv.core import _connect

    conn = _connect(db_path)
    conn.executemany(
        "INSERT INTO log (timestamp, topic, entry_id) VALUES (?, ?, ?)",
        [(timestamp, topic, eid) for eid in entry_ids],
    )
    conn.commit()
    conn.close()


class TestUndo:
    def test_dry_run(self, runner, mock_session):
        _seed_log(mock_session.db_path, "2026-06-01T00:00:00+00:00", "t", ["id1", "id2"])
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

        _seed_log(mock_session.db_path, "2026-06-01T00:00:00+00:00", "t", ["id1", "id2"])
        result = runner.invoke(cli, ["undo", "2026-05-30T00:00:00"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["count"] == 2
        assert len(graph.entries) == 0
        assert len(vector.entries) == 0

    def test_undo_nothing_to_delete(self, runner, mock_session):
        _seed_log(mock_session.db_path, "2026-01-01T00:00:00+00:00", "t", ["id1"])
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


class TestGetStructure:
    def test_get_structure(self, runner, mock_session):
        graph = mock_session.graph_db
        graph.create_topic("math")
        graph.create_subtopic("math", "algebra")
        result = runner.invoke(cli, ["get-structure"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert "math" in data

    def test_get_structure_empty(self, runner, mock_session):
        result = runner.invoke(cli, ["get-structure"], obj=mock_session)
        assert result.exit_code == 3  # EXIT_NOT_FOUND

    def test_get_structure_with_root(self, runner, mock_session):
        graph = mock_session.graph_db
        graph.create_topic("math")
        graph.create_subtopic("math", "algebra")
        result = runner.invoke(cli, ["get-structure", "--root", "math"], obj=mock_session)
        assert result.exit_code == 0

    def test_get_structure_root_not_found(self, runner, mock_session):
        graph = mock_session.graph_db
        graph.create_topic("math")
        result = runner.invoke(cli, ["get-structure", "--root", "nonexistent"], obj=mock_session)
        assert result.exit_code == 3


class TestCreateTopic:
    def test_create_topic(self, runner, mock_session):
        result = runner.invoke(cli, ["create-topic", "--name", "math"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["created"] is True
        assert data["topic"] == "math"
        assert mock_session.graph_db.topic_exists("math")

    def test_create_nested_with_parents(self, runner, mock_session):
        result = runner.invoke(cli, ["create-topic", "--name", "a/b/c", "--parents"], obj=mock_session)
        assert result.exit_code == 0
        assert mock_session.graph_db.topic_exists("a")
        assert mock_session.graph_db.topic_exists("a/b")
        assert mock_session.graph_db.topic_exists("a/b/c")

    def test_create_nested_without_parents_fails(self, runner, mock_session):
        result = runner.invoke(cli, ["create-topic", "--name", "a/b"], obj=mock_session)
        assert result.exit_code != 0

    def test_create_existing_topic_reports_existed(self, runner, mock_session):
        mock_session.graph_db.create_topic("math")
        result = runner.invoke(cli, ["create-topic", "--name", "math"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["created"] is False
        assert data["existed"] is True


class TestGetByTopic:
    def test_no_entries(self, runner, mock_session):
        result = runner.invoke(cli, ["get-by-topic", "--topic", "t"], obj=mock_session)
        assert result.exit_code == 3

    def test_with_entries(self, runner, mock_session):
        vector = mock_session.vector_db
        vector.entries["id1"] = {"text": "hello"}
        vector.topics["id1"] = "t"
        result = runner.invoke(cli, ["get-by-topic", "--topic", "t"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert len(data) == 1
        assert data[0]["id"] == "id1"


class TestGetVector:
    def test_found(self, runner, mock_session):
        mock_session.vector_db.entries["abc"] = {"text": "hello"}
        result = runner.invoke(cli, ["get-vector", "--id", "abc"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["id"] == "abc"

    def test_not_found(self, runner, mock_session):
        result = runner.invoke(cli, ["get-vector", "--id", "nonexistent"], obj=mock_session)
        assert result.exit_code == 3


class TestGetGraph:
    def test_found(self, runner, mock_session):
        mock_session.graph_db.entries["abc"] = {"text": "hello"}
        mock_session.graph_db.entry_topics["abc"] = "t"
        result = runner.invoke(cli, ["get-graph", "--id", "abc"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["id"] == "abc"
        assert data["topic"] == "t"

    def test_not_found(self, runner, mock_session):
        result = runner.invoke(cli, ["get-graph", "--id", "nonexistent"], obj=mock_session)
        assert result.exit_code == 3


class TestReviewApproveReject:
    def test_approve(self, runner, mock_session):
        review_add(mock_session.db_path, "t", ["id1"])
        result = runner.invoke(cli, ["approve", "--id", "id1"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["status"] == "approved"

    def test_approve_not_found(self, runner, mock_session):
        result = runner.invoke(cli, ["approve", "--id", "nonexistent"], obj=mock_session)
        assert result.exit_code == 3

    def test_reject(self, runner, mock_session):
        review_add(mock_session.db_path, "t", ["id1"])
        result = runner.invoke(cli, ["reject", "--id", "id1"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["status"] == "rejected"


class TestExport:
    def test_export_dry_run(self, runner, mock_session):
        mock_session.vector_db.entries["id1"] = {"text": "hello"}
        mock_session.vector_db.topics["id1"] = "t"
        result = runner.invoke(cli, ["export", "--output", "out.jsonl", "--dry-run"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["dry_run"] is True
        assert data["would_export"] == 1

    def test_export_writes_file(self, runner, mock_session, tmp_path):
        mock_session.vector_db.entries["id1"] = {"text": "hello"}
        mock_session.vector_db.topics["id1"] = "t"
        mock_session.graph_db.entries["id1"] = {"text": "hello"}
        mock_session.graph_db.entry_topics["id1"] = "t"
        out = str(tmp_path / "out.jsonl")
        result = runner.invoke(cli, ["export", "--output", out], obj=mock_session)
        assert result.exit_code == 0
        with open(out) as f:
            lines = f.readlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["id"] == "id1"
        assert row["topic"] == "t"

    def test_export_with_field_filter(self, runner, mock_session, tmp_path):
        mock_session.vector_db.entries["id1"] = {"text": "hello", "extra": "x"}
        mock_session.vector_db.topics["id1"] = "t"
        mock_session.graph_db.entries["id1"] = {"text": "hello"}
        mock_session.graph_db.entry_topics["id1"] = "t"
        out = str(tmp_path / "out.jsonl")
        result = runner.invoke(cli, ["export", "--output", out, "--fields", "text"], obj=mock_session)
        assert result.exit_code == 0
        with open(out) as f:
            row = json.loads(f.readline())
        assert "text" in row
        assert "extra" not in row
        assert "id" not in row


class TestExportSplit:
    def _seed_entries(self, mock_session, n, topic="t", difficulty=None):
        graph = mock_session.graph_db
        vector = mock_session.vector_db
        graph.create_topic(topic)
        for i in range(n):
            eid = f"{topic}-{difficulty or 'x'}-{i}"
            props = {"text": f"entry {i}"}
            if difficulty:
                props["difficulty"] = difficulty
            graph.entries[eid] = props
            graph.entry_topics[eid] = topic
            vector.entries[eid] = props
            vector.topics[eid] = topic

    def test_split_writes_one_file_per_split(self, runner, mock_session, tmp_path):
        self._seed_entries(mock_session, 10)
        out = str(tmp_path / "dataset.jsonl")
        result = runner.invoke(
            cli,
            ["export", "--output", out, "--split", "train=0.8,test=0.2"],
            obj=mock_session,
        )
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["exported"] == 10
        assert data["splits"]["train"]["count"] == 8
        assert data["splits"]["test"]["count"] == 2
        with open(tmp_path / "dataset-train.jsonl") as f:
            assert len(f.readlines()) == 8
        with open(tmp_path / "dataset-test.jsonl") as f:
            assert len(f.readlines()) == 2

    def test_split_stratifies_by_balance_fields(self, runner, tmp_path):
        class BalancedSchema(SimpleSchema):
            balance_fields = ["difficulty"]

        session = Session(
            graph_db=MockGraphDB(),
            vector_db=MockVectorDB(),
            embedder=fake_embedder,
            schema=BalancedSchema(),
            db_path=tmp_path / "okgv.db",
        )
        self._seed_entries(session, 4, difficulty="easy")
        self._seed_entries(session, 4, difficulty="hard")
        out = str(tmp_path / "dataset.jsonl")
        result = runner.invoke(
            cli,
            ["export", "--output", out, "--split", "train=0.5,test=0.5"],
            obj=session,
        )
        assert result.exit_code == 0
        # Each stratum (easy, hard) must split 2/2, so both splits hold 2 of each.
        for split in ("train", "test"):
            with open(tmp_path / f"dataset-{split}.jsonl") as f:
                rows = [json.loads(line) for line in f]
            difficulties = [r["difficulty"] for r in rows]
            assert difficulties.count("easy") == 2
            assert difficulties.count("hard") == 2

    def test_split_deterministic_for_seed(self, runner, mock_session, tmp_path):
        self._seed_entries(mock_session, 10)
        out1 = str(tmp_path / "a.jsonl")
        out2 = str(tmp_path / "b.jsonl")
        for out in (out1, out2):
            result = runner.invoke(
                cli,
                ["export", "--output", out, "--split", "train=0.8,test=0.2", "--seed", "7"],
                obj=mock_session,
            )
            assert result.exit_code == 0
        for split in ("train", "test"):
            with open(tmp_path / f"a-{split}.jsonl") as f1, open(tmp_path / f"b-{split}.jsonl") as f2:
                assert f1.read() == f2.read()

    def test_split_small_strata_respect_global_fractions(self, runner, mock_session, tmp_path):
        """Many tiny strata must not starve small splits.

        With per-stratum rounding, every 2-entry stratum would hand its
        leftover to train and val/test would end up near zero. Leftovers are
        allocated by global deficit instead, keeping overall fractions.
        """
        for i in range(20):
            self._seed_entries(mock_session, 2, topic=f"topic{i}")
        result = runner.invoke(
            cli,
            ["export", "--dry-run", "--split", "train=0.8,val=0.1,test=0.1"],
            obj=mock_session,
        )
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert sum(s["count"] for s in data["splits"].values()) == 40
        assert data["splits"]["train"]["count"] == 32
        assert data["splits"]["val"]["count"] == 4
        assert data["splits"]["test"]["count"] == 4

    def test_split_dry_run(self, runner, mock_session, tmp_path):
        self._seed_entries(mock_session, 10)
        result = runner.invoke(
            cli,
            ["export", "--dry-run", "--split", "train=0.8,test=0.2"],
            obj=mock_session,
        )
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["dry_run"] is True
        assert data["splits"] == {"train": {"count": 8}, "test": {"count": 2}}
        assert not list(tmp_path.iterdir())

    def test_split_dry_run_shows_balance_counts(self, runner, tmp_path):
        class BalancedSchema(SimpleSchema):
            balance_fields = ["difficulty"]

        session = Session(
            graph_db=MockGraphDB(),
            vector_db=MockVectorDB(),
            embedder=fake_embedder,
            schema=BalancedSchema(),
            db_path=tmp_path / "okgv.db",
        )
        self._seed_entries(session, 4, difficulty="easy")
        self._seed_entries(session, 4, difficulty="hard")
        result = runner.invoke(
            cli,
            ["export", "--dry-run", "--split", "train=0.5,test=0.5"],
            obj=session,
        )
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        for split in ("train", "test"):
            assert data["splits"][split]["count"] == 4
            assert data["splits"][split]["balance"] == {"difficulty": {"easy": 2, "hard": 2}}

    def test_split_bad_sum_rejected(self, runner, mock_session, tmp_path):
        out = str(tmp_path / "dataset.jsonl")
        result = runner.invoke(
            cli,
            ["export", "--output", out, "--split", "train=0.8,test=0.1"],
            obj=mock_session,
        )
        assert result.exit_code == 2
        assert "invalid_split" in result.stderr

    def test_split_bad_fraction_rejected(self, runner, mock_session, tmp_path):
        out = str(tmp_path / "dataset.jsonl")
        result = runner.invoke(
            cli,
            ["export", "--output", out, "--split", "train=lots,test=0.2"],
            obj=mock_session,
        )
        assert result.exit_code == 2
        assert "invalid_split" in result.stderr


def _seed_tree(graph_db):
    """algebra/{linear,abstract}, geometry/euclidean."""
    graph_db.create_topic("algebra")
    graph_db.create_subtopic("algebra", "linear")
    graph_db.create_subtopic("algebra", "abstract")
    graph_db.create_topic("geometry")
    graph_db.create_subtopic("geometry", "euclidean")


class TestTree:
    def test_root_not_found(self, runner, mock_session):
        _seed_tree(mock_session.graph_db)
        result = runner.invoke(cli, ["tree", "--root", "nope"], obj=mock_session)
        assert result.exit_code == 3
        assert "not_found" in result.stderr

    def test_no_topics(self, runner, mock_session):
        result = runner.invoke(cli, ["tree"], obj=mock_session)
        assert result.exit_code == 3
        assert "no_topics" in result.stderr

    def test_export_json_includes_root(self, runner, mock_session):
        _seed_tree(mock_session.graph_db)
        result = runner.invoke(cli, ["tree", "--root", "algebra", "--export", "json"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        # get_topic_tree returns the root itself as the top key.
        assert data == {"algebra": {"linear": {}, "abstract": {}}}

    def test_export_dot(self, runner, mock_session):
        _seed_tree(mock_session.graph_db)
        result = runner.invoke(cli, ["tree", "--export", "dot"], obj=mock_session)
        assert result.exit_code == 0
        assert "digraph topics" in result.output
        assert '"algebra"' in result.output
        assert '"linear"' in result.output

    def test_render_root_not_doubled(self, runner, mock_session):
        """Rich render must show the root once, not duplicated under itself."""
        _seed_tree(mock_session.graph_db)
        result = runner.invoke(cli, ["tree", "--root", "algebra"], obj=mock_session)
        assert result.exit_code == 0
        # Tree is rendered to a stderr Console.
        assert result.stderr.count("algebra") == 1
        assert "linear" in result.stderr
        assert "abstract" in result.stderr

    def test_counts_same_name_different_parents(self, runner, mock_session):
        """Sibling topics sharing a name must show their own counts, not collide."""
        graph = mock_session.graph_db
        graph.create_topic("algebra")
        graph.create_subtopic("algebra", "basics")
        graph.create_topic("geometry")
        graph.create_subtopic("geometry", "basics")
        for i in range(2):
            graph.entries[f"e{i}"] = {"text": str(i)}
            graph.entry_topics[f"e{i}"] = "algebra/basics"
        result = runner.invoke(cli, ["tree", "--counts"], obj=mock_session)
        assert result.exit_code == 0
        assert "(2)" in result.stderr  # algebra/basics
        assert "(0)" in result.stderr  # geometry/basics, not overwritten

    def test_counts_with_nested_root(self, runner, mock_session):
        """Counts under --root must be queried with full paths."""
        graph = mock_session.graph_db
        graph.create_topic("algebra")
        graph.create_subtopic("algebra", "linear")
        graph.create_subtopic("algebra/linear", "basics")
        graph.entries["e1"] = {"text": "x"}
        graph.entry_topics["e1"] = "algebra/linear/basics"
        result = runner.invoke(cli, ["tree", "--root", "algebra/linear", "--counts"], obj=mock_session)
        assert result.exit_code == 0
        assert "(1)" in result.stderr

    def test_render_full_tree(self, runner, mock_session):
        _seed_tree(mock_session.graph_db)
        result = runner.invoke(cli, ["tree"], obj=mock_session)
        assert result.exit_code == 0
        for name in ("topics", "algebra", "geometry", "linear", "euclidean"):
            assert name in result.stderr

    def test_render_missing_rich_errors_cleanly(self, runner, mock_session, monkeypatch):
        """Without rich, the default render must emit a friendly error, not a traceback."""
        import builtins

        _seed_tree(mock_session.graph_db)
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("rich"):
                raise ImportError("No module named 'rich'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        result = runner.invoke(cli, ["tree", "--root", "algebra"], obj=mock_session)
        assert result.exit_code == 1
        assert "missing_dependency" in result.stderr


class TestReport:
    def _seed(self, mock_session):
        graph = mock_session.graph_db
        graph.create_topic("weather")
        graph.create_subtopic("weather", "forecast")
        graph.create_subtopic("weather", "alerts")
        graph.entries["e1"] = {"difficulty": "easy"}
        graph.entry_topics["e1"] = "weather/forecast"
        graph.entries["e2"] = {"difficulty": "hard"}
        graph.entry_topics["e2"] = "weather/forecast"

    def test_counts_per_leaf(self, runner, mock_session):
        self._seed(mock_session)
        result = runner.invoke(cli, ["report"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["total_entries"] == 2
        assert data["leaf_topics"] == 2
        counts = {item["topic"]: item["count"] for item in data["leaves"]}
        assert counts == {"weather/forecast": 2, "weather/alerts": 0}
        assert data["least_filled_leaf"]["topic"] == "weather/alerts"
        assert data["most_filled_leaf"]["topic"] == "weather/forecast"

    def test_fields_produce_cells_with_empty_ones(self, runner, mock_session):
        self._seed(mock_session)
        result = runner.invoke(cli, ["report", "--fields", "difficulty"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        forecast = next(item for item in data["leaves"] if item["topic"] == "weather/forecast")
        # Observed values: easy, hard. Both cells filled for forecast.
        cell_counts = {c["fields"]["difficulty"]: c["count"] for c in forecast["cells"]}
        assert cell_counts == {"easy": 1, "hard": 1}
        # alerts leaf has both combinations empty.
        empty = {(e["topic"], e["fields"]["difficulty"]) for e in data["empty_cells"]}
        assert ("weather/alerts", "easy") in empty
        assert ("weather/alerts", "hard") in empty

    def test_declared_validator_values_show_missing_cells(self, tmp_path):
        from okgv.validators import OneOf

        class BalancedSchema(SimpleSchema):
            balance_fields = ["difficulty"]
            validators = [OneOf("difficulty", {"easy", "medium", "hard"})]

        session = Session(
            graph_db=MockGraphDB(),
            vector_db=MockVectorDB(),
            embedder=fake_embedder,
            schema=BalancedSchema(),
            db_path=tmp_path / "okgv.db",
        )
        graph = session.graph_db
        graph.create_topic("t")
        graph.entries["e1"] = {"difficulty": "easy"}
        graph.entry_topics["e1"] = "t"

        result = CliRunner().invoke(cli, ["report"], obj=session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["balance_fields"] == ["difficulty"]
        leaf = data["leaves"][0]
        cell_counts = {c["fields"]["difficulty"]: c["count"] for c in leaf["cells"]}
        # "medium" and "hard" were never generated but are declared by the validator.
        assert cell_counts == {"easy": 1, "hard": 0, "medium": 0}

    def test_non_leaf_entries_reported(self, runner, mock_session):
        graph = mock_session.graph_db
        graph.create_topic("root")
        graph.create_subtopic("root", "leaf")
        graph.entries["e1"] = {"text": "on parent"}
        graph.entry_topics["e1"] = "root"
        result = runner.invoke(cli, ["report"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["total_entries"] == 1
        assert data["non_leaf_entries"] == 1
        assert data["leaves"][0]["count"] == 0

    def test_scoped_to_subtree(self, runner, mock_session):
        self._seed(mock_session)
        graph = mock_session.graph_db
        graph.create_topic("other")
        graph.entries["e3"] = {"difficulty": "easy"}
        graph.entry_topics["e3"] = "other"
        result = runner.invoke(cli, ["report", "--topic", "weather"], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["total_entries"] == 2
        assert {item["topic"] for item in data["leaves"]} == {"weather/forecast", "weather/alerts"}

    def test_no_topics(self, runner, mock_session):
        result = runner.invoke(cli, ["report"], obj=mock_session)
        assert result.exit_code == 3


class TestOptionValidation:
    def test_review_status_rejects_unknown_value(self, runner, mock_session):
        result = runner.invoke(cli, ["review", "--status", "aproved"], obj=mock_session)
        assert result.exit_code == 2
        assert "pending" in result.stderr  # usage error lists valid choices

    def test_review_status_accepts_valid_value(self, runner, mock_session):
        result = runner.invoke(cli, ["review", "--status", "approved"], obj=mock_session)
        assert result.exit_code == 0

    def test_invalid_okgv_review_env_rejected(self, runner, mock_session, monkeypatch):
        monkeypatch.setenv("OKGV_REVIEW", "yes")
        raw = json.dumps({"text": "hello"})
        result = runner.invoke(cli, ["submit", "--topic", "t", "--entry", raw], obj=mock_session)
        assert result.exit_code == 2
        assert "invalid_config" in result.stderr

    def test_valid_okgv_review_env_accepted(self, runner, mock_session, monkeypatch):
        monkeypatch.setenv("OKGV_REVIEW", "all")
        raw = json.dumps({"text": "hello"})
        result = runner.invoke(cli, ["submit", "--topic", "t", "--entry", raw], obj=mock_session)
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["review"] is True


class TestUnexpectedErrors:
    def test_uncaught_exception_becomes_structured_error(self, runner, mock_session, monkeypatch):
        """Any unexpected exception must surface as JSON on stderr, not a traceback."""

        def boom(*args, **kwargs):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(mock_session.vector_db, "get_by_topic", boom)
        result = runner.invoke(cli, ["get-by-topic", "--topic", "t"], obj=mock_session)
        assert result.exit_code == 1
        assert "unexpected_error" in result.stderr
        assert "RuntimeError" in result.stderr
        assert "Traceback" not in result.stderr

    def test_help_still_works(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Knowledge base CLI" in result.output

    def test_version_flag(self, runner):
        from importlib.metadata import version

        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        # prog name differs under the test runner; assert on the version itself
        assert f"version {version('okgv')}" in result.output


class TestBrowseLazyVectorDB:
    def test_vector_db_resolved_lazily(self):
        """Browsing must not resolve the vector DB (which can trigger an
        embedding model load) until entries are actually fetched."""
        pytest.importorskip("textual")
        from okgv.tui import BrowseApp

        calls = []

        def get_vd():
            calls.append(1)
            return MockVectorDB()

        app = BrowseApp(graph_db=MockGraphDB(), get_vector_db=get_vd)
        assert calls == []  # not resolved at construction
        _ = app._vector_db
        assert len(calls) == 1  # resolved on first access
        _ = app._vector_db
        assert len(calls) == 1  # cached, not re-resolved


class TestCreateStructure:
    def test_meta_block_is_not_a_topic(self, runner, mock_session):
        structure = json.dumps(
            {
                "weather": {
                    "current": {
                        "_meta": {
                            "function": "get_current_weather",
                            "required": {"location": {"type": "not_empty", "field": "location"}},
                        }
                    }
                }
            }
        )
        result = runner.invoke(cli, ["create-structure", "--file", "-"], obj=mock_session, input=structure)
        assert result.exit_code == 0
        data = parse_json_output(result.stdout)
        assert set(data["created_topics"]) == {"weather", "weather/current"}
        assert "weather/_meta" not in mock_session.graph_db.topics

    def test_metaless_file_warns_global_schema_only(self, runner, mock_session):
        structure = json.dumps({"a": {"b": {}}})
        result = runner.invoke(cli, ["create-structure", "--file", "-"], obj=mock_session, input=structure)
        assert result.exit_code == 0
        data = parse_json_output(result.stdout)
        assert any("global schema only" in w for w in data["warnings"])

    def test_contradiction_aborts_before_writing(self, runner, mock_session):
        structure = json.dumps(
            {
                "p": {
                    "_meta": {"optional": {"u": {"type": "one_of", "field": "u", "valid": ["x"]}}},
                    "c": {"_meta": {"required": {"u": {"type": "one_of", "field": "u", "valid": ["y"]}}}},
                }
            }
        )
        result = runner.invoke(cli, ["create-structure", "--file", "-"], obj=mock_session, input=structure)
        assert result.exit_code == 2
        assert "invalid_meta" in result.stderr
        # nothing written: the fold runs before any create_topic call
        assert mock_session.graph_db.topics == {}


class TestSimilarScope:
    def _session_with_structure(self, tmp_path, monkeypatch, structure):
        sfile = tmp_path / "structure.json"
        sfile.write_text(json.dumps(structure))
        monkeypatch.setenv("OKGV_STRUCTURE", str(sfile))
        return Session(
            graph_db=MockGraphDB(),
            vector_db=MockVectorDB(),
            embedder=fake_embedder,
            schema=SimpleSchema(),
            db_path=tmp_path / "okgv.db",
        )

    def _seed(self, session, eid, topic):
        session.vector_db.upload_entry(eid, {"text": "seed"}, fake_embedder(["seed"])[0], topic=topic)
        session.graph_db.create_topic(topic.split("/")[0])
        session.graph_db.upload_entry(topic=topic, entry_id=eid, properties={"text": "seed"})

    def test_subtree_scope_surfaces_sibling_marked(self, runner, tmp_path, monkeypatch):
        structure = {"p": {"_meta": {"similarity_scope": "subtree"}, "a": {}, "b": {}}}
        session = self._session_with_structure(tmp_path, monkeypatch, structure)
        self._seed(session, "sibling-id", "p/b")
        result = runner.invoke(cli, ["similar", "--topic", "p/a", "--entry", json.dumps({"text": "hi"})], obj=session)
        assert result.exit_code == 0
        data = parse_json_output(result.stdout)
        assert data["scope"] == "subtree"
        match = next(m for m in data["similar"] if m["id"] == "sibling-id")
        assert match["topic"] == "p/b"
        assert match["sibling"] is True

    def test_leaf_scope_excludes_sibling(self, runner, tmp_path, monkeypatch):
        structure = {"p": {"a": {}, "b": {}}}  # no similarity_scope: default leaf
        session = self._session_with_structure(tmp_path, monkeypatch, structure)
        self._seed(session, "sibling-id", "p/b")
        result = runner.invoke(cli, ["similar", "--topic", "p/a", "--entry", json.dumps({"text": "hi"})], obj=session)
        assert result.exit_code == 0
        data = parse_json_output(result.stdout)
        assert data["scope"] == "leaf"
        assert all(m["id"] != "sibling-id" for m in data["similar"])
