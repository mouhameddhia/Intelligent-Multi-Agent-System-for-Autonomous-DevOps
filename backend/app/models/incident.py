"""The Incident record: the single shared state of the orchestrator graph and the stored unit.

Every agent reads a projection of this model and returns a typed output that the orchestrator merges back in.
After editing, regenerate the JSON Schema with `make schema` (tests check the exported schema is current).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_incident_id() -> str:
    return f"INC-{secrets.token_hex(3).upper()}"


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class IncidentStatus(str, Enum):
    monitoring = "monitoring"
    investigating = "investigating"
    diagnosing = "diagnosing"
    proposing = "proposing"
    gating = "gating"
    awaiting_approval = "awaiting_approval"
    executing = "executing"
    verifying = "verifying"
    resolved = "resolved"
    rejected = "rejected"
    blocked = "blocked"
    verification_failed = "verification_failed"
    needs_attention = "needs_attention"
    no_incident = "no_incident"


TERMINAL_STATUSES = {
    IncidentStatus.resolved,
    IncidentStatus.rejected,
    IncidentStatus.blocked,
    IncidentStatus.verification_failed,
    IncidentStatus.needs_attention,
    IncidentStatus.no_incident,
}


class ActionType(str, Enum):
    read = "read"
    diagnose = "diagnose"
    propose = "propose"
    rerun_pipeline = "rerun_pipeline"
    restart = "restart"
    rollback = "rollback"
    config_change = "config_change"
    pull_request = "pull_request"
    scale = "scale"
    delete_resource = "delete_resource"


class Risk(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class TimeWindow(Strict):
    start: datetime
    end: datetime


class Trigger(Strict):
    kind: Literal["pipeline_failure", "deployment_failure", "pod_failure", "metric_anomaly"]
    source: str = Field(description="Provider that produced the signal, e.g. orchestration/simulated")
    description: str
    detected_at: datetime
    fault_started_at: datetime | None = Field(
        default=None, description="Ground-truth fault start when known (simulation/evaluation only)"
    )
    signals: list[str] = Field(default_factory=list)


class ChangeInfo(Strict):
    repository: str
    commit_sha: str
    author: str
    message: str
    timestamp: datetime
    files_changed: list[str] = Field(default_factory=list)
    diff: str = ""


class PipelineInfo(Strict):
    provider: str
    pipeline_id: str
    name: str
    run_id: str
    status: str
    failed_stage: str | None = None
    branch: str | None = None
    url: str | None = None


class PodState(Strict):
    name: str
    phase: str
    reason: str | None = None
    restarts: int = 0
    ready: bool = False


class DeploymentInfo(Strict):
    namespace: str
    name: str
    service: str
    environment: str
    current_revision: int | None = None
    previous_revision: int | None = None
    image: str | None = None
    previous_image: str | None = None
    replicas_ready: int | None = None
    replicas_desired: int | None = None
    pods: list[PodState] = Field(default_factory=list)
    recent_events: list[str] = Field(default_factory=list)


class LogPattern(Strict):
    pattern_id: str
    template: str
    count: int
    level: str
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    sample: str
    sources: list[str] = Field(default_factory=list)


class LogAnalysisSummary(Strict):
    raw_line_count: int
    pattern_count: int
    patterns_sent_to_llm: int
    reduction_ratio: float = Field(description="1 - (lines in LLM context / raw lines)")
    interpretation: str
    key_errors: list[str] = Field(default_factory=list)


class MetricPoint(Strict):
    ts: datetime
    value: float


class MetricAnomaly(Strict):
    metric: str
    service: str
    unit: str = ""
    detector: Literal["ewma", "zscore"]
    baseline: float
    observed: float
    score: float
    threshold: float
    anomaly_start: datetime
    series: list[MetricPoint] = Field(default_factory=list)


class Evidence(Strict):
    kind: Literal["log", "metric", "change", "k8s", "pipeline", "history", "quality"]
    ref: str = Field(description="Pointer into the incident, e.g. log:P03, metric:error_rate, change:abc123")
    description: str
    weight: float = 0.0


class Hypothesis(Strict):
    id: str
    rank: int
    title: str
    category: str
    component: str
    description: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)
    similar_incidents: list[str] = Field(default_factory=list)


class ActionTarget(Strict):
    kind: str = Field(description="deployment | configmap | pipeline | repository | pod | ...")
    name: str
    namespace: str | None = None
    environment: str


class ProposedAction(Strict):
    type: ActionType
    target: ActionTarget
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_effect: str
    risk: Risk
    reversible: bool = True
    rationale: str = ""
    hypothesis_id: str | None = None
    source: str = Field(default="remediation", description="remediation | policy_gate_alternative")


class PolicyDecision(Strict):
    verdict: Literal["auto", "auto_logged", "require_approval", "forbidden"]
    rule_id: str
    reason: str
    action_type: ActionType
    environment: str
    evaluated_at: datetime
    blocked_action: ProposedAction | None = None
    alternative: ProposedAction | None = None
    alternative_verdict: Literal["auto", "auto_logged", "require_approval", "forbidden"] | None = None


class Approval(Strict):
    required: bool
    decision: Literal["pending", "approved", "rejected", "not_required"]
    approver: str | None = None
    reason: str | None = None
    timestamp: datetime | None = None


class ExecutionResult(Strict):
    status: Literal["success", "failed", "skipped"]
    action_type: ActionType
    provider: str
    details: str
    output: dict[str, Any] = Field(default_factory=dict)
    executed_at: datetime


class VerificationCheck(Strict):
    name: str
    passed: bool
    detail: str


class VerificationResult(Strict):
    status: Literal["healthy", "unhealthy", "inconclusive"]
    checks: list[VerificationCheck] = Field(default_factory=list)
    verified_at: datetime


class TimelineEvent(Strict):
    ts: datetime
    agent: str
    kind: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


AgentName = Literal[
    "monitor", "log_analysis", "diagnosis", "remediation", "policy_gate",
    "human_approval", "execute", "verify", "record",
]
AGENT_ORDER: list[str] = [
    "monitor", "log_analysis", "diagnosis", "remediation", "policy_gate",
    "human_approval", "execute", "verify", "record",
]


class AgentStep(Strict):
    agent: str
    status: Literal["pending", "running", "completed", "skipped", "failed", "waiting"] = "pending"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float | None = None
    summary: str = ""
    llm_provider: str | None = None


class RunMetrics(Strict):
    """Measurements captured during the run, used by the evaluation tab."""
    detection_delay_s: float | None = Field(default=None, description="Simulated time from fault to detection")
    analysis_wall_ms: float | None = Field(default=None, description="Wall-clock time from detection to diagnosis")
    raw_log_lines: int | None = None
    llm_context_lines: int | None = None


class Incident(Strict):
    id: str = Field(default_factory=new_incident_id)
    title: str = "Monitoring…"
    scenario_id: str | None = None
    source: Literal["demo", "evaluation", "api"] = "demo"
    severity: Severity = Severity.medium
    status: IncidentStatus = IncidentStatus.monitoring
    service: str | None = None
    environment: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    time_window: TimeWindow | None = None
    trigger: Trigger | None = None
    change: ChangeInfo | None = None
    pipeline: PipelineInfo | None = None
    deployment: DeploymentInfo | None = None
    log_patterns: list[LogPattern] = Field(default_factory=list)
    log_summary: LogAnalysisSummary | None = None
    metric_anomalies: list[MetricAnomaly] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    proposed_action: ProposedAction | None = None
    policy_decision: PolicyDecision | None = None
    approval: Approval | None = None
    execution_result: ExecutionResult | None = None
    verification_result: VerificationResult | None = None
    timeline: list[TimelineEvent] = Field(default_factory=list)
    steps: list[AgentStep] = Field(default_factory=lambda: [AgentStep(agent=a) for a in AGENT_ORDER])
    missing_data: list[str] = Field(default_factory=list)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    llm_providers_used: list[str] = Field(default_factory=list)

    # -- helpers -----------------------------------------------------------------------------------
    def step(self, agent: str) -> AgentStep:
        for s in self.steps:
            if s.agent == agent:
                return s
        s = AgentStep(agent=agent)
        self.steps.append(s)
        return s

    def log(self, agent: str, kind: str, message: str, **data: Any) -> None:
        self.timeline.append(TimelineEvent(ts=utcnow(), agent=agent, kind=kind, message=message, data=data))
        self.updated_at = utcnow()

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES
