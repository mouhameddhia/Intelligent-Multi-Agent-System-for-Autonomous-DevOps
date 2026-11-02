import pytest

from agent_system.learning.models import PolicyVersion, PriorCount, Rule
from agent_system.learning.registry import (
    InMemoryPolicyStore,
    MongoPolicyStore,
    NoPreviousVersion,
    PolicyRegistry,
    get_registry,
    set_registry,
)
from agent_system.learning import registry as registry_module


def version(text="Rerun after fixing", **kwargs):
    rule = Rule(id="r1", text=text, failure_category="dependency", status="active", support_count=2, success_count=2)
    return PolicyVersion(version=0, rules=[rule], **kwargs)


def test_empty_registry_has_no_policy():
    reg = PolicyRegistry(InMemoryPolicyStore())
    assert reg.get_active() is None and reg.get_previous() is None
    assert reg.list_versions() == []


def test_publish_assigns_increasing_versions_and_rotates_statuses():
    reg = PolicyRegistry(InMemoryPolicyStore())
    v1 = reg.publish(version("first rule text here"))
    v2 = reg.publish(version("second rule text here"))
    v3 = reg.publish(version("third rule text here"))
    assert (v1.version, v2.version, v3.version) == (1, 2, 3)
    statuses = {v.version: v.status for v in reg.list_versions()}
    assert statuses == {1: "archived", 2: "previous", 3: "active"}
    assert reg.get_active().version == 3
    assert reg.get_previous().version == 2


def test_rollback_restores_the_previous_version():
    reg = PolicyRegistry(InMemoryPolicyStore())
    reg.publish(version("good rule text here"))
    reg.publish(version("bad rule text here"))
    restored = reg.rollback()
    assert restored.version == 1 and restored.status == "active"
    assert reg.get_active().rules[0].text == "good rule text here"
    assert reg.get_previous() is None
    assert {v.version: v.status for v in reg.list_versions()} == {1: "active", 2: "archived"}


def test_rollback_without_previous_raises():
    reg = PolicyRegistry(InMemoryPolicyStore())
    with pytest.raises(NoPreviousVersion):
        reg.rollback()
    reg.publish(version())
    with pytest.raises(NoPreviousVersion):
        reg.rollback()


def test_second_rollback_has_nothing_to_restore_and_new_versions_keep_counting():
    reg = PolicyRegistry(InMemoryPolicyStore())
    reg.publish(version("one rule text here"))
    reg.publish(version("two rule text here"))
    reg.rollback()
    with pytest.raises(NoPreviousVersion):
        reg.rollback()
    nxt = reg.publish(version("three rule text here"))
    assert nxt.version == 3
    assert reg.get_previous().version == 1


def test_newest_active_wins_if_a_crash_left_two():
    store = InMemoryPolicyStore()
    reg = PolicyRegistry(store)
    reg.publish(version("one rule text here"))
    reg.publish(version("two rule text here"))
    store.set_status("global", 1, "active")  # simulate a half-finished publish
    assert reg.get_active().version == 2


def test_versions_keep_priors_and_scope():
    reg = PolicyRegistry(InMemoryPolicyStore())
    published = reg.publish(PolicyVersion(version=0, priors={"dependency|code_patch": PriorCount(success=3, failure=1)}))
    loaded = reg.get_active()
    assert loaded.scope == "global"
    assert loaded.priors["dependency|code_patch"].success == 3
    assert published.rules == []


def test_consumed_ids_accumulate_per_scope():
    reg = PolicyRegistry(InMemoryPolicyStore())
    reg.add_consumed(["a", "b"])
    reg.add_consumed(["b", "c"])
    assert reg.consumed_ids() == {"a", "b", "c"}
    assert reg.consumed_ids("user:1") == set()


def test_default_registry_can_be_overridden():
    fake = PolicyRegistry(InMemoryPolicyStore())
    set_registry(fake)
    try:
        assert get_registry() is fake
    finally:
        set_registry(None)
    assert isinstance(get_registry().store, MongoPolicyStore)
    set_registry(None)


def test_mongo_policy_store_is_a_noop_without_mongodb():
    store = MongoPolicyStore()
    store.save_version({"scope": "global", "version": 1})
    store.set_status("global", 1, "archived")
    store.add_consumed("global", ["a"])
    assert store.load_versions("global") == []
    assert store.consumed_ids("global") == set()


def test_mongo_policy_store_writes_use_stable_ids(monkeypatch):
    calls = []

    class Fake:
        def update_one(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(registry_module.db, "get_collection", lambda name: Fake())
    store = MongoPolicyStore()
    store.save_version({"scope": "global", "version": 4, "status": "active"})
    store.set_status("global", 4, "previous")
    store.add_consumed("global", ["a"])
    assert calls[0][0][0] == {"_id": "global:4"} and calls[0][1] == {"upsert": True}
    assert calls[1][0] == ({"_id": "global:4"}, {"$set": {"status": "previous"}})
    assert calls[2][0][1] == {"$addToSet": {"consumed_incident_ids": {"$each": ["a"]}}}


def test_mongo_policy_store_write_errors_are_swallowed(monkeypatch):
    class Boom:
        def update_one(self, *a, **k):
            raise RuntimeError("down")

    monkeypatch.setattr(registry_module.db, "get_collection", lambda name: Boom())
    store = MongoPolicyStore()
    store.save_version({"scope": "global", "version": 1})
    store.set_status("global", 1, "archived")
    store.add_consumed("global", ["a"])
