"""The existing routes must keep their responses and just schedule a record."""

import pytest

from app.api import investigation as inv


class FakeGraph:
    def __init__(self, result):
        self._result = result

    def get_state(self, config):
        class S:
            next = ("apply_fix",)

        return S()

    def invoke(self, command, config=None):
        return self._result


@pytest.fixture
def spy(monkeypatch):
    calls = []
    monkeypatch.setattr(inv, "schedule_record", lambda incident_id: calls.append(incident_id))
    monkeypatch.setattr(inv, "save_incident", lambda doc: None)
    return calls


def test_decide_schedules_a_record_and_response_is_unchanged(monkeypatch, spy):
    monkeypatch.setattr(inv, "investigation_graph", FakeGraph({"fix_result": {"applied": True, "message": "Pushed"}}))
    response = inv.decide(inv.DecisionRequest(incident_id="inc-1", decision="approve"), None)
    assert spy == ["inc-1"]
    assert response.status == "approved_fixed"
    assert response.message == "Pushed"
    assert response.fix_result == {"applied": True, "message": "Pushed"}


def test_deny_schedules_a_record(monkeypatch, spy):
    monkeypatch.setattr(inv, "investigation_graph", FakeGraph({}))
    response = inv.decide(inv.DecisionRequest(incident_id="inc-2", decision="deny"), None)
    assert spy == ["inc-2"]
    assert response.status == "denied"


def test_feedback_schedules_a_record_and_response_is_unchanged(monkeypatch, spy):
    monkeypatch.setattr(inv, "get_incident", lambda incident_id: {"_id": incident_id, "steps": [], "judge_overall_score": 80})
    response = inv.submit_feedback("inc-3", inv.FeedbackRequest(build_passed=True))
    assert spy == ["inc-3"]
    assert response == {"incident_id": "inc-3", "status": "feedback_saved"}


def test_feedback_404_does_not_schedule(monkeypatch, spy):
    from fastapi import HTTPException

    monkeypatch.setattr(inv, "get_incident", lambda incident_id: None)
    with pytest.raises(HTTPException):
        inv.submit_feedback("nope", inv.FeedbackRequest())
    assert spy == []
