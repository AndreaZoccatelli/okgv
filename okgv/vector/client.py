"""Weaviate implementation of the VectorDB protocol."""

from __future__ import annotations

import time
from typing import Callable, TypeVar

import weaviate
import weaviate.classes as wvc

from okgv.protocols import PropertyDefinition, VectorRecord

_T = TypeVar("_T")
_MAX_RETRIES = 2
_RETRY_DELAY = 1

_WEAVIATE_TYPE_MAP = {
    "text": wvc.config.DataType.TEXT,
    "int": wvc.config.DataType.INT,
    "float": wvc.config.DataType.NUMBER,
    "bool": wvc.config.DataType.BOOL,
    "text[]": wvc.config.DataType.TEXT_ARRAY,
    "number": wvc.config.DataType.NUMBER,
}

TOPIC_PROPERTY = "okgv_topic"


def _topic_filter(topic: str):
    """Filter for entries in a topic or any of its subtopics."""
    return wvc.query.Filter.by_property(TOPIC_PROPERTY).equal(
        topic
    ) | wvc.query.Filter.by_property(TOPIC_PROPERTY).like(f"{topic}/*")


class WeaviateVectorDB:
    def __init__(
        self,
        host: str,
        http_port: int,
        grpc_port: int,
        collection_name: str,
        property_definitions: list[PropertyDefinition],
        secure: bool = False,
        api_key: str | None = None,
    ) -> None:
        auth = weaviate.auth.AuthApiKey(api_key) if api_key else None
        self._client = weaviate.connect_to_custom(
            http_host=host,
            http_port=http_port,
            http_secure=secure,
            grpc_host=host,
            grpc_port=grpc_port,
            grpc_secure=secure,
            auth_credentials=auth,
        )
        self._collection_name = collection_name
        self._property_definitions = property_definitions

    @property
    def _collection(self):
        return self._client.collections.get(self._collection_name)

    def _with_retry(self, fn: Callable[[], _T]) -> _T:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return fn()
            except (ConnectionError, OSError, TimeoutError):
                if attempt == _MAX_RETRIES:
                    raise
                time.sleep(_RETRY_DELAY * (attempt + 1))
        raise RuntimeError("unreachable")

    def get_top_n(
        self,
        vector: list[float],
        n: int,
        filter_topic: str | None = None,
    ) -> list[tuple[str, float]]:
        def _op():
            filters = _topic_filter(filter_topic) if filter_topic else None
            response = self._collection.query.near_vector(
                near_vector=vector,
                limit=n,
                filters=filters,
                return_metadata=wvc.query.MetadataQuery(certainty=True),
            )
            return [(str(obj.uuid), obj.metadata.certainty) for obj in response.objects]
        return self._with_retry(_op)

    def get_by_id(self, entry_id: str) -> VectorRecord | None:
        def _op():
            obj = self._collection.query.fetch_object_by_id(entry_id)
            if obj is None:
                return None
            props = dict(obj.properties)
            props.pop(TOPIC_PROPERTY, None)
            return VectorRecord(id=str(obj.uuid), properties=props)
        return self._with_retry(_op)

    def get_by_ids(self, entry_ids: list[str]) -> list[VectorRecord]:
        def _op():
            response = self._collection.query.fetch_objects(
                filters=wvc.query.Filter.by_id().contains_any(entry_ids),
                limit=len(entry_ids),
            )
            results = []
            for obj in response.objects:
                props = dict(obj.properties)
                props.pop(TOPIC_PROPERTY, None)
                results.append(VectorRecord(id=str(obj.uuid), properties=props))
            return results
        return self._with_retry(_op)

    def get_by_topic(self, topic: str, limit: int) -> list[VectorRecord]:
        def _op():
            response = self._collection.query.fetch_objects(
                filters=_topic_filter(topic),
                limit=limit,
            )
            results = []
            for obj in response.objects:
                props = dict(obj.properties)
                props.pop(TOPIC_PROPERTY, None)
                results.append(VectorRecord(id=str(obj.uuid), properties=props))
            return results
        return self._with_retry(_op)

    def upload_entry(
        self,
        entry_id: str,
        properties: dict,
        vector: list[float],
        topic: str,
        overwrite: bool = False,
    ) -> None:
        from weaviate.exceptions import UnexpectedStatusCodeError

        self.ensure_collection()
        stored = {**properties, TOPIC_PROPERTY: topic}
        try:
            self._collection.data.insert(
                uuid=entry_id, properties=stored, vector=vector
            )
        except UnexpectedStatusCodeError as e:
            if e.status_code != 422:
                raise
            if not overwrite:
                raise ValueError(
                    f"Entry '{entry_id}' already exists in vector DB. "
                    f"Pass overwrite=True to replace."
                ) from e
            self._collection.data.replace(
                uuid=entry_id, properties=stored, vector=vector
            )

    def upload_entries_batch(
        self,
        entries: list[dict],
        vectors: list[list[float]],
        entry_ids: list[str],
        topic: str,
    ) -> list[str]:
        """Batch insert using Weaviate insert_many. Returns failed entry IDs."""
        self.ensure_collection()
        objects = [
            wvc.data.DataObject(
                uuid=eid,
                properties={**props, TOPIC_PROPERTY: topic},
                vector=vec,
            )
            for eid, props, vec in zip(entry_ids, entries, vectors)
        ]
        response = self._collection.data.insert_many(objects)
        failed_ids = []
        if response.errors:
            for idx in response.errors:
                failed_ids.append(entry_ids[idx])
        return failed_ids

    def delete_by_ids(self, entry_ids: list[str]) -> None:
        """Batch delete using Weaviate delete_many."""
        if not entry_ids:
            return
        def _op():
            self._collection.data.delete_many(
                where=wvc.query.Filter.by_id().contains_any(entry_ids)
            )
        self._with_retry(_op)

    def update_entry_topic(self, entry_id: str, new_topic: str) -> None:
        self._collection.data.update(
            uuid=entry_id,
            properties={TOPIC_PROPERTY: new_topic},
        )

    def update_topics(self, old_prefix: str, new_prefix: str) -> None:
        """Update topic for all entries matching old_prefix (exact or descendant)."""
        filters = _topic_filter(old_prefix)
        batch_size = 100
        while True:
            response = self._collection.query.fetch_objects(
                filters=filters,
                limit=batch_size,
                return_properties=[TOPIC_PROPERTY],
            )
            if not response.objects:
                break
            for obj in response.objects:
                old_topic = str(obj.properties.get(TOPIC_PROPERTY, ""))
                new_topic = new_prefix + old_topic[len(old_prefix):]
                self._collection.data.update(
                    uuid=obj.uuid,
                    properties={TOPIC_PROPERTY: new_topic},
                )

    def get_all_entry_ids(self) -> list[str]:
        def _op():
            results = []
            for obj in self._collection.iterator():
                results.append(str(obj.uuid))
            return results
        return self._with_retry(_op)

    def delete_by_id(self, entry_id: str) -> None:
        """Delete entry. No-op if not found. Raises on connection/server errors."""
        from weaviate.exceptions import UnexpectedStatusCodeError

        def _op():
            try:
                self._collection.data.delete_by_id(entry_id)
            except UnexpectedStatusCodeError as e:
                if e.status_code == 404:
                    return
                raise
        self._with_retry(_op)

    def ensure_collection(self) -> None:
        if not self._client.collections.exists(self._collection_name):
            weaviate_props = [
                wvc.config.Property(
                    name=TOPIC_PROPERTY, data_type=wvc.config.DataType.TEXT
                ),
            ]
            for pd in self._property_definitions:
                wv_type = _WEAVIATE_TYPE_MAP.get(pd.data_type)
                if wv_type is None:
                    raise ValueError(
                        f"Unknown data_type '{pd.data_type}' for property '{pd.name}'. "
                        f"Supported: {list(_WEAVIATE_TYPE_MAP)}"
                    )
                weaviate_props.append(
                    wvc.config.Property(name=pd.name, data_type=wv_type)
                )
            self._client.collections.create(
                name=self._collection_name,
                vector_config=wvc.config.Configure.Vectors.self_provided(),
                properties=weaviate_props,
            )

    def close(self) -> None:
        self._client.close()
