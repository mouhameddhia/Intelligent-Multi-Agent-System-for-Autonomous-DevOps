"""Baseline: static alert rules + a simulated manual investigation.

The static rules are real code run against the same provider data the agents see (Prometheus-style `expr > x for: N`).
The manual investigation time is a documented model (docs/evaluation.md), not a measurement of people:

  investigation = triage + log reading (raw lines / reading speed) + k8s inspection + change correlation + runbook search
  a wrong first guess adds a second investigation loop (REWORK_FACTOR of the first).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..agents.detectors import static_threshold
from ..agents.policy_gate import PolicyGate
from ..connectors.base import ProviderSet
from ..models.incident import ActionType

# Static alert rules (kube-prometheus / SLO style). Order breaks ties when several fire at the same time.
RULES: list[dict[str, Any]] = [
    {"alert": "HighErrorRate", "metric": "error_rate", "threshold": 0.05, "for_s": 300},
    {"alert": "HighLatencyP95", "metric": "latency_p95_ms", "threshold": 500, "for_s": 300},
    {"alert": "KubePodCrashLooping", "metric": "restarts", "threshold": 0, "for_s": 900},
    {"alert": "KubeDeploymentReplicasMismatch", "metric": "unavailable_replicas", "threshold": 0, "for_s": 900},
]
# What an on-call engineer typically suspects first for each alert (ordered guesses).
FIRST_GUESSES: dict[str, list[str]] = {
    "HighErrorRate": ["dependency_unavailable", "code_regression", "config_error"],
    "HighLatencyP95": ["cpu_throttling", "dependency_unavailable", "bad_image"],
    "KubePodCrashLooping": ["config_error", "memory_limit", "dependency_unavailable"],
    "KubeDeploymentReplicasMismatch": ["bad_image", "config_error", "memory_limit"],
    "CIPipelineFailed": ["flaky_test", "code_regression"],
    "UserReport": ["dependency_unavailable"],
}
# During triage the engineer runs `kubectl get pods`; a telling pod reason becomes the first guess.
POD_REASON_GUESS: dict[str, str] = {
    "CrashLoopBackOff": "config_error",
    "OOMKilled": "memory_limit",
    "ImagePullBackOff": "bad_image",
    "ErrImagePull": "bad_image",
}
TRIAGE_MIN = 5.0
LOG_LINES_PER_MIN = 400.0
LOG_READING_CAP_MIN = 25.0
K8S_INSPECTION_MIN = 4.0
CHANGE_CORRELATION_MIN = 8.0
RUNBOOK_SEARCH_MIN = 5.0
REWORK_FACTOR = 0.6
UNDETECTED_FALLBACK_S = 1800.0  # nobody alerted: users report it ~30 min later


@dataclass
class BaselineResult:
    alert: str
    detection_delay_s: float
    investigation_s: float
    time_to_diagnosis_s: float
    guesses: list[str]
    top1: bool
    top3: bool
    runbook_action: str | None
    unsafe_action_executed: bool
    approvals_needed: int
    breakdown: dict[str, float] = field(default_factory=dict)


def run_baseline(providers: ProviderSet, scenario: dict[str, Any], fault_index: int, interval_s: int,
                 raw_log_lines: int, has_change: bool, gate: PolicyGate, runbooks: list[dict[str, Any]]) -> BaselineResult:
    fired: list[tuple[float, int, str]] = []
    for order, rule in enumerate(RULES):
        for s in providers.observability.watched_series():
            if s.name != rule["metric"]:
                continue
            values = [v for _, v in s.points]
            idx = static_threshold(values, rule["threshold"], max(1, int(rule["for_s"] / s.interval_s)))
            if idx is not None and idx >= fault_index:
                fired.append(((idx - fault_index + 1) * interval_s, order, rule["alert"]))
    for pid in scenario.get("watch", {}).get("pipelines", []):
        run = providers.pipeline.get_pipeline_status(pid)
        if run and run.status == "failed" and "deploy" not in pid:
            fired.append((0.0, -1, "CIPipelineFailed"))
    if fired:
        delay, _, alert = min(fired)
    else:
        delay, alert = UNDETECTED_FALLBACK_S, "UserReport"

    gt = scenario["ground_truth"]
    guesses = list(FIRST_GUESSES.get(alert, []))
    for ns in scenario.get("watch", {}).get("namespaces", []):
        for pod in providers.orchestration.list_pods(ns):
            g = POD_REASON_GUESS.get(pod.reason or "")
            if g and g not in guesses[:1]:
                guesses = [g] + [x for x in guesses if x != g]
                break
    top1 = bool(guesses) and guesses[0] == gt["category"]
    top3 = gt["category"] in guesses[:3]

    log_min = min(LOG_READING_CAP_MIN, raw_log_lines / LOG_LINES_PER_MIN)
    k8s_min = K8S_INSPECTION_MIN if scenario.get("environment") != "ci" else 0.0
    change_min = CHANGE_CORRELATION_MIN if has_change else 0.0
    first = TRIAGE_MIN + log_min + k8s_min + change_min + RUNBOOK_SEARCH_MIN
    rework = 0.0 if top1 else first * REWORK_FACTOR
    investigation_s = (first + rework) * 60

    # The engineer applies the runbook fix for the true category; no gate exists in the baseline.
    rb = next((r for r in runbooks if r.get("category") == gt["category"]), None)
    rb_action = rb.get("fix_action") if rb else None
    unsafe = False
    if rb_action:
        verdict, _, _ = gate.check(ActionType(rb_action), scenario.get("environment", "production"))
        unsafe = verdict == "forbidden"
    return BaselineResult(
        alert=alert, detection_delay_s=delay, investigation_s=investigation_s,
        time_to_diagnosis_s=delay + investigation_s, guesses=guesses, top1=top1, top3=top3, runbook_action=rb_action,
        unsafe_action_executed=unsafe, approvals_needed=1,
        breakdown={"triage_min": TRIAGE_MIN, "log_reading_min": round(log_min, 2), "k8s_min": k8s_min,
                   "change_correlation_min": change_min, "runbook_min": RUNBOOK_SEARCH_MIN, "rework_min": round(rework, 2)})
