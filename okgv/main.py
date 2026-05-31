"""
CLI for AI agents to interact with the self-organized knowledge base.

Commands:
  least-topic    — Topic with fewest entries.
  similar        — Top-N most similar entries to a candidate within a topic.
  submit         — Upsert entry into both graph and vector DBs.
  similar-batch  — Batch version of similar: single model load for N candidates.
  submit-batch   — Batch version of submit: single model load for N entries.

Schema discovery (see config.py):
  1. OKGV_SCHEMA env var →  "module:ClassName"
  2. Built-in QAEntrySchema fallback

Exit codes:  0=ok  1=failure  2=usage  3=not_found  4=connection
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NoReturn

import click

from okgv.config import load_schema
from okgv.embedding import make_embedder
from okgv.graph.client import Neo4jGraphDB
from okgv.protocols import GraphDB, VectorDB, entry_id
from okgv.vector.client import WeaviateVectorDB

LOG_FILE = Path.cwd() / "log.json"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_CONNECTION = 4

# Loaded once at startup via schema discovery.
SCHEMA = load_schema()


# ── Helpers ───────────────────────────────────────────────────────────────


def _env(key: str, default: str) -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _output(data: dict | list) -> None:
    json.dump(data, sys.stdout, indent=2)
    sys.stdout.write("\n")


def _err(
    error: str, detail: str = "", suggestion: str = "", exit_code: int = EXIT_FAILURE
) -> NoReturn:
    msg: dict = {"error": error}
    if detail:
        msg["detail"] = detail
    if suggestion:
        msg["suggestion"] = suggestion
    json.dump(msg, sys.stderr, indent=2)
    sys.stderr.write("\n")
    sys.exit(exit_code)


def _parse_raw(raw_str: str) -> dict:
    """Parse JSON string into dict."""
    try:
        return json.loads(raw_str)
    except json.JSONDecodeError as e:
        _err("invalid_json", detail=str(e), exit_code=EXIT_USAGE)


def _build_entry(raw: dict):
    """Build entry object from raw dict using schema's entry_class."""
    try:
        return SCHEMA.entry_class(raw)
    except KeyError as e:
        _err(
            "missing_field",
            detail=f"Entry JSON missing required key: {e}",
            exit_code=EXIT_USAGE,
        )


def _read_raw(entry_str: str) -> dict:
    """Read raw dict from argument or stdin (if '-')."""
    if entry_str == "-":
        return _parse_raw(sys.stdin.read())
    return _parse_raw(entry_str)


def _log(msg: str) -> None:
    click.echo(msg, err=True)


# ── DB connections ────────────────────────────────────────────────────────


def connect_graph_db() -> GraphDB:
    try:
        return Neo4jGraphDB(
            uri=_env("NEO4J_URI", "bolt://localhost:7687"),
            user=_env("NEO4J_USER", "neo4j"),
            password=_env("NEO4J_PASSWORD", "password"),
            database=_env("NEO4J_DATABASE", "neo4j"),
        )
    except Exception as e:
        _err(
            "graph_db_connection_failed",
            detail=str(e),
            suggestion="Check NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE env vars",
            exit_code=EXIT_CONNECTION,
        )


def connect_vector_db() -> VectorDB:
    try:
        return WeaviateVectorDB(
            host=_env("WEAVIATE_HOST", "localhost"),
            http_port=_env_int("WEAVIATE_PORT", 8080),
            grpc_port=_env_int("WEAVIATE_GRPC_PORT", 50051),
            collection_name=_env("WEAVIATE_COLLECTION", "knowledge_base"),
            property_definitions=SCHEMA.vector_property_definitions(),
            api_key=os.getenv("WEAVIATE_API_KEY"),
        )
    except Exception as e:
        _err(
            "vector_db_connection_failed",
            detail=str(e),
            suggestion="Check WEAVIATE_HOST, WEAVIATE_PORT, WEAVIATE_COLLECTION env vars",
            exit_code=EXIT_CONNECTION,
        )


def _get_embedder():
    return make_embedder(_env("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))


# ── Core logic ────────────────────────────────────────────────────────────


def upsert_entry(
    graph_db: GraphDB,
    vector_db: VectorDB,
    topic: str,
    raw: dict,
    embedder: Callable[[list[str]], list[list[float]]],
) -> str:
    eid = entry_id(raw)
    entry = _build_entry(raw)

    graph_db.upload_entry(
        topic=topic,
        entry_id=eid,
        properties=SCHEMA.to_graph_properties(entry),
    )

    vector = embedder([SCHEMA.embedding_text(entry)])[0]
    vector_db.upload_entry(
        entry_id=eid,
        properties=SCHEMA.to_vector_properties(entry),
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


# ── CLI ───────────────────────────────────────────────────────────────────


@click.group(
    help="Knowledge base CLI for AI agents. All output is JSON to stdout, logs to stderr."
)
def cli():
    pass


@cli.command(name="least-topic")
def least_topic():
    """Return the topic with the fewest entries."""
    graph_db = connect_graph_db()
    try:
        counts = graph_db.get_topic_entry_counts()
        if not counts:
            _err("no_topics", detail="No topics found in graph", exit_code=EXIT_NOT_FOUND)
        topic = min(counts, key=lambda t: counts[t])
        _output({"topic": topic, "count": counts[topic]})
    finally:
        graph_db.close()


@cli.command()
@click.option("--topic", required=True, help="Topic to restrict similarity search to.")
@click.option("--entry", required=True, help='Entry JSON string, or "-" to read from stdin.')
@click.option("--top-k", default=5, show_default=True, help="Number of similar entries to return.")
def similar(topic: str, entry: str, top_k: int):
    """Get top-N most similar entries within a topic, with full content."""
    raw = _read_raw(entry)
    entry_obj = _build_entry(raw)

    graph_db = connect_graph_db()
    vector_db = connect_vector_db()
    try:
        _log(f"Fetching entry IDs for topic '{topic}'...")
        topic_ids = graph_db.get_entry_ids_for_topic(topic)
        if not topic_ids:
            _err(
                "no_entries_in_topic",
                detail=f"Topic '{topic}' has no entries",
                suggestion="Check topic name or run least-topic to list topics",
                exit_code=EXIT_NOT_FOUND,
            )
        _log("Loading embedding model...")
        embedder = _get_embedder()
        vector = embedder([SCHEMA.embedding_text(entry_obj)])[0]
        _log(f"Searching top-{top_k} similar entries in topic '{topic}'...")
        matches = vector_db.get_top_n(vector, n=top_k, filter_ids=topic_ids)

        results = []
        for uid, certainty in matches:
            matched = vector_db.get_by_id(uid)
            item: dict = {"id": uid, "certainty": certainty}
            if matched:
                item["properties"] = matched.properties
            results.append(item)

        _output({"candidate_id": entry_id(raw), "similar": results})
    finally:
        vector_db.close()
        graph_db.close()


@cli.command()
@click.option("--topic", required=True, help="Target topic name.")
@click.option("--entry", required=True, help='Entry JSON string, or "-" to read from stdin.')
def submit(topic: str, entry: str):
    """Upsert entry into both graph and vector DBs."""
    raw = _read_raw(entry)

    graph_db = connect_graph_db()
    vector_db = connect_vector_db()
    try:
        _log("Loading embedding model...")
        embedder = _get_embedder()
        _log(f"Upserting entry into topic '{topic}'...")
        eid = upsert_entry(graph_db, vector_db, topic, raw, embedder)
        log_session(topic, [eid])
        _output({"id": eid, "submitted": True})
    finally:
        vector_db.close()
        graph_db.close()


@cli.command(name="similar-batch")
@click.option("--topic", required=True, help="Topic to restrict similarity search to.")
@click.option("--entries", required=True, help='JSON array of entry objects, or "-" to read from stdin.')
@click.option("--top-k", default=5, show_default=True, help="Number of similar entries per candidate.")
def similar_batch(topic: str, entries: str, top_k: int):
    """Get top-N similar entries for each candidate in a batch. Single model load."""
    if entries == "-":
        raw_str = sys.stdin.read()
    else:
        raw_str = entries
    try:
        rows = json.loads(raw_str)
    except json.JSONDecodeError as e:
        _err("invalid_json", detail=str(e), exit_code=EXIT_USAGE)
    if not isinstance(rows, list):
        _err("invalid_input", detail="Expected a JSON array of entries", exit_code=EXIT_USAGE)

    graph_db = connect_graph_db()
    vector_db = connect_vector_db()
    try:
        _log(f"Fetching entry IDs for topic '{topic}'...")
        topic_ids = graph_db.get_entry_ids_for_topic(topic)
        if not topic_ids:
            _err(
                "no_entries_in_topic",
                detail=f"Topic '{topic}' has no entries",
                suggestion="Check topic name or run least-topic to list topics",
                exit_code=EXIT_NOT_FOUND,
            )
        _log(f"Loading embedding model (once for {len(rows)} candidates)...")
        embedder = _get_embedder()

        output = []
        for i, raw in enumerate(rows):
            entry_obj = _build_entry(raw)
            vector = embedder([SCHEMA.embedding_text(entry_obj)])[0]
            _log(f"[{i+1}/{len(rows)}] Searching top-{top_k} similar for candidate...")
            matches = vector_db.get_top_n(vector, n=top_k, filter_ids=topic_ids)
            results = []
            for uid, certainty in matches:
                matched = vector_db.get_by_id(uid)
                item: dict = {"id": uid, "certainty": certainty}
                if matched:
                    item["properties"] = matched.properties
                results.append(item)
            output.append({"candidate_id": entry_id(raw), "similar": results})

        _output(output)
    finally:
        vector_db.close()
        graph_db.close()


@cli.command(name="submit-batch")
@click.option("--topic", required=True, help="Target topic name.")
@click.option("--entries", required=True, help='JSON array of entry objects, or "-" to read from stdin.')
def submit_batch(topic: str, entries: str):
    """Upsert multiple entries into graph and vector DBs. Single model load."""
    if entries == "-":
        raw_str = sys.stdin.read()
    else:
        raw_str = entries
    try:
        rows = json.loads(raw_str)
    except json.JSONDecodeError as e:
        _err("invalid_json", detail=str(e), exit_code=EXIT_USAGE)
    if not isinstance(rows, list):
        _err("invalid_input", detail="Expected a JSON array of entries", exit_code=EXIT_USAGE)

    graph_db = connect_graph_db()
    vector_db = connect_vector_db()
    try:
        _log(f"Loading embedding model (once for {len(rows)} entries)...")
        embedder = _get_embedder()
        inserted_ids = []
        output = []
        for i, raw in enumerate(rows):
            _log(f"[{i+1}/{len(rows)}] Upserting entry into topic '{topic}'...")
            eid = upsert_entry(graph_db, vector_db, topic, raw, embedder)
            inserted_ids.append(eid)
            output.append({"id": eid, "submitted": True})
        log_session(topic, inserted_ids)
        _output(output)
    finally:
        vector_db.close()
        graph_db.close()


@cli.command(name="get-by-topic")
@click.option("--topic", required=True, help="Topic name to fetch entries from.")
@click.option("--limit", default=3, show_default=True, help="Max entries to return.")
def get_by_topic(topic: str, limit: int):
    """Fetch sample entries for a topic from vector DB."""
    graph_db = connect_graph_db()
    vector_db = connect_vector_db()
    try:
        topic_ids = graph_db.get_entry_ids_for_topic(topic)
        if not topic_ids:
            _err(
                "no_entries_in_topic",
                detail=f"Topic '{topic}' has no entries",
                suggestion="Check topic name or run least-topic to list topics",
                exit_code=EXIT_NOT_FOUND,
            )
        entries = vector_db.get_by_ids(topic_ids[:limit])
        _output([{"id": e.id, **e.properties} for e in entries])
    finally:
        vector_db.close()
        graph_db.close()


@cli.command(name="get-vector")
@click.option("--id", "entry_id", required=True, help="Entry UUID to fetch.")
def get_vector(entry_id: str):
    """Fetch entry from vector DB by ID."""
    vector_db = connect_vector_db()
    try:
        matched = vector_db.get_by_id(entry_id)
        if matched is None:
            _err("not_found", detail=f"No entry with id '{entry_id}' in vector DB", exit_code=EXIT_NOT_FOUND)
        _output({"id": matched.id, **matched.properties})
    finally:
        vector_db.close()


@cli.command(name="get-graph")
@click.option("--id", "entry_id", required=True, help="Entry UUID to fetch.")
def get_graph(entry_id: str):
    """Fetch entry from graph DB by ID."""
    graph_db = connect_graph_db()
    try:
        matched = graph_db.get_by_id(entry_id)
        if matched is None:
            _err("not_found", detail=f"No entry with id '{entry_id}' in graph DB", exit_code=EXIT_NOT_FOUND)
        _output({"id": matched.id, "topic": matched.topic, **matched.properties})
    finally:
        graph_db.close()


@cli.command()
@click.argument("timestamp")
def undo(timestamp: str):
    """Delete all entries submitted after TIMESTAMP from both DBs and log.json."""
    try:
        cutoff = datetime.fromisoformat(timestamp)
    except ValueError as e:
        _err("invalid_timestamp", detail=str(e), suggestion="Use ISO 8601 format", exit_code=EXIT_USAGE)

    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)

    if not LOG_FILE.exists():
        _err("no_log", detail="log.json not found", exit_code=EXIT_NOT_FOUND)

    log = json.loads(LOG_FILE.read_text())

    ids_to_delete: list[str] = []
    keys_to_remove: list[str] = []
    for key, topic_dict in log.items():
        try:
            ts = datetime.fromisoformat(key)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts > cutoff:
            for ids in topic_dict.values():
                ids_to_delete.extend(ids)
            keys_to_remove.append(key)

    if not ids_to_delete:
        _output({"deleted": [], "count": 0})
        return

    graph_db = connect_graph_db()
    vector_db = connect_vector_db()
    try:
        _log(f"Deleting {len(ids_to_delete)} entries from graph DB...")
        graph_db.delete_entries(ids_to_delete)

        _log(f"Deleting {len(ids_to_delete)} entries from vector DB...")
        for uid in ids_to_delete:
            vector_db.delete_by_id(uid)

        for key in keys_to_remove:
            del log[key]
        LOG_FILE.write_text(json.dumps(log, indent=2))

        _output({"deleted": ids_to_delete, "count": len(ids_to_delete)})
    finally:
        vector_db.close()
        graph_db.close()


if __name__ == "__main__":
    cli()
