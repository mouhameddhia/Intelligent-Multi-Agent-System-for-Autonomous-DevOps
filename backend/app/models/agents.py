"""Typed input/output contracts for each agent. All are tied to an incident id; no free-form chat."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .incident import (
    ChangeInfo, DeploymentInfo, ExecutionResult, Hypothesis, LogAnalysisSummary, LogPattern, MetricAnomaly,
    PipelineInfo, PolicyDecision, ProposedAction, Severity, TimeWindow, Trigger, VerificationResult,
)


class AgentIO(BaseModel):
    incident_id: str


class WatchConfig(BaseModel):
    namespaces: list[str] = Field(default_factory=list)
    pipelines: list[str] = Field(default_factory=list)
    repositories: list[str] = Field(default_factory=list)


# -- Monitor ----------------------------------------------------------------------------------------
class MonitorInput(AgentIO):
    watch: WatchConfig


class MonitorOutput(AgentIO):
    detected: bool
    title: str = ""
    severity: Severity = Severity.medium
    service: str | None = None
    namespace: str | None = None
    environment: str | None = None
    trigger: Trigger | None = None
    time_window: TimeWindow | None = None
    change: ChangeInfo | None = None
    pipeline: PipelineInfo | None = None
    deployment: DeploymentInfo | None = None
    metric_anomalies: list[MetricAnomaly] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    summary: str = ""


class VerifyInput(AgentIO):
    service: str
    namespace: str | None
    environment: str
    action: ProposedAction
    pipeline: PipelineInfo | None = None
    anomalies: list[MetricAnomaly] = Field(default_factory=list)


class VerifyOutput(AgentIO):
    result: VerificationResult
    summary: str


# -- Log analysis -----------------------------------------------------------------------------------
class LogAnalysisInput(AgentIO):
    service: str
    namespace: str | None
    window: TimeWindow
    pipeline: PipelineInfo | None = None
    runtime_logs: bool = Field(default=True, description="False for pure CI failures: only pipeline logs matter")
    top_n_for_llm: int = 8


class LogAnalysisOutput(AgentIO):
    patterns: list[LogPattern]
    summary: LogAnalysisSummary
    llm_provider: str
    missing_data: list[str] = Field(default_factory=list)


# -- Diagnosis --------------------------------------------------------------------------------------
class DiagnosisInput(AgentIO):
    service: str
    namespace: str | None
    environment: str
    trigger: Trigger
    patterns: list[LogPattern]
    log_summary: LogAnalysisSummary | None
    anomalies: list[MetricAnomaly]
    change: ChangeInfo | None
    pipeline: PipelineInfo | None
    deployment: DeploymentInfo | None
    exclude_history_sources: list[str] = Field(default_factory=list)


class DiagnosisOutput(AgentIO):
    hypotheses: list[Hypothesis]
    summary: str
    llm_provider: str
    sufficient_evidence: bool


# -- Remediation ------------------------------------------------------------------------------------
class RemediationInput(AgentIO):
    service: str
    namespace: str | None
    environment: str
    repository: str | None
    hypothesis: Hypothesis
    change: ChangeInfo | None
    pipeline: PipelineInfo | None
    deployment: DeploymentInfo | None


class RemediationOutput(AgentIO):
    action: ProposedAction | None
    summary: str
    llm_provider: str


# -- Policy gate ------------------------------------------------------------------------------------
class PolicyInput(AgentIO):
    action: ProposedAction


class PolicyOutput(AgentIO):
    decision: PolicyDecision
    effective_action: ProposedAction
    effective_verdict: Literal["auto", "auto_logged", "require_approval", "forbidden"]


# -- Execution --------------------------------------------------------------------------------------
class ExecuteInput(AgentIO):
    action: ProposedAction
    repository: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class ExecuteOutput(AgentIO):
    result: ExecutionResult


class ApprovalDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    approver: str = Field(min_length=1)
    reason: str = Field(min_length=3)
    timestamp: datetime | None = None
