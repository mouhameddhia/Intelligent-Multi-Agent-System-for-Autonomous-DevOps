"""Incident Advisor: a read-only Q&A agent over past incidents.

Distinct from the Diagnosis/Fix/Remediation agents on purpose — those are
write agents, triggered automatically by a CI failure, that act on one
live incident. This agent is triggered on demand by a human question, and
only reads incidents those other agents already finished writing. It never
re-diagnoses a failure from scratch; it summarizes/quotes the existing
diagnosis_report via retrieval (db.search_incidents_semantic).
"""

from datetime import datetime, timezone

from langchain_groq import ChatGroq

from .llm_retry import invoke_with_retry
from ..config import settings
from ..db import get_incident, list_incidents, search_incidents_semantic

SYSTEM_PROMPT = """You are PipeGuard's Incident Advisor. You answer questions about past CI/CD incidents using ONLY the retrieved incident snippets you are given below — you never re-diagnose a failure from scratch, you summarize and reference what the Diagnosis agent already wrote.

Rules:
- If the retrieved incidents don't contain the answer, say so plainly. Do not guess or invent details.
- When you reference a specific incident, name its job and build number so the engineer can look it up.
- If a follow-up incident is provided, keep answering about that exact incident. Never switch to a more semantically similar incident.
- Base proposed fixes on that incident's remediation report or structured steps. If neither contains a fix, say that the incident has no saved remediation plan.
- Keep answers short and actionable (a few sentences or a short list), not a full report.
- Use plain text. Do not emit Markdown markup, escaped Markdown, or HTML entities.
- You cannot compute aggregate statistics across all incidents (e.g. "which job fails most"). If asked, say that's shown on the Incidents Dashboard instead.
"""


def _format_context(incidents: list[dict]) -> str:
    if not incidents:
        return "No matching past incidents were found."

    blocks = []
    for doc in incidents:
        steps = doc.get("steps") or []
        step_lines = "\n".join(f"- {step.get('text', '')}" for step in steps[:5])
        blocks.append(
            f"Incident {doc.get('_id')} | job={doc.get('job_name')} build=#{doc.get('build_number')} "
            f"status={doc.get('status')} created_at={doc.get('created_at')}\n"
            f"Diagnosis: {(doc.get('diagnosis_report') or '')[:800]}\n"
            f"Remediation: {(doc.get('remediation_report') or '')[:1000]}\n"
            f"Steps:\n{step_lines or '(none)'}"
        )
    return "\n\n---\n\n".join(blocks)


def _is_latest_incident_question(message: str) -> bool:
    text = " ".join(message.lower().replace("'", "").split())
    if "incident" not in text:
        return False
    return any(phrase in text for phrase in ("most recent", "latest", "newest", "last incident"))


def _format_created_at(value) -> str:
    if not value:
        return "date unavailable"
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (TypeError, ValueError):
        return str(value)


def _answer_latest_incident(user_id: str | None) -> dict:
    incidents = (
        list_incidents(limit=1, user_id=user_id)
        if user_id is not None
        else list_incidents(limit=1)
    )
    if not incidents:
        return {"answer": "There are no saved incidents for this account yet.", "sources": []}

    incident = incidents[0]
    job = incident.get("job_name") or "Unknown job"
    build = incident.get("build_number")
    status = (incident.get("status") or "unknown").replace("_", " ")
    incident_id = incident.get("_id")
    build_text = f"build #{build}" if build is not None else "build number unavailable"
    answer = (
        f"The most recent incident is {job}, {build_text} ({status}), "
        f"created at {_format_created_at(incident.get('created_at'))}."
    )
    if incident_id:
        answer += f" Incident ID: {incident_id}."
    source = {
        "incident_id": incident_id,
        "job_name": job,
        "build_number": build,
        "status": incident.get("status"),
        "created_at": incident.get("created_at"),
    }
    return {"answer": answer, "sources": [source]}


def _is_follow_up_reference(message: str) -> bool:
    text = " ".join(message.lower().replace("'", "").split())
    return any(phrase in text for phrase in (
        "this incident", "that incident", "this build", "that build",
        "this failure", "that failure", "fix it", "fix this", "fix that",
        "resolve it", "resolve this", "how was it", "what should i do",
    ))


def answer_question(
    message: str,
    user_id: str | None = None,
    context_incident_id: str | None = None,
) -> dict:
    """Retrieve relevant past incidents and answer `message` grounded in
    them. Returns {"answer": str, "sources": [{incident_id, job_name,
    build_number, status, created_at}, ...]}."""
    if _is_latest_incident_question(message):
        return _answer_latest_incident(user_id)

    contextual_incident = None
    if context_incident_id and _is_follow_up_reference(message):
        contextual_incident = get_incident(context_incident_id, user_id=user_id)
    incidents = (
        [contextual_incident]
        if contextual_incident
        else search_incidents_semantic(message, limit=5, user_id=user_id)
    )
    context = _format_context(incidents)

    llm = ChatGroq(
        model=settings.llm_model,
        api_key=settings.groq_api_key,
        temperature=0,
        max_tokens=800,
    )
    result = invoke_with_retry(lambda: llm.invoke([
        ("system", SYSTEM_PROMPT),
        ("human", f"Retrieved past incidents:\n\n{context}\n\nQuestion: {message}"),
    ]))
    answer = result.content if isinstance(result.content, str) else str(result.content)

    sources = [
        {
            "incident_id": doc.get("_id"),
            "job_name": doc.get("job_name"),
            "build_number": doc.get("build_number"),
            "status": doc.get("status"),
            "created_at": doc.get("created_at"),
        }
        for doc in incidents
    ]
    return {"answer": answer, "sources": sources}
