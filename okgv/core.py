"""Core logic: upsert, logging, schema validation."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from okgv.helpers import err, EXIT_USAGE
from okgv.protocols import GraphDB, VectorDB, entry_id

LOG_FILE = Path.cwd() / "log.json"

_schema_validated = False


def validate_schema(schema, meta: dict, graph_props: dict, vector_props: dict) -> None:
    """Check for key collisions and missing vector property definitions."""
    global _schema_validated
    if _schema_validated:
        return

    # Key collision between metadata and per-DB properties
    graph_overlap = set(meta) & set(graph_props)
    if graph_overlap:
        err(
            "schema_key_collision",
            detail=f"metadata() and graph_properties() share keys: {graph_overlap}",
            suggestion="Remove duplicates from one of the methods",
            exit_code=EXIT_USAGE,
        )
    vector_overlap = set(meta) & set(vector_props)
    if vector_overlap:
        err(
            "schema_key_collision",
            detail=f"metadata() and vector_properties() share keys: {vector_overlap}",
            suggestion="Remove duplicates from one of the methods",
            exit_code=EXIT_USAGE,
        )

    # vector_property_definitions must cover all vector DB keys
    defined_names = {pd.name for pd in schema.vector_property_definitions()}
    actual_keys = set(meta) | set(vector_props)
    missing = actual_keys - defined_names
    if missing:
        err(
            "schema_missing_definitions",
            detail=f"vector_property_definitions() missing keys: {missing}",
            suggestion="Add PropertyDefinition entries for these keys",
            exit_code=EXIT_USAGE,
        )
    extra = defined_names - actual_keys
    if extra:
        err(
            "schema_extra_definitions",
            detail=f"vector_property_definitions() defines unused keys: {extra}",
            suggestion="Remove these PropertyDefinition entries or add them to metadata()/vector_properties()",
            exit_code=EXIT_USAGE,
        )

    _schema_validated = True


def build_entry(schema, raw: dict):
    """Build entry object from raw dict using schema's entry_class."""
    try:
        return schema.entry_class(raw)
    except KeyError as e:
        err(
            "missing_field",
            detail=f"Entry JSON missing required key: {e}",
            exit_code=EXIT_USAGE,
        )


def upsert_entry(
    schema,
    graph_db: GraphDB,
    vector_db: VectorDB,
    topic: str,
    raw: dict,
    embedder: Callable[[list[str]], list[list[float]]],
) -> str:
    eid = entry_id(raw)
    entry = build_entry(schema, raw)
    meta = schema.metadata(entry)
    graph_props = schema.graph_properties(entry)
    vector_props = schema.vector_properties(entry)

    validate_schema(schema, meta, graph_props, vector_props)

    graph_db.upload_entry(
        topic=topic,
        entry_id=eid,
        properties={**meta, **graph_props},
    )

    vector = embedder([schema.embedding_text(entry)])[0]
    vector_db.upload_entry(
        entry_id=eid,
        properties={**meta, **vector_props},
        vector=vector,
    )

    return eid


def log_session(topic: str, inserted_ids: list[str]) -> None:
    log = {}
    if LOG_FILE.exists():
        log = json.loads(LOG_FILE.read_text())
    timestamp = datetime.now(timezone.utc).isoformat()
    log[timestamp] = {topic: inserted_ids}
    LOG_FILE.write_text(json.dumps(log, indent=2))
