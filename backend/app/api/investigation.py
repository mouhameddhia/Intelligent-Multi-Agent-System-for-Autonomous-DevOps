
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
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from langgraph.types import Command

from agent_system.orchestrator.graph import investigation_graph
from agent_system.config import settings
from agent_system.llm_log import performance_summary
from agent_system.db import DatabaseUnavailableError, count_incidents, save_incident, update_incident, get_incident, list_incidents
from pymongo.errors import PyMongoError
from agent_system.agents.report_assembly import build_unified_report
from agent_system.agents.confidence import summarize_confidence, judge_overall_score
from app.api.auth import require_user
from agent_system.learning.recorder import schedule_record

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
    use_local_llm: bool = False
    use_personalized_answers: bool = False


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
    judge_verdict: str = ""
    judge_scores: dict[str, int | None] = Field(default_factory=dict)
    judge_historical_match_quality: int | None = None
    judge_hallucination_detected: bool | None = None
    judge_issues: list[str] = Field(default_factory=list)
    judge_overall_score: int | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    pipeline_duration_ms: int = 0
    llm_performance: dict[str, Any] = Field(default_factory=dict)
    engineer_id: str = ""
    source: str = ""
    personalization_enabled: bool = False
    policy_version_used: int | None = None


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
def investigate(request: InvestigateRequest, http_request: Request, user: dict = Depends(require_user)):
    """Run Monitoring -> Diagnosis -> Remediation, then pause for approval."""

    incident_id = str(uuid.uuid4())

    initial_state = {
        "incident_id": incident_id,
        "user_id": str(user["_id"]),
        "job_name": request.job_name,
        "build_number": request.build_number,
        "jenkins_url": request.jenkins_url,
        "jenkins_username": request.jenkins_username,
        "jenkins_api_token": request.jenkins_api_token,
        "evidence": request.evidence,
        "github_token": request.github_token,
        "github_owner": request.github_owner,
        "github_repo": request.github_repo,
        "engineer_id": user["name"],
        "source": request.source,
        "incident_type": request.incident_type,
        "use_local_llm": request.use_local_llm,
        "use_personalized_answers": request.use_personalized_answers and settings.learning_enabled,
        "steps": [],
        "monitoring_report": "",
        "diagnosis_report": "",
        "remediation_report": "",
        "approval_status": "",
        "fix_result": {},
        "historical_incident": None,
        "llm_calls": [],
        "judge_verdict": "",
        "judge_scores": {},
        "judge_historical_match_quality": None,
        "judge_hallucination_detected": None,
        "judge_issues": [],
    }

    config = {
        "configurable": {"thread_id": incident_id},
        "recursion_limit": 30,
    }

    try:
        pipeline_started = time.perf_counter()
        result = investigation_graph.invoke(initial_state, config=config)
        pipeline_duration_ms = round((time.perf_counter() - pipeline_started) * 1000)
        llm_calls = result.get("llm_calls", [])
        llm_performance = performance_summary(
            llm_calls,
            qlora_requested=request.use_local_llm,
        )

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
            "user_id": str(user["_id"]),
            "job_name": request.job_name,
            "build_number": request.build_number,
            "evidence": request.evidence,
            "monitoring_report": result.get("monitoring_report", ""),
            "diagnosis_report": result.get("diagnosis_report", ""),
            "remediation_report": result.get("remediation_report", ""),
            "remediation_source_evidence": result.get("remediation_source_evidence", []),
            "unified_report": unified_report,
            "historical_incident": historical_incident,
            "agent_confidence": agent_confidence,
            "overall_confidence": overall_confidence,
            "confidence_source": confidence_source,
            "judge_verdict": result.get("judge_verdict", ""),
            "judge_scores": result.get("judge_scores", {}),
            "judge_historical_match_quality": result.get("judge_historical_match_quality"),
            "judge_hallucination_detected": result.get("judge_hallucination_detected"),
            "judge_issues": result.get("judge_issues", []),
            "judge_overall_score": judge_overall_score(result.get("judge_scores") or {}),
            "schema_version": 4,
            "engineer_id": user["name"],
            "source": request.source,
            "incident_type": request.incident_type,
            "steps": result.get("steps", []),
            "personalization_enabled": request.use_personalized_answers and settings.learning_enabled,
            "policy_version_used": result.get("policy_version_used"),
            "pipeline_duration_ms": pipeline_duration_ms,
            "llm_performance": llm_performance,
            "llm_calls": llm_calls if settings.store_llm_calls else [],
            "solved_step_id": None,
            "status": "awaiting_approval",
            "decision": None,
            "fix_result": None,
            "created_at": _now(),
            "updated_at": _now(),
        }

        save_incident(incident_document, raise_on_error=True)

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
            judge_verdict=result.get("judge_verdict", ""),
            judge_scores=result.get("judge_scores", {}),
            judge_historical_match_quality=result.get("judge_historical_match_quality"),
            judge_hallucination_detected=result.get("judge_hallucination_detected"),
            judge_issues=result.get("judge_issues", []),
            judge_overall_score=judge_overall_score(result.get("judge_scores") or {}),
            steps=result.get("steps", []),
            pipeline_duration_ms=pipeline_duration_ms,
            llm_performance=llm_performance,
            engineer_id=user["name"],
            source=request.source,
            personalization_enabled=request.use_personalized_answers and settings.learning_enabled,
            policy_version_used=result.get("policy_version_used"),
        )

    except DatabaseUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Incident storage is unavailable; the investigation was not saved.") from exc
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
def decide(request: DecisionRequest, http_request: Request, user: dict = Depends(require_user)):
    """Resume a paused investigation with the engineer's decision."""

    config = {"configurable": {"thread_id": request.incident_id}}

    try:
        incident = get_incident(request.incident_id, user_id=str(user["_id"]))
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
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

        update_incident(request.incident_id, str(user["_id"]), {
            "status": status,
            "decision": {
                "decision": request.decision,
                "approver": user["name"],
                "decided_at": _now(),
            },
            "fix_result": fix_result,
            "updated_at": _now(),
        })
        if incident.get("personalization_enabled") is True:
            schedule_record(request.incident_id, str(user["_id"]))

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

    except DatabaseUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Incident storage is unavailable.") from exc
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
def get_incidents(limit: int = 20, user: dict = Depends(require_user)):
    """List recent incidents."""
    request_id = str(uuid.uuid4())
    started = time.perf_counter()


    try:
        incidents = list_incidents(limit=limit, user_id=str(user["_id"]))
        total_count = count_incidents(user_id=str(user["_id"]))
        return {"count": total_count, "returned_count": len(incidents), "incidents": incidents}

    except DatabaseUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Incident storage is unavailable.") from exc
    except Exception:
        logger.exception(
            "INVESTIGATION_TRACE get_incidents_failed request_id=%s",
            request_id,
        )
        raise


@router.get("/api/incidents/{incident_id}")
def get_incident_by_id(incident_id: str, user: dict = Depends(require_user)):
    """Fetch one incident document."""
    request_id = str(uuid.uuid4())
    started = time.perf_counter()



    try:
        incident = get_incident(incident_id, user_id=str(user["_id"]))



        if incident is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Incident not found (either it does not exist, "
                    "or MongoDB is not configured/reachable)."
                ),
            )

        return incident

    except DatabaseUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Incident storage is unavailable.") from exc
    except HTTPException:
        raise

    except Exception:
        logger.exception(
            "INVESTIGATION_TRACE get_incident_failed request_id=%s",
            request_id,
        )
        raise

@router.get("/api/judge/calibration")
def get_judge_calibration(limit: int = 500, user: dict = Depends(require_user)):
    """How often the judge's predicted-good/bad call matched what engineers
    actually reported. Best-effort: incidents without feedback or without a
    judge score are simply excluded, not counted as wrong."""
    try:
        incidents = list_incidents(limit=limit, user_id=str(user["_id"]))
    except DatabaseUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Incident storage is unavailable.") from exc
    calibrated = [
        inc.get("judge_calibration")
        for inc in incidents
        if isinstance(inc.get("judge_calibration"), dict)
    ]

    total = len(calibrated)
    correct = sum(1 for c in calibrated if c.get("correct") is True)

    return {
        "total_calibrated_incidents": total,
        "correct": correct,
        "accuracy_pct": round(100 * correct / total) if total else None,
        "samples": calibrated[:20],
    }


@router.post("/api/investigate/stream")
def investigate_stream(request: InvestigateRequest, http_request: Request, user: dict = Depends(require_user)):
    """Same pipeline as /api/investigate, but streams a Server-Sent Event per
    node as it completes, so the frontend can show live step-by-step
    progress instead of one long silent wait."""
    incident_id = str(uuid.uuid4())

    initial_state = {
        "incident_id": incident_id,
        "user_id": str(user["_id"]),
        "job_name": request.job_name,
        "build_number": request.build_number,
        "jenkins_url": request.jenkins_url,
        "jenkins_username": request.jenkins_username,
        "jenkins_api_token": request.jenkins_api_token,
        "evidence": request.evidence,
        "github_token": request.github_token,
        "github_owner": request.github_owner,
        "github_repo": request.github_repo,
        "engineer_id": user["name"],
        "source": request.source,
        "incident_type": request.incident_type,
        "use_local_llm": request.use_local_llm,
                "use_personalized_answers": request.use_personalized_answers and settings.learning_enabled,
        "steps": [],
        "monitoring_report": "",
        "diagnosis_report": "",
        "remediation_report": "",
        "approval_status": "",
        "fix_result": {},
        "historical_incident": None,
        "judge_verdict": "",
        "judge_scores": {},
        "judge_historical_match_quality": None,
        "judge_hallucination_detected": None,
        "judge_issues": [],
    }

    config = {
        "configurable": {"thread_id": incident_id},
        "recursion_limit": 30,
    }

    def event_stream():
        node_started_at = time.perf_counter()
        pipeline_started_at = time.perf_counter()
        try:
            for update in investigation_graph.stream(initial_state, config=config, stream_mode="updates"):
                for node_name in update:
                    if node_name == "__interrupt__":
                        continue  # expected: graph pauses at human_approval
                    elapsed_ms = int((time.perf_counter() - node_started_at) * 1000)
                    node_started_at = time.perf_counter()
                    logger.info(
                        "INVESTIGATION_TRACE node_complete incident_id=%s node=%s elapsed_ms=%s",
                        incident_id, node_name, elapsed_ms,
                    )
                    yield f"data: {json.dumps({'type': 'node_complete', 'node': node_name, 'elapsed_ms': elapsed_ms})}\n\n"

            # Graph paused at human_approval (interrupt) — fetch the
            # authoritative merged state, same call the decide() endpoint
            # already uses.
            graph_state = investigation_graph.get_state(config)
            result = graph_state.values
            pipeline_duration_ms = round((time.perf_counter() - pipeline_started_at) * 1000)
            llm_calls = result.get("llm_calls", [])
            llm_performance = performance_summary(
                llm_calls, qlora_requested=request.use_local_llm
            )

            historical_incident = result.get("historical_incident")
            agent_confidence, overall_confidence = summarize_confidence(result)
            confidence_source = {"monitoring": result.get("monitoring_confidence_source") or "agent"}
            unified_report = build_unified_report({**result, "historical_incident": historical_incident})

            incident_document = {
                "_id": incident_id,
                "user_id": str(user["_id"]),
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
                "judge_verdict": result.get("judge_verdict", ""),
                "judge_scores": result.get("judge_scores", {}),
                "judge_historical_match_quality": result.get("judge_historical_match_quality"),
                "judge_hallucination_detected": result.get("judge_hallucination_detected"),
                "judge_issues": result.get("judge_issues", []),
                "judge_overall_score": judge_overall_score(result.get("judge_scores") or {}),
                "schema_version": 4,
                "engineer_id": user["name"],
                "source": request.source,
                "incident_type": request.incident_type,
                "steps": result.get("steps", []),
                "personalization_enabled": request.use_personalized_answers and settings.learning_enabled,
                "policy_version_used": result.get("policy_version_used"),
                "pipeline_duration_ms": pipeline_duration_ms,
                "llm_performance": llm_performance,
                "llm_calls": llm_calls if settings.store_llm_calls else [],
                "solved_step_id": None,
                "status": "awaiting_approval",
                "decision": None,
                "fix_result": None,
                "created_at": _now(),
                "updated_at": _now(),
            }
            save_incident(incident_document, raise_on_error=True)

            payload = {
                "incident_id": incident_id,
                "job_name": request.job_name,
                "build_number": request.build_number,
                "status": "awaiting_approval",
                "monitoring_report": result.get("monitoring_report", ""),
                "diagnosis_report": result.get("diagnosis_report", ""),
                "remediation_report": result.get("remediation_report", ""),
                "unified_report": unified_report,
                "historical_incident": historical_incident,
                "agent_confidence": agent_confidence,
                "overall_confidence": overall_confidence,
                "confidence_source": confidence_source,
                "judge_verdict": result.get("judge_verdict", ""),
                "judge_scores": result.get("judge_scores", {}),
                "judge_historical_match_quality": result.get("judge_historical_match_quality"),
                "judge_hallucination_detected": result.get("judge_hallucination_detected"),
                "judge_issues": result.get("judge_issues", []),
                "judge_overall_score": judge_overall_score(result.get("judge_scores") or {}),
                "steps": result.get("steps", []),
                "pipeline_duration_ms": pipeline_duration_ms,
                "llm_performance": llm_performance,
                "engineer_id": user["name"],
                "source": request.source,
                "personalization_enabled": request.use_personalized_answers and settings.learning_enabled,
                "policy_version_used": result.get("policy_version_used"),
            }
            yield f"data: {json.dumps({'type': 'complete', 'data': payload})}\n\n"

        except Exception as exc:
            logger.exception("Streaming investigation failed: incident_id=%s", incident_id)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")

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
def submit_feedback(incident_id: str, request: FeedbackRequest, user: dict = Depends(require_user)):
    """Store feedback and manual-resolution step outcomes."""
    request_id = str(uuid.uuid4())
    started = time.perf_counter()

    try:

        incident = get_incident(incident_id, user_id=str(user["_id"]))



        if incident is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Incident not found (it does not exist, "
                    "or MongoDB is not configured/reachable)."
                ),
            )

        judge_score = incident.get("judge_overall_score")
        judge_hallucination = incident.get("judge_hallucination_detected")
        judge_predicted_good = (
            judge_score is not None
            and judge_score >= 70
            and judge_hallucination is not True
        )
        judge_calibration = {
            "judge_predicted_good": judge_predicted_good,
            "actual_good": request.build_passed,
            "correct": judge_predicted_good == request.build_passed,
            "judge_score_at_time": judge_score,
        } if judge_score is not None else None

        doc = {
            "feedback": {
                "build_passed": request.build_passed,
                "liked_fix": request.liked_fix,
                "system_efficient": request.system_efficient,
                "comment": request.comment.strip(),
                "submitted_by": user["name"],
                "submitted_at": _now(),
            },
            "resolution_path": request.resolution_path,
            "status": (
                "resolved" if request.build_passed else "needs_followup"
            ),
            "judge_calibration": judge_calibration,
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


        if not update_incident(incident_id, str(user["_id"]), doc):
            raise HTTPException(status_code=404, detail="Incident not found.")

        if incident.get("personalization_enabled") is True:
            schedule_record(incident_id, str(user["_id"]))



        return {
            "incident_id": incident_id,
            "status": "feedback_saved",
        }

    except HTTPException:
        raise

    except (DatabaseUnavailableError, PyMongoError) as exc:
        logger.warning(
            "INVESTIGATION_TRACE feedback_database_unavailable request_id=%s error_type=%s",
            request_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Feedback could not be saved because the incident database is "
                "temporarily unreachable. Check the MongoDB Atlas network/TLS "
                "connection and submit again."
            ),
        ) from exc

    except Exception:
        logger.exception(
            "INVESTIGATION_TRACE feedback_failed request_id=%s",
            request_id,
        )
        raise


