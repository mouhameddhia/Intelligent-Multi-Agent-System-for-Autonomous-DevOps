"""Persistence for learning records. Writes are best-effort and never raise;
reads return [] / None when MongoDB is unconfigured and raise on real errors,
matching db.py. Tests use InMemoryRecordStore."""

from typing import Protocol

from .. import db
from .models import GLOBAL_SCOPE

RECORDS_COLLECTION = "learning_records"

_indexes_ensured = False


class RecordStore(Protocol):
    def upsert(self, record: dict) -> None: ...
    def get(self, incident_id: str) -> dict | None: ...
    def list(self, scope: str = GLOBAL_SCOPE, limit: int = 5000) -> list[dict]: ...
    def set_evaluation(self, incident_id: str, evaluation: dict) -> None: ...


class MongoRecordStore:
    def _collection(self):
        global _indexes_ensured
        collection = db.get_collection(RECORDS_COLLECTION)
        if collection is not None and not _indexes_ensured:
            try:
                collection.create_index([("scope", 1), ("failure_category", 1)])
                collection.create_index([("scope", 1), ("failure_signature", 1)])
            except Exception:
                pass
            _indexes_ensured = True
        return collection

    def upsert(self, record: dict) -> None:
        try:
            collection = self._collection()
            if collection is None:
                return
            fields = {k: v for k, v in record.items() if k not in ("incident_id", "created_at")}
            update = {"$set": {"incident_id": record["incident_id"], **fields}}
            if record.get("created_at"):
                update["$setOnInsert"] = {"created_at": record["created_at"]}
            collection.update_one({"_id": record["incident_id"]}, update, upsert=True)
        except Exception:
            pass

    def set_evaluation(self, incident_id: str, evaluation: dict) -> None:
        """Attach an evaluation to an existing record; never creates one."""
        try:
            collection = self._collection()
            if collection is None:
                return
            collection.update_one({"_id": incident_id}, {"$set": {"evaluation": evaluation}})
        except Exception:
            pass

    def get(self, incident_id: str) -> dict | None:
        collection = self._collection()
        if collection is None:
            return None
        return collection.find_one({"_id": incident_id})

    def list(self, scope: str = GLOBAL_SCOPE, limit: int = 5000) -> list[dict]:
        collection = self._collection()
        if collection is None:
            return []
        return list(collection.find({"scope": scope}).sort("created_at", -1).limit(limit))


class InMemoryRecordStore:
    def __init__(self) -> None:
        self._docs: dict[str, dict] = {}

    def upsert(self, record: dict) -> None:
        existing = self._docs.get(record["incident_id"])
        merged = {**(existing or {}), **record}
        if existing and existing.get("created_at"):
            merged["created_at"] = existing["created_at"]
        self._docs[record["incident_id"]] = merged

    def set_evaluation(self, incident_id: str, evaluation: dict) -> None:
        if incident_id in self._docs:
            self._docs[incident_id]["evaluation"] = evaluation

    def get(self, incident_id: str) -> dict | None:
        return self._docs.get(incident_id)

    def list(self, scope: str = GLOBAL_SCOPE, limit: int = 5000) -> list[dict]:
        docs = [d for d in self._docs.values() if d.get("scope") == scope]
        return sorted(docs, key=lambda d: d.get("created_at", ""), reverse=True)[:limit]


_default_store: RecordStore | None = None


def get_store() -> RecordStore:
    global _default_store
    if _default_store is None:
        _default_store = MongoRecordStore()
    return _default_store


def set_store(store: RecordStore | None) -> None:
    """Override the default store (tests); None restores the Mongo store."""
    global _default_store
    _default_store = store
