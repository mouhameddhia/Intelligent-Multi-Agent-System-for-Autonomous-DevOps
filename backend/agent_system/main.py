import uvicorn
from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent_system.config import settings
from agent_system.orchestrator.graph import investigation_graph

app = FastAPI(
    title="PipeGuard Agent API",
    description="Autonomous investigation pipeline for failed Jenkins builds.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class InvestigateRequest(BaseModel):
    job_name: str
    build_number: int
    # Jenkins credentials are optional.
    # When provided, agents fetch real build data.
    # When absent, agents use the synthetic scenario from data.py.
    jenkins_url: str = ""
    jenkins_username: str = ""
    jenkins_api_token: str = ""
    # Evidence already collected for the exact selected build by the frontend.
    evidence: dict[str, Any] = Field(default_factory=dict)


class InvestigateResponse(BaseModel):
    job_name: str
    build_number: int
    monitoring_report: str
    diagnosis_report: str
    remediation_report: str


@app.get("/")
def root():
    return {
        "name": "PipeGuard Agent API",
        "status": "running",
        "version": "0.1.0",
    }


@app.get("/api/health")
def health():
    return {"status": "healthy", "service": "pipeguard-agents"}


@app.post("/api/investigate", response_model=InvestigateResponse)
def investigate(request: InvestigateRequest):
    """
    Trigger the full investigation pipeline for a failed Jenkins build.
    Runs: Monitoring -> Diagnosis -> Remediation (via LangGraph).
    Pass Jenkins credentials to investigate a real build; omit them for synthetic data.
    """
    initial_state = {
        "job_name": request.job_name,
        "build_number": request.build_number,
        "jenkins_url": request.jenkins_url,
        "jenkins_username": request.jenkins_username,
        "jenkins_api_token": request.jenkins_api_token,
        "evidence": request.evidence,
        "monitoring_report": "",
        "diagnosis_report": "",
        "remediation_report": "",
    }

    try:
        final_state = investigation_graph.invoke(initial_state)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Investigation pipeline failed: {str(exc)}",
        ) from exc

    return InvestigateResponse(
        job_name=final_state["job_name"],
        build_number=final_state["build_number"],
        monitoring_report=final_state["monitoring_report"],
        diagnosis_report=final_state["diagnosis_report"],
        remediation_report=final_state["remediation_report"],
    )


if __name__ == "__main__":
    uvicorn.run("agent_system.main:app", host="0.0.0.0", port=settings.agent_port, reload=True)
