"""Test configuration: MOCK_LLM forced, no step delay, an isolated SQLite file per session."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="devops-agents-tests-")
os.environ["MOCK_LLM"] = "true"
os.environ["STEP_DELAY_S"] = "0"
os.environ["DB_PATH"] = str(Path(_TMP) / "test.db")
for key in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "OLLAMA_ENABLED"):
    os.environ.pop(key, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings, reset_settings  # noqa: E402
from app.events import EventBus  # noqa: E402
from app.llm.client import LLMClient  # noqa: E402
from app.orchestrator.runner import RunManager  # noqa: E402
from app.store.db import Store  # noqa: E402
from app.store.history import HistoryIndex, seed_history  # noqa: E402

reset_settings()


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture()
def store(tmp_path, settings):
    st = Store(tmp_path / "t.db")
    seed_history(st, settings.data_dir / "history")
    return st


@pytest.fixture()
def manager(settings, store):
    return RunManager(settings, store, EventBus(), HistoryIndex(store), LLMClient(settings))
