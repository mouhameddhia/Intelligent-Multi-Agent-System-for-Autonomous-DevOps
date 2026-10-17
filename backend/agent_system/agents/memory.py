"""Experience retrieval: surfaces similar past incidents as bounded, text-only
context for Diagnosis and Remediation. This is build-order item #3 from the
target architecture (the "Experience store" read, before the step ranker and
LLM judge exist).

Only incidents with a KNOWN-GOOD outcome are ever surfaced (a step marked
solved, a custom_solution, or an applied auto-fix) — a denied or unresolved
incident teaches nothing and would just add noise. See db.find_similar_incidents
for the tiered query (same job first, same touched file as fallback).

Each match is condensed to a few bounded fields before it ever reaches a
prompt, so token cost stays flat no matter how verbose a past report was.
format_memory_for_prompt() is the single shared formatter used by both
Diagnosis and Remediation, so they describe history identically.
"""

from typing import Any

from ..db import find_similar_incidents
from ..orchestrator.state import InvestigationState

MAX_MEMORY_ITEMS = 3
EXCERPT_LENGTH = 220


def _root_cause_excerpt(diagnosis_report: str) -> str:
    text = (diagnosis_report or "").strip()
    lower = text.lower()
    idx = lower.find("root cause")
    excerpt = text[idx:] if idx != -1 else text
    return excerpt.strip()[:EXCERPT_LENGTH].strip()


def _what_worked(doc: dict) -> str | None:
    solved_id = doc.get("solved_step_id")
    if solved_id:
        for step in doc.get("steps") or []:
            if step.get("id") == solved_id:
                text = step.get("text")
                if text:
                    return text
    custom = (doc.get("custom_solution") or "").strip()
    if custom:
        return custom
    fix_result = doc.get("fix_result") or {}
    if fix_result.get("applied"):
        return f"PipeGuard auto-fixed {fix_result.get('file', 'the responsible file')}."
    return None


def _condense(doc: dict) -> dict[str, Any] | None:
    worked = _what_worked(doc)
    if not worked:
        return None
    feedback = doc.get("feedback") or {}
    build_passed = feedback.get("build_passed")
    if build_passed is None:
        build_passed = doc.get("status") == "approved_fixed"
    return {
        "job_name": doc.get("job_name", ""),
        "build_number": doc.get("build_number"),
        "root_cause_excerpt": _root_cause_excerpt(doc.get("diagnosis_report", "")),
        "what_worked": worked,
        "build_passed": bool(build_passed),
    }


def retrieve_memory_context(job_name: str, files_touched: list[str], limit: int = MAX_MEMORY_ITEMS) -> list[dict]:
    raw_docs = find_similar_incidents(job_name=job_name, files_touched=files_touched, limit=limit)
    condensed = [item for item in (_condense(doc) for doc in raw_docs) if item]
    return condensed[:limit]


def run_retrieve_memory(state: InvestigationState) -> InvestigationState:
    evidence = state.get("evidence") or {}
    github = evidence.get("github") or {}
    files_touched = [
        f.get("filename")
        for f in (github.get("files") or [])
        if isinstance(f, dict) and f.get("filename")
    ]

    memory_context = retrieve_memory_context(state.get("job_name", ""), files_touched)
    return {**state, "memory_context": memory_context}


def format_memory_for_prompt(memory_context: list[dict]) -> str:
    """Single shared formatter — Diagnosis and Remediation must describe past
    incidents identically, so this is the only place that renders them."""
    if not memory_context:
        return "No similar past incidents with a confirmed fix were found yet."

    lines = []
    for i, item in enumerate(memory_context, start=1):
        lines.append(
            f"{i}. Job {item['job_name']} build #{item.get('build_number', '?')} "
            f"(build passed after the fix: {item['build_passed']}). "
            f"Root cause then: {item['root_cause_excerpt']} "
            f"What actually worked: {item['what_worked']}"
        )
    return "\n".join(lines)
