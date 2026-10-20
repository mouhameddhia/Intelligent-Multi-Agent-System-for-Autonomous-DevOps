
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Literal
import faulthandler
import os
import sys
import threading
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from langgraph.types import Command

from agent_system.orchestrator.graph import investigation_graph
from agent_system.db import save_incident, get_incident, list_incidents
from agent_system.agents.report_assembly import build_unified_report
from agent_system.agents.confidence import summarize_confidence


router = APIRouter(tags=["investigation"])
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()



class InvestigateRequest(BaseModel):
    job_name: str
    build_number: int
    jenkins_url: str = ""
    jenkins_username: str = ""
    jenkins_api_token: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    github_token: str = ""
    github_owner: str = ""
    github_repo: str = ""
    engineer_id: str = "devops-engineer"
    source: str = "jenkins_build"
    incident_type: str = ""


class InvestigateResponse(BaseModel):
    incident_id: str
    job_name: str
    build_number: int
    status: str
    monitoring_report: str
    diagnosis_report: str
    remediation_report: str
    unified_report: str
    historical_incident: dict[str, Any] | None = None
    agent_confidence: dict[str, int | None] = Field(default_factory=dict)
    overall_confidence: int | None = None
    confidence_source: dict[str, str] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    engineer_id: str = ""
    source: str = ""


class DecisionRequest(BaseModel):
    incident_id: str
    decision: Literal["approve", "deny"]
    approver: str = "devops-engineer"


class DecisionResponse(BaseModel):
    incident_id: str
    status: str
    fix_result: dict[str, Any] | None = None
    message: str = ""



@router.post("/api/investigate", response_model=InvestigateResponse)
def investigate(request: InvestigateRequest, http_request: Request):
    """Run Monitoring -> Diagnosis -> Remediation, then pause for approval."""

    incident_id = str(uuid.uuid4())

    initial_state = {
        "incident_id": incident_id,
        "job_name": request.job_name,
        "build_number": request.build_number,
        "jenkins_url": request.jenkins_url,
        "jenkins_username": request.jenkins_username,
        "jenkins_api_token": request.jenkins_api_token,
        "evidence": request.evidence,
        "github_token": request.github_token,
        "github_owner": request.github_owner,
        "github_repo": request.github_repo,
        "engineer_id": request.engineer_id,
        "source": request.source,
        "incident_type": request.incident_type,
        "steps": [],
        "monitoring_report": "",
        "diagnosis_report": "",
        "remediation_report": "",
        "approval_status": "",
        "fix_result": {},
        "historical_incident": None,
    }

    config = {
        "configurable": {"thread_id": incident_id},
        "recursion_limit": 30,
    }

    try:
        result = investigation_graph.invoke(initial_state, config=config)

        # Diagnosis already retrieved, judged, and verified this via its
        # search_similar_incidents tool + resolve_historical_match() — the API
        # layer just reads the result, it does not retrieve anything itself.
        historical_incident = result.get("historical_incident")

        agent_confidence, overall_confidence = summarize_confidence(result)
        confidence_source = {
            "monitoring": result.get("monitoring_confidence_source") or "agent",
        }
        unified_report = build_unified_report({
            **result,
            "historical_incident": historical_incident,
        })

        incident_document = {
            "_id": incident_id,
            "job_name": request.job_name,
            "build_number": request.build_number,
            "evidence": request.evidence,
            "monitoring_report": result.get("monitoring_report", ""),
            "diagnosis_report": result.get("diagnosis_report", ""),
            "remediation_report": result.get("remediation_report", ""),
            "unified_report": unified_report,
            "historical_incident": historical_incident,
            "agent_confidence": agent_confidence,
            "overall_confidence": overall_confidence,
            "confidence_source": confidence_source,
            "schema_version": 3,
            "engineer_id": request.engineer_id,
            "source": request.source,
            "incident_type": request.incident_type,
            "steps": result.get("steps", []),
            "solved_step_id": None,
            "status": "awaiting_approval",
            "decision": None,
            "fix_result": None,
            "created_at": _now(),
            "updated_at": _now(),
        }

        save_incident(incident_document)

        return InvestigateResponse(
            incident_id=incident_id,
            job_name=request.job_name,
            build_number=request.build_number,
            status="awaiting_approval",
            monitoring_report=result.get("monitoring_report", ""),
            diagnosis_report=result.get("diagnosis_report", ""),
            remediation_report=result.get("remediation_report", ""),
            unified_report=unified_report,
            historical_incident=historical_incident,
            agent_confidence=agent_confidence,
            overall_confidence=overall_confidence,
            confidence_source=confidence_source,
            steps=result.get("steps", []),
            engineer_id=request.engineer_id,
            source=request.source,
        )

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Investigation pipeline failed: incident_id=%s",
            incident_id,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Investigation pipeline failed: {exc}",
        ) from exc



@router.post("/api/investigate/decision", response_model=DecisionResponse)
def decide(request: DecisionRequest, http_request: Request):
    """Resume a paused investigation with the engineer's decision."""

    config = {"configurable": {"thread_id": request.incident_id}}

    try:
        graph_state = investigation_graph.get_state(config)

        if not graph_state.next:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This investigation is no longer waiting for a decision. "
                    "The backend was probably restarted, or the decision was "
                    "already submitted. Run Investigate on the build again."
                ),
            )

        result = investigation_graph.invoke(
            Command(resume={"decision": request.decision}),
            config=config,
        )

        status = (
            "approved_fixed"
            if request.decision == "approve"
            else "denied"
        )
        fix_result = result.get("fix_result") or None

        save_incident({
            "_id": request.incident_id,
            "status": status,
            "decision": {
                "decision": request.decision,
                "approver": request.approver,
                "decided_at": _now(),
            },
            "fix_result": fix_result,
            "updated_at": _now(),
        })

        if request.decision == "deny":
            message = "Denied by devops engineer. No changes were made."
        elif fix_result:
            message = fix_result.get("message", "")
        else:
            message = "Approved, but no fix result was produced."

        return DecisionResponse(
            incident_id=request.incident_id,
            status=status,
            fix_result=fix_result,
            message=message,
        )

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Failed to resume investigation: incident_id=%s",
            request.incident_id,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Resuming investigation failed: {exc}",
        ) from exc


@router.get("/api/incidents")
def get_incidents(limit: int = 20):
    """List recent incidents."""
    request_id = str(uuid.uuid4())
    started = time.perf_counter()


    try:
        incidents = list_incidents(limit=limit)

        

        return {"count": len(incidents), "incidents": incidents}

    except Exception:
        logger.exception(
            "INVESTIGATION_TRACE get_incidents_failed request_id=%s",
            request_id,
        )
        raise


@router.get("/api/incidents/{incident_id}")
def get_incident_by_id(incident_id: str):
    """Fetch one incident document."""
    request_id = str(uuid.uuid4())
    started = time.perf_counter()



    try:
        incident = get_incident(incident_id)



        if incident is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Incident not found (either it does not exist, "
                    "or MongoDB is not configured/reachable)."
                ),
            )

        return incident

    except HTTPException:
        raise

    except Exception:
        logger.exception(
            "INVESTIGATION_TRACE get_incident_failed request_id=%s",
            request_id,
        )
        raise


class FeedbackRequest(BaseModel):
    build_passed: bool = False
    liked_fix: bool = False
    system_efficient: bool = False
    comment: str = ""
    submitted_by: str = "devops-engineer"
    resolution_path: Literal["auto_fix", "manual"] = "auto_fix"
    followed_steps: dict[str, bool] = Field(default_factory=dict)
    solved_step_id: str | None = None
    custom_solution: str = ""


@router.post("/api/incidents/{incident_id}/feedback")
def submit_feedback(incident_id: str, request: FeedbackRequest):
    """Store feedback and manual-resolution step outcomes."""
    request_id = str(uuid.uuid4())
    started = time.perf_counter()

    try:

        incident = get_incident(incident_id)



        if incident is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Incident not found (it does not exist, "
                    "or MongoDB is not configured/reachable)."
                ),
            )

        doc = {
            "_id": incident_id,
            "feedback": {
                "build_passed": request.build_passed,
                "liked_fix": request.liked_fix,
                "system_efficient": request.system_efficient,
                "comment": request.comment.strip(),
                "submitted_by": request.submitted_by,
                "submitted_at": _now(),
            },
            "resolution_path": request.resolution_path,
            "status": (
                "resolved" if request.build_passed else "needs_followup"
            ),
            "updated_at": _now(),
        }

        if request.resolution_path == "manual":
            steps = incident.get("steps") or []
            known = {step.get("id") for step in steps}

            for step_id in request.followed_steps:
                if step_id not in known:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Unknown step id: {step_id}",
                    )

            if request.solved_step_id is not None:
                if request.solved_step_id not in known:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Unknown step id: {request.solved_step_id}",
                    )

                if not request.followed_steps.get(request.solved_step_id):
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "solved_step_id must be one of the steps "
                            "marked as followed."
                        ),
                    )

            doc["steps"] = [
                {
                    **step,
                    "followed": bool(
                        request.followed_steps.get(step.get("id"), False)
                    ),
                    "solved": step.get("id") == request.solved_step_id,
                }
                for step in steps
            ]

            doc["custom_solution"] = request.custom_solution.strip()
            doc["solved_step_id"] = request.solved_step_id


        save_incident(doc)



        return {
            "incident_id": incident_id,
            "status": "feedback_saved",
        }

    except HTTPException:
        raise

    except Exception:
        logger.exception(
            "INVESTIGATION_TRACE feedback_failed request_id=%s",
            request_id,
        )
        raise


