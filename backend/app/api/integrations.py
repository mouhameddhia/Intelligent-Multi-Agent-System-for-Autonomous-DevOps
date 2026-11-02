"""Authenticated persistence for integration connection settings."""

import json
from typing import Any

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from agent_system.db import (
    DatabaseUnavailableError,
    load_user_integration_settings,
    save_user_integration_settings,
)
from app.api.auth import require_user
from app.integration_vault import decrypt_integration_settings, encrypt_integration_settings

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
SUPPORTED_INTEGRATIONS = {
    "jenkins", "github", "prometheus", "loki", "sonarqube", "kubernetes", "grafana",
}


class IntegrationSettingsPayload(BaseModel):
    config: dict[str, Any] = Field(max_length=32)


@router.get("")
def get_integration_settings(response: Response, user: dict = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    try:
        encrypted = load_user_integration_settings(str(user["_id"]))
        if encrypted is None:
            return {"configs": {}}
        configs = json.loads(decrypt_integration_settings(encrypted))
        if not isinstance(configs, dict):
            raise ValueError("Stored integration settings have an invalid shape.")
        return {"configs": configs}
    except DatabaseUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail="Saved integration settings could not be decrypted. Check that the backend encryption key is unchanged.",
        ) from exc


@router.put("/{provider}")
def put_integration_settings(
    provider: str,
    payload: IntegrationSettingsPayload,
    user: dict = Depends(require_user),
):
    if provider not in SUPPORTED_INTEGRATIONS:
        raise HTTPException(status_code=404, detail="Unsupported integration.")
    try:
        encrypted_existing = load_user_integration_settings(str(user["_id"]))
        configs = json.loads(decrypt_integration_settings(encrypted_existing)) if encrypted_existing else {}
        configs[provider] = payload.config
        serialized = json.dumps(configs, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        encrypted = encrypt_integration_settings(serialized)
        save_user_integration_settings(str(user["_id"]), encrypted)
        return {"saved": True}
    except DatabaseUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (InvalidToken, OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Could not encrypt integration settings on this backend.") from exc
