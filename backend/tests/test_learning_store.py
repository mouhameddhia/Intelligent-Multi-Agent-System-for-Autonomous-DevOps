from agent_system import db
from agent_system.learning import store as store_module
from agent_system.learning.store import InMemoryRecordStore, MongoRecordStore, get_store, set_store


def test_mongo_store_is_a_noop_without_mongodb(monkeypatch):
    monkeypatch.setattr(db.settings, "mongodb_uri", "")
    store = MongoRecordStore()
    store.upsert({"incident_id": "x", "scope": "global"})  # must not raise
    assert store.get("x") is None
    assert store.list() == []


def test_mongo_store_upsert_swallows_errors(monkeypatch):
    class Boom:
        def update_one(self, *a, **k):
            raise RuntimeError("down")

    monkeypatch.setattr(db, "get_collection", lambda name: Boom())
    MongoRecordStore().upsert({"incident_id": "x"})


def test_mongo_store_upserts_by_incident_id(monkeypatch):
    calls = []

    class Fake:
        def create_index(self, *a, **k):
            pass

        def update_one(self, flt, update, upsert):
            calls.append((flt, update, upsert))

    monkeypatch.setattr(db, "get_collection", lambda name: Fake())
    monkeypatch.setattr(store_module, "_indexes_ensured", True)
    MongoRecordStore().upsert({"incident_id": "x", "scope": "global", "created_at": "t0", "updated_at": "t1"})
    flt, update, upsert = calls[0]
    assert flt == {"_id": "x"} and upsert is True
    assert update["$set"]["incident_id"] == "x"
    assert "created_at" not in update["$set"]
    assert update["$setOnInsert"] == {"created_at": "t0"}


def test_get_collection_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(db.settings, "mongodb_uri", "")
    assert db.get_collection("learning_records") is None


def test_in_memory_store_filters_by_scope():
    store = InMemoryRecordStore()
    store.upsert({"incident_id": "a", "scope": "global", "created_at": "1"})
    store.upsert({"incident_id": "b", "scope": "user:1", "created_at": "2"})
    assert [d["incident_id"] for d in store.list("global")] == ["a"]


def test_set_store_overrides_and_restores():
    fake = InMemoryRecordStore()
    set_store(fake)
    try:
        assert get_store() is fake
    finally:
        set_store(None)
    assert isinstance(get_store(), MongoRecordStore)


def test_mongo_set_evaluation_updates_without_upserting(monkeypatch):
    calls = []

    class Fake:
        def create_index(self, *a, **k):
            pass

        def update_one(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(db, "get_collection", lambda name: Fake())
    monkeypatch.setattr(store_module, "_indexes_ensured", True)
    MongoRecordStore().set_evaluation("x", {"outcome_reward": 1.0})
    args, kwargs = calls[0]
    assert args == ({"_id": "x"}, {"$set": {"evaluation": {"outcome_reward": 1.0}}})
    assert "upsert" not in kwargs


def test_mongo_set_evaluation_is_a_noop_without_mongodb(monkeypatch):
    monkeypatch.setattr(db.settings, "mongodb_uri", "")
    MongoRecordStore().set_evaluation("x", {})  # must not raise


def test_in_memory_set_evaluation_ignores_unknown_ids():
    store = InMemoryRecordStore()
    store.set_evaluation("ghost", {"outcome_reward": 1.0})
    assert store.get("ghost") is None
