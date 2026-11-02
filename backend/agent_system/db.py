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


_EMBEDDABLE_FIELDS = ("diagnosis_report", "remediation_report", "unified_report", "custom_solution")

# nomic-embed-text's context window is 2048 tokens (~4 chars/token). A full
# unified_report + diagnosis_report + remediation_report easily exceeds that
# and Ollama returns a 500 rather than truncating itself — so truncate here.
_MAX_EMBED_CHARS = 4000


def _embed(text: str) -> list[float]:
    """Embed `text` via a local Ollama server (settings.ollama_url /
    ollama_embedding_model). Normalized so cosine similarity in
    search_incidents_semantic is just a dot product."""
    import httpx
    response = httpx.post(
        f"{settings.ollama_url}/api/embeddings",
        json={"model": settings.ollama_embedding_model, "prompt": text[:_MAX_EMBED_CHARS]},
        timeout=30.0,
    )
    response.raise_for_status()
    vector = response.json()["embedding"]
    norm = sum(v * v for v in vector) ** 0.5
    return [v / norm for v in vector] if norm else vector


def save_incident(doc: dict) -> None:
    """Upsert partial or full incident fields by _id.

    Also refreshes a semantic-search embedding (for the Incident Advisor
    chatbot's retrieval) whenever this upsert carries new report text —
    this is the single incident-persistence path, embedding generation is
    just a side effect of it, not a parallel save. Embedding failures (e.g.
    the model can't be loaded) never block the incident save itself.

    The job name is prefixed onto the embedded text on purpose: diagnosis
    reports share a lot of boilerplate structure ("Root cause", "Evidence",
    "Conclusion"...), which otherwise dominates similarity and crowds out
    the actual topic once there are many incidents from the same job —
    verified empirically, this flips a real incident from ranking 28th out
    of 29 (wrong job entirely) to ranking 1st for a query naming its job."""
    text = "\n\n".join(doc[field] for field in _EMBEDDABLE_FIELDS if doc.get(field))
    if text:
        if doc.get("job_name"):
            text = f"Job: {doc['job_name']}\n\n{text}"
        try:
            doc["embedding"] = _embed(text)
        except Exception:
            pass

    try:
        collection = _get_collection()
        if collection is None:
            return
        doc_id = doc["_id"]
        collection.update_one({"_id": doc_id}, {"$set": doc}, upsert=True)
    except Exception:
        pass


def list_incidents_missing_embedding() -> list[dict]:
    """Incidents that have report text to embed but no embedding yet —
    i.e. ones saved before the Incident Advisor chatbot existed. Used by
    scripts/backfill_embeddings.py. Best-effort: [] if Mongo is
    unavailable."""
    collection = _get_collection()
    if collection is None:
        return []
    try:
        return list(collection.find({
            "embedding": {"$exists": False},
            "$or": [{field: {"$exists": True, "$ne": ""}} for field in _EMBEDDABLE_FIELDS],
        }))
    except Exception:
        return []


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


def rank_incidents_by_similarity(query: str) -> list[tuple[dict, float]]:
    """Every embedded incident, ranked by similarity to `query`, paired
    with its score. Embeddings are pre-normalized (see _embed), so cosine
    similarity is just the dot product — no numpy needed at this incident
    count. Used directly by scripts/eval_rag.py for retrieval-quality
    metrics (Precision@k, MRR, score margin); search_incidents_semantic
    below is a thin wrapper over this for the chatbot's actual use.

    Best-effort: [] if Mongo is unavailable or embedding the query fails.
    """
    collection = _get_collection()
    if collection is None:
        return []

    try:
        query_vector = _embed(query)
    except Exception:
        return []

    try:
        candidates = list(collection.find({"embedding": {"$exists": True}}))
        scored = [
            (doc, sum(q * v for q, v in zip(query_vector, doc["embedding"])))
            for doc in candidates
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored
    except Exception:
        return []


def search_incidents_semantic(query: str, limit: int = 5) -> list[dict]:
    """Semantic search over incidents by meaning, for the Incident Advisor
    chatbot — a different consumer from find_similar_incidents() above:
    that one feeds the automated Diagnosis agent exact job/file matches
    restricted to known-good outcomes; this one answers free-text
    questions over ALL incidents (a denied or unresolved one is still a
    valid answer to "why did this fail")."""
    ranked = rank_incidents_by_similarity(query)
    return [doc for doc, _ in ranked[:limit]]
