"""One-off migration: backfill semantic-search embeddings for incidents
saved before the Incident Advisor chatbot existed.

Safe to re-run — it only touches incidents missing an embedding
(agent_system.db.list_incidents_missing_embedding), and it re-saves each
one through save_incident(), the same persistence path every incident
already goes through, not a parallel write.

Usage (from backend/):
    python -m scripts.backfill_embeddings
    python -m scripts.backfill_embeddings --dry-run
    python -m scripts.backfill_embeddings --force   # re-embed EVERYTHING,
                                                      # not just what's missing
                                                      # (use after changing how
                                                      # text is embedded, e.g.
                                                      # the job-name-prefix fix)
"""

import argparse
import sys

from agent_system.db import get_incident, list_incidents, list_incidents_missing_embedding, save_incident


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be backfilled, without writing anything",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-embed every incident with report text, not just ones missing an embedding",
    )
    args = parser.parse_args()

    incidents = list_incidents(limit=10_000) if args.force else list_incidents_missing_embedding()
    if not incidents:
        print("Nothing to backfill — every incident with report text already has an embedding.")
        return

    print(f"Found {len(incidents)} incident(s) missing an embedding.")

    if args.dry_run:
        for doc in incidents:
            print(f"  would backfill {doc['_id']} (job={doc.get('job_name')})")
        return

    # save_incident() swallows embedding failures on purpose (so the live
    # investigation pipeline never breaks if Ollama is down) — which means
    # "no exception" here does NOT mean the embedding actually landed. We
    # verify by re-reading the document afterward.
    succeeded = 0
    failed = []
    for doc in incidents:
        try:
            save_incident(doc)
            refreshed = get_incident(doc["_id"])
            if refreshed and "embedding" in refreshed:
                succeeded += 1
                print(f"  embedded {doc['_id']}")
            else:
                failed.append(doc["_id"])
                print(f"  FAILED {doc['_id']}: embedding did not land (Ollama unreachable or rejected the text)")
        except Exception as exc:
            failed.append(doc["_id"])
            print(f"  FAILED {doc['_id']}: {exc}")

    print(f"\nDone: {succeeded} embedded, {len(failed)} failed.")
    if failed:
        print("Failed ids:", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
