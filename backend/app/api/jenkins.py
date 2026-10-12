
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
