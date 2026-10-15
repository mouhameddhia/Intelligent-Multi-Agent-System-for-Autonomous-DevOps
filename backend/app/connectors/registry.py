"""Builds the ProviderSet for a run from config/adapters.yaml."""
from __future__ import annotations

import logging
from typing import Any

from ..config import Settings
from .base import ProviderSet, ProviderUnavailable, QualityProvider
from .real import GitHubActionsAdapter, KubernetesAdapter, LokiAdapter, PrometheusAdapter
from .simulated import (
    SimulatedLoggingProvider, SimulatedObservabilityProvider, SimulatedOrchestrationProvider,
    SimulatedPipelineProvider, SimulatedQualityProvider, SimulatedRepositoryProvider, SimulatedWorld,
)

log = logging.getLogger(__name__)

SIMULATED = {
    "pipeline": SimulatedPipelineProvider,
    "logging": SimulatedLoggingProvider,
    "observability": SimulatedObservabilityProvider,
    "repository": SimulatedRepositoryProvider,
    "orchestration": SimulatedOrchestrationProvider,
    "quality": SimulatedQualityProvider,
}
REAL = {
    ("pipeline", "github_actions"): GitHubActionsAdapter,
    ("repository", "github_actions"): GitHubActionsAdapter,
    ("observability", "prometheus"): PrometheusAdapter,
    ("logging", "loki"): LokiAdapter,
    ("orchestration", "kubernetes"): KubernetesAdapter,
}


def adapter_selection(settings: Settings) -> dict[str, str]:
    cfg = settings.load_yaml("adapters.yaml")
    sel = {k: "simulated" for k in SIMULATED}
    sel.update(cfg.get("providers", {}) or {})
    return sel


def build_providers(settings: Settings, world: SimulatedWorld | None) -> ProviderSet:
    """Instantiate the configured adapter per provider. A disabled real adapter falls back to simulated."""
    cfg = settings.load_yaml("adapters.yaml")
    selection = adapter_selection(settings)
    adapters_cfg: dict[str, Any] = cfg.get("adapters", {}) or {}
    built: dict[str, Any] = {}
    for kind, choice in selection.items():
        if choice != "simulated":
            cls = REAL.get((kind, choice))
            if cls is None:
                raise ValueError(f"unknown adapter '{choice}' for provider '{kind}'")
            try:
                built[kind] = cls(adapters_cfg.get(choice, {}))
                continue
            except ProviderUnavailable as e:
                log.warning("%s; falling back to simulated", e)
        if world is None:
            raise ProviderUnavailable(f"provider '{kind}' is simulated but no scenario world was given")
        built[kind] = SIMULATED[kind](world)
    if not isinstance(built["quality"], QualityProvider):
        built["quality"] = SimulatedQualityProvider(world)  # type: ignore[arg-type]
    return ProviderSet(**built)
