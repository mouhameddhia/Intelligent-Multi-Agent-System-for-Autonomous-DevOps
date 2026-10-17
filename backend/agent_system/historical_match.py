"""Application-only deterministic historical matcher; never imported by agents."""
import re
from typing import Any

def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())

def _log(evidence: dict[str, Any]) -> str:
    jenkins = evidence.get("jenkins") or {}
    if isinstance(jenkins, dict) and jenkins.get("console_log"):
        return str(jenkins["console_log"])
    return str(evidence.get("console_log") or "")

def _signature(evidence: dict[str, Any]) -> tuple[str, str] | None:
    log = _log(evidence or {})
    if not log.strip(): return None
    test_id = ""
    for line in log.splitlines():
        m = re.search(r"\b(?:FAILED|ERROR)\s+([^\s]+)", line, re.I)
        if m:
            target = m.group(1).rstrip(":,")
            test_id = target.split("::")[-1] if "::" in target else target
            break
    if not test_id:
        m = re.search(r"\b(test_[A-Za-z0-9_]+)\b", log)
        if m: test_id = m.group(1)
    assertion = ""
    for line in log.splitlines():
        candidate = re.sub(r"^(?:E\s+)+", "", line.strip())
        # Recognize unittest calls such as self.assertEqual(...).
        m = re.search(r"\b(?:self\.)?(assert[A-Za-z0-9_]*\s*\([^\n]*\))", candidate, re.I)
        if m:
            assertion = re.sub(r"\s+", " ", m.group(1).strip())
            break
        m = re.search(r"\bassert\s+(.+)$", candidate, re.I)
        if m:
            assertion = "assert " + m.group(1).strip()
            break
    if not test_id or not assertion: return None
    return _norm(test_id), _norm(assertion)

def _resolution(doc: dict[str, Any]) -> dict[str, Any] | None:
    path, fix = doc.get("resolution_path"), doc.get("fix_result") or {}
    actions = []
    if path == "manual":
        solved_id = doc.get("solved_step_id")
        for step in doc.get("steps") or []:
            if step.get("followed") is True and step.get("text"):
                actions.append({"text": step["text"], "solved": step.get("id") == solved_id})
        custom = (doc.get("custom_solution") or "").strip()
        if custom: actions.append({"text": custom, "solved": solved_id is None})
        if not actions: return None
        method = "Manual"
    elif fix.get("applied") is True:
        details = [f"{label}: {fix[key]}" for key, label in (("message", "Recorded result"), ("file", "File"), ("commit_url", "Commit")) if fix.get(key)]
        actions.append({"text": "\n".join(details) if details else "PipeGuard recorded that it applied the fix.", "solved": False})
        method = "Automatic (PipeGuard)"
    else: return None
    feedback = doc.get("feedback") or {}
    passed = feedback.get("build_passed")
    if not isinstance(passed, bool): passed = None
    return {"incident_id": str(doc.get("_id") or ""), "job_name": str(doc.get("job_name") or "Unknown job"),
            "build_number": doc.get("build_number"), "resolution_method": method, "actions": actions,
            "build_passed": passed, "feedback_comment": (feedback.get("comment") or "").strip()}

def find_related_historical_incident(current_evidence: dict[str, Any], incidents: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Newest exact normalized test identifier + assertion match with recorded actions."""
    current = _signature(current_evidence or {})
    if current is None: return None
    for doc in incidents:  # list_incidents supplies newest-first order
        if isinstance(doc, dict) and _signature(doc.get("evidence") or {}) == current:
            summary = _resolution(doc)
            if summary: return summary
    return None
