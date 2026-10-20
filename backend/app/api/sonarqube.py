from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from app.connectors.sonarqube import (
    SonarQubeConnectionError,
    SonarQubeConnector,
)

router = APIRouter(prefix="/api/sonarqube", tags=["SonarQube"])


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
    connector = create_connector(credentials)

    try:
        result = connector.test_connection()
        return {
            "status": "connected",
            "provider": "sonarqube",
            **result,
        }
    except SonarQubeConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/projects")
def list_sonarqube_projects(credentials: SonarQubeCredentials):
    connector = create_connector(credentials)

    try:
        projects = connector.get_projects()
        return {
            "provider": "sonarqube",
            "count": len(projects),
            "projects": projects,
        }
    except SonarQubeConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class SonarQubeIssuesRequest(SonarQubeCredentials):
    project_key: str = Field(min_length=1, max_length=400)
    file_path: str = ""
    limit: int = Field(default=20, ge=1, le=100)


@router.post("/issues")
def list_sonarqube_issues(request: SonarQubeIssuesRequest):
    connector = create_connector(request)

    try:
        issues = connector.get_issues(
            project_key=request.project_key,
            file_path=request.file_path or None,
            limit=request.limit,
        )
        return {
            "provider": "sonarqube",
            "project_key": request.project_key,
            "count": len(issues),
            "issues": issues,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SonarQubeConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
