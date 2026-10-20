import json

import httpx
from langchain_core.tools import tool

from ..data import (
    LIVE_BUILD,
    LIVE_CONSOLE_LOG,
    LIVE_SYSTEM_METRICS,
    RECENT_COMMITS,
    BUILD_HISTORY,
    FAILURE_PATTERNS,
    PAST_FIXES,
    RUNBOOKS,
    TEAM_CONTACTS,
)


# ---------------------------------------------------------------------------
# Monitoring tools — live data (Jenkins + system metrics)
# ---------------------------------------------------------------------------

def make_monitoring_tools(
    jenkins_url: str = "",
    jenkins_username: str = "",
    jenkins_api_token: str = "",
) -> list:
    """
    Tools for the Monitoring Agent.
    Use real Jenkins when credentials are provided; fall back to synthetic live data otherwise.
    """
    use_real = bool(jenkins_url and jenkins_username and jenkins_api_token)
    base_url = jenkins_url.rstrip("/") if jenkins_url else ""
    auth = (jenkins_username, jenkins_api_token)

    @tool
    def get_build_metadata(job_name: str) -> str:
        """Return metadata for the latest failed build of a Jenkins job."""
        if use_real:
            try:
                url = f"{base_url}/job/{job_name}/lastFailedBuild/api/json"
                response = httpx.get(url, auth=auth, timeout=10.0, follow_redirects=False)
                response.raise_for_status()
                data = response.json()
                return json.dumps({
                    "name": job_name,
                    "build_number": data.get("number"),
                    "result": data.get("result"),
                    "duration_seconds": (data.get("duration") or 0) // 1000,
                    "url": data.get("url"),
                    "timestamp": data.get("timestamp"),
                }, indent=2)
            except Exception as exc:
                return f"Could not fetch real build metadata: {exc}"
        return json.dumps(LIVE_BUILD, indent=2)

    @tool
    def get_console_log(job_name: str) -> str:
        """Return the full console log of the latest failed build."""
        if use_real:
            try:
                url = f"{base_url}/job/{job_name}/lastFailedBuild/consoleText"
                response = httpx.get(url, auth=auth, timeout=15.0, follow_redirects=False)
                response.raise_for_status()
                text = response.text
                return text[-8000:] if len(text) > 8000 else text
            except Exception as exc:
                return f"Could not fetch real console log: {exc}"
        return LIVE_CONSOLE_LOG

    @tool
    def get_system_metrics(job_name: str) -> str:
        """Return system metrics (CPU, memory, I/O) captured at the time of the build failure."""
        return json.dumps(LIVE_SYSTEM_METRICS, indent=2)

    return [get_build_metadata, get_console_log, get_system_metrics]


# ---------------------------------------------------------------------------
# Diagnosis tools — live + historical data
# ---------------------------------------------------------------------------

def make_diagnosis_tools() -> list:
    """
    Tools for the Diagnosis Agent.
    Provides recent commits, past build history, and recurring failure patterns.
    """

    @tool
    def get_recent_commits(job_name: str) -> str:
        """Return the most recent commits on the repository linked to a Jenkins job."""
        return json.dumps(RECENT_COMMITS, indent=2)

    @tool
    def get_build_history(job_name: str) -> str:
        """Return the last several build results for a Jenkins job to identify patterns."""
        return json.dumps(BUILD_HISTORY, indent=2)

    @tool
    def get_failure_patterns(job_name: str) -> str:
        """Return known recurring failure patterns for this job over the past 30 days."""
        return json.dumps(FAILURE_PATTERNS, indent=2)

    return [get_recent_commits, get_build_history, get_failure_patterns]


# ---------------------------------------------------------------------------
# Remediation tools — knowledge base data
# ---------------------------------------------------------------------------

def make_remediation_tools() -> list:
    """
    Tools for the Remediation Agent.
    Provides past fixes, runbooks, and team contact information.
    """

    @tool
    def get_past_fixes(failure_type: str) -> str:
        """Return past incidents and their fixes for a given failure type."""
        return json.dumps(PAST_FIXES, indent=2)

    @tool
    def get_runbook(failure_type: str) -> str:
        """Return the step-by-step runbook for handling a given type of failure."""
        return json.dumps(RUNBOOKS, indent=2)

    @tool
    def get_team_contacts(service_name: str) -> str:
        """Return the team contacts and on-call information for a given service."""
        return json.dumps(TEAM_CONTACTS, indent=2)

    return [get_past_fixes, get_runbook, get_team_contacts]


# ---------------------------------------------------------------------------
# Historical retrieval tool — Diagnosis Agent only (Remediation reads the
# verified result from state; it never calls a tool for history).
# ---------------------------------------------------------------------------

def make_historical_search_tool(evidence: dict, job_name: str) -> list:
    """One-shot grounded retrieval for the Diagnosis Agent. Closes over the
    CURRENT build's evidence/job_name so the LLM never supplies query
    parameters itself (nothing to hallucinate there). Enforces a single call
    per investigation via a closure-held counter: a second call returns an
    explicit refusal message instead of hitting Mongo again or letting the
    agent loop, independent of whatever iteration limit the agent framework
    itself may or may not enforce."""
    from ..historical_match import get_candidate_incidents

    call_count = {"n": 0}

    @tool
    def search_similar_incidents() -> str:
        """Search for past RESOLVED incidents whose evidence matches this
        build. Returns a JSON array (up to 3) of candidates, each with
        incident_id, job_name, build_number, exact_signature_match,
        resolution_method, and that incident's own diagnosis_report — compare
        its root cause to yours before deciding on a match. Empty array if
        none found. Call this tool AT MOST ONCE."""
        call_count["n"] += 1
        if call_count["n"] > 1:
            return json.dumps({
                "error": "search_similar_incidents was already called once "
                         "for this investigation. Use the candidates already "
                         "returned; do not call this tool again."
            })
        candidates = get_candidate_incidents(evidence, job_name, limit=3)
        return json.dumps(candidates, default=str)

    return [search_similar_incidents]