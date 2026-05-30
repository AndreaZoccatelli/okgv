"""
Upload train_set_generation/training.jsonl to Weaviate.

Source format per line:
  {
    "dictionary": {"option_key": "description", ...},
    "question":   str,
    "answer":     str  (one of the dictionary keys)
  }

UUID is the same UUID5 used in Neo4j (via upload_to_neo4j.entry_id).

Usage:
  python knowledge_base/upload_existing_to_weaviate.py \
      --model sentence-transformers/all-MiniLM-L6-v2
"""

import argparse
import json
import os
from pathlib import Path

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import weaviate
from hashing import entry_id
from upload_to_weaviate import WeaviateEntry, make_embedder, upload_batch

JSONL_FILE = Path(__file__).parent.parent / "train_set_generation" / "training.jsonl"


def load_entries(path: Path) -> list[WeaviateEntry]:
    """Parse training.jsonl (dictionary-keyed format) into WeaviateEntry list."""
    entries = []
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            entries.append(
                WeaviateEntry(
                    id=entry_id(row),
                    question=row["question"],
                    options=row["dictionary"],
                    answer=row["answer"],
                )
            )
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload existing training data to Weaviate"
    )
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
        "--input", default=str(JSONL_FILE), help="Path to training.jsonl"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Replace existing entries with same id"
    )
    args = parser.parse_args()

    entries = load_entries(Path(args.input))
    print(f"Loaded {len(entries)} entries from {args.input}")

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
        upload_batch(client, entries, embedder, args.collection, overwrite=args.overwrite)
        print(f"Uploaded {len(entries)} entries to collection '{args.collection}'")
    finally:
        client.close()


if __name__ == "__main__":
    main()
