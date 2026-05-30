"""
CLI for AI agents to interact with the self-organized knowledge base.

Commands:
  least-topic    — Topic with fewest entries.
  similar        — Top-N most similar entries to a candidate within a topic (with content).
  submit         — Upsert entry into both Neo4j and Weaviate.
  similar-batch  — Batch version of similar: single model load for N candidates.
  submit-batch   — Batch version of submit: single model load for N entries.

Agent workflow (single):
  1. Agent generates a candidate entry.
  2. Agent calls `similar` to retrieve most similar existing entries.
  3. Agent reads content, decides if candidate needs editing.
  4. Agent calls `submit` to insert.

Agent workflow (batch):
  1. Agent generates N candidate entries.
  2. Agent calls `similar-batch` to get similarity results for all (1 model load).
  3. Agent reviews JSON output, selects novel candidates.
  4. Agent calls `submit-batch` with approved entries (1 model load).

Exit codes:
  0 = success
  1 = general failure
  2 = usage error (bad input, missing fields)
  3 = resource not found (topic, collection)
  4 = connection error (Neo4j or Weaviate unreachable)

Examples:
  python candidate.py least-topic
  python candidate.py similar --topic algebra --entry '{"question":"...","answer":"...","dictionary":{"A":"..."}}'
  python candidate.py similar --topic algebra --entry -        # read from stdin
  python candidate.py submit --topic algebra --entry '{"question":"...","answer":"...","dictionary":{"A":"..."}}'
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NoReturn

import click

_KB_DIR = Path(__file__).parent
sys.path.insert(0, str(_KB_DIR))
sys.path.insert(0, str(_KB_DIR / "neo4j"))
sys.path.insert(0, str(_KB_DIR / "weaviate"))

from neo4j_utils import Neo4jEntry, get_by_id as neo4j_get_by_id
from upload_to_neo4j import DatasetEntry
from upload_to_neo4j import upload_entry as neo4j_upload_entry
from upload_to_weaviate import upload_entry as weaviate_upload_entry
from weaviate_utils import (
    WeaviateEntry,
    get_by_id,
    get_by_ids,
    get_top_n_by_similarity,
    make_embedder,
)

from hashing import entry_id

LOG_FILE = _KB_DIR / "log.json"

# Exit codes
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_CONNECTION = 4


# ── Helpers ───────────────────────────────────────────────────────────────


def _env(key: str, default: str) -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _output(data: dict | list) -> None:
    """Write JSON to stdout."""
    json.dump(data, sys.stdout, indent=2)
    sys.stdout.write("\n")


def _err(
    error: str, detail: str = "", suggestion: str = "", exit_code: int = EXIT_FAILURE
) -> NoReturn:
    """Write structured error to stderr and exit."""
    msg: dict = {"error": error}
    if detail:
        msg["detail"] = detail
    if suggestion:
        msg["suggestion"] = suggestion
    json.dump(msg, sys.stderr, indent=2)
    sys.stderr.write("\n")
    sys.exit(exit_code)


def _parse_entry(raw: str) -> dict:
    """Parse and validate entry JSON."""
    try:
        row = json.loads(raw)
    except json.JSONDecodeError as e:
        _err("invalid_json", detail=str(e), exit_code=EXIT_USAGE)
    for key in ("question", "answer", "dictionary"):
        if key not in row:
            _err(
                "missing_field",
                detail=f"Entry JSON missing required key: {key}",
                suggestion=f'Ensure entry has "{key}" field',
                exit_code=EXIT_USAGE,
            )
    return row


def _read_entry(entry_str: str) -> dict:
    """Read entry from argument or stdin (if '-')."""
    if entry_str == "-":
        raw = sys.stdin.read()
    else:
        raw = entry_str
    return _parse_entry(raw)


def _log(msg: str) -> None:
    """Progress/info to stderr."""
    click.echo(msg, err=True)


# ── DB connections ────────────────────────────────────────────────────────


def connect_neo4j():
    from neo4j import GraphDatabase

    try:
        driver = GraphDatabase.driver(
            _env("NEO4J_URI", "bolt://localhost:7687"),
            auth=(_env("NEO4J_USER", "neo4j"), _env("NEO4J_PASSWORD", "password")),
        )
        driver.verify_connectivity()
        return driver
    except Exception as e:
        _err(
            "neo4j_connection_failed",
            detail=str(e),
            suggestion="Check NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD env vars",
            exit_code=EXIT_CONNECTION,
        )


def connect_weaviate():
    import weaviate

    try:
        api_key = os.getenv("WEAVIATE_API_KEY")
        auth = weaviate.auth.AuthApiKey(api_key) if api_key else None
        host = _env("WEAVIATE_HOST", "localhost")
        return weaviate.connect_to_custom(
            http_host=host,
            http_port=_env_int("WEAVIATE_PORT", 8080),
            http_secure=False,
            grpc_host=host,
            grpc_port=_env_int("WEAVIATE_GRPC_PORT", 50051),
            grpc_secure=False,
            auth_credentials=auth,
        )
    except Exception as e:
        _err(
            "weaviate_connection_failed",
            detail=str(e),
            suggestion="Check WEAVIATE_HOST, WEAVIATE_PORT env vars",
            exit_code=EXIT_CONNECTION,
        )


def _get_embedder():
    return make_embedder(_env("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))


# ── Core logic ────────────────────────────────────────────────────────────


def get_topic_entry_counts(neo4j_session) -> dict[str, int]:
    result = neo4j_session.run(
        """
        MATCH (t:Topic)
        RETURN t.name AS topic, coalesce(t.entry_count, 0) AS count
        """
    )
    return {r["topic"]: r["count"] for r in result}


def get_entry_ids_for_topic(neo4j_session, topic: str) -> list[str]:
    result = neo4j_session.run(
        """
        MATCH (t:Topic {name: $topic})-[:HAS_ENTRY]->(e:Entry)
        RETURN e.id AS id
        """,
        topic=topic,
    )
    return [r["id"] for r in result]


def upsert_entry(
    neo4j_session,
    weaviate_client,
    topic: str,
    row: dict,
    embedder: Callable[[list[str]], list[list[float]]],
    collection_name: str = "knowledge_base",
) -> str:
    eid = entry_id(row)
    options = list(row["dictionary"].keys())

    neo4j_entry = DatasetEntry(
        topic=topic,
        id=eid,
        line=0,
        question=row["question"],
        answer=row["answer"],
        options=options,
        num_options=len(options),
    )
    neo4j_upload_entry(neo4j_session, neo4j_entry)

    weaviate_entry = WeaviateEntry(
        id=eid,
        question=row["question"],
        options=row["dictionary"],
        answer=row["answer"],
    )
    weaviate_upload_entry(weaviate_client, weaviate_entry, embedder, collection_name)

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
    """Return the topic with the fewest entries.

    \b
    Output: {"topic": "name", "count": 3}
    Example: python candidate.py least-topic
    """
    driver = connect_neo4j()
    try:
        with driver.session() as session:
            counts = get_topic_entry_counts(session)
        if not counts:
            _err("no_topics", detail="No topics found in graph", exit_code=EXIT_NOT_FOUND)
        topic = min(counts, key=lambda t: counts[t])
        _output({"topic": topic, "count": counts[topic]})
    finally:
        driver.close()


@cli.command()
@click.option("--topic", required=True, help="Topic to restrict similarity search to.")
@click.option(
    "--entry", required=True, help='Entry JSON string, or "-" to read from stdin.'
)
@click.option(
    "--top-k", default=5, show_default=True, help="Number of similar entries to return."
)
def similar(topic: str, entry: str, top_k: int):
    """Get top-N most similar entries within a topic, with full content.

    \b
    The agent uses this to decide if a candidate is novel enough.
    Restricts search to entries belonging to the given topic.
    Returns candidate_id and list of similar entries with question, answer, options, certainty.

    \b
    Output: {"candidate_id": "...", "similar": [{"id": "...", "certainty": 0.89, "question": "...", ...}]}
    Examples:
      python candidate.py similar --topic algebra --entry '{"question":"...","answer":"...","dictionary":{"A":"..."}}'
      echo '{"question":"...","answer":"...","dictionary":{"A":"..."}}' | python candidate.py similar --topic algebra --entry -
    """
    row = _read_entry(entry)
    collection = _env("COLLECTION_NAME", "knowledge_base")

    driver = connect_neo4j()
    client = connect_weaviate()
    try:
        _log(f"Fetching entry IDs for topic '{topic}'...")
        with driver.session() as session:
            topic_ids = get_entry_ids_for_topic(session, topic)
        if not topic_ids:
            _err(
                "no_entries_in_topic",
                detail=f"Topic '{topic}' has no entries",
                suggestion="Check topic name or run least-topic to list topics",
                exit_code=EXIT_NOT_FOUND,
            )
        _log("Loading embedding model...")
        embedder = _get_embedder()
        query_text = f"{row['question']} {row['answer']}"
        _log(f"Searching top-{top_k} similar entries in topic '{topic}'...")
        matches = get_top_n_by_similarity(
            client, query_text, embedder, n=top_k, collection_name=collection,
            filter_ids=topic_ids,
        )

        results = []
        for uid, certainty in matches:
            matched = get_by_id(client, uid, collection_name=collection)
            item: dict = {"id": uid, "certainty": certainty}
            if matched:
                item["question"] = matched.question
                item["answer"] = matched.answer
                item["options"] = matched.options
            results.append(item)

        _output({"candidate_id": entry_id(row), "similar": results})
    finally:
        client.close()
        driver.close()


@cli.command()
@click.option("--topic", required=True, help="Target topic name.")
@click.option(
    "--entry", required=True, help='Entry JSON string, or "-" to read from stdin.'
)
def submit(topic: str, entry: str):
    """Upsert entry into both Neo4j and Weaviate.

    \b
    Idempotent — safe to retry. Same entry ID produces same result.
    No similarity check — agent already decided this entry is novel.

    \b
    Output: {"id": "uuid5", "submitted": true}
    Examples:
      python candidate.py submit --topic algebra --entry '{"question":"...","answer":"...","dictionary":{"A":"..."}}'
      echo '...' | python candidate.py submit --topic algebra --entry -
    """
    row = _read_entry(entry)
    collection = _env("COLLECTION_NAME", "knowledge_base")

    driver = connect_neo4j()
    client = connect_weaviate()
    try:
        _log("Loading embedding model...")
        embedder = _get_embedder()
        _log(f"Upserting entry into topic '{topic}'...")
        with driver.session() as session:
            eid = upsert_entry(session, client, topic, row, embedder, collection)
        log_session(topic, [eid])
        _output({"id": eid, "submitted": True})
    finally:
        client.close()
        driver.close()


@cli.command(name="similar-batch")
@click.option("--topic", required=True, help="Topic to restrict similarity search to.")
@click.option(
    "--entries", required=True, help='JSON array of entry objects, or "-" to read from stdin.'
)
@click.option(
    "--top-k", default=5, show_default=True, help="Number of similar entries to return per candidate."
)
def similar_batch(topic: str, entries: str, top_k: int):
    """Get top-N similar entries for each candidate in a batch. Single model load.

    \b
    Strict approach: topic IDs snapshot taken once before any submissions.
    Agent reviews output JSON and decides which candidates to submit.

    \b
    Output: [{"candidate_id": "...", "similar": [{"id": "...", "certainty": 0.89, ...}]}, ...]
    Examples:
      python candidate.py similar-batch --topic algebra --entries '[{"question":"...","answer":"...","dictionary":{"A":"..."}}, ...]'
      echo '[...]' | python candidate.py similar-batch --topic algebra --entries -
    """
    if entries == "-":
        raw = sys.stdin.read()
    else:
        raw = entries
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError as e:
        _err("invalid_json", detail=str(e), exit_code=EXIT_USAGE)
    if not isinstance(rows, list):
        _err("invalid_input", detail="Expected a JSON array of entries", exit_code=EXIT_USAGE)
    parsed = [_parse_entry(json.dumps(r)) for r in rows]

    collection = _env("COLLECTION_NAME", "knowledge_base")
    driver = connect_neo4j()
    client = connect_weaviate()
    try:
        _log(f"Fetching entry IDs for topic '{topic}'...")
        with driver.session() as session:
            topic_ids = get_entry_ids_for_topic(session, topic)
        if not topic_ids:
            _err(
                "no_entries_in_topic",
                detail=f"Topic '{topic}' has no entries",
                suggestion="Check topic name or run least-topic to list topics",
                exit_code=EXIT_NOT_FOUND,
            )
        _log(f"Loading embedding model (once for {len(parsed)} candidates)...")
        embedder = _get_embedder()

        output = []
        for i, row in enumerate(parsed):
            query_text = f"{row['question']} {row['answer']}"
            _log(f"[{i+1}/{len(parsed)}] Searching top-{top_k} similar for candidate...")
            matches = get_top_n_by_similarity(
                client, query_text, embedder, n=top_k,
                collection_name=collection, filter_ids=topic_ids,
            )
            results = []
            for uid, certainty in matches:
                matched = get_by_id(client, uid, collection_name=collection)
                item: dict = {"id": uid, "certainty": certainty}
                if matched:
                    item["question"] = matched.question
                    item["answer"] = matched.answer
                    item["options"] = matched.options
                results.append(item)
            output.append({"candidate_id": entry_id(row), "similar": results})

        _output(output)
    finally:
        client.close()
        driver.close()


@cli.command(name="submit-batch")
@click.option("--topic", required=True, help="Target topic name.")
@click.option(
    "--entries", required=True, help='JSON array of entry objects, or "-" to read from stdin.'
)
def submit_batch(topic: str, entries: str):
    """Upsert multiple entries into Neo4j and Weaviate. Single model load.

    \b
    Idempotent — safe to retry. Each entry ID is deterministic.
    No similarity check — agent already reviewed similar-batch output.

    \b
    Output: [{"id": "uuid5", "submitted": true}, ...]
    Examples:
      python candidate.py submit-batch --topic algebra --entries '[{"question":"...","answer":"...","dictionary":{"A":"..."}}, ...]'
      echo '[...]' | python candidate.py submit-batch --topic algebra --entries -
    """
    if entries == "-":
        raw = sys.stdin.read()
    else:
        raw = entries
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError as e:
        _err("invalid_json", detail=str(e), exit_code=EXIT_USAGE)
    if not isinstance(rows, list):
        _err("invalid_input", detail="Expected a JSON array of entries", exit_code=EXIT_USAGE)
    parsed = [_parse_entry(json.dumps(r)) for r in rows]

    collection = _env("COLLECTION_NAME", "knowledge_base")
    driver = connect_neo4j()
    client = connect_weaviate()
    try:
        _log(f"Loading embedding model (once for {len(parsed)} entries)...")
        embedder = _get_embedder()
        inserted_ids = []
        output = []
        with driver.session() as session:
            for i, row in enumerate(parsed):
                _log(f"[{i+1}/{len(parsed)}] Upserting entry into topic '{topic}'...")
                eid = upsert_entry(session, client, topic, row, embedder, collection)
                inserted_ids.append(eid)
                output.append({"id": eid, "submitted": True})
        log_session(topic, inserted_ids)
        _output(output)
    finally:
        client.close()
        driver.close()


@cli.command(name="get-by-topic")
@click.option("--topic", required=True, help="Topic name to fetch entries from.")
@click.option("--limit", default=3, show_default=True, help="Max entries to return.")
def get_by_topic(topic: str, limit: int):
    """Fetch sample entries for a topic from Weaviate. Useful to understand entry structure.

    \b
    Output: [{"id": "...", "question": "...", "answer": "...", "options": {...}}, ...]
    Example: python candidate.py get-by-topic --topic algebra --limit 3
    """
    collection = _env("COLLECTION_NAME", "knowledge_base")
    driver = connect_neo4j()
    client = connect_weaviate()
    try:
        with driver.session() as session:
            topic_ids = get_entry_ids_for_topic(session, topic)
        if not topic_ids:
            _err(
                "no_entries_in_topic",
                detail=f"Topic '{topic}' has no entries",
                suggestion="Check topic name or run least-topic to list topics",
                exit_code=EXIT_NOT_FOUND,
            )
        entries = get_by_ids(client, topic_ids[:limit], collection_name=collection)
        _output([
            {"id": e.id, "question": e.question, "answer": e.answer, "options": e.options}
            for e in entries
        ])
    finally:
        client.close()
        driver.close()


@cli.command(name="get-weaviate")
@click.option("--id", "entry_id", required=True, help="Entry UUID to fetch.")
def get_weaviate(entry_id: str):
    """Fetch entry from Weaviate by ID.

    \b
    Output: {"id": "...", "question": "...", "answer": "...", "options": {...}}
    Example: python candidate.py get-weaviate --id <uuid>
    """
    collection = _env("COLLECTION_NAME", "knowledge_base")
    client = connect_weaviate()
    try:
        matched = get_by_id(client, entry_id, collection_name=collection)
        if matched is None:
            _err("not_found", detail=f"No entry with id '{entry_id}' in Weaviate", exit_code=EXIT_NOT_FOUND)
        _output({"id": matched.id, "question": matched.question, "answer": matched.answer, "options": matched.options})
    finally:
        client.close()


@cli.command(name="get-neo4j")
@click.option("--id", "entry_id", required=True, help="Entry UUID to fetch.")
def get_neo4j(entry_id: str):
    """Fetch entry from Neo4j by ID.

    \b
    Output: {"id": "...", "topic": "...", "question": "...", "answer": "...", "options": [...]}
    Example: python candidate.py get-neo4j --id <uuid>
    """
    driver = connect_neo4j()
    try:
        with driver.session() as session:
            matched = neo4j_get_by_id(session, entry_id)
        if matched is None:
            _err("not_found", detail=f"No entry with id '{entry_id}' in Neo4j", exit_code=EXIT_NOT_FOUND)
        _output({
            "id": matched.id,
            "topic": matched.topic,
            "question": matched.question,
            "answer": matched.answer,
            "options": matched.options,
        })
    finally:
        driver.close()


@cli.command()
@click.argument("timestamp")
def undo(timestamp: str):
    """Delete all entries submitted after TIMESTAMP from Neo4j, Weaviate, and log.json.

    \b
    TIMESTAMP: ISO 8601 string (e.g. "2026-05-30T11:06:00+00:00").
    Entries whose log session key is strictly after TIMESTAMP are deleted.

    \b
    Output: {"deleted": ["uuid1", ...], "count": N}
    Example: python candidate.py undo "2026-05-30T11:06:00+00:00"
    """
    from datetime import datetime, timezone

    try:
        cutoff = datetime.fromisoformat(timestamp)
    except ValueError as e:
        _err("invalid_timestamp", detail=str(e), suggestion="Use ISO 8601 format, e.g. 2026-05-30T11:06:00+00:00", exit_code=EXIT_USAGE)

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

    collection = _env("COLLECTION_NAME", "knowledge_base")
    driver = connect_neo4j()
    client = connect_weaviate()
    try:
        _log(f"Deleting {len(ids_to_delete)} entries from Neo4j...")
        with driver.session() as session:
            session.run(
                """
                UNWIND $ids AS id
                MATCH (t:Topic)-[:HAS_ENTRY]->(e:Entry {id: id})
                SET t.entry_count = coalesce(t.entry_count, 0) - 1
                DETACH DELETE e
                """,
                ids=ids_to_delete,
            )

        _log(f"Deleting {len(ids_to_delete)} entries from Weaviate...")
        weaviate_collection = client.collections.get(collection)
        for uid in ids_to_delete:
            try:
                weaviate_collection.data.delete_by_id(uid)
            except Exception:
                pass  # not found is fine — idempotent

        for key in keys_to_remove:
            del log[key]
        LOG_FILE.write_text(json.dumps(log, indent=2))

        _output({"deleted": ids_to_delete, "count": len(ids_to_delete)})
    finally:
        client.close()
        driver.close()


if __name__ == "__main__":
    cli()
