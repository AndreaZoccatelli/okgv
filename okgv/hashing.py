import json
import uuid


def entry_id(row: dict) -> str:
    """Deterministic UUID5 from question + answer + sorted options."""
    canonical = json.dumps(
        {
            "question": row["question"],
            "answer": row["answer"],
            "options": sorted(row["dictionary"].keys()),
        },
        sort_keys=True,
    )
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, canonical))
