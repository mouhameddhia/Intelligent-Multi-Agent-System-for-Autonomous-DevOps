import logging
from typing import Any, Callable

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from pydantic import BaseModel

from agent_system.config import settings
from agent_system.learning.evaluator import included_in_learning
from agent_system.learning.metrics import compute_metrics
from agent_system.learning.models import GLOBAL_SCOPE, PolicyVersion
from agent_system.learning.registry import NoPreviousVersion, get_registry
from agent_system.learning.render import render_guidance
from agent_system.learning.store import get_store
from agent_system.learning.trigger import is_running, run_learning_pipeline

router = APIRouter(prefix="/api/learning", tags=["learning"])
logger = logging.getLogger(__name__)


def _read(fn: Callable[..., Any], *args: Any) -> Any:
    """Storage reads raise on real errors; surface those as 503, not a crash."""
    try:
        return fn(*args)
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
def get_policy(failure_category: str | None = None):
    """The active policy. With ?failure_category=..., also the exact section
    Remediation would receive for that category."""
    active = _read(get_registry().get_active, GLOBAL_SCOPE)
    body: dict[str, Any] = {
        "learning_enabled": settings.learning_enabled,
        "policy": active.model_dump(mode="json") if active else None,
    }
    if failure_category:
        body["rendered_guidance"] = render_guidance(active, failure_category)
    return body


@router.get("/versions")
def get_versions(include_rules: bool = False):
    """All policy versions, newest first. Rules are omitted unless asked for."""
    versions = sorted(_read(get_registry().list_versions, GLOBAL_SCOPE), key=lambda v: v.version, reverse=True)
    return {
        "count": len(versions),
        "versions": [
            {**_summary(v), **({"rules": [r.model_dump(mode="json") for r in v.rules]} if include_rules else {})}
            for v in versions
        ],
    }


class RunRequest(BaseModel):
    force: bool = True  # learn from whatever is waiting, even below the batch size
    wait: bool = False  # run inline and return the result instead of starting in the background


@router.post("/run")
def run_learning(response: Response, background_tasks: BackgroundTasks, body: RunRequest | None = None):
    """Evaluate pending records and learn now. Runs in the background by
    default (202); pass wait=true to block and get the run result."""
    body = body or RunRequest()
    if is_running():
        raise HTTPException(status_code=409, detail="A learning run is already in progress.")
    if body.wait:
        return run_learning_pipeline(force=body.force).model_dump(mode="json")
    background_tasks.add_task(run_learning_pipeline, force=body.force)
    response.status_code = 202
    return {"status": "started", "force": body.force}


@router.post("/rollback")
def rollback_policy():
    """Make the previous policy version active again."""
    try:
        restored = get_registry().rollback(GLOBAL_SCOPE)
    except NoPreviousVersion as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("policy rollback failed")
        raise HTTPException(status_code=503, detail=f"Learning storage unavailable: {exc}") from exc
    return {"restored_version": restored.version, "status": restored.status}


@router.get("/metrics")
def get_metrics():
    """Approval, rebuild-pass and first-fix success rates, per policy version."""
    records = _read(get_store().list, GLOBAL_SCOPE)
    consumed = _read(get_registry().consumed_ids, GLOBAL_SCOPE)
    evaluated = [r for r in records if isinstance(r.get("evaluation"), dict)]
    learnable = [r for r in evaluated if included_in_learning(r["evaluation"])]
    return {
        "scope": GLOBAL_SCOPE,
        "records": {
            "total": len(records),
            "evaluated": len(evaluated),
            "excluded_unknown_outcome": len(evaluated) - len(learnable),
            "awaiting_learning": sum(1 for r in learnable if r["incident_id"] not in consumed),
        },
        **compute_metrics(records),
    }
