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

    # Set by the human_approval node once the devops engineer responds.
    approval_status: str

    # Set by the apply_fix node after a real commit is pushed (or fails).
    fix_result: dict[str, Any]
