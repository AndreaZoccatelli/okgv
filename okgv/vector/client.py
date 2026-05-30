"""Weaviate implementation of the VectorDB protocol."""

from __future__ import annotations

import json

import weaviate
import weaviate.classes as wvc

from protocols import VectorEntry


class WeaviateVectorDB:
    def __init__(
        self,
        host: str,
        http_port: int,
        grpc_port: int,
        collection_name: str,
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

    @property
    def _collection(self):
        return self._client.collections.get(self._collection_name)

    def get_top_n(
        self,
        vector: list[float],
        n: int,
        filter_ids: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        filters = (
            wvc.query.Filter.by_id().contains_any(filter_ids)
            if filter_ids
            else None
        )
        response = self._collection.query.near_vector(
            near_vector=vector,
            limit=n,
            filters=filters,
            return_metadata=wvc.query.MetadataQuery(certainty=True),
        )
        return [(str(obj.uuid), obj.metadata.certainty) for obj in response.objects]

    def get_by_id(self, entry_id: str) -> VectorEntry | None:
        obj = self._collection.query.fetch_object_by_id(entry_id)
        if obj is None:
            return None
        return VectorEntry(
            id=str(obj.uuid),
            question=obj.properties["question"],
            options=json.loads(obj.properties["options"]),
            answer=obj.properties["answer"],
        )

    def get_by_ids(self, entry_ids: list[str]) -> list[VectorEntry]:
        response = self._collection.query.fetch_objects(
            filters=wvc.query.Filter.by_id().contains_any(entry_ids),
            limit=len(entry_ids),
        )
        return [
            VectorEntry(
                id=str(obj.uuid),
                question=obj.properties["question"],
                options=json.loads(obj.properties["options"]),
                answer=obj.properties["answer"],
            )
            for obj in response.objects
        ]

    def upload_entry(
        self,
        entry_id: str,
        properties: dict,
        vector: list[float],
        overwrite: bool = False,
    ) -> None:
        self.ensure_collection()
        exists = self._collection.query.fetch_object_by_id(entry_id) is not None
        if exists and not overwrite:
            return
        props = {
            "question": properties["question"],
            "options": json.dumps(properties["options"]),
            "answer": properties["answer"],
        }
        if exists:
            self._collection.data.replace(
                uuid=entry_id, properties=props, vector=vector
            )
        else:
            self._collection.data.insert(
                uuid=entry_id, properties=props, vector=vector
            )

    def delete_by_id(self, entry_id: str) -> None:
        try:
            self._collection.data.delete_by_id(entry_id)
        except Exception:
            pass

    def ensure_collection(self) -> None:
        if not self._client.collections.exists(self._collection_name):
            self._client.collections.create(
                name=self._collection_name,
                vector_config=wvc.config.Configure.Vectors.self_provided(),
                properties=[
                    wvc.config.Property(
                        name="question", data_type=wvc.config.DataType.TEXT
                    ),
                    wvc.config.Property(
                        name="options", data_type=wvc.config.DataType.TEXT
                    ),
                    wvc.config.Property(
                        name="answer", data_type=wvc.config.DataType.TEXT
                    ),
                ],
            )

    def close(self) -> None:
        self._client.close()
