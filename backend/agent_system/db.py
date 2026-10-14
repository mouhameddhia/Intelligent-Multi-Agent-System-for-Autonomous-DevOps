"""Best-effort incident persistence. If MongoDB is unreachable or unconfigured,
write calls silently no-op so the investigation pipeline never breaks. Read
calls raise, since a failed read should be visible to whoever is asking."""

from .config import settings

_client = None


def _get_collection():
    global _client
    if not settings.mongodb_uri:
        return None
    try:
        if _client is None:
            from pymongo import MongoClient
            _client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=3000)
        return _client[settings.mongodb_db]["incidents"]
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
