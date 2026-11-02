"""Local email/password accounts backed by MongoDB and opaque bearer sessions."""

import base64
import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field, SecretStr

from agent_system.db import (
    DatabaseUnavailableError,
    create_auth_session,
    create_user,
    claim_legacy_incidents,
    delete_auth_session,
    find_auth_session,
    find_user_by_email,
    find_user_by_id,
)

router = APIRouter(prefix="/api/auth", tags=["authentication"])
SESSION_DAYS = 7
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=10, max_length=128)


class SignUpRequest(Credentials):
    name: str = Field(min_length=1, max_length=80)


def _normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if not EMAIL_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    return normalized


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R,
        p=SCRYPT_P, dklen=32, maxmem=64 * 1024 * 1024,
    )
    return "$".join((
        "scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
        base64.urlsafe_b64encode(salt).decode(),
        base64.urlsafe_b64encode(derived).decode(),
    ))


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, expected_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(expected_text.encode())
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r),
            p=int(p), dklen=len(expected), maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, MemoryError):
        return False


def _public_user(user: dict) -> dict:
    return {"id": str(user["_id"]), "name": user["name"], "email": user["email"]}


def _new_session(user: dict) -> dict:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    create_auth_session({
        "_id": str(uuid.uuid4()),
        "user_id": str(user["_id"]),
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "created_at": datetime.now(timezone.utc),
        "expires_at": expires_at,
    })
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at.isoformat(),
        "user": _public_user(user),
    }


def require_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[7:].strip()
    if not token or len(token) > 512:
        raise HTTPException(status_code=401, detail="Invalid session.", headers={"WWW-Authenticate": "Bearer"})
    try:
        session = find_auth_session(
            hashlib.sha256(token.encode()).hexdigest(), datetime.now(timezone.utc)
        )
        user = find_user_by_id(session["user_id"]) if session else None
    except DatabaseUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not user:
        raise HTTPException(status_code=401, detail="Session expired. Sign in again.", headers={"WWW-Authenticate": "Bearer"})
    return user


@router.post("/signup")
def signup(request: SignUpRequest):
    email = _normalize_email(request.email)
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Enter your name.")
    user = {
        "_id": str(uuid.uuid4()),
        "name": name,
        "email": email,
        "password_hash": _hash_password(request.password.get_secret_value()),
        "created_at": datetime.now(timezone.utc),
    }
    try:
        create_user(user)
        claim_legacy_incidents(str(user["_id"]))
        return _new_session(user)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DatabaseUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/login")
def login(request: Credentials):
    email = _normalize_email(request.email)
    try:
        user = find_user_by_email(email)
        # Do comparable password-hash work for unknown accounts to reduce
        # timing-based account enumeration.
        if user is None:
            _hash_password(request.password.get_secret_value())
            raise HTTPException(status_code=401, detail="Email or password is incorrect.")
        if not _verify_password(request.password.get_secret_value(), user.get("password_hash", "")):
            raise HTTPException(status_code=401, detail="Email or password is incorrect.")
        return _new_session(user)
    except DatabaseUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/me")
def current_user(user: dict = Depends(require_user)):
    return _public_user(user)


@router.post("/logout")
def logout(authorization: str | None = Header(default=None), user: dict = Depends(require_user)):
    del user
    token = authorization[7:].strip()
    try:
        delete_auth_session(hashlib.sha256(token.encode()).hexdigest())
        return {"status": "signed_out"}
    except DatabaseUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
