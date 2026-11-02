"""Historical incident matching.

get_candidate_incidents() / resolve_historical_match() ARE used by an agent
(Diagnosis, via build_tools.make_historical_search_tool) — retrieval now
happens at runtime inside the investigation graph, not after it.

find_related_historical_incident() and _resolution() below are the original
deterministic, non-agent matcher; kept for compatibility, no longer called by
the API layer."""
import re
from typing import Any

from .agents.evidence_utils import compact_evidence

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
    return {
        "incident_id": str(doc.get("_id") or ""), "job_name": str(doc.get("job_name") or "Unknown job"),
        "build_number": doc.get("build_number"), "resolution_method": method, "actions": actions,
        "build_passed": passed, "feedback_comment": (feedback.get("comment") or "").strip(),
        # Added for Remediation's consumption (not required by the incident
        # schema's historical_incident block, but additionalProperties is
        # unrestricted there, so this stays schema-conformant):
        "resolution_path": path,
        "steps": doc.get("steps") or [],
    }

def _files_touched(evidence: dict[str, Any]) -> list[str]:
    github = (evidence or {}).get("github") or {}
    files = github.get("files") or []
    return [f.get("filename") for f in files if isinstance(f, dict) and f.get("filename")]


def get_candidate_incidents(
    current_evidence: dict[str, Any],
    job_name: str,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Ranked, deduplicated KNOWN-GOOD candidates for the Diagnosis agent to
    judge semantically. Tier A (exact evidence signature, via the same
    _signature() used by find_related_historical_incident) is listed first
    and flagged exact_signature_match=True; Tier B (same job / touched files,
    via db.find_similar_incidents) fills remaining slots. Each candidate
    carries the matched incident's OWN diagnosis_report so the agent can
    compare root-cause mechanism, not just surface signals. Never raises —
    returns [] on any failure, consistent with db.py's best-effort contract."""
    from .db import find_similar_incidents, list_incidents  # local: avoid import-time cycles

    try:
        current_sig = _signature(current_evidence or {})
        candidates: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        if current_sig is not None:
            for doc in list_incidents(limit=500):
                if not isinstance(doc, dict) or _signature(doc.get("evidence") or {}) != current_sig:
                    continue
                summary = _resolution(doc)
                if summary is None or summary["incident_id"] in seen_ids:
                    continue
                candidates.append({
                    "incident_id": summary["incident_id"],
                    "job_name": summary["job_name"],
                    "build_number": summary["build_number"],
                    "exact_signature_match": True,
                    "signature_basis": {
                        "test_id": current_sig[0],
                        "assertion": current_sig[1],
                        "meaning": "Same normalized failed test and assertion; this does not by itself prove the same root cause.",
                    },
                    "resolution_method": summary["resolution_method"],
                    "diagnosis_report": (doc.get("diagnosis_report") or "")[:2000],
                    "evidence": compact_evidence(
                        doc.get("evidence") or {},
                        include_console_log=True,
                        patch_limit=2000,
                        file_list_limit=40,
                    ),
                })
                seen_ids.add(summary["incident_id"])
                if len(candidates) >= limit:
                    return candidates

        if len(candidates) < limit:
            for doc in find_similar_incidents(job_name, _files_touched(current_evidence), limit=limit):
                summary = _resolution(doc)
                if summary is None or summary["incident_id"] in seen_ids:
                    continue
                candidates.append({
                    "incident_id": summary["incident_id"],
                    "job_name": summary["job_name"],
                    "build_number": summary["build_number"],
                    "exact_signature_match": False,
                    "resolution_method": summary["resolution_method"],
                    "diagnosis_report": (doc.get("diagnosis_report") or "")[:2000],
                    "evidence": compact_evidence(
                        doc.get("evidence") or {},
                        include_console_log=True,
                        patch_limit=2000,
                        file_list_limit=40,
                    ),
                })
                seen_ids.add(summary["incident_id"])
                if len(candidates) >= limit:
                    break

        return candidates
    except Exception:
        return []


def resolve_historical_match(
    incident_id: str | None,
    current_evidence: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Given an incident_id the Diagnosis agent CLAIMS matches, re-fetch the
    FULL record from Mongo and reshape it with _resolution(). This is the only
    place an LLM-claimed match becomes a trusted, schema-conformant
    historical_incident — the agent's own paraphrase is never stored or
    passed to Remediation directly.

    If current_evidence is supplied, also recomputes exact_signature_match
    deterministically (same signature logic the candidate search used), so
    downstream consumers — including the judge — can verify a claim like
    "exact_signature_match: true" against something real, instead of that
    detail only existing transiently inside Diagnosis's tool output."""
    if not incident_id:
        return None
    from .db import get_incident  # local: avoid import-time cycles
    try:
        doc = get_incident(incident_id)
        if not isinstance(doc, dict):
            return None
        resolved = _resolution(doc)
        if resolved is None:
            return None
        if current_evidence is not None:
            current_sig = _signature(current_evidence or {})
            matched_sig = _signature(doc.get("evidence") or {})
            resolved["exact_signature_match"] = current_sig is not None and current_sig == matched_sig
            resolved["signature_basis"] = {
                "current": (
                    {"test_id": current_sig[0], "assertion": current_sig[1]}
                    if current_sig else None
                ),
                "historical": (
                    {"test_id": matched_sig[0], "assertion": matched_sig[1]}
                    if matched_sig else None
                ),
                "meaning": "The check compares normalized failed test identifiers and assertion text; it does not prove the same root cause.",
            }
        # Give the judge the historical basis it needs to audit a claimed
        # match. Bound it here so it does not make API responses unbounded.
        resolved["diagnosis_report"] = (doc.get("diagnosis_report") or "")[:4000]
        resolved["evidence"] = compact_evidence(
            doc.get("evidence") or {},
            include_console_log=True,
            patch_limit=4000,
            file_list_limit=40,
        )
        return resolved
    except Exception:
        return None


def find_related_historical_incident(current_evidence: dict[str, Any], incidents: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Newest exact normalized test identifier + assertion match with recorded actions."""
    current = _signature(current_evidence or {})
    if current is None: return None
    for doc in incidents:  # list_incidents supplies newest-first order
        if isinstance(doc, dict) and _signature(doc.get("evidence") or {}) == current:
            summary = _resolution(doc)
            if summary: return summary
    return None
