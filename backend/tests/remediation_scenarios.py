"""Fixed Remediation inputs plus a harness that runs run_remediation with a fake
LLM and captures exactly what the model would have been sent. Used both to
generate the golden prompts (from the pre-learning code) and to check that an
empty policy leaves the prompts byte-for-byte unchanged."""

from agent_system.agents import remediation

DIAGNOSIS = (
    "Root cause: an AssertionError in test_add; the function returns the wrong "
    "value and the expected result differs. Evidence: calc.py line 4."
)

BASE_STATE = {
    "incident_id": "inc-1",
    "user_id": "test-user",
    "use_personalized_answers": True,
    "job_name": "PipeGuard-Lab",
    "build_number": 7,
    "incident_type": "test_failure",
    "monitoring_report": "Build 7 failed in the test stage.",
    "diagnosis_report": DIAGNOSIS,
    "steps": [],
}

HISTORICAL = {
    "job_name": "PipeGuard-Lab",
    "build_number": 3,
    "resolution_method": "Manual",
    "actions": [{"text": "Corrected the add function", "solved": True}],
    "steps": [{"text": "Fix calc.py add", "id": "s1"}],
}

SCENARIOS = {
    "evidence": {**BASE_STATE, "evidence": {"jenkins": {"console_log": "FAILED test_add"}}},
    "evidence_with_history": {
        **BASE_STATE,
        "evidence": {"jenkins": {"console_log": "FAILED test_add"}},
        "historical_incident": HISTORICAL,
    },
    "legacy": {**BASE_STATE, "evidence": {}},
}


class FakeChat:
    """Stands in for ChatGroq: records every invoke() and returns canned text."""

    instances: list["FakeChat"] = []

    def __init__(self, *args, **kwargs):
        self.calls: list[list[tuple[str, str]]] = []
        FakeChat.instances.append(self)

    def invoke(self, messages):
        self.calls.append([tuple(m) for m in messages])

        class Result:
            content = "Recommended action: fix it.\nAGENT_CONFIDENCE: 80"

        return Result()


def run_scenario(state: dict, monkeypatch=None):
    """Returns (messages sent to the LLM, resulting state)."""
    FakeChat.instances = []
    original = remediation.ChatGroq
    remediation.ChatGroq = FakeChat
    try:
        result = remediation.run_remediation(dict(state))
    finally:
        remediation.ChatGroq = original
    return [list(m) for m in FakeChat.instances[0].calls[0]], result
