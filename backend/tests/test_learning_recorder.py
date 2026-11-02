import json

from agent_system.learning import recorder
from agent_system.learning.recorder import build_record, is_closed, record_incident_closed, schedule_record
from agent_system.learning.store import InMemoryRecordStore

from learning_fixtures import approved, denied, incident, with_feedback


def test_awaiting_approval_is_not_closed():
    assert not is_closed(incident())


def test_decision_or_feedback_closes():
    assert is_closed(approved())
    assert is_closed(with_feedback(incident(), True))


def test_approved_record_fields():
    record = build_record(approved())
    assert record.incident_id == "inc-1"
    assert record.scope == "global"
    assert record.failure_category == "test_assertion"
    assert record.fix_action_type == "code_patch"
    assert record.human_decision.decision == "approve"
    assert record.fix_applied is True
    assert record.rebuild_result == "unknown"
    assert record.engineer_feedback is None
    assert record.proposed_steps[0].startswith("Fix the add function")
    assert record.judge_scores["diagnosis"] == 75
    assert record.policy_version_used is None


def test_denied_record_has_no_fix_applied():
    record = build_record(denied())
    assert record.human_decision.decision == "deny"
    assert record.fix_applied is False
    assert record.fix_action_type == "restart"


def test_rebuild_result_from_feedback():
    assert build_record(with_feedback(approved(), True)).rebuild_result == "pass"
    assert build_record(with_feedback(approved(), False)).rebuild_result == "fail"
    assert build_record(with_feedback(approved(), None)).rebuild_result == "unknown"


def test_policy_version_is_carried_over():
    assert build_record(approved(policy_version_used=3)).policy_version_used == 3


def test_record_contains_no_raw_console_log():
    dumped = json.dumps(build_record(approved()).model_dump(mode="json"))
    assert "RAW LOG MUST NEVER" not in dumped


def test_summary_is_bounded_and_starts_at_root_cause():
    record = build_record(approved(diagnosis_report="Intro. Root cause: " + "x" * 2000))
    assert record.diagnosis_summary.startswith("Root cause")
    assert len(record.diagnosis_summary) <= 400


def test_record_incident_closed_stores_and_updates_one_record():
    store = InMemoryRecordStore()
    docs = {"inc-1": approved()}

    first = record_incident_closed("inc-1", store=store, loader=docs.get)
    assert first is not None and first.rebuild_result == "unknown"
    created = store.get("inc-1")["created_at"]

    docs["inc-1"] = with_feedback(approved(), True)
    record_incident_closed("inc-1", store=store, loader=docs.get)

    assert len(store.list()) == 1
    stored = store.get("inc-1")
    assert stored["rebuild_result"] == "pass"
    assert stored["created_at"] == created


def test_open_incident_is_not_recorded():
    store = InMemoryRecordStore()
    assert record_incident_closed("inc-1", store=store, loader=lambda _id: incident()) is None
    assert store.list() == []


def test_missing_incident_is_not_recorded():
    store = InMemoryRecordStore()
    assert record_incident_closed("nope", store=store, loader=lambda _id: None) is None


def test_recording_never_raises():
    def boom(_id):
        raise RuntimeError("mongo down")

    assert record_incident_closed("inc-1", store=InMemoryRecordStore(), loader=boom) is None

    class BrokenStore(InMemoryRecordStore):
        def upsert(self, record):
            raise RuntimeError("write failed")

    assert record_incident_closed("inc-1", store=BrokenStore(), loader=lambda _id: approved()) is None


def test_schedule_record_swallows_errors(monkeypatch):
    def boom(_id, **_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(recorder, "record_incident_closed", boom)
    thread = schedule_record("inc-1")
    assert thread is not None
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_schedule_record_writes_through_the_default_store(monkeypatch):
    store = InMemoryRecordStore()
    monkeypatch.setattr(recorder, "get_store", lambda: store)
    monkeypatch.setattr("agent_system.db.get_incident", lambda incident_id: approved(incident_id))
    thread = schedule_record("inc-9")
    thread.join(timeout=2)
    assert store.get("inc-9")["human_decision"]["decision"] == "approve"
