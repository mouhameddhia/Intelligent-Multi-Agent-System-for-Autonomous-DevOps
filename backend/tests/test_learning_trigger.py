"""End-to-end offline pipeline: closed incidents -> records -> evaluation ->
learning -> candidate policy, all with fake LLMs and in-memory stores."""

import json
import threading

from agent_system.config import settings
from agent_system.learning import trigger
from agent_system.learning.evaluator import needs_evaluation
from agent_system.learning.recorder import record_incident_closed
from agent_system.learning.registry import InMemoryPolicyStore, PolicyRegistry
from agent_system.learning.store import InMemoryRecordStore
from agent_system.learning.trigger import run_learning_pipeline

from learning_fixtures import approved, denied, with_feedback

RUBRIC = '{"diagnosis_quality": 80, "fix_quality": 75, "rationale": "fine"}'
LESSON = "Fix the add function in the module and rerun the failing unit test"


def world(n_passed=5):
    store, reg = InMemoryRecordStore(), PolicyRegistry(InMemoryPolicyStore())
    docs = {f"inc-{i}": with_feedback(approved(f"inc-{i}"), True) for i in range(n_passed)}
    for incident_id in docs:
        record_incident_closed(incident_id, store=store, loader=docs.get)
    return store, reg


def learner_llm(system, human):
    return json.dumps({"operations": [{
        "op": "ADD", "text": LESSON, "failure_category": "test_assertion",
        "evidence_incident_ids": ["inc-0", "inc-1"],
    }]})


def test_five_closed_incidents_produce_a_policy():
    store, reg = world(5)
    result = run_learning_pipeline(store=store, registry=reg, eval_llm=lambda s, h: RUBRIC, learn_llm=learner_llm)
    assert result.status == "published" and result.records_evaluated == 5
    active = reg.get_active()
    assert active.rules[0].text == LESSON and active.rules[0].status == "active"
    assert active.priors["test_assertion|code_patch"].success == 5
    assert not any(needs_evaluation(r) for r in store.list())


def test_below_threshold_evaluates_but_does_not_learn():
    store, reg = world(3)
    result = run_learning_pipeline(store=store, registry=reg, eval_llm=lambda s, h: RUBRIC, learn_llm=learner_llm)
    assert result.status == "not_due" and result.records_evaluated == 3
    assert reg.get_active() is None


def test_batch_size_is_configurable(monkeypatch):
    monkeypatch.setattr(settings, "learning_batch_size", 2)
    store, reg = world(2)
    result = run_learning_pipeline(store=store, registry=reg, eval_llm=lambda s, h: RUBRIC, learn_llm=learner_llm)
    assert result.status == "published"


def test_denied_and_unknown_outcomes_count_correctly():
    store, reg = InMemoryRecordStore(), PolicyRegistry(InMemoryPolicyStore())
    docs = {f"d{i}": denied(f"d{i}") for i in range(5)}
    for incident_id in docs:
        record_incident_closed(incident_id, store=store, loader=docs.get)
    result = run_learning_pipeline(
        store=store, registry=reg, eval_llm=lambda s, h: RUBRIC,
        learn_llm=lambda s, h: json.dumps({"operations": []}),
    )
    assert result.status == "published"
    assert sum(c.failure for c in reg.get_active().priors.values()) == 5


def test_disabled_does_nothing(monkeypatch):
    monkeypatch.setattr(settings, "learning_enabled", False)
    store, reg = world(5)

    def must_not_run(s, h):
        raise AssertionError("should not run")

    result = run_learning_pipeline(store=store, registry=reg, eval_llm=must_not_run, learn_llm=must_not_run)
    assert result.status == "disabled"
    assert all(needs_evaluation(r) for r in store.list())


def test_force_does_not_override_global_disable(monkeypatch):
    monkeypatch.setattr(settings, "learning_enabled", False)
    store, reg = world(5)
    result = run_learning_pipeline(
        store=store, registry=reg, eval_llm=lambda s, h: RUBRIC, learn_llm=learner_llm, force=True
    )
    assert result.status == "disabled"
    assert all(needs_evaluation(r) for r in store.list())


def test_concurrent_run_returns_busy():
    store, reg = world(5)
    assert trigger._run_lock.acquire(blocking=False)
    try:
        result = run_learning_pipeline(store=store, registry=reg, eval_llm=lambda s, h: RUBRIC, learn_llm=learner_llm)
    finally:
        trigger._run_lock.release()
    assert result.status == "busy"
    assert reg.get_active() is None


def test_pipeline_never_raises_and_releases_the_lock():
    class Broken(InMemoryRecordStore):
        def list(self, scope="global", limit=5000):
            raise RuntimeError("mongo down")

    result = run_learning_pipeline(store=Broken(), registry=PolicyRegistry(InMemoryPolicyStore()))
    assert result.status == "llm_failed"
    assert trigger._run_lock.acquire(blocking=False)
    trigger._run_lock.release()


def test_llm_outage_still_records_rewards_and_retries_learning_later():
    store, reg = world(5)

    def down(s, h):
        raise RuntimeError("quota")

    first = run_learning_pipeline(store=store, registry=reg, eval_llm=down, learn_llm=down)
    assert first.status == "llm_failed" and reg.consumed_ids() == set()
    second = run_learning_pipeline(store=store, registry=reg, eval_llm=down, learn_llm=learner_llm)
    assert second.status == "published"


def test_recorder_thread_triggers_the_pipeline(monkeypatch):
    from agent_system.learning import recorder

    store = InMemoryRecordStore()
    monkeypatch.setattr(recorder, "get_store", lambda: store)
    monkeypatch.setattr("agent_system.db.get_incident", lambda incident_id, user_id=None: approved(incident_id, user_id=user_id))
    triggered = threading.Event()
    monkeypatch.setattr(trigger, "on_incident_closed", lambda scope: triggered.set())
    recorder.schedule_record("inc-1", "test-user").join(timeout=2)
    assert triggered.is_set()


def test_open_incident_does_not_trigger(monkeypatch):
    from agent_system.learning import recorder
    from learning_fixtures import incident

    monkeypatch.setattr(recorder, "get_store", lambda: InMemoryRecordStore())
    monkeypatch.setattr("agent_system.db.get_incident", lambda incident_id, user_id=None: incident(user_id=user_id))
    triggered = threading.Event()
    monkeypatch.setattr(trigger, "on_incident_closed", lambda scope: triggered.set())
    recorder.schedule_record("inc-1", "test-user").join(timeout=2)
    assert not triggered.is_set()
