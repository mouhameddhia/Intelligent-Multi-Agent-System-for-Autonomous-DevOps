"""Abstract provider interfaces. Agents depend on these only — never on a concrete tool or SDK.

Each provider returns small typed DTOs defined here so adapters (simulated or real) are interchangeable.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ReadOnlyError(PermissionError):
    """Raised by real adapters when a write is attempted while `allow_writes` is false."""


class ProviderUnavailable(RuntimeError):
    """Raised when an adapter is disabled or cannot reach its backend."""


# ---- DTOs ---------------------------------------------------------------------------------------
class PipelineSummary(BaseModel):
    id: str
    name: str
    repository: str
    default_branch: str = "main"


class PipelineStage(BaseModel):
    name: str
    status: str  # success | failed | skipped | running
    started_at: datetime | None = None
    finished_at: datetime | None = None


class PipelineRun(BaseModel):
    pipeline_id: str
    run_id: str
    name: str
    status: str  # success | failed | running
    branch: str = "main"
    commit_sha: str | None = None
    url: str | None = None
    started_at: datetime | None = None
    stages: list[PipelineStage] = Field(default_factory=list)


class LogLine(BaseModel):
    ts: datetime | None
    source: str
    level: str
    message: str
    raw: str


class MetricSeries(BaseModel):
    name: str
    service: str
    unit: str = ""
    interval_s: int
    points: list[tuple[datetime, float]]


class Commit(BaseModel):
    repository: str
    sha: str
    author: str
    message: str
    timestamp: datetime
    files_changed: list[str] = Field(default_factory=list)
    diff: str = ""


class Pod(BaseModel):
    name: str
    namespace: str
    service: str
    phase: str
    reason: str | None = None
    restarts: int = 0
    ready: bool = True
    image: str | None = None


class K8sEvent(BaseModel):
    ts: datetime
    namespace: str
    object: str
    type: str  # Normal | Warning
    reason: str
    message: str


class DeploymentRevision(BaseModel):
    revision: int
    image: str
    created_at: datetime
    change_cause: str = ""


class Deployment(BaseModel):
    name: str
    namespace: str
    service: str
    environment: str
    replicas_desired: int
    replicas_ready: int
    image: str
    revisions: list[DeploymentRevision] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class QualityReport(BaseModel):
    ref: str
    tests_total: int
    tests_failed: int
    failed_tests: list[str] = Field(default_factory=list)
    coverage: float | None = None


class ActionOutcome(BaseModel):
    ok: bool
    provider: str
    message: str
    output: dict[str, Any] = Field(default_factory=dict)


# ---- Interfaces ---------------------------------------------------------------------------------
class Provider(ABC):
    name: str = "provider"
    read_only: bool = True


class PipelineProvider(Provider):
    @abstractmethod
    def list_pipelines(self) -> list[PipelineSummary]: ...

    @abstractmethod
    def get_pipeline(self, pipeline_id: str) -> PipelineSummary: ...

    @abstractmethod
    def get_pipeline_status(self, pipeline_id: str) -> PipelineRun | None:
        """Latest run of the pipeline."""

    @abstractmethod
    def get_pipeline_stages(self, pipeline_id: str, run_id: str) -> list[PipelineStage]: ...

    @abstractmethod
    def get_pipeline_logs(self, pipeline_id: str, run_id: str, stage: str | None = None) -> list[LogLine]: ...

    @abstractmethod
    def trigger_pipeline(self, pipeline_id: str, ref: str | None = None) -> ActionOutcome: ...


class LoggingProvider(Provider):
    @abstractmethod
    def query_logs(self, service: str, start: datetime, end: datetime, limit: int = 5000) -> list[LogLine]: ...


class ObservabilityProvider(Provider):
    @abstractmethod
    def list_metrics(self, service: str | None = None) -> list[str]: ...

    @abstractmethod
    def get_metric_series(self, metric: str, service: str, start: datetime | None = None,
                          end: datetime | None = None) -> MetricSeries | None: ...

    @abstractmethod
    def watched_series(self) -> list[MetricSeries]:
        """All series the monitor should watch (golden signals per service)."""


class RepositoryProvider(Provider):
    @abstractmethod
    def get_recent_commits(self, repository: str, since: datetime | None = None, limit: int = 10) -> list[Commit]: ...

    @abstractmethod
    def get_commit(self, repository: str, sha: str) -> Commit | None: ...

    @abstractmethod
    def create_pull_request(self, repository: str, title: str, body: str, changes: dict[str, Any]) -> ActionOutcome: ...


class OrchestrationProvider(Provider):
    @abstractmethod
    def list_deployments(self, namespace: str | None = None) -> list[Deployment]: ...

    @abstractmethod
    def get_deployment(self, namespace: str, name: str) -> Deployment | None: ...

    @abstractmethod
    def list_pods(self, namespace: str, service: str | None = None) -> list[Pod]: ...

    @abstractmethod
    def list_events(self, namespace: str, since: datetime | None = None) -> list[K8sEvent]: ...

    @abstractmethod
    def get_deployment_revisions(self, namespace: str, name: str) -> list[DeploymentRevision]: ...

    @abstractmethod
    def rollback(self, namespace: str, name: str, to_revision: int | None = None) -> ActionOutcome: ...

    @abstractmethod
    def restart(self, namespace: str, name: str) -> ActionOutcome: ...

    @abstractmethod
    def patch_config(self, namespace: str, name: str, patch: dict[str, Any]) -> ActionOutcome: ...

    @abstractmethod
    def scale(self, namespace: str, name: str, replicas: int) -> ActionOutcome: ...

    @abstractmethod
    def delete(self, namespace: str, kind: str, name: str) -> ActionOutcome: ...


class QualityProvider(Provider):
    @abstractmethod
    def get_quality_report(self, ref: str) -> QualityReport | None: ...


class ProviderSet(BaseModel):
    """The bundle of providers handed to agents for one run."""
    model_config = {"arbitrary_types_allowed": True}

    pipeline: PipelineProvider
    logging: LoggingProvider
    observability: ObservabilityProvider
    repository: RepositoryProvider
    orchestration: OrchestrationProvider
    quality: QualityProvider

    def describe(self) -> dict[str, str]:
        return {k: getattr(self, k).name for k in
                ("pipeline", "logging", "observability", "repository", "orchestration", "quality")}
