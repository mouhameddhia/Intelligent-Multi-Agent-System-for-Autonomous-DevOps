"""policy_version_used flows from graph state onto the stored incident, and from
there onto the learning record, via the unchanged investigation routes."""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import investigation as inv
from agent_system.learning.recorder import build_record

from learning_fixtures import approved

RESULT = {
    "monitoring_report": "m", "diagnosis_report": "Root cause: AssertionError, expected value differs",
    "remediation_report": "r", "steps": [{"id": "s1", "text": "Fix the add function in calc.py"}],
    "policy_version_used": 3,
}
BODY = {"job_name": "j", "build_number": 1}


class FakeGraph:
    def __init__(self, result):
        self.result = result

    def invoke(self, state, config=None):
        return dict(self.result)

    def stream(self, state, config=None, stream_mode=None):
        yield {"remediation": {}}

    def get_state(self, config):
        class S:
            values = dict(RESULT)

        S.values = dict(self.result)
        return S()


def client(monkeypatch, result):
    saved = []
    monkeypatch.setattr(inv, "investigation_graph", FakeGraph(result))
    monkeypatch.setattr(inv, "save_incident", saved.append)
    app = FastAPI()
    app.include_router(inv.router)
    return TestClient(app), saved


def test_investigate_stores_policy_version_used(monkeypatch):
    http, saved = client(monkeypatch, RESULT)
    response = http.post("/api/investigate", json=BODY)
    assert response.status_code == 200
    assert saved[0]["policy_version_used"] == 3
    assert "policy_version_used" not in response.json()  # response shape unchanged


def test_investigate_without_a_policy_stores_none(monkeypatch):
    http, saved = client(monkeypatch, {k: v for k, v in RESULT.items() if k != "policy_version_used"})
    assert http.post("/api/investigate", json=BODY).status_code == 200
    assert saved[0]["policy_version_used"] is None


def test_stream_stores_policy_version_used(monkeypatch):
    http, saved = client(monkeypatch, RESULT)
    response = http.post("/api/investigate/stream", json=BODY)
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1]["type"] == "complete"
    assert saved[0]["policy_version_used"] == 3
    assert "policy_version_used" not in events[-1]["data"]


def test_policy_version_reaches_the_learning_record():
    assert build_record(approved(policy_version_used=3)).policy_version_used == 3
    assert build_record(approved(policy_version_used=None)).policy_version_used is None
