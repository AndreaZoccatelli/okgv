"""Weaviate implementation of the VectorDB protocol."""

from __future__ import annotations

import weaviate
import weaviate.classes as wvc

from okgv.protocols import PropertyDefinition, VectorRecord

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

    def get_top_n(
        self,
        vector: list[float],
        n: int,
        filter_topic: str | None = None,
    ) -> list[tuple[str, float]]:
        filters = _topic_filter(filter_topic) if filter_topic else None
        response = self._collection.query.near_vector(
            near_vector=vector,
            limit=n,
            filters=filters,
            return_metadata=wvc.query.MetadataQuery(certainty=True),
        )
        return [(str(obj.uuid), obj.metadata.certainty) for obj in response.objects]

    def get_by_id(self, entry_id: str) -> VectorRecord | None:
        obj = self._collection.query.fetch_object_by_id(entry_id)
        if obj is None:
            return None
        props = dict(obj.properties)
        props.pop(TOPIC_PROPERTY, None)
        return VectorRecord(id=str(obj.uuid), properties=props)

    def get_by_ids(self, entry_ids: list[str]) -> list[VectorRecord]:
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

    def get_by_topic(self, topic: str, limit: int) -> list[VectorRecord]:
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
        results = []
        for obj in self._collection.iterator():
            results.append(str(obj.uuid))
        return results

    def delete_by_id(self, entry_id: str) -> None:
        """Delete entry. No-op if not found. Raises on connection/server errors."""
        from weaviate.exceptions import UnexpectedStatusCodeError

        try:
            self._collection.data.delete_by_id(entry_id)
        except UnexpectedStatusCodeError as e:
            if e.status_code == 404:
                return
            raise

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
