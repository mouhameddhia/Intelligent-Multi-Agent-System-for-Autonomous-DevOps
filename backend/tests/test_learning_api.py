import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import learning as api
from agent_system.config import settings
from agent_system.learning import trigger
from agent_system.learning.models import LearningRunResult, PolicyVersion, PriorCount, Rule
from agent_system.learning.registry import InMemoryPolicyStore, PolicyRegistry, set_registry
from agent_system.learning.store import InMemoryRecordStore, set_store
from app.api.auth import require_user

TEST_USER = {"_id": "user-a", "name": "Ada"}


def rule(rid="r1", text="Fix the function under test and rerun the failing unit test", status="active"):
    return Rule(id=rid, text=text, failure_category="test_assertion", status=status, support_count=3, success_count=3)


@pytest.fixture
def world():
    store, registry = InMemoryRecordStore(), PolicyRegistry(InMemoryPolicyStore())
    set_store(store)
    set_registry(registry)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[require_user] = lambda: TEST_USER
    return TestClient(app), store, registry


def publish(registry, rules=None, priors=None, sources=()):
    return registry.publish(PolicyVersion(
        version=0, scope="user-a", rules=rules or [rule()],
        priors=priors if priors is not None else {"test_assertion|code_patch": PriorCount(success=4, failure=1)},
        source_record_ids=list(sources), change_summary="test",
    ))


def record(incident_id, **kw):
    return {"incident_id": incident_id, "scope": "user-a", "created_at": incident_id,
            "rebuild_result": "unknown", "fix_applied": None, "human_decision": {}, **kw}


# ---- policy / versions ----------------------------------------------------------

def test_policy_is_null_when_nothing_is_learned(world):
    http, _, _ = world
    body = http.get("/api/learning/policy").json()
    assert body == {"learning_enabled": True, "scope": "user", "policy": None}


def test_policy_returns_the_active_version(world):
    http, _, registry = world
    publish(registry)
    publish(registry, [rule(text="A newer lesson for the same failure type")])
    body = http.get("/api/learning/policy").json()
    assert body["policy"]["version"] == 2 and body["policy"]["status"] == "active"
    assert body["policy"]["rules"][0]["text"].startswith("A newer lesson")


def test_policy_can_preview_the_rendered_guidance(world):
    http, _, registry = world
    publish(registry)
    body = http.get("/api/learning/policy", params={"failure_category": "test_assertion"}).json()
    assert "Learned guidance from past incidents (advisory)" in body["rendered_guidance"]
    assert http.get("/api/learning/policy", params={"failure_category": "config"}).json()["rendered_guidance"] == ""


def test_policy_reports_the_enabled_flag(world, monkeypatch):
    http, _, _ = world
    monkeypatch.setattr(settings, "learning_enabled", False)
    assert http.get("/api/learning/policy").json()["learning_enabled"] is False


def test_versions_newest_first_without_rules_by_default(world):
    http, _, registry = world
    publish(registry, sources=["a", "b"])
    publish(registry, [rule(), rule("r2", "Another distinct lesson about this failure", status="candidate")])
    body = http.get("/api/learning/versions").json()
    assert body["count"] == 2
    newest, oldest = body["versions"]
    assert (newest["version"], newest["status"]) == (2, "active")
    assert newest["rule_counts"] == {"active": 1, "candidate": 1, "retired": 0}
    assert oldest["status"] == "previous" and oldest["source_record_count"] == 2
    assert "rules" not in newest


def test_versions_can_include_rules(world):
    http, _, registry = world
    publish(registry)
    version = http.get("/api/learning/versions", params={"include_rules": True}).json()["versions"][0]
    assert version["rules"][0]["id"] == "r1"


def test_learning_status_explains_missing_policy_and_threshold(world):
    http, store, registry = world
    store.upsert(record("pending", evaluation={"outcome_reward": 0.5}))
    registry.add_consumed([], scope="user-a")
    body = http.get("/api/learning/status").json()
    assert body["active_policy_version"] is None
    assert body["awaiting_learning"] == 1
    assert body["batch_size"] == settings.learning_batch_size
    assert body["ready_to_learn"] is False


# ---- rollback -----------------------------------------------------------------------

def test_rollback_restores_the_previous_version(world):
    http, _, registry = world
    publish(registry, [rule(text="The good lesson from the first version")])
    publish(registry, [rule(text="The bad lesson from the second version")])
    response = http.post("/api/learning/rollback")
    assert response.status_code == 200
    assert response.json() == {"restored_version": 1, "status": "active"}
    assert http.get("/api/learning/policy").json()["policy"]["rules"][0]["text"].startswith("The good lesson")


def test_rollback_without_a_previous_version_is_409(world):
    http, _, registry = world
    assert http.post("/api/learning/rollback").status_code == 409
    publish(registry)
    assert http.post("/api/learning/rollback").status_code == 409


# ---- run ---------------------------------------------------------------------------

def test_run_wait_returns_the_result(world, monkeypatch):
    http, _, _ = world
    seen = {}

    def fake(scope, force):
        seen["force"] = force
        seen["scope"] = scope
        return LearningRunResult(status="published", version=3)

    monkeypatch.setattr(api, "run_learning_pipeline", fake)
    response = http.post("/api/learning/run", json={"wait": True, "force": False})
    assert response.status_code == 200 and response.json()["version"] == 3
    assert seen == {"force": False, "scope": "user-a"}


def test_run_defaults_to_background_and_force(world, monkeypatch):
    http, _, _ = world
    calls = []
    monkeypatch.setattr(api, "run_learning_pipeline", lambda scope, force: calls.append((scope, force)))
    response = http.post("/api/learning/run")  # no body at all
    assert response.status_code == 202
    assert response.json() == {"status": "started", "force": True}
    assert calls == [("user-a", True)]  # TestClient runs background tasks before returning


def test_run_while_another_is_running_is_409(world):
    http, _, _ = world
    assert trigger._run_lock.acquire(blocking=False)
    try:
        assert http.post("/api/learning/run").status_code == 409
    finally:
        trigger._run_lock.release()


def test_opt_in_enrolls_a_bounded_batch_for_the_authenticated_account(world, monkeypatch):
    http, _, _ = world
    docs = [
        {"_id": f"i{index}", "decision": {"decision": "approve"}, "personalization_enabled": False}
        for index in range(7)
    ]
    updated, scheduled = [], []
    monkeypatch.setattr(api, "list_incidents", lambda **kwargs: docs)
    monkeypatch.setattr(
        api,
        "update_incident",
        lambda incident_id, user_id, fields: updated.append((incident_id, user_id, fields)) or True,
    )
    monkeypatch.setattr(api, "schedule_records", lambda incident_ids, user_id: scheduled.append((incident_ids, user_id)))

    response = http.post("/api/learning/opt-in")
    assert response.status_code == 200
    assert response.json()["initial_records_enrolled"] == api.INITIAL_HISTORY_BATCH == 5
    assert len(updated) == 5 and len(scheduled) == 1
    assert all(user_id == "user-a" for _, user_id, _ in updated)
    assert scheduled[0][1] == "user-a" and len(scheduled[0][0]) == 5


def test_real_run_learns_from_stored_records(world, monkeypatch):
    http, store, registry = world
    for n in range(2):
        store.upsert({
            **record(f"i{n}"), "failure_category": "test_assertion", "fix_action_type": "code_patch",
            "rebuild_result": "pass",
            "evaluation": {"outcome_reward": 1.0, "outcome_signature": "pass|None|None|None"},
        })
    import json
    from agent_system.learning import learner

    def llm(system, human):
        return json.dumps({"operations": [{
            "op": "ADD", "text": "Fix the function under test and rerun the failing unit test",
            "failure_category": "test_assertion", "evidence_incident_ids": ["i0", "i1"],
        }]})

    original = learner.run_learning
    # evaluation is stubbed as already current, so only the learner LLM is needed
    monkeypatch.setattr(trigger, "run_learning", lambda **kw: original(**{**kw, "llm_call": llm}))
    body = http.post("/api/learning/run", json={"wait": True}).json()
    assert body["status"] == "published"
    assert registry.get_active("user-a").rules[0].status == "active"


# ---- metrics -----------------------------------------------------------------------

def test_metrics_per_policy_version_and_record_counts(world):
    http, store, registry = world
    store.upsert(record("a", rebuild_result="pass", fix_applied=True, human_decision={"decision": "approve"},
                        policy_version_used=1, evaluation={"outcome_reward": 1.0}))
    store.upsert(record("b", human_decision={"decision": "deny"}, evaluation={"outcome_reward": None}))
    store.upsert(record("c"))
    registry.add_consumed(["zzz"], scope="user-a")
    body = http.get("/api/learning/metrics").json()
    assert body["records"] == {"total": 3, "evaluated": 2, "excluded_unknown_outcome": 1, "awaiting_learning": 1}
    rows = {r["policy_version_used"]: r for r in body["by_policy_version"]}
    assert rows[1]["first_fix_success_rate"] == 1.0
    assert rows[None]["approval_rate"] == 0.0
    assert body["overall"]["incidents"] == 3


def test_metrics_with_no_data(world):
    http, _, _ = world
    body = http.get("/api/learning/metrics").json()
    assert body["records"]["total"] == 0 and body["by_policy_version"] == []


# ---- storage failures --------------------------------------------------------------

def test_storage_read_errors_are_503_not_500(world):
    http, _, _ = world

    class Broken(InMemoryPolicyStore):
        def load_versions(self, scope):
            raise RuntimeError("mongo down")

    set_registry(PolicyRegistry(Broken()))
    for path in ("/api/learning/policy", "/api/learning/versions"):
        assert http.get(path).status_code == 503
    assert http.post("/api/learning/rollback").status_code == 503


def test_endpoints_work_without_mongodb_configured():
    set_store(None)
    set_registry(None)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[require_user] = lambda: TEST_USER
    http = TestClient(app)
    assert http.get("/api/learning/policy").json()["policy"] is None
    assert http.get("/api/learning/versions").json()["count"] == 0
    assert http.get("/api/learning/metrics").json()["records"]["total"] == 0


def test_learning_routes_require_authentication():
    app = FastAPI()
    app.include_router(api.router)
    http = TestClient(app)
    assert http.get("/api/learning/policy").status_code == 401
    assert http.get("/api/learning/metrics").status_code == 401
    assert http.post("/api/learning/run").status_code == 401
    assert http.post("/api/learning/opt-in").status_code == 401


def test_router_is_registered_on_the_main_app():
    from app.main import app

    paths = set(app.openapi()["paths"])
    for path in ("/api/learning/policy", "/api/learning/versions", "/api/learning/status", "/api/learning/run",
                 "/api/learning/rollback", "/api/learning/metrics"):
        assert path in paths
    for path in ("/api/investigate", "/api/investigate/decision", "/api/incidents/{incident_id}/feedback"):
        assert path in paths
