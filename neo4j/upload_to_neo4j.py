"""
Load training_groups.json into Neo4j.

Graph schema:
  (:Topic {name: str})
    -[:HAS_ENTRY]->
  (:Entry {id: str (UUID5), line: int, question: str, answer: str, options: list[str], num_options: int})

Usage:
  pip install neo4j
  python knowledge_base/load_neo4j.py \
      --uri bolt://localhost:7687 \
      --user neo4j \
      --password <password>
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from neo4j import GraphDatabase

from hashing import entry_id


@dataclass
class DatasetEntry:
    topic: str
    id: str
    line: int
    question: str
    answer: str
    options: list[str]
    num_options: int


def _entry_to_dict(entry: DatasetEntry) -> dict:
    return {
        "id": entry.id,
        "line": entry.line,
        "question": entry.question,
        "answer": entry.answer,
        "options": entry.options,
        "num_options": entry.num_options,
    }


def upload_entry(session, entry: DatasetEntry) -> None:
    """Upsert single DatasetEntry into Neo4j."""
    session.run(
        "MERGE (t:Topic {name: $topic}) ON CREATE SET t.entry_count = 0",
        topic=entry.topic,
    )
    session.run(
        """
        MATCH (t:Topic {name: $topic})
        MERGE (e:Entry {id: $id})
          ON CREATE SET
            e.question    = $question,
            e.answer      = $answer,
            e.options     = $options,
            e.num_options = $num_options
          ON MATCH SET
            e.question    = $question,
            e.answer      = $answer,
            e.options     = $options,
            e.num_options = $num_options
        WITH t, e
        MERGE (t)-[r:HAS_ENTRY]->(e)
          ON CREATE SET t.entry_count = coalesce(t.entry_count, 0) + 1
        """,
        topic=entry.topic,
        **_entry_to_dict(entry),
    )


def upload_batch(session, entries: list[DatasetEntry]) -> None:
    """Upsert list of DatasetEntry (same topic) into Neo4j."""
    if not entries:
        return
    topic = entries[0].topic
    session.run(
        "MERGE (t:Topic {name: $topic}) ON CREATE SET t.entry_count = 0",
        topic=topic,
    )
    session.run(
        """
        MATCH (t:Topic {name: $topic})
        UNWIND $rows AS row
        MERGE (e:Entry {id: row.id})
          ON CREATE SET
            e.question    = row.question,
            e.answer      = row.answer,
            e.options     = row.options,
            e.num_options = row.num_options
          ON MATCH SET
            e.question    = row.question,
            e.answer      = row.answer,
            e.options     = row.options,
            e.num_options = row.num_options
        WITH t, e
        MERGE (t)-[r:HAS_ENTRY]->(e)
          ON CREATE SET t.entry_count = coalesce(t.entry_count, 0) + 1
        """,
        topic=topic,
        rows=[_entry_to_dict(e) for e in entries],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Load training groups into Neo4j")
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default=os.getenv("NEO4J_PASSWORD", "password"))
    parser.add_argument(
        "--input",
        required=True,
        help=".json for single entry upload, .jsonl for batch upload",
    )
    parser.add_argument(
        "--topic", required=True, help="Topic name for uploaded entries"
    )
    args = parser.parse_args()

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))

    try:
        with driver.session() as session:
            session.run(
                "CREATE INDEX topic_name IF NOT EXISTS FOR (t:Topic) ON (t.name)"
            )
            session.run("CREATE INDEX entry_id IF NOT EXISTS FOR (e:Entry) ON (e.id)")

            input_path = Path(args.input)
            if input_path.suffix == ".json":
                with open(input_path) as f:
                    data = json.load(f)
                options = list(data["dictionary"].keys())
                entry = DatasetEntry(
                    topic=args.topic,
                    id=entry_id(data),
                    line=0,
                    question=data["question"],
                    answer=data["answer"],
                    options=options,
                    num_options=len(options),
                )
                upload_entry(session, entry)
                print(f"Uploaded 1 entry (topic={entry.topic}, id={entry.id})")
            elif input_path.suffix == ".jsonl":
                entries = []
                with open(input_path) as f:
                    for i, line in enumerate(f):
                        if not line.strip():
                            continue
                        row = json.loads(line)
                        options = list(row["dictionary"].keys())
                        entries.append(DatasetEntry(
                            topic=args.topic,
                            id=entry_id(row),
                            line=i,
                            question=row["question"],
                            answer=row["answer"],
                            options=options,
                            num_options=len(options),
                        ))
                upload_batch(session, entries)
                print(f"Uploaded {len(entries)} entries")
            else:
                raise ValueError(
                    f"Unsupported file type: {input_path.suffix!r}. Use .json or .jsonl"
                )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
