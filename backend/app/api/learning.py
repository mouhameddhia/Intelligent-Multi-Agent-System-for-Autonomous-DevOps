import logging
from typing import Any, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pydantic import BaseModel

from agent_system.config import settings
from agent_system.learning.evaluator import included_in_learning
from agent_system.learning.metrics import compute_metrics
from agent_system.learning.models import PolicyVersion
from agent_system.learning.registry import NoPreviousVersion, get_registry
from agent_system.learning.render import render_guidance
from agent_system.learning.store import get_store
from agent_system.learning.trigger import is_running, run_learning_pipeline
from agent_system.learning.recorder import is_closed, schedule_records
from agent_system.db import DatabaseUnavailableError, list_incidents, update_incident
from app.api.auth import require_user

router = APIRouter(prefix="/api/learning", tags=["learning"])
logger = logging.getLogger(__name__)

INITIAL_HISTORY_BATCH = 5
OPT_IN_INCIDENT_FIELDS = {
    "incident_type": 1,
    "diagnosis_report": 1,
    "evidence.github.files.filename": 1,
    "steps": 1,
    "decision": 1,
    "fix_result": 1,
    "feedback": 1,
    "judge_scores": 1,
    "policy_version_used": 1,
    "resolution_path": 1,
    "personalization_enabled": 1,
    "created_at": 1,
}


def _read(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Storage reads raise on real errors; surface those as 503, not a crash."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        logger.exception("learning storage read failed")
        raise HTTPException(status_code=503, detail=f"Learning storage unavailable: {exc}") from exc


def _summary(version: PolicyVersion) -> dict[str, Any]:
    counts = {"active": 0, "candidate": 0, "retired": 0}
    for rule in version.rules:
        counts[rule.status] += 1
    return {
        "version": version.version,
        "scope": version.scope,
        "status": version.status,
        "created_at": version.created_at,
        "change_summary": version.change_summary,
        "rule_counts": counts,
        "source_record_count": len(version.source_record_ids),
        "prior_keys": len(version.priors),
    }


@router.get("/policy")
def get_policy(failure_category: str | None = None, user: dict = Depends(require_user)):
    """The active policy. With ?failure_category=..., also the exact section
    Remediation would receive for that category."""
    scope = str(user["_id"])
    active = _read(get_registry().get_active, scope)
    body: dict[str, Any] = {
        "learning_enabled": settings.learning_enabled,
        "scope": "user",
        "policy": active.model_dump(mode="json") if active else None,
    }
    if failure_category:
        body["rendered_guidance"] = render_guidance(active, failure_category)
    return body


@router.get("/versions")
def get_versions(include_rules: bool = False, user: dict = Depends(require_user)):
    """All policy versions, newest first. Rules are omitted unless asked for."""
    scope = str(user["_id"])
    versions = sorted(_read(get_registry().list_versions, scope), key=lambda v: v.version, reverse=True)
    return {
        "count": len(versions),
        "versions": [
            {**_summary(v), **({"rules": [r.model_dump(mode="json") for r in v.rules]} if include_rules else {})}
            for v in versions
        ],
    }


@router.get("/status")
def get_learning_status(user: dict = Depends(require_user)):
    """Explain whether an account policy can be used or what remains pending."""
    scope = str(user["_id"])
    records = _read(get_store().list, scope)
    consumed = _read(get_registry().consumed_ids, scope)
    eligible = [r for r in records if included_in_learning(r.get("evaluation"))]
    waiting = [r for r in eligible if r["incident_id"] not in consumed]
    active = _read(get_registry().get_active, scope)
    return {
        "learning_enabled": settings.learning_enabled,
        "running": is_running(),
        "batch_size": settings.learning_batch_size,
        "records": len(records),
        "evaluated_with_outcome": len(eligible),
        "awaiting_learning": len(waiting),
        "active_policy_version": active.version if active else None,
        "active_rule_count": sum(1 for rule in active.rules if rule.status == "active") if active else 0,
        "ready_to_learn": settings.learning_enabled and len(waiting) >= settings.learning_batch_size,
    }


class RunRequest(BaseModel):
    force: bool = True  # learn from whatever is waiting, even below the batch size
    wait: bool = False  # run inline and return the result instead of starting in the background


@router.post("/opt-in")
def opt_in_personalization(user: dict = Depends(require_user)):
    """Enroll up to five of this account's most recent closed incidents.

    Called only after the user accepts the frontend's data-use notice. The
    incident records are marked opted-in before asynchronous processing starts.
    """
    scope = str(user["_id"])
    recent = _read(
        list_incidents,
        limit=200,
        user_id=scope,
        projection=OPT_IN_INCIDENT_FIELDS,
    )
    closed = [incident for incident in recent if is_closed(incident)][:INITIAL_HISTORY_BATCH]
    enrolled = []
    for incident in closed:
        if incident.get("personalization_enabled") is True:
            continue
        try:
            updated = update_incident(
                str(incident["_id"]), scope, {"personalization_enabled": True}
            )
        except DatabaseUnavailableError as exc:
            raise HTTPException(status_code=503, detail="Incident storage is unavailable.") from exc
        if updated:
            enrolled.append(str(incident["_id"]))
    schedule_records(enrolled, scope)
    return {"scope": "user", "initial_records_enrolled": len(enrolled), "batch_limit": INITIAL_HISTORY_BATCH}


@router.post("/run")
def run_learning(
    response: Response,
    background_tasks: BackgroundTasks,
    body: RunRequest | None = None,
    user: dict = Depends(require_user),
):
    """Evaluate pending records and learn now. Runs in the background by
    default (202); pass wait=true to block and get the run result."""
    body = body or RunRequest()
    scope = str(user["_id"])
    if is_running():
        raise HTTPException(status_code=409, detail="A learning run is already in progress.")
    if body.wait:
        return run_learning_pipeline(scope=scope, force=body.force).model_dump(mode="json")
    background_tasks.add_task(run_learning_pipeline, scope=scope, force=body.force)
    response.status_code = 202
    return {"status": "started", "force": body.force}


@router.post("/rollback")
def rollback_policy(user: dict = Depends(require_user)):
    """Make the previous policy version active again."""
    try:
        restored = get_registry().rollback(str(user["_id"]))
    except NoPreviousVersion as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("policy rollback failed")
        raise HTTPException(status_code=503, detail=f"Learning storage unavailable: {exc}") from exc
    return {"restored_version": restored.version, "status": restored.status}


@router.get("/metrics")
def get_metrics(user: dict = Depends(require_user)):
    """Approval, rebuild-pass and first-fix success rates, per policy version."""
    scope = str(user["_id"])
    records = _read(get_store().list, scope)
    consumed = _read(get_registry().consumed_ids, scope)
    evaluated = [r for r in records if isinstance(r.get("evaluation"), dict)]
    learnable = [r for r in evaluated if included_in_learning(r["evaluation"])]
    return {
        "scope": "user",
        "records": {
            "total": len(records),
            "evaluated": len(evaluated),
            "excluded_unknown_outcome": len(evaluated) - len(learnable),
            "awaiting_learning": sum(1 for r in learnable if r["incident_id"] not in consumed),
        },
        **compute_metrics(records),
    }
