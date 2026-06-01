"""Shared fixtures: mock DB backends and schema."""

import pytest

from okgv.protocols import GraphRecord, PropertyDefinition, VectorRecord


class MockGraphDB:
    """In-memory graph DB for testing."""

    def __init__(self):
        self.topics: dict[str, set[str]] = {}  # path -> set of child paths
        self.entries: dict[str, dict] = {}  # entry_id -> properties
        self.entry_topics: dict[str, str] = {}  # entry_id -> topic path
        self.deleted: list[str] = []

    def topic_exists(self, path: str) -> bool:
        return path in self.topics

    def create_topic(self, name: str) -> None:
        self.topics.setdefault(name, set())

    def create_subtopic(self, parent: str, name: str) -> None:
        path = f"{parent}/{name}"
        self.topics.setdefault(path, set())
        self.topics.setdefault(parent, set()).add(path)

    def get_subtopics(self, topic: str) -> list[str]:
        return list(self.topics.get(topic, set()))

    def get_topic_entry_counts(self, parent: str | None = None) -> dict[str, int]:
        if parent is None:
            roots = [p for p in self.topics if "/" not in p]
        else:
            roots = list(self.topics.get(parent, set()))
        counts = {}
        for root in roots:
            count = sum(1 for eid, t in self.entry_topics.items() if t == root or t.startswith(root + "/"))
            counts[root] = count
        return counts

    def get_entry_ids_for_topic(self, topic: str) -> list[str]:
        return [eid for eid, t in self.entry_topics.items() if t == topic or t.startswith(topic + "/")]

    def get_entries_for_topic(self, topic: str) -> list[GraphRecord]:
        return [
            GraphRecord(id=eid, topic=t, properties=self.entries[eid])
            for eid, t in self.entry_topics.items()
            if t == topic or t.startswith(topic + "/")
        ]

    def upload_entry(self, topic: str, entry_id: str, properties: dict, overwrite: bool = False) -> None:
        if entry_id in self.entries and not overwrite:
            raise ValueError(f"Entry '{entry_id}' already exists in graph DB. Pass overwrite=True to replace.")
        self.entries[entry_id] = properties
        self.entry_topics[entry_id] = topic

    def get_by_id(self, entry_id: str) -> GraphRecord | None:
        if entry_id not in self.entries:
            return None
        return GraphRecord(id=entry_id, topic=self.entry_topics[entry_id], properties=self.entries[entry_id])

    def get_all_entry_ids(self) -> list[str]:
        return list(self.entries.keys())

    def delete_entries(self, ids: list[str]) -> None:
        for eid in ids:
            self.entries.pop(eid, None)
            self.entry_topics.pop(eid, None)
            self.deleted.append(eid)

    def move_topic(self, source: str, destination: str) -> None:
        name = source.rsplit("/", 1)[-1]
        new_path = f"{destination}/{name}"
        if new_path in self.topics:
            raise ValueError(f"Destination '{destination}' already has subtopic '{name}'")
        self.topics[new_path] = self.topics.pop(source, set())
        self.topics.setdefault(destination, set()).add(new_path)

    def move_entry(self, entry_id: str, new_topic: str) -> None:
        self.entry_topics[entry_id] = new_topic

    def close(self) -> None:
        pass


class MockVectorDB:
    """In-memory vector DB for testing."""

    def __init__(self, fail_on_upload=False, fail_on_delete_id=None):
        self.entries: dict[str, dict] = {}  # entry_id -> properties (without topic)
        self.topics: dict[str, str] = {}  # entry_id -> topic
        self.vectors: dict[str, list[float]] = {}
        self.fail_on_upload = fail_on_upload
        self.fail_on_delete_id = fail_on_delete_id

    def _matches_topic(self, entry_id: str, topic: str) -> bool:
        t = self.topics.get(entry_id, "")
        return t == topic or t.startswith(topic + "/")

    def get_top_n(self, vector: list[float], n: int, filter_topic: str | None = None) -> list[tuple[str, float]]:
        ids = list(self.entries.keys())
        if filter_topic is not None:
            ids = [i for i in ids if self._matches_topic(i, filter_topic)]
        return [(eid, 0.95) for eid in ids[:n]]

    def get_by_id(self, entry_id: str) -> VectorRecord | None:
        if entry_id not in self.entries:
            return None
        return VectorRecord(id=entry_id, properties=self.entries[entry_id])

    def get_by_ids(self, entry_ids: list[str]) -> list[VectorRecord]:
        return [VectorRecord(id=eid, properties=self.entries[eid]) for eid in entry_ids if eid in self.entries]

    def get_by_topic(self, topic: str, limit: int) -> list[VectorRecord]:
        results = []
        for eid in self.entries:
            if self._matches_topic(eid, topic):
                results.append(VectorRecord(id=eid, properties=self.entries[eid]))
                if len(results) >= limit:
                    break
        return results

    def upload_entry(self, entry_id: str, properties: dict, vector: list[float], topic: str, overwrite: bool = False) -> None:
        if self.fail_on_upload:
            raise ConnectionError("Vector DB unavailable")
        if entry_id in self.entries and not overwrite:
            raise ValueError(f"Entry '{entry_id}' already exists in vector DB. Pass overwrite=True to replace.")
        self.entries[entry_id] = properties
        self.topics[entry_id] = topic
        self.vectors[entry_id] = vector

    def upload_entries_batch(self, entries: list[dict], vectors: list[list[float]], entry_ids: list[str], topic: str) -> list[str]:
        if self.fail_on_upload:
            return entry_ids  # all failed
        failed = []
        for eid, props, vec in zip(entry_ids, entries, vectors):
            if eid in self.entries:
                failed.append(eid)
                continue
            self.entries[eid] = props
            self.topics[eid] = topic
            self.vectors[eid] = vec
        return failed

    def update_entry_topic(self, entry_id: str, new_topic: str) -> None:
        self.topics[entry_id] = new_topic

    def update_topics(self, old_prefix: str, new_prefix: str) -> None:
        for eid in list(self.topics):
            t = self.topics[eid]
            if t == old_prefix or t.startswith(old_prefix + "/"):
                self.topics[eid] = new_prefix + t[len(old_prefix):]

    def get_all_entry_ids(self) -> list[str]:
        return list(self.entries.keys())

    def delete_by_id(self, entry_id: str) -> None:
        if self.fail_on_delete_id and entry_id == self.fail_on_delete_id:
            raise ConnectionError(f"Failed to delete {entry_id}")
        self.entries.pop(entry_id, None)
        self.topics.pop(entry_id, None)
        self.vectors.pop(entry_id, None)

    def delete_by_ids(self, entry_ids: list[str]) -> None:
        for eid in entry_ids:
            self.entries.pop(eid, None)
            self.topics.pop(eid, None)
            self.vectors.pop(eid, None)

    def ensure_collection(self) -> None:
        pass

    def close(self) -> None:
        pass


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
    return [[0.1, 0.2, 0.3] for _ in texts]


@pytest.fixture
def graph_db():
    return MockGraphDB()


@pytest.fixture
def vector_db():
    return MockVectorDB()


@pytest.fixture
def schema():
    return SimpleSchema()


@pytest.fixture
def session(graph_db, vector_db, schema, tmp_path):
    from okgv.session import Session

    return Session(
        graph_db=graph_db,
        vector_db=vector_db,
        embedder=fake_embedder,
        schema=schema,
        log_db=tmp_path / "log.db",
    )
