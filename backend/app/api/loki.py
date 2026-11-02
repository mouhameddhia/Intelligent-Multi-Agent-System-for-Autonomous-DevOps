from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from app.connectors.alloy import AlloyProvisioningError, ensure_alloy_collector
from app.connectors.loki import LokiConnectionError, LokiConnector
from agent_system.config import settings

from app.api.auth import require_user

router = APIRouter(prefix="/api/loki", tags=["Loki"], dependencies=[Depends(require_user)])


class LokiConfiguration(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    project_name: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    username: str = Field(default="", max_length=256)
    token: SecretStr | None = None


def create_connector(configuration: LokiConfiguration) -> LokiConnector:
    token = configuration.token.get_secret_value() if configuration.token else ""
    try:
        return LokiConnector(
            base_url=configuration.url,
            username=configuration.username,
            token=token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/test")
def test_loki_connection(configuration: LokiConfiguration):
    try:
        connector = create_connector(configuration)
        result = connector.test_connection()
        token = configuration.token.get_secret_value() if configuration.token else ""
        collector = ensure_alloy_collector(
            vagrant_dir=settings.loki_vagrant_dir,
            project_name=configuration.project_name,
            loki_url=configuration.url.rstrip("/"),
            username=configuration.username,
            token=token,
        )
        return {"status": "connected", "provider": "loki", **result, "collector": collector}
    except AlloyProvisioningError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Loki is reachable, but Alloy could not be provisioned: {exc}",
        ) from exc
    except LokiConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/labels")
def list_loki_labels(configuration: LokiConfiguration):
    try:
        labels = create_connector(configuration).get_labels()
        return {"provider": "loki", "count": len(labels), "labels": labels}
    except LokiConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class LokiQueryRangeRequest(LokiConfiguration):
    start: float
    end: float
    build_number: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    limit: int = Field(default=100, ge=1, le=500)


@router.post("/query-range")
def query_loki_range(request: LokiQueryRangeRequest):
    try:
        lines = create_connector(request).query_range(
            expression=(
                f'{{project="{request.project_name}", '
                f'build_number="{request.build_number}"}}'
            ),
            start=request.start,
            end=request.end,
            limit=request.limit,
        )
        return {
            "provider": "loki",
            "query": (
                f'{{project="{request.project_name}", '
                f'build_number="{request.build_number}"}}'
            ),
            "project_name": request.project_name,
            "build_number": request.build_number,
            "start": request.start,
            "end": request.end,
            "count": len(lines),
            "lines": lines,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LokiConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
