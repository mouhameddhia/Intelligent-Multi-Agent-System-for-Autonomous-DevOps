from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from app.connectors.grafana import (
    GrafanaConnectionError,
    GrafanaConnector,
)

router = APIRouter(prefix="/api/grafana", tags=["Grafana"])


class GrafanaCredentials(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    api_token: SecretStr


def create_connector(credentials: GrafanaCredentials) -> GrafanaConnector:
    try:
        return GrafanaConnector(
            base_url=credentials.url,
            api_token=credentials.api_token.get_secret_value(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/test")
def test_grafana_connection(credentials: GrafanaCredentials):
    connector = create_connector(credentials)

    try:
        result = connector.test_connection()
        return {
            "status": "connected",
            "provider": "grafana",
            **result,
        }
    except GrafanaConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/dashboards")
def list_grafana_dashboards(credentials: GrafanaCredentials):
    connector = create_connector(credentials)

    try:
        dashboards = connector.get_dashboards()
        return {
            "provider": "grafana",
            "count": len(dashboards),
            "dashboards": dashboards,
        }
    except GrafanaConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class GrafanaAnnotationsRequest(GrafanaCredentials):
    start: float
    end: float


@router.post("/annotations")
def list_grafana_annotations(request: GrafanaAnnotationsRequest):
    connector = create_connector(request)

    try:
        annotations = connector.get_annotations(
            start=request.start,
            end=request.end,
        )
        return {
            "provider": "grafana",
            "start": request.start,
            "end": request.end,
            "count": len(annotations),
            "annotations": annotations,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GrafanaConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
