"""Versioned policy registry.

Each learning run that changes anything publishes a new immutable PolicyVersion.
The registry keeps one `active` and one `previous` version (older ones become
`archived` and are never rolled back to). Rollback makes `previous` active again.

Writes are best-effort like the rest of the layer; reads return empty when
MongoDB is unconfigured, so callers fall back to "no policy".
"""

import logging
import threading
import time
from typing import Callable, Protocol

from .. import db
from .models import GLOBAL_SCOPE, PolicyVersion

logger = logging.getLogger(__name__)

POLICIES_COLLECTION = "learning_policies"
STATE_COLLECTION = "learning_state"


class NoPreviousVersion(Exception):
    """Rollback requested but there is no previous version to restore."""


class PolicyStore(Protocol):
    def load_versions(self, scope: str) -> list[dict]: ...
    def save_version(self, doc: dict) -> None: ...
    def set_status(self, scope: str, version: int, status: str) -> None: ...
    def consumed_ids(self, scope: str) -> set[str]: ...
    def add_consumed(self, scope: str, incident_ids: list[str]) -> None: ...


class MongoPolicyStore:
    def load_versions(self, scope: str) -> list[dict]:
        collection = db.get_collection(POLICIES_COLLECTION)
        if collection is None:
            return []
        return list(collection.find({"scope": scope}).sort("version", 1))

    def save_version(self, doc: dict) -> None:
        try:
            collection = db.get_collection(POLICIES_COLLECTION)
            if collection is None:
                return
            collection.update_one(
                {"_id": f"{doc['scope']}:{doc['version']}"}, {"$set": doc}, upsert=True
            )
        except Exception:
            logger.warning("policy version save failed", exc_info=True)

    def set_status(self, scope: str, version: int, status: str) -> None:
        try:
            collection = db.get_collection(POLICIES_COLLECTION)
            if collection is None:
                return
            collection.update_one({"_id": f"{scope}:{version}"}, {"$set": {"status": status}})
        except Exception:
            logger.warning("policy status update failed", exc_info=True)

    def consumed_ids(self, scope: str) -> set[str]:
        collection = db.get_collection(STATE_COLLECTION)
        if collection is None:
            return set()
        doc = collection.find_one({"_id": scope}) or {}
        return set(doc.get("consumed_incident_ids") or [])

    def add_consumed(self, scope: str, incident_ids: list[str]) -> None:
        try:
            collection = db.get_collection(STATE_COLLECTION)
            if collection is None or not incident_ids:
                return
            collection.update_one(
                {"_id": scope},
                {"$addToSet": {"consumed_incident_ids": {"$each": incident_ids}}},
                upsert=True,
            )
        except Exception:
            logger.warning("learning state update failed", exc_info=True)


class InMemoryPolicyStore:
    def __init__(self) -> None:
        self._versions: dict[tuple[str, int], dict] = {}
        self._consumed: dict[str, set[str]] = {}

    def load_versions(self, scope: str) -> list[dict]:
        return sorted(
            (dict(d) for (s, _), d in self._versions.items() if s == scope),
            key=lambda d: d["version"],
        )

    def save_version(self, doc: dict) -> None:
        self._versions[(doc["scope"], doc["version"])] = dict(doc)

    def set_status(self, scope: str, version: int, status: str) -> None:
        if (scope, version) in self._versions:
            self._versions[(scope, version)]["status"] = status

    def consumed_ids(self, scope: str) -> set[str]:
        return set(self._consumed.get(scope, set()))

    def add_consumed(self, scope: str, incident_ids: list[str]) -> None:
        self._consumed.setdefault(scope, set()).update(incident_ids)


class PolicyRegistry:
    def __init__(self, store: PolicyStore | None = None) -> None:
        self.store: PolicyStore = store or MongoPolicyStore()

    def list_versions(self, scope: str = GLOBAL_SCOPE) -> list[PolicyVersion]:
        return [PolicyVersion.model_validate(d) for d in self.store.load_versions(scope)]

    def _latest_with_status(self, status: str, scope: str) -> PolicyVersion | None:
        matches = [v for v in self.list_versions(scope) if v.status == status]
        return max(matches, key=lambda v: v.version) if matches else None

    def get_active(self, scope: str = GLOBAL_SCOPE) -> PolicyVersion | None:
        # If a crash left two active versions, the newest wins.
        return self._latest_with_status("active", scope)

    def get_previous(self, scope: str = GLOBAL_SCOPE) -> PolicyVersion | None:
        return self._latest_with_status("previous", scope)

    def publish(self, version: PolicyVersion) -> PolicyVersion:
        """Store `version` as the new active one. Assigns the next version
        number; the old active becomes previous, the old previous archived."""
        scope = version.scope
        existing = self.list_versions(scope)
        version = version.model_copy(update={
            "version": max((v.version for v in existing), default=0) + 1,
            "status": "active",
        })
        # New version first: a crash mid-way leaves two actives, never none.
        self.store.save_version(version.model_dump(mode="json"))
        for old in existing:
            if old.status == "previous":
                self.store.set_status(scope, old.version, "archived")
        for old in existing:
            if old.status == "active":
                self.store.set_status(scope, old.version, "previous")
        invalidate_policy_cache(scope)
        return version

    def rollback(self, scope: str = GLOBAL_SCOPE) -> PolicyVersion:
        """Archive the active version and reactivate the previous one."""
        active, previous = self.get_active(scope), self.get_previous(scope)
        if previous is None:
            raise NoPreviousVersion("There is no previous policy version to restore.")
        # Deactivate first: a crash mid-way leaves no active policy (safe
        # fallback to no guidance), never the version being rolled back.
        if active is not None:
            self.store.set_status(scope, active.version, "archived")
        self.store.set_status(scope, previous.version, "active")
        invalidate_policy_cache(scope)
        return previous.model_copy(update={"status": "active"})

    def consumed_ids(self, scope: str = GLOBAL_SCOPE) -> set[str]:
        return self.store.consumed_ids(scope)

    def add_consumed(self, incident_ids: list[str], scope: str = GLOBAL_SCOPE) -> None:
        self.store.add_consumed(scope, incident_ids)


_default_registry: PolicyRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> PolicyRegistry:
    global _default_registry
    with _registry_lock:
        if _default_registry is None:
            _default_registry = PolicyRegistry()
        return _default_registry


def set_registry(registry: PolicyRegistry | None) -> None:
    """Override the default registry (tests); None restores the Mongo one."""
    global _default_registry
    with _registry_lock:
        _default_registry = registry
    invalidate_policy_cache()


# The Remediation hot path reads the active policy through this cache so a slow
# or unreachable MongoDB costs at most one lookup per TTL, never one per
# investigation. Failures are cached too (as "no policy").
POLICY_CACHE_TTL = 60.0
_policy_cache: dict[str, tuple[float, PolicyVersion | None]] = {}


def get_active_policy_cached(
    scope: str = GLOBAL_SCOPE,
    ttl: float = POLICY_CACHE_TTL,
    clock: Callable[[], float] = time.monotonic,
) -> PolicyVersion | None:
    """Never raises; returns None when there is no policy or it cannot be read."""
    hit = _policy_cache.get(scope)
    if hit is not None and hit[0] > clock():
        return hit[1]
    try:
        policy = get_registry().get_active(scope)
    except Exception:
        logger.warning("active policy lookup failed", exc_info=True)
        policy = None
    _policy_cache[scope] = (clock() + ttl, policy)
    return policy


def invalidate_policy_cache(scope: str | None = None) -> None:
    if scope is None:
        _policy_cache.clear()
    else:
        _policy_cache.pop(scope, None)
