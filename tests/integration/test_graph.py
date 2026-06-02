"""Integration tests for SQLite GraphDB client."""

import pytest


class TestTopics:
    def test_create_and_exists(self, graph_db):
        graph_db.create_topic("math")
        assert graph_db.topic_exists("math")

    def test_not_exists(self, graph_db):
        assert not graph_db.topic_exists("nonexistent")

    def test_create_idempotent(self, graph_db):
        graph_db.create_topic("math")
        graph_db.create_topic("math")
        assert graph_db.topic_exists("math")

    def test_create_subtopic(self, graph_db):
        graph_db.create_topic("math")
        graph_db.create_subtopic("math", "algebra")
        assert graph_db.topic_exists("math/algebra")

    def test_nested_subtopics(self, graph_db):
        graph_db.create_topic("math")
        graph_db.create_subtopic("math", "algebra")
        graph_db.create_subtopic("math/algebra", "linear")
        assert graph_db.topic_exists("math/algebra/linear")

    def test_get_subtopics(self, graph_db):
        graph_db.create_topic("root")
        graph_db.create_subtopic("root", "a")
        graph_db.create_subtopic("root", "b")
        subs = graph_db.get_subtopics("root")
        assert sorted(subs) == ["root/a", "root/b"]


class TestEntries:
    def test_upload_and_get(self, graph_db):
        graph_db.create_topic("t")
        graph_db.upload_entry("t", "e1", {"text": "hello", "text_length": 5})
        record = graph_db.get_by_id("e1")
        assert record is not None
        assert record.topic == "t"
        assert record.properties["text"] == "hello"

    def test_duplicate_raises(self, graph_db):
        graph_db.create_topic("t")
        graph_db.upload_entry("t", "e1", {"text": "a"})
        with pytest.raises(ValueError, match="already exists"):
            graph_db.upload_entry("t", "e1", {"text": "b"})

    def test_overwrite(self, graph_db):
        graph_db.create_topic("t")
        graph_db.upload_entry("t", "e1", {"text": "old"})
        graph_db.upload_entry("t", "e1", {"text": "new"}, overwrite=True)
        record = graph_db.get_by_id("e1")
        assert record.properties["text"] == "new"

    def test_get_nonexistent(self, graph_db):
        assert graph_db.get_by_id("nonexistent") is None

    def test_delete(self, graph_db):
        graph_db.create_topic("t")
        graph_db.upload_entry("t", "e1", {"text": "bye"})
        graph_db.delete_entries(["e1"])
        assert graph_db.get_by_id("e1") is None

    def test_get_all_entry_ids(self, graph_db):
        graph_db.create_topic("t")
        graph_db.upload_entry("t", "e1", {"text": "a"})
        graph_db.upload_entry("t", "e2", {"text": "b"})
        ids = graph_db.get_all_entry_ids()
        assert sorted(ids) == ["e1", "e2"]


class TestEntryCounts:
    def test_counts_per_child(self, graph_db):
        graph_db.create_topic("root")
        graph_db.create_subtopic("root", "a")
        graph_db.create_subtopic("root", "b")
        graph_db.upload_entry("root/a", "e1", {"text": "x"})
        graph_db.upload_entry("root/a", "e2", {"text": "y"})
        graph_db.upload_entry("root/b", "e3", {"text": "z"})
        counts = graph_db.get_topic_entry_counts(parent="root")
        assert counts["root/a"] == 2
        assert counts["root/b"] == 1

    def test_recursive_counts(self, graph_db):
        graph_db.create_topic("root")
        graph_db.create_subtopic("root", "a")
        graph_db.create_subtopic("root/a", "deep")
        graph_db.upload_entry("root/a/deep", "e1", {"text": "x"})
        counts = graph_db.get_topic_entry_counts(parent="root")
        assert counts["root/a"] == 1  # includes descendant

    def test_root_counts(self, graph_db):
        graph_db.create_topic("alpha")
        graph_db.create_topic("beta")
        graph_db.upload_entry("alpha", "e1", {"text": "x"})
        counts = graph_db.get_topic_entry_counts(parent=None)
        assert counts["alpha"] == 1
        assert counts["beta"] == 0

    def test_get_entries_for_topic(self, graph_db):
        graph_db.create_topic("t")
        graph_db.create_subtopic("t", "sub")
        graph_db.upload_entry("t", "e1", {"text": "a"})
        graph_db.upload_entry("t/sub", "e2", {"text": "b"})
        entries = graph_db.get_entries_for_topic("t")
        assert len(entries) == 2
        ids = {e.id for e in entries}
        assert ids == {"e1", "e2"}

    def test_get_entry_ids_for_topic(self, graph_db):
        graph_db.create_topic("t")
        graph_db.create_subtopic("t", "sub")
        graph_db.upload_entry("t/sub", "e1", {"text": "x"})
        ids = graph_db.get_entry_ids_for_topic("t")
        assert ids == ["e1"]


class TestMoveTopic:
    def test_move_topic(self, graph_db):
        graph_db.create_topic("src")
        graph_db.create_subtopic("src", "child")
        graph_db.create_topic("dst")
        graph_db.move_topic("src/child", "dst")
        assert graph_db.topic_exists("dst/child")
        assert not graph_db.topic_exists("src/child")

    def test_move_topic_updates_descendant_paths(self, graph_db):
        graph_db.create_topic("a")
        graph_db.create_subtopic("a", "b")
        graph_db.create_subtopic("a/b", "c")
        graph_db.create_topic("x")
        graph_db.move_topic("a/b", "x")
        assert graph_db.topic_exists("x/b")
        assert graph_db.topic_exists("x/b/c")
        assert not graph_db.topic_exists("a/b")

    def test_move_topic_conflict(self, graph_db):
        graph_db.create_topic("a")
        graph_db.create_subtopic("a", "child")
        graph_db.create_topic("b")
        graph_db.create_subtopic("b", "child")
        with pytest.raises(ValueError, match="already has subtopic"):
            graph_db.move_topic("a/child", "b")


class TestMoveEntry:
    def test_move_entry(self, graph_db):
        graph_db.create_topic("old")
        graph_db.create_topic("new")
        graph_db.upload_entry("old", "e1", {"text": "x"})
        graph_db.move_entry("e1", "new")
        record = graph_db.get_by_id("e1")
        assert record.topic == "new"
