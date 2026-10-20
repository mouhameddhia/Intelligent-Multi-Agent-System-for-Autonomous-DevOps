from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.connectors.loki import (
    LokiConnectionError,
    LokiConnector,
)

router = APIRouter(prefix="/api/loki", tags=["Loki"])


class LokiConfiguration(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


def create_connector(configuration: LokiConfiguration) -> LokiConnector:
    try:
        return LokiConnector(base_url=configuration.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/test")
def test_loki_connection(configuration: LokiConfiguration):
    connector = create_connector(configuration)

    try:
        result = connector.test_connection()
        return {
            "status": "connected",
            "provider": "loki",
            **result,
        }
    except LokiConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/labels")
def list_loki_labels(configuration: LokiConfiguration):
    connector = create_connector(configuration)

    try:
        labels = connector.get_labels()
        return {
            "provider": "loki",
            "count": len(labels),
            "labels": labels,
        }
    except LokiConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class LokiQueryRangeRequest(LokiConfiguration):
    query: str = Field(min_length=1, max_length=2048)
    start: float
    end: float
    limit: int = Field(default=100, ge=1, le=500)


@router.post("/query-range")
def query_loki_range(request: LokiQueryRangeRequest):
    connector = create_connector(request)

    try:
        lines = connector.query_range(
            expression=request.query,
            start=request.start,
            end=request.end,
            limit=request.limit,
        )
        return {
            "provider": "loki",
            "query": request.query,
            "start": request.start,
            "end": request.end,
            "count": len(lines),
            "lines": lines,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LokiConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
