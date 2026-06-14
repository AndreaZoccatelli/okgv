"""Tests for M10 lifecycle hardening: overwrite-no-relocate, interior-node
submission ban, move revalidation, and the revalidate command."""

import json

import pytest
from click.testing import CliRunner

from okgv.core import review_get_pending_ids
from okgv.main import cli
from okgv.protocols import PropertyDefinition, entry_id
from okgv.session import Session
from tests.unit.conftest import MockGraphDB, MockVectorDB, fake_embedder


@pytest.fixture
def runner():
    return CliRunner()


def parse_json_output(out: str):
    lines = out.strip().split("\n")
    for i in range(len(lines)):
        try:
            return json.loads("\n".join(lines[i:]))
        except json.JSONDecodeError:
            continue
    raise ValueError(f"No JSON in output: {out!r}")


class _Entry:
    def __init__(self, raw: dict):
        self.text = raw["text"]
        self.kind = raw["kind"]


class _KindSchema:
    """Entry must declare a `kind` matching the root segment of its topic.

    Keying on the root (not the leaf) means a topic move — which re-parents a
    node and so changes its descendants' root — can flip an entry from valid to
    invalid, which is exactly what move revalidation must catch.
    """

    entry_class = _Entry

    @staticmethod
    def metadata(entry):
        return {"kind": entry.kind}

    @staticmethod
    def graph_properties(entry):
        return {"text": entry.text}

    @staticmethod
    def vector_properties(entry):
        return {"text": entry.text}

    @staticmethod
    def embedding_text(entry):
        return entry.text

    @staticmethod
    def vector_property_definitions():
        return [PropertyDefinition("kind", "text"), PropertyDefinition("text", "text")]

    @staticmethod
    def validate_for_topic(entry, topic):
        expected = topic.split("/")[0]
        if entry.kind != expected:
            raise ValueError(f"kind must be '{expected}', got '{entry.kind}'")


def _kind_session(tmp_path):
    return Session(
        graph_db=MockGraphDB(),
        vector_db=MockVectorDB(),
        embedder=fake_embedder,
        schema=_KindSchema(),
        db_path=tmp_path / "okgv.db",
    )


def _seed(session, eid, topic, kind, text="t"):
    props = {"kind": kind, "text": text}
    session.graph_db.upload_entry(topic=topic, entry_id=eid, properties=props)
    session.vector_db.upload_entry(eid, props, fake_embedder([text])[0], topic=topic)


# ── M10a: overwrite must not relocate ─────────────────────────────────────


class TestOverwriteNoRelocate:
    def _session(self, tmp_path):
        from tests.unit.conftest import SimpleSchema

        return Session(
            graph_db=MockGraphDB(),
            vector_db=MockVectorDB(),
            embedder=fake_embedder,
            schema=SimpleSchema(),
            db_path=tmp_path / "okgv.db",
        )

    def test_overwrite_into_different_topic_rejected(self, runner, tmp_path):
        session = self._session(tmp_path)
        raw = {"text": "hello"}
        eid = entry_id(raw)
        session.graph_db.create_topic("a")
        session.graph_db.upload_entry(topic="a", entry_id=eid, properties={"text": "hello", "text_length": 5})
        result = runner.invoke(cli, ["submit", "--topic", "b", "--entry", json.dumps(raw), "--overwrite"], obj=session)
        assert result.exit_code == 2
        assert "overwrite_relocation" in result.stderr
        # original entry stays in topic a
        assert session.graph_db.get_by_id(eid).topic == "a"

    def test_overwrite_same_topic_allowed(self, runner, tmp_path):
        session = self._session(tmp_path)
        raw = {"text": "hello"}
        eid = entry_id(raw)
        session.graph_db.create_topic("a")
        session.graph_db.upload_entry(topic="a", entry_id=eid, properties={"text": "hello", "text_length": 5})
        session.vector_db.upload_entry(eid, {"text": "hi", "text_length": 5}, fake_embedder(["x"])[0], topic="a")
        result = runner.invoke(cli, ["submit", "--topic", "a", "--entry", json.dumps(raw), "--overwrite"], obj=session)
        assert result.exit_code == 0


# ── M10b: forbid interior-node submission ─────────────────────────────────


class TestInteriorNodeSubmission:
    def _session(self, tmp_path):
        from tests.unit.conftest import SimpleSchema

        s = Session(
            graph_db=MockGraphDB(),
            vector_db=MockVectorDB(),
            embedder=fake_embedder,
            schema=SimpleSchema(),
            db_path=tmp_path / "okgv.db",
        )
        s.graph_db.create_topic("p")
        s.graph_db.create_subtopic("p", "c")
        return s

    def test_submit_to_interior_node_rejected(self, runner, tmp_path):
        session = self._session(tmp_path)
        result = runner.invoke(cli, ["submit", "--topic", "p", "--entry", json.dumps({"text": "x"})], obj=session)
        assert result.exit_code == 2
        assert "interior_topic" in result.stderr

    def test_submit_to_leaf_allowed(self, runner, tmp_path):
        session = self._session(tmp_path)
        result = runner.invoke(cli, ["submit", "--topic", "p/c", "--entry", json.dumps({"text": "x"})], obj=session)
        assert result.exit_code == 0

    def test_submit_batch_to_interior_node_rejected(self, runner, tmp_path):
        session = self._session(tmp_path)
        result = runner.invoke(
            cli, ["submit-batch", "--topic", "p", "--entries", json.dumps([{"text": "x"}])], obj=session
        )
        assert result.exit_code == 2
        assert "interior_topic" in result.stderr


# ── M10c: moves revalidate against the destination spec ───────────────────


class TestMoveRevalidation:
    def _session(self, tmp_path):
        session = _kind_session(tmp_path)
        for t in ("animals", "animals/cat", "animals/dog", "zoo"):
            parent, _, leaf = t.rpartition("/")
            if parent:
                session.graph_db.create_subtopic(parent, leaf)
            else:
                session.graph_db.create_topic(t)
        return session

    def test_move_entry_violating_destination_rejected(self, runner, tmp_path):
        # kind 'animals' is valid only under the animals/ root; moving to zoo
        # would make its root 'zoo' and break the spec.
        session = self._session(tmp_path)
        _seed(session, "e1", "animals/cat", kind="animals")
        result = runner.invoke(cli, ["move-entry", "--id", "e1", "--destination", "zoo"], obj=session)
        assert result.exit_code == 2
        assert "invalid_for_topic" in result.stderr
        assert session.graph_db.get_by_id("e1").topic == "animals/cat"  # unchanged

    def test_move_entry_compatible_destination_allowed(self, runner, tmp_path):
        session = self._session(tmp_path)
        _seed(session, "e1", "animals/cat", kind="animals")
        result = runner.invoke(cli, ["move-entry", "--id", "e1", "--destination", "animals/dog"], obj=session)
        assert result.exit_code == 0
        assert session.graph_db.get_by_id("e1").topic == "animals/dog"

    def test_move_topic_blocked_when_a_descendant_entry_would_violate(self, runner, tmp_path):
        # animals/cat holds an entry of kind 'animals'. Moving animals/cat under
        # zoo re-parents it to zoo/cat (root 'zoo'), which the spec rejects.
        session = self._session(tmp_path)
        _seed(session, "e1", "animals/cat", kind="animals")
        result = runner.invoke(cli, ["move-topic", "--source", "animals/cat", "--destination", "zoo"], obj=session)
        assert result.exit_code == 2
        assert "invalid_for_topic" in result.stderr
        assert session.graph_db.get_by_id("e1").topic == "animals/cat"  # unchanged

    def test_move_topic_allowed_when_root_unchanged(self, runner, tmp_path):
        session = self._session(tmp_path)
        _seed(session, "e1", "animals/cat", kind="animals")
        result = runner.invoke(
            cli, ["move-topic", "--source", "animals/cat", "--destination", "animals/dog"], obj=session
        )
        assert result.exit_code == 0  # new path animals/dog/cat keeps root 'animals'


class TestMoveUpdatesReview:
    """A move must carry the entry's review-queue row to the new topic, so
    `review --topic` and per-topic counts stay consistent; status is preserved."""

    def _session(self, tmp_path):
        session = _kind_session(tmp_path)
        for t in ("animals", "animals/cat", "animals/dog", "zoo"):
            parent, _, leaf = t.rpartition("/")
            if parent:
                session.graph_db.create_subtopic(parent, leaf)
            else:
                session.graph_db.create_topic(t)
        return session

    def test_move_entry_carries_review_row(self, runner, tmp_path):
        from okgv.core import review_add, review_list

        session = self._session(tmp_path)
        _seed(session, "e1", "animals/cat", kind="animals")
        review_add(session.db_path, "animals/cat", ["e1"])

        result = runner.invoke(cli, ["move-entry", "--id", "e1", "--destination", "animals/dog"], obj=session)
        assert result.exit_code == 0

        row = {r["entry_id"]: r for r in review_list(session.db_path, status="pending")}["e1"]
        assert row["topic"] == "animals/dog"  # followed the move
        assert row["status"] == "pending"  # status preserved

    def test_move_topic_reparents_review_rows(self, runner, tmp_path):
        from okgv.core import review_add, review_list

        session = self._session(tmp_path)
        _seed(session, "e1", "animals/cat", kind="animals")
        review_add(session.db_path, "animals/cat", ["e1"])

        result = runner.invoke(
            cli, ["move-topic", "--source", "animals/cat", "--destination", "animals/dog"], obj=session
        )
        assert result.exit_code == 0

        row = {r["entry_id"]: r for r in review_list(session.db_path, status="pending")}["e1"]
        assert row["topic"] == "animals/dog/cat"  # prefix swap applied


# ── M10d: revalidate command + create-structure warning ───────────────────


class TestRevalidateCommand:
    def _session(self, tmp_path):
        session = _kind_session(tmp_path)
        session.graph_db.create_topic("animals")
        session.graph_db.create_subtopic("animals", "cat")
        return session

    def test_reports_and_queues_violators(self, runner, tmp_path):
        session = self._session(tmp_path)
        _seed(session, "ok1", "animals/cat", kind="animals")
        _seed(session, "bad1", "animals/cat", kind="dog")  # root is 'animals', not 'dog'
        result = runner.invoke(cli, ["revalidate", "--topic", "animals"], obj=session)
        assert result.exit_code == 0
        data = parse_json_output(result.stdout)
        assert data["violation_count"] == 1
        assert data["violations"][0]["id"] == "bad1"
        assert data["queued"] is True
        assert "bad1" in review_get_pending_ids(session.db_path)

    def test_no_queue_flag_skips_review(self, runner, tmp_path):
        session = self._session(tmp_path)
        _seed(session, "bad1", "animals/cat", kind="dog")
        result = runner.invoke(cli, ["revalidate", "--no-queue"], obj=session)
        assert result.exit_code == 0
        data = parse_json_output(result.stdout)
        assert data["violation_count"] == 1
        assert data["queued"] is False
        assert review_get_pending_ids(session.db_path) == set()

    def test_clean_dataset_reports_nothing(self, runner, tmp_path):
        session = self._session(tmp_path)
        _seed(session, "ok1", "animals/cat", kind="animals")
        result = runner.invoke(cli, ["revalidate"], obj=session)
        data = parse_json_output(result.stdout)
        assert data["violation_count"] == 0


class TestCreateStructureWarnsOverNonEmptyDB:
    def test_warning_emitted_when_entries_exist(self, runner, tmp_path):
        from tests.unit.conftest import SimpleSchema

        session = Session(
            graph_db=MockGraphDB(),
            vector_db=MockVectorDB(),
            embedder=fake_embedder,
            schema=SimpleSchema(),
            db_path=tmp_path / "okgv.db",
        )
        session.graph_db.create_topic("old")
        session.graph_db.upload_entry(topic="old", entry_id="e1", properties={"text": "x"})
        result = runner.invoke(cli, ["create-structure", "--file", "-"], obj=session, input=json.dumps({"new": {}}))
        assert result.exit_code == 0
        data = parse_json_output(result.stdout)
        assert any("run `revalidate`" in w for w in data["warnings"])
