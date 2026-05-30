"""
Retrieve a Weaviate entry by UUID.

Usage:
  uv run knowledge_base/weaviate/id_retrieval.py --id <uuid>
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import weaviate
from weaviate_utils import get_by_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieve Weaviate entry by ID")
    parser.add_argument("--host", default=os.getenv("WEAVIATE_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("WEAVIATE_PORT", "8080")))
    parser.add_argument("--grpc-port", type=int, default=int(os.getenv("WEAVIATE_GRPC_PORT", "50051")))
    parser.add_argument("--secure", action="store_true")
    parser.add_argument("--api-key", default=os.getenv("WEAVIATE_API_KEY"))
    parser.add_argument("--collection", default="knowledge_base")
    parser.add_argument("--id", required=True, help="UUID of the entry to retrieve")
    args = parser.parse_args()

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
        entry = get_by_id(client, args.id, collection_name=args.collection)
        if entry is None:
            print(f"Not found: {args.id}")
        else:
            print(entry)
    finally:
        client.close()


if __name__ == "__main__":
    main()
