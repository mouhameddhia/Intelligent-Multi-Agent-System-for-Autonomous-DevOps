"""Assemble agent Markdown into one report. Historical matching is performed
by the Diagnosis agent at runtime (see agents/diagnosis.py + historical_match
.resolve_historical_match) and verified server-side before reaching here —
this module only renders the final result, it does not compute the match."""
from typing import Any

from .confidence import summarize_confidence, judge_overall_score
def _steps_as_markdown(steps: list[dict]) -> str:
    if not steps:
        return "_No structured steps were extracted for this incident._"
    lines = []
    for step in steps:
        marker = "⚠️ " if step.get("disruptive") else ""
        lines.append(f"{step.get('order_shown', '?')}. {marker}{step.get('text', '')}")
    return "\n".join(lines)

def _judge_as_markdown(state: dict[str, Any]) -> str:
    scores = state.get("judge_scores") or {}
    hist_quality = state.get("judge_historical_match_quality")
    hallucination = state.get("judge_hallucination_detected")
    issues = state.get("judge_issues") or []
    verdict = (state.get("judge_verdict") or "").strip()
    overall = judge_overall_score(scores)

    lines = []
    if overall is not None:
        clean = hallucination is False and not issues
        headline = "✅ Looks solid — no hallucination, claims check out against the evidence." if clean \
            else "⚠️ Worth a second look before approving." if (hallucination or issues) else ""
        lines.append(f"**Judge overall: {overall}%**" + (f" — {headline}" if headline else ""))
    score_parts = [f"{name.capitalize()} ({score}%)" for name, score in scores.items() if score is not None]
    if score_parts:
        lines.append(f"**Per-agent scores:** {', '.join(score_parts)}")
    if hist_quality is not None:
        lines.append(f"**Historical match quality:** {hist_quality}%")
    if hallucination is True:
        lines.append("**⚠ Hallucination or unsupported claims detected.**")
    elif hallucination is False:
        lines.append("**✅ No hallucination detected.**")
    if issues:
        lines.append("")
        lines.append("**Flagged issues:**")
        lines.extend(f"- {issue}" for issue in issues)
    if verdict:
        lines.extend(["", verdict])
    return "\n".join(lines) if lines else "_Judge verdict unavailable for this incident._"
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
    sections.extend([
        "",
        "## Independent audit (LLM judge)",
        _judge_as_markdown(result),
    ])
    sections.extend(["", "---", "", "## Overall agent confidence estimate", ""])
    if overall_confidence is None:
        sections.append("Overall confidence is unavailable because no agent returned a valid estimate.")
    else:
        parts = [f"{name.capitalize()} ({score}%)" for name, score in agent_scores.items() if score is not None]
        missing = [name.capitalize() for name, score in agent_scores.items() if score is None]
        judge_part = judge_overall_score(result.get("judge_scores") or {})
        if judge_part is not None:
            parts.append(f"Judge ({judge_part}%)")
        elif "judge" not in [p.lower() for p in missing]:
            missing.append("Judge")
        note = f" Not included (no valid estimate): {', '.join(missing)}." if missing else ""
        sections.append(f"**{overall_confidence}%** — arithmetic mean of {', '.join(parts)}.{note} This is an agent estimate, not a calibrated probability.")
    sections.extend(["", "**Human approval required.** Review the evidence and proposed steps. Choose **Approve & fix** to let PipeGuard attempt the automated fix, or **Deny** to make no automated changes and handle the incident manually."])
    return "\n".join(sections).strip()
