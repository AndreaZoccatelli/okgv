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
}


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

    def get_by_id(self, entry_id: str) -> VectorRecord | None:
        obj = self._collection.query.fetch_object_by_id(entry_id)
        if obj is None:
            return None
        return VectorRecord(
            id=str(obj.uuid),
            properties=dict(obj.properties),
        )

    def get_by_ids(self, entry_ids: list[str]) -> list[VectorRecord]:
        response = self._collection.query.fetch_objects(
            filters=wvc.query.Filter.by_id().contains_any(entry_ids),
            limit=len(entry_ids),
        )
        return [
            VectorRecord(id=str(obj.uuid), properties=dict(obj.properties))
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
            raise ValueError(
                f"Entry '{entry_id}' already exists in vector DB. "
                f"Pass overwrite=True to replace."
            )
        if exists:
            self._collection.data.replace(
                uuid=entry_id, properties=properties, vector=vector
            )
        else:
            self._collection.data.insert(
                uuid=entry_id, properties=properties, vector=vector
            )

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
            weaviate_props = []
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
