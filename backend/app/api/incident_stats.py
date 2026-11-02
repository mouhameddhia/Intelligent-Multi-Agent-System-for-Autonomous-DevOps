"""Aggregated KPIs for the Incidents dashboard (frontend/src/IncidentsDashboard.jsx).

PORTABLE MODULE — self-contained on purpose, so this one file can be copied
into another project on its own: its only project dependency is
`agent_system.db.list_incidents` (read-only, best-effort — already degrades
gracefully to an empty list if MongoDB is unreachable or unconfigured, so
this endpoint never needs its own error handling for that case).

Every number here is computed from real stored fields, nothing invented:
- status / decision / feedback / overall_confidence / job_name are the
  actual fields `app/api/investigation.py` writes to each incident document.

Wiring note: this router MUST be included in main.py BEFORE the investigation
router, because FastAPI matches routes in registration order and
`/api/incidents/{incident_id}` (a dynamic path) would otherwise swallow
`/api/incidents/stats` by treating "stats" as an incident id.
"""
from collections import Counter

from fastapi import APIRouter

from agent_system.db import list_incidents

router = APIRouter(prefix="/api/incidents", tags=["incidents-dashboard"])


def _percent(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


@router.get("/stats")
def get_incident_stats(limit: int = 500):
    """KPIs computed over the most recent `limit` incidents (default 500,
    newest first — same ordering as list_incidents). Returns zeroed/None
    values rather than erroring when there are no incidents yet, so the
    dashboard always has something safe to render."""
    incidents = list_incidents(limit=limit)
    total = len(incidents)

    status_counts = Counter(doc.get("status") or "unknown" for doc in incidents)

    decided = [doc for doc in incidents if (doc.get("decision") or {}).get("decision")]
    approved = sum(1 for doc in decided if doc["decision"]["decision"] == "approve")

    with_feedback = [
        doc for doc in incidents
        if isinstance((doc.get("feedback") or {}).get("build_passed"), bool)
    ]
    build_passed = sum(1 for doc in with_feedback if doc["feedback"]["build_passed"])

    confidences = [
        doc.get("overall_confidence") for doc in incidents
        if isinstance(doc.get("overall_confidence"), (int, float))
        and not isinstance(doc.get("overall_confidence"), bool)
    ]

    job_counts = Counter(doc.get("job_name") or "unknown" for doc in incidents)

    return {
        "total": total,
        "status_counts": dict(status_counts),
        "decided_count": len(decided),
        "approved_count": approved,
        "approval_rate": _percent(approved, len(decided)),
        "feedback_count": len(with_feedback),
        "build_passed_count": build_passed,
        "build_passed_rate": _percent(build_passed, len(with_feedback)),
        "avg_confidence": round(sum(confidences) / len(confidences), 1) if confidences else None,
        "top_jobs": [
            {"job_name": name, "count": count}
            for name, count in job_counts.most_common(5)
        ],
        "latest_created_at": incidents[0].get("created_at") if incidents else None,
    }
