"""MongoDB persistence for auth sessions, users, and user-owned incidents.

The legacy compatibility paths can still use best-effort helpers; authenticated
user and incident operations raise when persistence is unavailable.
"""

from .config import settings

_client = None
_indexes_ensured = False
_auth_indexes_ensured = False


class DatabaseUnavailableError(RuntimeError):
    """Raised when a caller requires persistence but MongoDB is unavailable."""


def _get_database():
    global _client
    if not settings.mongodb_uri:
        return None
    try:
        if _client is None:
            from pymongo import MongoClient
            _client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=3000)
        return _client[settings.mongodb_db]
    except Exception:
        return None


def _get_collection():
    global _indexes_ensured
    database = _get_database()
    if database is None:
        return None
    try:
        collection = database["incidents"]
        if not _indexes_ensured:
            try:
                collection.create_index([("job_name", 1), ("created_at", -1)])
                collection.create_index([("evidence.github.files.filename", 1), ("created_at", -1)])
                collection.create_index([("user_id", 1), ("created_at", -1)])
            except Exception:
                pass
            _indexes_ensured = True
        return collection
    except Exception:
        return None


def get_collection(name: str):
    """Return a handle to a Mongo collection used by optional subsystems."""
    database = _get_database()
    return database[name] if database is not None else None


def _require_auth_collections():
    global _auth_indexes_ensured
    database = _get_database()
    if database is None:
        raise DatabaseUnavailableError("MongoDB is not configured or unavailable.")
    users = database["users"]
    sessions = database["auth_sessions"]
    if not _auth_indexes_ensured:
        try:
            users.create_index("email", unique=True)
            sessions.create_index("token_hash", unique=True)
            sessions.create_index("expires_at", expireAfterSeconds=0)
        except Exception as exc:
            raise DatabaseUnavailableError("Could not initialize MongoDB auth indexes.") from exc
        _auth_indexes_ensured = True
    return users, sessions


def create_user(user: dict) -> None:
    users, _ = _require_auth_collections()
    try:
        users.insert_one(user)
    except Exception as exc:
        from pymongo.errors import DuplicateKeyError
        if isinstance(exc, DuplicateKeyError):
            raise ValueError("An account with this email already exists.") from exc
        raise DatabaseUnavailableError("Could not create the account in MongoDB.") from exc


def find_user_by_email(email: str) -> dict | None:
    users, _ = _require_auth_collections()
    try:
        return users.find_one({"email": email})
    except Exception as exc:
        raise DatabaseUnavailableError("Could not read users from MongoDB.") from exc


def find_user_by_id(user_id: str) -> dict | None:
    users, _ = _require_auth_collections()
    try:
        return users.find_one({"_id": user_id})
    except Exception as exc:
        raise DatabaseUnavailableError("Could not read users from MongoDB.") from exc


def create_auth_session(session: dict) -> None:
    _, sessions = _require_auth_collections()
    try:
        sessions.insert_one(session)
    except Exception as exc:
        raise DatabaseUnavailableError("Could not create an auth session in MongoDB.") from exc


def find_auth_session(token_hash: str, now) -> dict | None:
    _, sessions = _require_auth_collections()
    try:
        return sessions.find_one({"token_hash": token_hash, "expires_at": {"$gt": now}})
    except Exception as exc:
        raise DatabaseUnavailableError("Could not read auth sessions from MongoDB.") from exc


def delete_auth_session(token_hash: str) -> None:
    _, sessions = _require_auth_collections()
    try:
        sessions.delete_one({"token_hash": token_hash})
    except Exception as exc:
        raise DatabaseUnavailableError("Could not end the auth session in MongoDB.") from exc


def save_user_integration_settings(user_id: str, encrypted_settings: bytes) -> None:
    """Persist encrypted integration settings for one authenticated user."""
    database = _get_database()
    if database is None:
        raise DatabaseUnavailableError("MongoDB is not configured or unavailable.")
    try:
        from datetime import datetime, timezone
        database["user_integrations"].replace_one(
            {"_id": user_id},
            {
                "_id": user_id,
                "encrypted_settings": encrypted_settings,
                "updated_at": datetime.now(timezone.utc),
            },
            upsert=True,
        )
    except Exception as exc:
        raise DatabaseUnavailableError("Could not save integration settings in MongoDB.") from exc


def load_user_integration_settings(user_id: str) -> bytes | None:
    """Load encrypted integration settings for one authenticated user."""
    database = _get_database()
    if database is None:
        raise DatabaseUnavailableError("MongoDB is not configured or unavailable.")
    try:
        document = database["user_integrations"].find_one(
            {"_id": user_id}, {"encrypted_settings": 1}
        )
        return document.get("encrypted_settings") if document else None
    except Exception as exc:
        raise DatabaseUnavailableError("Could not load integration settings from MongoDB.") from exc


def claim_legacy_incidents(user_id: str) -> None:
    """Attach pre-auth incidents to the first account created in this database."""
    users, _ = _require_auth_collections()
    try:
        if users.count_documents({}) == 1:
            collection = _get_collection()
            if collection is None:
                raise DatabaseUnavailableError("MongoDB is not configured or unavailable.")
            collection.update_many(
                {"user_id": {"$exists": False}}, {"$set": {"user_id": user_id}}
            )
    except DatabaseUnavailableError:
        raise
    except Exception as exc:
        raise DatabaseUnavailableError("Could not migrate existing incidents to the first account.") from exc


def save_incident(doc: dict, *, raise_on_error: bool = False) -> None:
    """Upsert incident fields and best-effort refresh its semantic embedding."""
    embeddable_fields = ("diagnosis_report", "remediation_report", "unified_report", "custom_solution")
    text = "\n\n".join(doc[field] for field in embeddable_fields if doc.get(field))
    if text:
        if doc.get("job_name"):
            text = f"Job: {doc['job_name']}\n\n{text}"
        try:
            doc["embedding"] = _embed(text)
        except Exception:
            # Embeddings are an optional read-side capability and must never
            # prevent a real investigation from being persisted.
            pass
    try:
        collection = _get_collection()
        if collection is None:
            if raise_on_error:
                raise DatabaseUnavailableError("MongoDB is not configured or unavailable.")
            return
        doc_id = doc["_id"]
        collection.update_one({"_id": doc_id}, {"$set": doc}, upsert=True)
    except Exception as exc:
        if raise_on_error:
            raise
        pass


_MAX_EMBED_CHARS = 4000


def _embed(text: str) -> list[float]:
    """Create a normalized embedding through the configured Ollama server."""
    import httpx

    response = httpx.post(
        f"{settings.ollama_url.rstrip('/')}/api/embeddings",
        json={"model": settings.ollama_embedding_model, "prompt": text[:_MAX_EMBED_CHARS]},
        timeout=30.0,
    )
    response.raise_for_status()
    vector = response.json()["embedding"]
    norm = sum(value * value for value in vector) ** 0.5
    return [value / norm for value in vector] if norm else vector


def list_incidents_missing_embedding() -> list[dict]:
    """Return incidents with report text that predate semantic embeddings."""
    collection = _get_collection()
    if collection is None:
        return []
    fields = ("diagnosis_report", "remediation_report", "unified_report", "custom_solution")
    try:
        return list(collection.find({
            "embedding": {"$exists": False},
            "$or": [{field: {"$exists": True, "$ne": ""}} for field in fields],
        }))
    except Exception:
        return []


def update_incident(incident_id: str, user_id: str, updates: dict) -> bool:
    """Update an incident only when it belongs to the authenticated user."""
    collection = _get_collection()
    if collection is None:
        raise DatabaseUnavailableError("MongoDB is not configured or unavailable.")
    try:
        result = collection.update_one(
            {"_id": incident_id, "user_id": user_id}, {"$set": updates}
        )
        return result.matched_count == 1
    except Exception as exc:
        raise DatabaseUnavailableError("Could not update the incident in MongoDB.") from exc


def get_incident(incident_id: str, user_id: str | None = None) -> dict | None:
    """Fetch one incident document by id. Returns None if not found or Mongo unavailable."""
    collection = _get_collection()
    if collection is None:
        if user_id is not None:
            raise DatabaseUnavailableError("MongoDB is not configured or unavailable.")
        return None
    query = {"_id": incident_id}
    if user_id is not None:
        query["user_id"] = user_id
    try:
        return collection.find_one(query)
    except Exception as exc:
        if user_id is not None:
            raise DatabaseUnavailableError("Could not read this user's incident from MongoDB.") from exc
        raise


def list_incidents(
    limit: int = 20,
    user_id: str | None = None,
    projection: dict | None = None,
) -> list[dict]:
    """Fetch the most recent incidents, newest first. Empty list if Mongo unavailable."""
    collection = _get_collection()
    if collection is None:
        if user_id is not None:
            raise DatabaseUnavailableError("MongoDB is not configured or unavailable.")
        return []
    query = {"user_id": user_id} if user_id is not None else {}
    try:
        cursor = collection.find(query, projection) if projection is not None else collection.find(query)
        return list(cursor.sort("created_at", -1).limit(limit))
    except Exception as exc:
        if user_id is not None:
            raise DatabaseUnavailableError("Could not read this user's incidents from MongoDB.") from exc
        raise


def count_incidents(user_id: str | None = None) -> int:
    """Count all stored incidents, independent of a page/list limit."""
    collection = _get_collection()
    if collection is None:
        if user_id is not None:
            raise DatabaseUnavailableError("MongoDB is not configured or unavailable.")
        return 0
    query = {"user_id": user_id} if user_id is not None else {}
    try:
        return collection.count_documents(query)
    except Exception as exc:
        if user_id is not None:
            raise DatabaseUnavailableError("Could not count this user's incidents in MongoDB.") from exc
        raise


def rank_incidents_by_similarity(
    query: str, user_id: str | None = None,
) -> list[tuple[dict, float]]:
    """Rank embedded incidents by cosine similarity; optionally restrict to one account."""
    collection = _get_collection()
    if collection is None:
        return []
    try:
        query_vector = _embed(query)
        criteria = {"embedding": {"$exists": True}}
        if user_id is not None:
            criteria["user_id"] = user_id
        candidates = list(collection.find(criteria))
        scored = [
            (doc, sum(left * right for left, right in zip(query_vector, doc["embedding"])))
            for doc in candidates
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored
    except Exception:
        return []


def search_incidents_semantic(
    query: str, limit: int = 5, user_id: str | None = None,
) -> list[dict]:
    """Semantic search over reports, optionally restricted to one account."""
    ranked = rank_incidents_by_similarity(query, user_id=user_id)
    return [doc for doc, _score in ranked[:limit]]


_KNOWN_GOOD_OUTCOME = {
    "$or": [
        {"solved_step_id": {"$ne": None}},
        {"custom_solution": {"$ne": ""}},
        {"fix_result.applied": True},
    ]
}


def find_similar_incidents(job_name: str, files_touched: list[str] | None = None, limit: int = 3, user_id: str | None = None) -> list[dict]:
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
        owner_filter = {"user_id": user_id} if user_id is not None else {}
        tier1_filter = {**_KNOWN_GOOD_OUTCOME, **owner_filter, "job_name": job_name}
        results = list(collection.find(tier1_filter).sort("created_at", -1).limit(limit))

        if len(results) < limit and files_touched:
            seen_ids = {doc["_id"] for doc in results}
            tier2_filter = {
                **_KNOWN_GOOD_OUTCOME,
                **owner_filter,
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
