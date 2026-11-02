from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.connectors.grafana import GrafanaConnectionError, GrafanaConnector

from app.api.auth import require_user

router = APIRouter(prefix="/api/grafana", tags=["Grafana"], dependencies=[Depends(require_user)])


class GrafanaConfiguration(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


@router.post("/test")
def test_grafana_connection(configuration: GrafanaConfiguration):
    try:
        connector = GrafanaConnector(configuration.url)
        return {"provider": "grafana", "status": "connected", **connector.test_connection()}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GrafanaConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
