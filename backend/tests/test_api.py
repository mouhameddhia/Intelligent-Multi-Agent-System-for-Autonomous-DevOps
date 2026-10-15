"""API smoke test: replay, SSE-backed state, approval with a mandatory reason."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.api.main import create_app


def wait_status(client, iid, statuses, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        inc = client.get(f"/api/incidents/{iid}").json()
        if inc["status"] in statuses:
            return inc
        time.sleep(0.1)
    raise AssertionError(f"timeout, last status {inc['status']}")


def test_replay_and_approve(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").json()["mock_llm"] is True
        assert len(client.get("/api/scenarios").json()) >= 5
        inc = client.post("/api/scenarios/catalog-bad-image/replay").json()
        inc = wait_status(client, inc["id"], {"awaiting_approval"})
        # a reason is required
        r = client.post(f"/api/incidents/{inc['id']}/approval", json={"decision": "approved", "reason": ""})
        assert r.status_code == 422
        r = client.post(f"/api/incidents/{inc['id']}/approval", json={"decision": "approved", "reason": "rollback is safe"})
        assert r.status_code == 202
        inc = wait_status(client, inc["id"], {"resolved"})
        assert inc["approval"]["reason"] == "rollback is safe"
        audit = client.get(f"/api/audit?incident_id={inc['id']}").json()
        assert any(a["action"] == "approval:approved" for a in audit)
        assert client.get("/api/architecture").json()["providers"]["orchestration"] == "simulated"
