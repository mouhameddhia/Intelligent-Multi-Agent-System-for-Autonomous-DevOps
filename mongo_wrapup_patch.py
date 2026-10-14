#!/usr/bin/env python3
"""
Patch 6: expose incidents stored in MongoDB via two read endpoints, so the
final incident report (monitoring/diagnosis/remediation reports + human
decision + fix result, all in one document) can be shown live in a browser
or via curl during a demo.

New endpoints:
  GET /api/incidents            -> list the most recent incidents
  GET /api/incidents/{id}       -> full document for one incident

Run from your repo root:
    python3 mongo_wrapup_patch.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "backend" / "agent_system" / "db.py"
API_PATH = ROOT / "backend" / "app" / "api" / "investigation.py"

NEW_DB_CONTENT = '''"""Best-effort incident persistence. If MongoDB is unreachable or unconfigured,
write calls silently no-op so the investigation pipeline never breaks. Read
calls raise, since a failed read should be visible to whoever is asking."""

from .config import settings

_client = None


def _get_collection():
    global _client
    if not settings.mongodb_uri:
        return None
    try:
        if _client is None:
            from pymongo import MongoClient
            _client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=3000)
        return _client[settings.mongodb_db]["incidents"]
    except Exception:
        return None


def save_incident(doc: dict) -> None:
    """Upsert partial or full incident fields by _id."""
    try:
        collection = _get_collection()
        if collection is None:
            return
        doc_id = doc["_id"]
        collection.update_one({"_id": doc_id}, {"$set": doc}, upsert=True)
    except Exception:
        pass


def get_incident(incident_id: str) -> dict | None:
    """Fetch one incident document by id. Returns None if not found or Mongo unavailable."""
    collection = _get_collection()
    if collection is None:
        return None
    return collection.find_one({"_id": incident_id})


def list_incidents(limit: int = 20) -> list[dict]:
    """Fetch the most recent incidents, newest first. Empty list if Mongo unavailable."""
    collection = _get_collection()
    if collection is None:
        return []
    return list(collection.find().sort("created_at", -1).limit(limit))
'''

NEW_API_CONTENT = '''import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from langgraph.types import Command

from agent_system.orchestrator.graph import investigation_graph
from agent_system.db import save_incident, get_incident, list_incidents

router = APIRouter(tags=["investigation"])


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


class InvestigateResponse(BaseModel):
    incident_id: str
    job_name: str
    build_number: int
    status: str
    monitoring_report: str
    diagnosis_report: str
    remediation_report: str


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
def investigate(request: InvestigateRequest):
    """Run Monitoring -> Diagnosis -> Remediation, then pause for human approval."""
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
        "monitoring_report": "",
        "diagnosis_report": "",
        "remediation_report": "",
        "approval_status": "",
        "fix_result": {},
    }
    config = {"configurable": {"thread_id": incident_id}}

    try:
        result = investigation_graph.invoke(initial_state, config=config)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Investigation pipeline failed: {exc}",
        ) from exc

    save_incident({
        "_id": incident_id,
        "job_name": request.job_name,
        "build_number": request.build_number,
        "evidence": request.evidence,
        "monitoring_report": result.get("monitoring_report", ""),
        "diagnosis_report": result.get("diagnosis_report", ""),
        "remediation_report": result.get("remediation_report", ""),
        "status": "awaiting_approval",
        "decision": None,
        "fix_result": None,
        "created_at": _now(),
        "updated_at": _now(),
    })

    return InvestigateResponse(
        incident_id=incident_id,
        job_name=request.job_name,
        build_number=request.build_number,
        status="awaiting_approval",
        monitoring_report=result.get("monitoring_report", ""),
        diagnosis_report=result.get("diagnosis_report", ""),
        remediation_report=result.get("remediation_report", ""),
    )


@router.post("/api/investigate/decision", response_model=DecisionResponse)
def decide(request: DecisionRequest):
    """Resume a paused investigation with the devops engineer's decision."""
    config = {"configurable": {"thread_id": request.incident_id}}

    try:
        result = investigation_graph.invoke(
            Command(resume={"decision": request.decision}),
            config=config,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Resuming investigation failed: {exc}",
        ) from exc

    status = "approved_fixed" if request.decision == "approve" else "denied"
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


@router.get("/api/incidents")
def get_incidents(limit: int = 20):
    """List the most recent incidents (newest first) straight from MongoDB."""
    incidents = list_incidents(limit=limit)
    return {"count": len(incidents), "incidents": incidents}


@router.get("/api/incidents/{incident_id}")
def get_incident_by_id(incident_id: str):
    """Fetch the full incident document: reports, human decision, fix result."""
    incident = get_incident(incident_id)
    if incident is None:
        raise HTTPException(
            status_code=404,
            detail="Incident not found (either it does not exist, or MongoDB is not configured/reachable).",
        )
    return incident
'''


def main():
    if not DB_PATH.exists() or not API_PATH.exists():
        print("[FAIL] Expected backend/agent_system/db.py and backend/app/api/investigation.py — run from repo root.")
        raise SystemExit(1)

    DB_PATH.write_text(NEW_DB_CONTENT, encoding="utf-8")
    print(f"[OK] rewrote {DB_PATH} (added get_incident, list_incidents)")

    API_PATH.write_text(NEW_API_CONTENT, encoding="utf-8")
    print(f"[OK] rewrote {API_PATH} (added GET /api/incidents, GET /api/incidents/{{id}})")

    print("\n[OK] Patch complete. Restart the backend for this to take effect.")


if __name__ == "__main__":
    main()