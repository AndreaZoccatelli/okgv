"""Tests for protocol utilities."""

from okgv.protocols import entry_id


class TestEntryId:
    def test_deterministic(self):
        raw = {"question": "What is 2+2?", "answer": "4"}
        assert entry_id(raw) == entry_id(raw)

    def test_key_order_irrelevant(self):
        raw1 = {"a": 1, "b": 2}
        raw2 = {"b": 2, "a": 1}
        assert entry_id(raw1) == entry_id(raw2)

    def test_different_content_different_id(self):
        assert entry_id({"x": 1}) != entry_id({"x": 2})

    def test_returns_valid_uuid(self):
        import uuid
        result = entry_id({"test": True})
        uuid.UUID(result)  # raises if invalid
