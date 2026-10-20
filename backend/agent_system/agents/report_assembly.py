"""Assemble agent Markdown into one report. Historical matching is performed
by the Diagnosis agent at runtime (see agents/diagnosis.py + historical_match
.resolve_historical_match) and verified server-side before reaching here —
this module only renders the final result, it does not compute the match."""
from typing import Any

from .confidence import summarize_confidence

def _steps_as_markdown(steps: list[dict]) -> str:
    if not steps:
        return "_No structured steps were extracted for this incident._"
    lines = []
    for step in steps:
        marker = "⚠️ " if step.get("disruptive") else ""
        lines.append(f"{step.get('order_shown', '?')}. {marker}{step.get('text', '')}")
    return "\n".join(lines)


def _historical_as_markdown(item: dict[str, Any]) -> str:
    """Short footnote only — Diagnosis's Historical confirmation and
    Remediation's Recommended action already explain the match and reused
    steps inline. This just cites the record and the factual verification
    outcome, which the agents are never given to restate."""
    lines = [
        f"Diagnosis confirmed a root-cause match with "
        f"**{item.get('job_name', 'Unknown job')} build #{item.get('build_number', '?')}** "
        f"(incident `{item.get('incident_id', '')}`), "
        f"recorded resolution: {item.get('resolution_method', 'Not recorded')}.",
    ]
    passed = item.get("build_passed")
    if passed is True:
        lines.append("Recorded verification: the build passed after that resolution.")
    elif passed is False:
        lines.append("Recorded verification: the build did not pass after that resolution.")
    comment = (item.get("feedback_comment") or "").strip()
    if comment:
        lines.append(f"Recorded feedback: {comment}")
    return " ".join(lines)
def build_unified_report(result: dict[str, Any]) -> str:
    """result: the graph's final state dict (or any dict with the same keys) —
    monitoring_report, diagnosis_report, remediation_report, steps, memory_context."""
    monitoring = (result.get("monitoring_report") or "").strip()
    diagnosis = (result.get("diagnosis_report") or "").strip()
    remediation = (result.get("remediation_report") or "").strip()
    steps = result.get("steps") or []
    historical_incident = result.get("historical_incident")
    agent_scores, overall_confidence = summarize_confidence(result)
    sections = [
        "## Monitoring — what happened",
        monitoring or "_No monitoring data available._",
        "",
        "## Diagnosis — why it happened",
        diagnosis or "_No diagnosis available._",
        "",
        "## Remediation — what to do",
        remediation or "_No remediation proposal available._",
        "",
        "### Proposed steps",
        _steps_as_markdown(steps),
        "",
    ]
    if historical_incident:
        sections.extend([
            "",
            "## Related historical incident (confirmed by Diagnosis)",
            _historical_as_markdown(historical_incident),
        ])
    sections.extend(["", "---", "", "## Overall agent confidence estimate", ""])
    if overall_confidence is None:
        sections.append("Overall confidence is unavailable because no agent returned a valid estimate.")
    else:
        parts = [f"{name.capitalize()} ({score}%)" for name, score in agent_scores.items() if score is not None]
        missing = [name.capitalize() for name, score in agent_scores.items() if score is None]
        note = f" Not included (no valid estimate): {', '.join(missing)}." if missing else ""
        sections.append(f"**{overall_confidence}%** — arithmetic mean of {', '.join(parts)}.{note} This is an agent estimate, not a calibrated probability.")
    sections.extend(["", "**Human approval required.** Review the evidence and proposed steps. Choose **Approve & fix** to let PipeGuard attempt the automated fix, or **Deny** to make no automated changes and handle the incident manually."])
    return "\n".join(sections).strip()
