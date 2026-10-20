from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from app.connectors.kubernetes import (
    KubernetesConnectionError,
    KubernetesConnector,
)

router = APIRouter(prefix="/api/kubernetes", tags=["Kubernetes"])


class KubernetesCredentials(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    token: SecretStr
    verify_tls: bool = True


def create_connector(credentials: KubernetesCredentials) -> KubernetesConnector:
    try:
        return KubernetesConnector(
            base_url=credentials.url,
            token=credentials.token.get_secret_value(),
            verify_tls=credentials.verify_tls,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/test")
def test_kubernetes_connection(credentials: KubernetesCredentials):
    connector = create_connector(credentials)

    try:
        result = connector.test_connection()
        return {
            "status": "connected",
            "provider": "kubernetes",
            **result,
        }
    except KubernetesConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/namespaces")
def list_kubernetes_namespaces(credentials: KubernetesCredentials):
    connector = create_connector(credentials)

    try:
        namespaces = connector.get_namespaces()
        return {
            "provider": "kubernetes",
            "count": len(namespaces),
            "namespaces": namespaces,
        }
    except KubernetesConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class KubernetesEventsRequest(KubernetesCredentials):
    namespace: str = Field(min_length=1, max_length=253)
    start: float
    end: float


@router.post("/events")
def list_kubernetes_events(request: KubernetesEventsRequest):
    connector = create_connector(request)

    try:
        events = connector.get_events(
            namespace=request.namespace,
            start=request.start,
            end=request.end,
        )
        return {
            "provider": "kubernetes",
            "namespace": request.namespace,
            "start": request.start,
            "end": request.end,
            "count": len(events),
            "events": events,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KubernetesConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
