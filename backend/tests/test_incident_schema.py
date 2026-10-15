"""Incident model and exported JSON Schema."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.models.incident import Incident, IncidentStatus, Severity
from app.models.schema_export import SCHEMA_PATH, incident_schema, render

REQUIRED_FIELDS = [
    "id", "severity", "status", "time_window", "trigger", "change", "pipeline", "deployment", "log_patterns",
    "metric_anomalies", "hypotheses", "proposed_action", "policy_decision", "approval", "execution_result",
    "verification_result", "timeline",
]


def test_schema_file_is_current():
    assert SCHEMA_PATH.exists(), "run `make schema`"
    assert SCHEMA_PATH.read_text() == render(), "schema/incident.schema.json is stale: run `make schema`"


def test_schema_has_all_spec_fields():
    props = incident_schema()["properties"]
    for f in REQUIRED_FIELDS:
        assert f in props, f


def test_hypothesis_has_evidence_and_confidence():
    defs = incident_schema()["$defs"]
    hp = defs["Hypothesis"]["properties"]
    assert {"rank", "evidence", "confidence"} <= set(hp)
    assert {"type", "target", "expected_effect", "risk"} <= set(defs["ProposedAction"]["properties"])
    assert {"approver", "decision", "reason", "timestamp"} <= set(defs["Approval"]["properties"])


def test_roundtrip_json():
    inc = Incident(severity=Severity.high)
    inc.log("monitor", "detected", "test")
    again = Incident.model_validate_json(inc.model_dump_json())
    assert again == inc
    assert again.status == IncidentStatus.monitoring
    assert [s.agent for s in again.steps][0] == "monitor"


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        Incident.model_validate({"id": "INC-1", "unknown": 1})


def test_confidence_bounds():
    from app.models.incident import Hypothesis
    with pytest.raises(ValidationError):
        Hypothesis(id="H1", rank=1, title="t", category="c", component="x", description="d", confidence=1.5)


def test_stored_incidents_validate_against_schema(manager):
    """A full run's stored JSON validates against the exported schema's required/enum structure."""
    inc = manager.run_sync("cart-oom-killed", record_history=False)
    data = json.loads(inc.model_dump_json())
    schema = incident_schema()
    assert set(data) <= set(schema["properties"])
    assert data["status"] in schema["$defs"]["IncidentStatus"]["enum"]
