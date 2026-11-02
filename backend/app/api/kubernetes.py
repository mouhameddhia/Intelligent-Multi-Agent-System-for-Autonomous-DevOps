from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from app.connectors.kubernetes import KubernetesConnectionError, KubernetesConnector

from app.api.auth import require_user

router = APIRouter(prefix="/api/kubernetes", tags=["Kubernetes"], dependencies=[Depends(require_user)])


class KubernetesConfiguration(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    namespace: str = Field(default="pipeguard-lab", min_length=1, max_length=63)
    token: SecretStr
    ca_certificate: str = Field(default="", max_length=16384)


def create_connector(configuration: KubernetesConfiguration) -> KubernetesConnector:
    try:
        return KubernetesConnector(
            base_url=configuration.url,
            namespace=configuration.namespace,
            token=configuration.token.get_secret_value(),
            ca_certificate=configuration.ca_certificate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/test")
def test_kubernetes_connection(configuration: KubernetesConfiguration):
    try:
        overview = create_connector(configuration).get_overview()
        return {"provider": "kubernetes", **overview}
    except KubernetesConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/overview")
def get_kubernetes_overview(configuration: KubernetesConfiguration):
    try:
        return {"provider": "kubernetes", **create_connector(configuration).get_overview()}
    except KubernetesConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
