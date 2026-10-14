from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.connectors.prometheus import (
    PrometheusConnectionError,
    PrometheusConnector,
)

router = APIRouter(prefix="/api/prometheus", tags=["Prometheus"])


class PrometheusConfiguration(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class PrometheusQuery(PrometheusConfiguration):
    query: str = Field(min_length=1, max_length=2048)


def create_connector(
    configuration: PrometheusConfiguration,
) -> PrometheusConnector:
    try:
        return PrometheusConnector(base_url=configuration.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/test")
def test_prometheus_connection(
    configuration: PrometheusConfiguration,
):
    connector = create_connector(configuration)

    try:
        return {
            "status": "connected",
            "provider": "prometheus",
            **connector.test_connection(),
        }
    except PrometheusConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/targets")
def list_prometheus_targets(
    configuration: PrometheusConfiguration,
):
    connector = create_connector(configuration)

    try:
        targets = connector.get_targets()
        return {
            "provider": "prometheus",
            "count": len(targets),
            "targets": targets,
        }
    except PrometheusConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class PrometheusRangeQuery(PrometheusConfiguration):
    query: str = Field(min_length=1, max_length=2048)
    start: float
    end: float
    step: str = Field(default="30s", min_length=2, max_length=20)


@router.post("/query-range")
def query_prometheus_range(configuration: PrometheusRangeQuery):
    connector = create_connector(configuration)
    try:
        results = connector.query_range(
            expression=configuration.query,
            start=configuration.start,
            end=configuration.end,
            step=configuration.step,
        )
        return {
            "provider": "prometheus",
            "query": configuration.query,
            "start": configuration.start,
            "end": configuration.end,
            "step": configuration.step,
            "count": len(results),
            "results": results,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PrometheusConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/query")
def query_prometheus(configuration: PrometheusQuery):
    connector = create_connector(configuration)

    try:
        results = connector.query(configuration.query)
        return {
            "provider": "prometheus",
            "query": configuration.query,
            "count": len(results),
            "results": results,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PrometheusConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
