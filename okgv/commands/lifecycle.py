"""Lifecycle commands: keeping stored entries consistent with evolving specs."""

from collections import defaultdict

import click

from okgv.commands.structure import _revalidate_entry
from okgv.core import review_add
from okgv.helpers import EXIT_NOT_FOUND, err, output
from okgv.session import Session


@click.command()
@click.option("--topic", default=None, help="Topic subtree to check. Default: every entry.")
@click.option(
    "--queue/--no-queue",
    default=True,
    show_default=True,
    help="Queue violating entries for review.",
)
@click.pass_obj
def revalidate(session: Session, topic: str | None, queue: bool):
    """Report entries that violate the current effective spec for their topic.

    Tightening a node's `_meta` after entries exist leaves violators in the DB;
    nothing revalidates automatically. This rebuilds each stored entry and runs
    the schema's `validate_for_topic` hook against the entry's *current* topic,
    surfacing the ones a stricter spec now rejects. Violators are queued for
    review unless --no-queue. Entries that cannot be reconstructed from their
    stored properties are skipped (unverifiable, not reported as violations).
    """
    graph_db = session.graph_db

    if topic is not None:
        if not graph_db.topic_exists(topic):
            err("not_found", detail=f"Topic '{topic}' does not exist", exit_code=EXIT_NOT_FOUND)
        records = graph_db.get_entries_for_topic(topic)
    else:
        records = [r for r in (graph_db.get_by_id(eid) for eid in graph_db.get_all_entry_ids()) if r is not None]

    violations = []
    for rec in records:
        msg = _revalidate_entry(session, rec, rec.topic)
        if msg is not None:
            violations.append({"id": rec.id, "topic": rec.topic, "error": msg})

    queued = False
    if queue and violations:
        by_topic: dict[str, list[str]] = defaultdict(list)
        for v in violations:
            by_topic[v["topic"]].append(v["id"])
        for t, ids in by_topic.items():
            review_add(session.db_path, t, ids)
        queued = True

    output(
        {
            "checked": len(records),
            "violation_count": len(violations),
            "violations": violations,
            "queued": queued,
        }
    )


commands = (revalidate,)
