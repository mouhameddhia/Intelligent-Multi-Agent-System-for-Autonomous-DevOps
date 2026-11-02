"""Builds a LearningRecord when an incident is closed (a decision was made or
feedback was received) and stores it. Best-effort end to end: nothing here may
raise into, or slow down, an investigation route."""

import logging
import threading
from typing import Any, Callable

from .classify import (
    classify_failure,
    classify_fix_action,
    failure_signature,
    files_touched,
)
from .models import EngineerFeedback, HumanDecision, LearningRecord, utc_now
from .store import RecordStore, get_store

logger = logging.getLogger(__name__)

SUMMARY_LENGTH = 400


def _summary(diagnosis_report: str) -> str:
    text = (diagnosis_report or "").strip()
    idx = text.lower().find("root cause")
    return (text[idx:] if idx != -1 else text)[:SUMMARY_LENGTH].strip()


def is_closed(doc: dict[str, Any]) -> bool:
    decision = doc.get("decision")
    has_decision = isinstance(decision, dict) and decision.get("decision") in ("approve", "deny")
    return has_decision or isinstance(doc.get("feedback"), dict)


def _rebuild_result(feedback: dict | None) -> str:
    passed = (feedback or {}).get("build_passed")
    if not isinstance(passed, bool):
        return "unknown"
    return "pass" if passed else "fail"


def build_record(doc: dict[str, Any]) -> LearningRecord:
    """Pure transformation of an `incidents` document into a LearningRecord."""
    steps = [s.get("text", "") for s in (doc.get("steps") or []) if isinstance(s, dict) and s.get("text")]
    fix_result = doc.get("fix_result") if isinstance(doc.get("fix_result"), dict) else None
    decision_doc = doc.get("decision") if isinstance(doc.get("decision"), dict) else {}
    feedback = doc.get("feedback") if isinstance(doc.get("feedback"), dict) else None

    decision = decision_doc.get("decision")
    if decision == "deny":
        fix_applied: bool | None = False
    elif fix_result is not None:
        fix_applied = bool(fix_result.get("applied"))
    else:
        fix_applied = None

    incident_type = doc.get("incident_type") or ""
    category = classify_failure(doc.get("diagnosis_report", ""), incident_type)
    signature = failure_signature(category, incident_type, files_touched(doc.get("evidence")))

    return LearningRecord(
        incident_id=str(doc["_id"]),
        failure_signature=signature,
        failure_category=category,
        diagnosis_summary=_summary(doc.get("diagnosis_report", "")),
        proposed_steps=steps,
        fix_action_type=classify_fix_action(steps, fix_result),
        human_decision=HumanDecision(
            decision=decision if decision in ("approve", "deny") else None,
            reason=str(decision_doc.get("reason") or ""),
            approver=str(decision_doc.get("approver") or ""),
            decided_at=decision_doc.get("decided_at"),
        ),
        fix_applied=fix_applied,
        rebuild_result=_rebuild_result(feedback),  # type: ignore[arg-type]
        engineer_feedback=(
            EngineerFeedback(
                build_passed=feedback.get("build_passed") if isinstance(feedback.get("build_passed"), bool) else None,
                liked_fix=feedback.get("liked_fix"),
                system_efficient=feedback.get("system_efficient"),
                comment=str(feedback.get("comment") or ""),
                resolution_path=str(doc.get("resolution_path") or ""),
                submitted_at=feedback.get("submitted_at"),
            )
            if feedback
            else None
        ),
        judge_scores=doc.get("judge_scores") if isinstance(doc.get("judge_scores"), dict) else {},
        policy_version_used=doc.get("policy_version_used") if isinstance(doc.get("policy_version_used"), int) else None,
        scope=str(doc.get("user_id") or "global"),
    )


def record_incident_closed(
    incident_id: str,
    *,
    user_id: str | None = None,
    store: RecordStore | None = None,
    loader: Callable[[str], dict | None] | None = None,
) -> LearningRecord | None:
    """Load the incident, and if it is closed, upsert its LearningRecord.
    Returns the record, or None if nothing was recorded. Never raises."""
    try:
        if loader is None:
            from ..db import get_incident as loader
            doc = loader(incident_id, user_id=user_id)
        else:
            doc = loader(incident_id)
        if not isinstance(doc, dict) or not is_closed(doc):
            return None
        if doc.get("personalization_enabled") is not True:
            return None
        if user_id is not None and str(doc.get("user_id")) != user_id:
            return None
        record = build_record(doc)
        record.updated_at = utc_now()
        # `evaluation` is owned by the evaluator; leaving it out of the upsert
        # keeps a stored evaluation intact when feedback re-records the incident.
        (store or get_store()).upsert(record.model_dump(mode="json", exclude={"evaluation"}))
        return record
    except Exception:
        logger.warning("learning record failed: incident_id=%s", incident_id, exc_info=True)
        return None


def _run(incident_id: str, user_id: str) -> None:
    try:
        record = record_incident_closed(incident_id, user_id=user_id)
        if record is not None:
            from .trigger import on_incident_closed  # local: trigger imports the learner stack

            on_incident_closed(record.scope)
    except Exception:
        logger.warning("learning record worker failed: incident_id=%s", incident_id, exc_info=True)


def _run_many(incident_ids: list[str], user_id: str) -> None:
    """Record a bounded opt-in batch, then trigger learning once.

    One thread per incident can race the process-wide learning lock: the first
    worker may observe a partial batch while the others return busy, leaving
    eligible records without a future trigger.
    """
    try:
        scope = None
        for incident_id in incident_ids:
            record = record_incident_closed(incident_id, user_id=user_id)
            if record is not None:
                scope = record.scope
        if scope is not None:
            from .trigger import on_incident_closed

            on_incident_closed(scope)
    except Exception:
        logger.warning("learning batch worker failed", exc_info=True)


def schedule_record(incident_id: str, user_id: str) -> threading.Thread | None:
    """Fire-and-forget version used by the API routes, so recording can never
    add latency to an investigation response. Never raises."""
    try:
        thread = threading.Thread(target=_run, args=(incident_id, user_id), daemon=True)
        thread.start()
        return thread
    except Exception:
        return None


def schedule_records(incident_ids: list[str], user_id: str) -> threading.Thread | None:
    """Process newly enrolled incidents in a single worker."""
    if not incident_ids:
        return None
    try:
        thread = threading.Thread(target=_run_many, args=(list(incident_ids), user_id), daemon=True)
        thread.start()
        return thread
    except Exception:
        return None
