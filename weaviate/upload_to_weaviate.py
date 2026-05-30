"""
Upload entries to Weaviate with vector embeddings.

Collection schema:
  Entry
    - question: text
    - options:  text[]
    - answer:   text
    - vector:   float[]  (embedded from question + answer)

  Object UUID = same UUID5 used in Neo4j (from upload_to_neo4j.entry_id).

Usage:
  pip install weaviate-client sentence-transformers
  python knowledge_base/upload_to_weaviate.py \
      --input entries.jsonl \
      --model sentence-transformers/all-MiniLM-L6-v2
"""

import argparse
import json
import os
from pathlib import Path
from typing import Callable

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import weaviate
import weaviate.classes as wvc
from hashing import entry_id
from weaviate_utils import WeaviateEntry, make_embedder


def _embed_text(entry: WeaviateEntry) -> str:
    return f"{entry.question} {entry.answer}"


def _ensure_collection(client: weaviate.WeaviateClient, collection_name: str) -> None:
    if not client.collections.exists(collection_name):
        client.collections.create(
            name=collection_name,
            vector_config=wvc.config.Configure.Vectors.self_provided(),
            properties=[
                wvc.config.Property(
                    name="question", data_type=wvc.config.DataType.TEXT
                ),
                wvc.config.Property(name="options", data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="answer", data_type=wvc.config.DataType.TEXT),
            ],
        )


def _build_props(entry: WeaviateEntry) -> dict:
    return {
        "question": entry.question,
        "options": json.dumps(entry.options),
        "answer": entry.answer,
    }


def upload_entry(
    client: weaviate.WeaviateClient,
    entry: WeaviateEntry,
    embedder: Callable[[list[str]], list[list[float]]],
    collection_name: str = "knowledge_base",
    overwrite: bool = False,
) -> None:
    _ensure_collection(client, collection_name)
    vector = embedder([_embed_text(entry)])[0]
    collection = client.collections.get(collection_name)
    exists = collection.query.fetch_object_by_id(entry.id) is not None
    if exists and not overwrite:
        print(f"Skipped (already exists): {entry.id}")
        return
    if exists:
        collection.data.replace(uuid=entry.id, properties=_build_props(entry), vector=vector)
    else:
        collection.data.insert(uuid=entry.id, properties=_build_props(entry), vector=vector)


def upload_batch(
    client: weaviate.WeaviateClient,
    entries: list[WeaviateEntry],
    embedder: Callable[[list[str]], list[list[float]]],
    collection_name: str = "knowledge_base",
    overwrite: bool = False,
) -> None:
    if not entries:
        return
    _ensure_collection(client, collection_name)
    vectors = embedder([_embed_text(e) for e in entries])
    collection = client.collections.get(collection_name)
    inserted = replaced = skipped = 0
    for entry, vector in zip(entries, vectors):
        exists = collection.query.fetch_object_by_id(entry.id) is not None
        if exists and not overwrite:
            skipped += 1
        elif exists:
            collection.data.replace(uuid=entry.id, properties=_build_props(entry), vector=vector)
            replaced += 1
        else:
            collection.data.insert(uuid=entry.id, properties=_build_props(entry), vector=vector)
            inserted += 1
    print(f"  inserted={inserted}  replaced={replaced}  skipped={skipped}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload entries to Weaviate")
    parser.add_argument("--host", default=os.getenv("WEAVIATE_HOST", "localhost"))
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("WEAVIATE_PORT", "8080"))
    )
    parser.add_argument(
        "--grpc-port", type=int, default=int(os.getenv("WEAVIATE_GRPC_PORT", "50051"))
    )
    parser.add_argument("--secure", action="store_true")
    parser.add_argument("--api-key", default=os.getenv("WEAVIATE_API_KEY"))
    parser.add_argument(
        "--model", required=True, help="SentenceTransformers model name"
    )
    parser.add_argument("--collection", default="knowledge_base")
    parser.add_argument(
        "--input", required=True, help=".json for single entry, .jsonl for batch"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Replace existing entries with same id"
    )
    args = parser.parse_args()

    embedder = make_embedder(args.model)

    auth = weaviate.auth.AuthApiKey(args.api_key) if args.api_key else None
    client = weaviate.connect_to_custom(
        http_host=args.host,
        http_port=args.port,
        http_secure=args.secure,
        grpc_host=args.host,
        grpc_port=args.grpc_port,
        grpc_secure=args.secure,
        auth_credentials=auth,
    )

    try:
        input_path = Path(args.input)
        if input_path.suffix == ".json":
            with open(input_path) as f:
                data = json.load(f)
            entry = WeaviateEntry(
                id=entry_id(data),
                question=data["question"],
                options=data["dictionary"],
                answer=data["answer"],
            )
            upload_entry(client, entry, embedder, args.collection, overwrite=args.overwrite)
            print(f"Uploaded 1 entry (id={entry.id})")
        elif input_path.suffix == ".jsonl":
            entries = []
            with open(input_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    entries.append(WeaviateEntry(
                        id=entry_id(row),
                        question=row["question"],
                        options=row["dictionary"],
                        answer=row["answer"],
                    ))
            upload_batch(client, entries, embedder, args.collection, overwrite=args.overwrite)
            print(f"Uploaded {len(entries)} entries")
        else:
            raise ValueError(
                f"Unsupported file type: {input_path.suffix!r}. Use .json or .jsonl"
            )
    finally:
        client.close()


if __name__ == "__main__":
    main()
