from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from app.connectors.sonarqube import SonarQubeConnectionError, SonarQubeConnector

from app.api.auth import require_user

router = APIRouter(prefix="/api/sonarqube", tags=["SonarQube"], dependencies=[Depends(require_user)])


class SonarQubeCredentials(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    token: SecretStr


def create_connector(credentials: SonarQubeCredentials) -> SonarQubeConnector:
    try:
        return SonarQubeConnector(
            base_url=credentials.url,
            token=credentials.token.get_secret_value(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/test")
def test_sonarqube_connection(credentials: SonarQubeCredentials):
    try:
        return {
            "status": "connected",
            "provider": "sonarqube",
            **create_connector(credentials).test_connection(),
        }
    except SonarQubeConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/projects")
def list_sonarqube_projects(credentials: SonarQubeCredentials):
    try:
        projects = create_connector(credentials).get_projects()
        return {"provider": "sonarqube", "count": len(projects), "projects": projects}
    except SonarQubeConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class SonarQubeEvidenceRequest(SonarQubeCredentials):
    project_key: str = Field(min_length=1, max_length=400)
    build_commit_sha: str = Field(default="", max_length=64)
    limit: int = Field(default=30, ge=1, le=100)


@router.post("/evidence")
def sonarqube_project_evidence(request: SonarQubeEvidenceRequest):
    try:
        return create_connector(request).get_project_evidence(
            project_key=request.project_key,
            build_commit_sha=request.build_commit_sha,
            limit=request.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SonarQubeConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
