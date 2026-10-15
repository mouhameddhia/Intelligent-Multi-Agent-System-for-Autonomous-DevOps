"""Runtime settings (environment variables) and YAML config loading."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _path(env: str, default: Path) -> Path:
    v = os.getenv(env)
    return Path(v).resolve() if v else default


class Settings(BaseModel):
    root: Path
    config_dir: Path
    scenarios_dir: Path
    schema_dir: Path
    data_dir: Path
    db_path: Path
    frontend_dist: Path
    mock_llm: bool
    step_delay_s: float
    seed_history: bool

    def load_yaml(self, name: str) -> dict[str, Any]:
        p = self.config_dir / name
        if not p.exists():
            return {}
        return yaml.safe_load(p.read_text()) or {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    root = _path("APP_ROOT", REPO_ROOT)
    data_dir = _path("DATA_DIR", root / "data")
    return Settings(
        root=root,
        config_dir=_path("CONFIG_DIR", root / "config"),
        scenarios_dir=_path("SCENARIOS_DIR", root / "scenarios"),
        schema_dir=root / "schema",
        data_dir=data_dir,
        db_path=_path("DB_PATH", data_dir / "devops_agents.db"),
        frontend_dist=_path("FRONTEND_DIST", root / "frontend" / "dist"),
        mock_llm=_env_bool("MOCK_LLM", False),
        step_delay_s=float(os.getenv("STEP_DELAY_S", "1.0")),
        seed_history=_env_bool("SEED_HISTORY", True),
    )


def reset_settings() -> None:
    get_settings.cache_clear()
