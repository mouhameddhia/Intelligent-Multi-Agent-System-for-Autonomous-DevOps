"""Encrypt per-user integration settings before storing them in MongoDB."""

import os
from pathlib import Path

from cryptography.fernet import Fernet

from agent_system.config import settings


_KEY_PATH = Path(__file__).resolve().parents[1] / ".local-secrets" / "integrations.key"


def _load_or_create_key() -> bytes:
    configured = settings.integration_encryption_key.strip()
    if configured:
        return configured.encode("ascii")

    _KEY_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(_KEY_PATH.parent, 0o700)
    try:
        descriptor = os.open(_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        os.chmod(_KEY_PATH, 0o600)
        return _KEY_PATH.read_bytes().strip()

    key = Fernet.generate_key()
    with os.fdopen(descriptor, "wb") as key_file:
        key_file.write(key)
        key_file.flush()
        os.fsync(key_file.fileno())
    return key


def encrypt_integration_settings(serialized_settings: bytes) -> bytes:
    return Fernet(_load_or_create_key()).encrypt(serialized_settings)


def decrypt_integration_settings(ciphertext: bytes) -> bytes:
    return Fernet(_load_or_create_key()).decrypt(ciphertext)
