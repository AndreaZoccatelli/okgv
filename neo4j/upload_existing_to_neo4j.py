"""
Load training_groups.json into Neo4j.

Graph schema:
  (:Topic {name: str})
    -[:HAS_ENTRY]->
  (:Entry {id: str (UUID5), line: int, question: str, answer: str, options: list[str], num_options: int})

Usage:
  pip install neo4j
  python knowledge_base/load_existing_to_neo4j.py \
      --uri bolt://localhost:7687 \
      --user neo4j \
      --password <password>
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from neo4j import GraphDatabase
from upload_to_neo4j import (
    DatasetEntry,
    upload_batch,
)

from hashing import entry_id

GROUPS_FILE = (
    Path(__file__).parent.parent.parent / "train_set_generation" / "training_groups.json"
)
JSONL_FILE = Path(__file__).parent.parent.parent / "train_set_generation" / "training.jsonl"


def load_entries(path: Path, topic: str) -> dict[int, DatasetEntry]:
    """Return {index: DatasetEntry} from training.jsonl (dictionary-keyed format)."""
    entries = {}
    with open(path) as f:
        for i, line in enumerate(f):
            row = json.loads(line)
            options = list(row["dictionary"].keys())
            entries[i] = DatasetEntry(
                topic=topic,
                id=entry_id(row),
                line=i,
                question=row["question"],
                answer=row["answer"],
                options=options,
                num_options=len(options),
            )
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description="Load training groups into Neo4j")
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default=os.getenv("NEO4J_PASSWORD", "password"))
    args = parser.parse_args()

    with open(GROUPS_FILE) as f:
        groups: dict[str, list[int]] = json.load(f)

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))

    try:
        with driver.session() as session:
            session.run(
                "CREATE INDEX topic_name IF NOT EXISTS FOR (t:Topic) ON (t.name)"
            )
            session.run("CREATE INDEX entry_id IF NOT EXISTS FOR (e:Entry) ON (e.id)")

            for topic, ids in groups.items():
                all_entries = load_entries(JSONL_FILE, topic)
                batch = [all_entries[i] for i in ids]
                upload_batch(session, batch)
                print(f"  loaded  {topic}  ({len(ids)} entries)")

        print(
            f"\nDone. {len(groups)} topics, {sum(len(v) for v in groups.values())} entries."
        )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
