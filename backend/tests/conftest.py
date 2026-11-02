import pytest

from agent_system.config import settings


@pytest.fixture(autouse=True)
def reset_learning_singletons():
    """Default store/registry/policy cache must not leak between tests."""
    from agent_system.learning.registry import set_registry
    from agent_system.learning.store import set_store

    set_store(None)
    set_registry(None)
    yield
    set_store(None)
    set_registry(None)


@pytest.fixture(autouse=True)
def isolate_external_services(monkeypatch):
    """Tests must never reach MongoDB or an LLM, even if the developer's .env
    has real credentials."""
    monkeypatch.setattr(settings, "mongodb_uri", "")
    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(settings, "judge_api_key", "")
    monkeypatch.setattr(settings, "learning_enabled", True)
    monkeypatch.setattr(settings, "learning_batch_size", 5)
