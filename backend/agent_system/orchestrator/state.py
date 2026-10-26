from typing import Any, TypedDict


class InvestigationState(TypedDict, total=False):
    """Shared state passed between every agent in the investigation pipeline."""

    incident_id: str
    job_name: str
    build_number: int

    # Jenkins credentials — present when investigating a real build, empty for synthetic.
    jenkins_url: str
    jenkins_username: str
    jenkins_api_token: str

    # Live evidence collected for the exact build under investigation.
    evidence: dict[str, Any]

    # Needed only if the human approves the fix — used to fetch + push the real patch.
    github_token: str
    github_owner: str
    github_repo: str

    # Each agent writes its report into the field below it.
    monitoring_report: str
    diagnosis_report: str
    remediation_report: str

    # Agent-provided estimates, 0-100; None means unavailable.
    monitoring_confidence: int | None
    diagnosis_confidence: int | None
    remediation_confidence: int | None
    
    
    # Where the Monitoring score came from: "agent" (model-reported),
    # "evidence_coverage" (deterministic fallback) or "unavailable".
    monitoring_confidence_source: str

    # Phase 1: who is investigating, and what kind of incident this is.
    engineer_id: str
    source: str
    incident_type: str

    # Structured remediation steps (id, text, order_shown, disruptive, outcome).
    steps: list[dict[str, Any]]

    # Set by Diagnosis after its one search_similar_incidents call, verified
    # server-side via historical_match.resolve_historical_match(). None if no
    # genuine match. Remediation reads this; it makes no DB/tool call of its own.
    historical_incident: dict[str, Any] | None

    # Set by the human_approval node once the devops engineer responds.
    approval_status: str

    # Set by the apply_fix node after a real commit is pushed (or fails).
    fix_result: dict[str, Any]

    # Set by apply_fix's internal judge gate before any GitHub push is
    # attempted. {"verdict": "pass"|"fail", "reasoning": str, "source": str}.
    judge_result: dict[str, Any]
