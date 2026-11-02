"""Incident Advisor: a read-only Q&A agent over past incidents.

Distinct from the Diagnosis/Fix/Remediation agents on purpose — those are
write agents, triggered automatically by a CI failure, that act on one
live incident. This agent is triggered on demand by a human question, and
only reads incidents those other agents already finished writing. It never
re-diagnoses a failure from scratch; it summarizes/quotes the existing
diagnosis_report via retrieval (db.search_incidents_semantic).
"""

from langchain_groq import ChatGroq

from .llm_retry import invoke_with_retry
from ..config import settings
from ..db import search_incidents_semantic

SYSTEM_PROMPT = """You are PipeGuard's Incident Advisor. You answer questions about past CI/CD incidents using ONLY the retrieved incident snippets you are given below — you never re-diagnose a failure from scratch, you summarize and reference what the Diagnosis agent already wrote.

Rules:
- If the retrieved incidents don't contain the answer, say so plainly. Do not guess or invent details.
- When you reference a specific incident, name its job and build number so the engineer can look it up.
- Keep answers short and actionable (a few sentences or a short list), not a full report.
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
            f"Steps:\n{step_lines or '(none)'}"
        )
    return "\n\n---\n\n".join(blocks)


def answer_question(message: str) -> dict:
    """Retrieve relevant past incidents and answer `message` grounded in
    them. Returns {"answer": str, "sources": [{incident_id, job_name,
    build_number, status, created_at}, ...]}."""
    incidents = search_incidents_semantic(message, limit=5)
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
