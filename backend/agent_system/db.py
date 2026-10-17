"""Best-effort incident persistence. If MongoDB is unreachable or unconfigured,
write calls silently no-op so the investigation pipeline never breaks. Read
calls raise, since a failed read should be visible to whoever is asking."""

from .config import settings

_client = None
_indexes_ensured = False


def _get_collection():
    global _client, _indexes_ensured
    if not settings.mongodb_uri:
        return None
    try:
        if _client is None:
            from pymongo import MongoClient
            _client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=3000)
        collection = _client[settings.mongodb_db]["incidents"]
        if not _indexes_ensured:
            try:
                collection.create_index([("job_name", 1), ("created_at", -1)])
                collection.create_index([("evidence.github.files.filename", 1), ("created_at", -1)])
            except Exception:
                pass
            _indexes_ensured = True
        return collection
    except Exception:
        return None


def save_incident(doc: dict) -> None:
    """Upsert partial or full incident fields by _id."""
    try:
        collection = _get_collection()
        if collection is None:
            return
        doc_id = doc["_id"]
        collection.update_one({"_id": doc_id}, {"$set": doc}, upsert=True)
    except Exception:
        pass


def get_incident(incident_id: str) -> dict | None:
    """Fetch one incident document by id. Returns None if not found or Mongo unavailable."""
    collection = _get_collection()
    if collection is None:
        return None
    return collection.find_one({"_id": incident_id})


def list_incidents(limit: int = 20) -> list[dict]:
    """Fetch the most recent incidents, newest first. Empty list if Mongo unavailable."""
    collection = _get_collection()
    if collection is None:
        return []
    return list(collection.find().sort("created_at", -1).limit(limit))


_KNOWN_GOOD_OUTCOME = {
    "$or": [
        {"solved_step_id": {"$ne": None}},
        {"custom_solution": {"$ne": ""}},
        {"fix_result.applied": True},
    ]
}


def find_similar_incidents(job_name: str, files_touched: list[str] | None = None, limit: int = 3) -> list[dict]:
    """Tiered retrieval of past incidents with a KNOWN-GOOD outcome only — a
    denied or unresolved incident teaches nothing and would just add noise.

    Tier 1: exact job_name match.
    Tier 2 (only if Tier 1 returned fewer than `limit`): any job whose
            GitHub evidence touched one of the same files.

    Best-effort: never raises, returns [] if Mongo is unavailable or the
    query fails for any reason.
    """
    collection = _get_collection()
    if collection is None:
        return []

    try:
        tier1_filter = {**_KNOWN_GOOD_OUTCOME, "job_name": job_name}
        results = list(collection.find(tier1_filter).sort("created_at", -1).limit(limit))

        if len(results) < limit and files_touched:
            seen_ids = {doc["_id"] for doc in results}
            tier2_filter = {
                **_KNOWN_GOOD_OUTCOME,
                "evidence.github.files.filename": {"$in": files_touched},
            }
            remaining_needed = limit - len(results)
            extra_candidates = collection.find(tier2_filter).sort("created_at", -1).limit(remaining_needed + len(results) + 5)
            for doc in extra_candidates:
                if doc["_id"] not in seen_ids:
                    results.append(doc)
                    seen_ids.add(doc["_id"])
                if len(results) >= limit:
                    break

        return results
    except Exception:
        return []
