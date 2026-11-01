"""Fixed incident-document fixtures shared by the learning tests."""

from copy import deepcopy

BASE = {
    "_id": "inc-1",
    "job_name": "PipeGuard-Lab",
    "build_number": 7,
    "evidence": {
        "jenkins": {"console_log": "RAW LOG MUST NEVER REACH THE RECORD"},
        "github": {"files": [{"filename": "App.py"}, {"filename": "calc.py"}]},
    },
    "diagnosis_report": (
        "Summary: build failed.\nRoot cause: an AssertionError in test_add; the "
        "function returns the wrong value and the expected result differs."
    ),
    "incident_type": "test_failure",
    "steps": [
        {"id": "s1", "text": "Fix the add function in calc.py so it sums its inputs"},
        {"id": "s2", "text": "Rerun the build in Jenkins"},
    ],
    "judge_scores": {"monitoring": 80, "diagnosis": 75, "remediation": 70},
    "status": "awaiting_approval",
    "decision": None,
    "fix_result": None,
    "created_at": "2026-01-01T00:00:00+00:00",
}


def incident(**overrides):
    doc = deepcopy(BASE)
    doc.update(overrides)
    return doc


def approved(incident_id="inc-1", applied=True, **overrides):
    return incident(
        _id=incident_id,
        status="approved_fixed",
        decision={"decision": "approve", "approver": "eng", "decided_at": "2026-01-01T01:00:00+00:00"},
        fix_result={"applied": applied, "file": "calc.py"},
        **overrides,
    )


def denied(incident_id="inc-2", **overrides):
    return incident(
        _id=incident_id,
        status="denied",
        decision={"decision": "deny", "approver": "eng", "decided_at": "2026-01-01T01:00:00+00:00"},
        fix_result=None,
        steps=[{"id": "s1", "text": "Restart the Jenkins agent and redeploy"}],
        **overrides,
    )


def with_feedback(doc, build_passed, **extra):
    doc = deepcopy(doc)
    doc["feedback"] = {
        "build_passed": build_passed,
        "liked_fix": build_passed,
        "system_efficient": True,
        "comment": "ok",
        "submitted_by": "eng",
        "submitted_at": "2026-01-01T02:00:00+00:00",
        **extra,
    }
    doc["resolution_path"] = "auto_fix"
    return doc
