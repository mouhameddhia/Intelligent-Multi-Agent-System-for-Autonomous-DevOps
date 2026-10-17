"""Assemble agent Markdown into one report. Historical matching happens after the graph finishes; historical data is never passed to agents or prompts."""

from typing import Any


def _steps_as_markdown(steps: list[dict]) -> str:
    if not steps:
        return "_No structured steps were extracted for this incident._"
    lines = []
    for step in steps:
        marker = "⚠️ " if step.get("disruptive") else ""
        lines.append(f"{step.get('order_shown', '?')}. {marker}{step.get('text', '')}")
    return "\n".join(lines)


def _historical_as_markdown(item: dict[str, Any]) -> str:
    lines = [
        f"**Previous incident:** {item.get('job_name', 'Unknown job')} build #{item.get('build_number', '?')}",
        f"**Recorded resolution method:** {item.get('resolution_method', 'Not recorded')}",
        "", "| Recorded action |", "|---|",
    ]
    for action in item.get("actions") or []:
        value = str(action.get("text") or "").replace("\n", "<br>").replace("|", "\\|")
        if action.get("solved"):
            value = "**Recorded as the step that solved it:** " + value
        lines.append(f"| {value} |")
    passed = item.get("build_passed")
    if passed is True: lines.extend(["", "**Recorded verification:** The build passed after the resolution."])
    elif passed is False: lines.extend(["", "**Recorded verification:** The build did not pass after the resolution."])
    comment = (item.get("feedback_comment") or "").strip()
    if comment: lines.extend(["", f"**Recorded feedback:** {comment.replace('|', '\\|')}"])
    return "\n".join(lines)


def build_unified_report(result: dict[str, Any]) -> str:
    """result: the graph's final state dict (or any dict with the same keys) —
    monitoring_report, diagnosis_report, remediation_report, steps, memory_context."""
    monitoring = (result.get("monitoring_report") or "").strip()
    diagnosis = (result.get("diagnosis_report") or "").strip()
    remediation = (result.get("remediation_report") or "").strip()
    steps = result.get("steps") or []
    historical_incident = result.get("historical_incident")
    confidence_values = [result.get("monitoring_confidence"), result.get("diagnosis_confidence"), result.get("remediation_confidence")]
    overall_confidence = round(sum(confidence_values) / 3) if all(isinstance(v, (int, float)) and 0 <= v <= 100 for v in confidence_values) else None

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
            "## Related historical incident (deterministic match)",
            _historical_as_markdown(historical_incident),
        ])
    sections.extend(["", "---", "", "## Overall agent confidence estimate", ""])
    if overall_confidence is None:
        sections.append("Overall confidence is unavailable because one or more agents did not return a valid estimate.")
    else:
        sections.append(f"**{overall_confidence}%** — arithmetic mean of Monitoring ({confidence_values[0]}%), Diagnosis ({confidence_values[1]}%), and Remediation ({confidence_values[2]}%). This is an agent estimate, not a calibrated probability.")
    sections.extend(["", "**Human approval required.** Review the evidence and proposed steps. Choose **Approve & fix** to let PipeGuard attempt the automated fix, or **Deny** to make no automated changes and handle the incident manually."])
    return "\n".join(sections).strip()
