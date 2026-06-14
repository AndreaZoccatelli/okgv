"""Tests for the interactive undo view: grouping the log into operations, the
checkpoint plan, that the plan's cutoff selects exactly what `undo` deletes, and
the `okgv undo` / `okgv undo -i` CLI wiring."""

from datetime import UTC, datetime

import pytest
from click.testing import CliRunner

from okgv import core
from okgv.core import log_get_entries_after, log_operations
from okgv.main import cli
from okgv.session import Session
from tests.unit.conftest import MockGraphDB, MockVectorDB, fake_embedder

T1 = "2025-01-01T10:00:00+00:00"  # older operation: 3 entries under a/x
T2 = "2025-01-01T11:00:00+00:00"  # newer operation: 1 entry under b/y


@pytest.fixture
def runner():
    return CliRunner()


def _seed_log(db_path, rows):
    """Insert (timestamp, topic, entry_id) rows directly, for deterministic
    timestamps (log_session would stamp now())."""
    conn = core._connect(db_path)
    try:
        conn.executemany("INSERT INTO log (timestamp, topic, entry_id) VALUES (?, ?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


def _seed_two_ops(db_path):
    _seed_log(
        db_path,
        [(T1, "a/x", "e1"), (T1, "a/x", "e2"), (T1, "a/x", "e3"), (T2, "b/y", "e4")],
    )


class TestLogOperations:
    def test_groups_by_submit_newest_first(self, tmp_path):
        db = tmp_path / "okgv.db"
        _seed_two_ops(db)
        ops = log_operations(db)
        assert ops[0] == {"timestamp": T2, "count": 1, "topics": {"b/y": 1}}
        assert ops[1] == {"timestamp": T1, "count": 3, "topics": {"a/x": 3}}

    def test_empty_log(self, tmp_path):
        db = tmp_path / "okgv.db"
        core._connect(db).close()  # create schema, no rows
        assert log_operations(db) == []


class TestUndoPlan:
    """The pure planning logic: rows above the cursor (newer) are deleted, the
    cursor row and below are kept; the sentinel deletes everything."""

    def _app(self, tmp_path):
        pytest.importorskip("textual")
        from okgv.tui import UndoApp

        ops = [
            {"timestamp": T2, "count": 1, "topics": {"b/y": 1}},
            {"timestamp": T1, "count": 3, "topics": {"a/x": 3}},
        ]
        return UndoApp(
            db_path=tmp_path / "okgv.db",
            graph_db=MockGraphDB(),
            vector_db=MockVectorDB(),
            operations=ops,
            total=4,
        )

    def test_newest_checkpoint_deletes_nothing(self, tmp_path):
        plan = self._app(tmp_path)._plan(0)
        assert plan["delete_count"] == 0
        assert plan["keep_count"] == 4
        assert plan["cutoff_iso"] == T2  # keep the newest op (strict > cutoff)

    def test_middle_checkpoint_deletes_newer_only(self, tmp_path):
        plan = self._app(tmp_path)._plan(1)
        assert plan["delete_count"] == 1
        assert plan["delete_topics"] == {"b/y": 1}
        assert plan["keep_count"] == 3
        assert plan["cutoff_iso"] == T1

    def test_sentinel_deletes_everything(self, tmp_path):
        plan = self._app(tmp_path)._plan(2)
        assert plan["delete_count"] == 4
        assert plan["keep_count"] == 0
        assert plan["cutoff_iso"] is None


class TestPlanCutoffDrivesDeleteSet:
    """The plan's cutoff must select exactly the entries `undo` would delete."""

    def test_cutoff_matches_log_get_entries_after(self, tmp_path):
        pytest.importorskip("textual")
        from okgv.tui import UndoApp

        db = tmp_path / "okgv.db"
        _seed_two_ops(db)
        ops = log_operations(db)
        app = UndoApp(db_path=db, graph_db=MockGraphDB(), vector_db=MockVectorDB(), operations=ops, total=4)

        cutoff = datetime.fromisoformat(app._plan(1)["cutoff_iso"])  # checkpoint at older op
        assert set(log_get_entries_after(db, cutoff)) == {"e4"}  # only the newer entry
        assert set(log_get_entries_after(db, datetime(1, 1, 1, tzinfo=UTC))) == {"e1", "e2", "e3", "e4"}


class TestUndoPilot:
    """Drive the TUI headlessly to cover on_mount, marker/preview sync, the
    bindings, and the guarded commit -> delete path end to end."""

    def test_commit_flow_deletes_newer_only(self, tmp_path):
        pytest.importorskip("textual")
        import asyncio

        from okgv.tui import UndoApp

        db = tmp_path / "okgv.db"
        _seed_two_ops(db)
        graph_db, vector_db = MockGraphDB(), MockVectorDB()
        for eid in ("e1", "e2", "e3", "e4"):
            graph_db.entries[eid] = {}
            graph_db.entry_topics[eid] = "a/x"
        app = UndoApp(db_path=db, graph_db=graph_db, vector_db=vector_db, operations=log_operations(db), total=4)

        async def scenario():
            async with app.run_test() as pilot:
                await pilot.press("down")  # checkpoint at the older op (keep a/x, delete b/y)
                await pilot.press("c")  # arm
                await pilot.press("c")  # confirm
            return app.return_value

        result = asyncio.run(scenario())
        assert result == {"deleted": ["e4"], "count": 1, "kept": 3}
        assert graph_db.deleted == ["e4"]
        assert log_operations(db) == [{"timestamp": T1, "count": 3, "topics": {"a/x": 3}}]


class TestUndoCli:
    def _session(self, tmp_path):
        return Session(
            graph_db=MockGraphDB(),
            vector_db=MockVectorDB(),
            embedder=fake_embedder,
            db_path=tmp_path / "okgv.db",
        )

    def test_no_timestamp_no_interactive_errors(self, runner, tmp_path):
        result = runner.invoke(cli, ["undo"], obj=self._session(tmp_path))
        assert result.exit_code == 2
        assert "missing_argument" in result.stderr

    def test_interactive_empty_log_reports_nothing(self, runner, tmp_path):
        core._connect(tmp_path / "okgv.db").close()  # db exists, empty log
        result = runner.invoke(cli, ["undo", "-i"], obj=self._session(tmp_path))
        assert result.exit_code == 0
        assert "No submissions to undo" in result.stdout
