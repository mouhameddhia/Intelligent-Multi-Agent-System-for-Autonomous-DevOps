
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from app.connectors.jenkins import (
    JenkinsConnectionError,
    JenkinsConnector,
)

router = APIRouter(prefix="/api/jenkins", tags=["Jenkins"])


class JenkinsCredentials(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    username: str = Field(min_length=1, max_length=256)
    api_token: SecretStr


def create_connector(credentials: JenkinsCredentials) -> JenkinsConnector:
    try:
        return JenkinsConnector(
            base_url=credentials.url,
            username=credentials.username,
            api_token=credentials.api_token.get_secret_value(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/test")
def test_jenkins_connection(credentials: JenkinsCredentials):
    connector = create_connector(credentials)

    try:
        result = connector.test_connection()
        return {
            "status": "connected",
            "provider": "jenkins",
            **result,
        }
    except JenkinsConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/jobs")
def list_jenkins_jobs(credentials: JenkinsCredentials):
    connector = create_connector(credentials)

    try:
        jobs = connector.get_jobs()
        return {
            "provider": "jenkins",
            "count": len(jobs),
            "jobs": jobs,
        }
    except JenkinsConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class JenkinsBuildHistoryRequest(JenkinsCredentials):
    job_name: str = Field(min_length=1, max_length=256)
    limit: int = Field(default=30, ge=1, le=100)


class JenkinsBuildEvidenceRequest(JenkinsCredentials):
    job_name: str = Field(min_length=1, max_length=256)
    build_number: int = Field(ge=1)


@router.post("/builds")
def list_jenkins_builds(request: JenkinsBuildHistoryRequest):
    connector = create_connector(request)

    try:
        builds = connector.get_builds(
            job_name=request.job_name,
            limit=request.limit,
        )
        return {
            "provider": "jenkins",
            "job_name": request.job_name,
            "count": len(builds),
            "builds": builds,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except JenkinsConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/build-evidence")
def retrieve_jenkins_build_evidence(
    request: JenkinsBuildEvidenceRequest,
):
    connector = create_connector(request)

    try:
        evidence = connector.get_build_evidence(
            job_name=request.job_name,
            build_number=request.build_number,
        )
        return {
            "provider": "jenkins",
            **evidence,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except JenkinsConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
