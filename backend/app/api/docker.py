from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.connectors.docker import (
    DockerConnectionError,
    DockerConnector,
)

router = APIRouter(prefix="/api/docker", tags=["Docker"])


class DockerConfiguration(BaseModel):
    host: str = Field(min_length=1, max_length=2048)


def create_connector(configuration: DockerConfiguration) -> DockerConnector:
    try:
        return DockerConnector(host=configuration.host)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/test")
def test_docker_connection(configuration: DockerConfiguration):
    connector = create_connector(configuration)

    try:
        result = connector.test_connection()
        return {
            "status": "connected",
            "provider": "docker",
            **result,
        }
    except DockerConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/containers")
def list_docker_containers(configuration: DockerConfiguration):
    connector = create_connector(configuration)

    try:
        containers = connector.get_containers()
        return {
            "provider": "docker",
            "count": len(containers),
            "containers": containers,
        }
    except DockerConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class DockerEventsRequest(DockerConfiguration):
    start: float
    end: float


@router.post("/events")
def list_docker_events(request: DockerEventsRequest):
    connector = create_connector(request)

    try:
        events = connector.get_events(start=request.start, end=request.end)
        return {
            "provider": "docker",
            "start": request.start,
            "end": request.end,
            "count": len(events),
            "events": events,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DockerConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
