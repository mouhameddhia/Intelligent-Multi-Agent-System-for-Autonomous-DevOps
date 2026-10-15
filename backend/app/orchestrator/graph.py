"""LangGraph state graph: Monitor → LogAnalysis → Diagnosis → Remediation → PolicyGate → HumanApproval (interrupt /
resume) → Execute → Verify → Record.

The graph state carries the Incident record (as JSON) plus a routing hint. Every node merges a typed agent output into
the incident, persists it, writes audit rows and publishes SSE events. Missing data stops the run and flags it.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..agents.diagnosis import DiagnosisAgent
from ..agents.executor import Executor
from ..agents.log_analysis import LogAnalysisAgent
from ..agents.monitor import MonitorAgent
from ..agents.policy_gate import PolicyGate
from ..agents.remediation import RemediationAgent
from ..config import Settings
from ..connectors.base import ProviderSet
from ..connectors.simulated import SimulatedWorld
from ..events import EventBus
from ..llm.client import LLMClient
from ..models.agents import (
    ApprovalDecision, DiagnosisInput, ExecuteInput, LogAnalysisInput, MonitorInput, PolicyInput, RemediationInput,
    VerifyInput, WatchConfig,
)
from ..models.incident import AGENT_ORDER, ActionType, Approval, Incident, IncidentStatus, utcnow
from ..store.db import Store
from ..store.history import HistoryIndex

log = logging.getLogger(__name__)


class GraphState(TypedDict):
    incident: dict[str, Any]
    route: str


@dataclass
class RunContext:
    settings: Settings
    store: Store
    bus: EventBus
    llm: LLMClient
    history: HistoryIndex
    providers: ProviderSet
    watch: WatchConfig
    world: SimulatedWorld | None = None
    step_delay: float = 0.0
    record_history: bool = True
    exclude_history_sources: list[str] = field(default_factory=list)
    auto_approver: Callable[[Incident], ApprovalDecision] | None = None


def build_graph(ctx: RunContext) -> Any:
    s = ctx.settings
    monitor = MonitorAgent(ctx.providers)
    log_agent = LogAnalysisAgent(ctx.providers, ctx.llm)
    diag = DiagnosisAgent(ctx.providers, ctx.llm, ctx.history, s.load_yaml("signatures.yaml"))
    remed = RemediationAgent(ctx.providers, ctx.llm, ctx.store, s.load_yaml("remediations.yaml"))
    gate = PolicyGate(s.load_yaml("policies.yaml"))
    executor = Executor(ctx.providers)

    # -- helpers ---------------------------------------------------------------------------------
    def persist(inc: Incident, event_type: str = "incident.updated", **extra: Any) -> None:
        inc.updated_at = utcnow()
        ctx.store.save_incident(inc)
        ctx.bus.publish({"type": event_type, "incident_id": inc.id, "incident": inc.model_dump(mode="json"), **extra})

    def audit(inc: Incident, actor: str, what: str, outcome: str = "", /, **detail: Any) -> None:
        ctx.store.audit(inc.id, actor, what, outcome, detail)

    def gate_read(inc: Incident, agent: str, action: ActionType) -> None:
        verdict, rule, _ = gate.check(action, inc.environment or "unknown")
        audit(inc, agent, f"authorize:{action.value}", verdict, rule=rule)

    def node(name: str, fn: Callable[[Incident], str]) -> Callable[[GraphState], GraphState]:
        def wrapped(state: GraphState) -> GraphState:
            inc = Incident.model_validate(state["incident"])
            if name == "human_approval":  # resuming after interrupt: continue from the persisted waiting state
                stored = ctx.store.get_incident(inc.id)
                if stored and stored.status == IncidentStatus.awaiting_approval:
                    inc = stored
            step = inc.step(name)
            if step.status != "waiting":
                step.status, step.started_at, step.finished_at = "running", utcnow(), None
                persist(inc, "agent.started", agent=name)
                if ctx.step_delay:
                    time.sleep(ctx.step_delay)
            t0 = time.perf_counter()
            try:
                route = fn(inc)
            except GraphBubbleUp:
                raise
            except Exception as e:  # noqa: BLE001 - a failing agent flags the incident instead of crashing the run
                log.exception("agent %s failed", name)
                step.status = "failed"
                step.summary = f"{type(e).__name__}: {e}"
                inc.missing_data.append(f"{name} failed: {type(e).__name__}")
                inc.status = IncidentStatus.needs_attention
                inc.log(name, "error", f"{name} failed: {e}")
                audit(inc, name, "agent_failed", "error", error=str(e))
                route = "record"
            if step.status == "running":
                step.status = "completed"
            step.finished_at = utcnow()
            step.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
            persist(inc, "agent.completed", agent=name)
            return {"incident": inc.model_dump(mode="json"), "route": route}
        return wrapped

    # -- nodes -----------------------------------------------------------------------------------
    def n_monitor(inc: Incident) -> str:
        out = monitor.run(MonitorInput(incident_id=inc.id, watch=ctx.watch))
        st = inc.step("monitor")
        st.summary = out.summary
        audit(inc, "monitor", "scan", "detected" if out.detected else "healthy", summary=out.summary)
        if not out.detected:
            inc.status = IncidentStatus.no_incident
            inc.title = "No incident detected"
            return "record"
        inc.title, inc.severity, inc.service, inc.environment = out.title, out.severity, out.service, out.environment
        inc.trigger, inc.time_window, inc.change = out.trigger, out.time_window, out.change
        inc.pipeline, inc.deployment, inc.metric_anomalies = out.pipeline, out.deployment, out.metric_anomalies
        inc.missing_data += out.missing_data
        if ctx.world is not None and inc.trigger:
            inc.trigger.fault_started_at = ctx.world.fault_at
            inc.metrics.detection_delay_s = max(0.0, (inc.trigger.detected_at - ctx.world.fault_at).total_seconds())
        inc.status = IncidentStatus.investigating
        inc.log("monitor", "detected", out.summary, trigger=out.trigger.kind if out.trigger else None)
        audit(inc, "monitor", "open_incident", inc.severity.value, title=inc.title)
        if not inc.service:
            inc.missing_data.append("service could not be determined")
            inc.status = IncidentStatus.needs_attention
            return "record"
        return "log_analysis"

    def n_log_analysis(inc: Incident) -> str:
        gate_read(inc, "log_analysis", ActionType.read)
        assert inc.time_window is not None
        out = log_agent.run(LogAnalysisInput(
            incident_id=inc.id, service=inc.service or "", namespace=inc.deployment.namespace if inc.deployment else None,
            window=inc.time_window, pipeline=inc.pipeline,
            runtime_logs=not (inc.trigger and inc.trigger.kind == "pipeline_failure")))
        inc.log_patterns, inc.log_summary = out.patterns, out.summary
        inc.metrics.raw_log_lines = out.summary.raw_line_count
        inc.metrics.llm_context_lines = out.summary.patterns_sent_to_llm
        inc.llm_providers_used.append(f"log_analysis:{out.llm_provider}")
        st = inc.step("log_analysis")
        st.llm_provider = out.llm_provider
        st.summary = (f"{out.summary.raw_line_count} lines → {out.summary.pattern_count} patterns; "
                      f"{out.summary.patterns_sent_to_llm} sent to LLM ({out.summary.reduction_ratio:.1%} reduction)")
        inc.log("log_analysis", "patterns", st.summary)
        audit(inc, "log_analysis", "analyze_logs", "ok", raw=out.summary.raw_line_count, patterns=out.summary.pattern_count,
              llm=out.llm_provider)
        if out.missing_data:
            inc.missing_data += out.missing_data
            if not inc.metric_anomalies and not (inc.deployment and inc.deployment.pods):
                inc.status = IncidentStatus.needs_attention
                inc.log("log_analysis", "missing_data", "No logs and no other evidence: stopping for a human.")
                return "record"
        inc.status = IncidentStatus.diagnosing
        return "diagnosis"

    def n_diagnosis(inc: Incident) -> str:
        gate_read(inc, "diagnosis", ActionType.diagnose)
        assert inc.trigger is not None
        out = diag.run(DiagnosisInput(
            incident_id=inc.id, service=inc.service or "", namespace=inc.deployment.namespace if inc.deployment else (ctx.watch.namespaces or [None])[0],
            environment=inc.environment or "unknown", trigger=inc.trigger, patterns=inc.log_patterns,
            log_summary=inc.log_summary, anomalies=inc.metric_anomalies, change=inc.change, pipeline=inc.pipeline,
            deployment=inc.deployment, exclude_history_sources=ctx.exclude_history_sources))
        inc.hypotheses = out.hypotheses
        inc.llm_providers_used.append(f"diagnosis:{out.llm_provider}")
        st = inc.step("diagnosis")
        st.llm_provider = out.llm_provider
        st.summary = out.summary
        inc.log("diagnosis", "hypotheses", out.summary, top=[h.title for h in out.hypotheses])
        audit(inc, "diagnosis", "rank_hypotheses", "ok" if out.sufficient_evidence else "insufficient",
              hypotheses=[(h.category, h.confidence) for h in out.hypotheses], llm=out.llm_provider)
        if not out.sufficient_evidence:
            inc.status = IncidentStatus.needs_attention
            inc.missing_data.append("insufficient evidence for diagnosis")
            return "record"
        inc.status = IncidentStatus.proposing
        return "remediation"

    def n_remediation(inc: Incident) -> str:
        gate_read(inc, "remediation", ActionType.propose)
        top = inc.hypotheses[0]
        out = remed.run(RemediationInput(
            incident_id=inc.id, service=inc.service or "", namespace=inc.deployment.namespace if inc.deployment else (ctx.watch.namespaces or [None])[0],
            environment=inc.environment or "unknown", repository=(ctx.watch.repositories or [None])[0], hypothesis=top,
            change=inc.change, pipeline=inc.pipeline, deployment=inc.deployment))
        st = inc.step("remediation")
        st.llm_provider = out.llm_provider
        st.summary = out.summary
        inc.llm_providers_used.append(f"remediation:{out.llm_provider}")
        audit(inc, "remediation", "propose_action", "proposed" if out.action else "none",
              action=out.action.model_dump(mode="json") if out.action else None)
        if not out.action:
            inc.status = IncidentStatus.needs_attention
            inc.missing_data.append("no remediation could be built")
            return "record"
        inc.proposed_action = out.action
        inc.log("remediation", "proposal", out.summary)
        inc.status = IncidentStatus.gating
        return "policy_gate"

    def n_policy(inc: Incident) -> str:
        assert inc.proposed_action is not None
        out = gate.evaluate(PolicyInput(incident_id=inc.id, action=inc.proposed_action))
        d = out.decision
        inc.policy_decision = d
        st = inc.step("policy_gate")
        if d.verdict == "forbidden":
            audit(inc, "policy_gate", f"evaluate:{d.action_type.value}", "forbidden", rule=d.rule_id, reason=d.reason)
            inc.log("policy_gate", "blocked", f"BLOCKED {d.action_type.value} on {inc.proposed_action.target.name} ({d.rule_id}).")
            if d.alternative:
                inc.proposed_action = out.effective_action
                inc.log("policy_gate", "alternative", f"Safer alternative: {d.alternative.type.value} → {out.effective_verdict}")
                audit(inc, "policy_gate", f"evaluate:{d.alternative.type.value}", out.effective_verdict, alternative=True)
            st.summary = (f"Blocked {d.action_type.value} ({d.rule_id}); alternative "
                          f"{d.alternative.type.value + ' → ' + out.effective_verdict if d.alternative else 'none'}")
        else:
            audit(inc, "policy_gate", f"evaluate:{d.action_type.value}", d.verdict, rule=d.rule_id, reason=d.reason)
            inc.log("policy_gate", "verdict", f"{d.verdict} by {d.rule_id}: {d.reason}")
            st.summary = f"{d.verdict.replace('_', ' ')} ({d.rule_id})"
        v = out.effective_verdict
        if v == "forbidden":
            inc.status = IncidentStatus.blocked
            inc.approval = Approval(required=False, decision="not_required")
            return "record"
        if v == "require_approval":
            inc.approval = Approval(required=True, decision="pending")
            return "human_approval"
        inc.approval = Approval(required=False, decision="not_required")
        inc.step("human_approval").status = "skipped"
        inc.step("human_approval").summary = f"Not required ({v.replace('_', ' ')})"
        return "execute"

    def n_human(inc: Incident) -> str:
        st = inc.step("human_approval")
        if ctx.auto_approver is not None:
            decision = ctx.auto_approver(inc)
        else:
            stored = ctx.store.get_incident(inc.id)
            if not stored or stored.status != IncidentStatus.awaiting_approval:
                inc.status = IncidentStatus.awaiting_approval
                st.status = "waiting"
                st.summary = "Waiting for a human decision"
                inc.log("human_approval", "requested", "Approval requested for "
                        f"{inc.proposed_action.type.value if inc.proposed_action else '?'}")
                audit(inc, "orchestrator", "approval_requested", "pending")
                persist(inc, "approval.required", agent="human_approval")
            raw = interrupt({"incident_id": inc.id, "action": inc.proposed_action.model_dump(mode="json") if inc.proposed_action else None})
            decision = ApprovalDecision.model_validate(raw)
        inc.approval = Approval(required=True, decision=decision.decision, approver=decision.approver,
                                reason=decision.reason, timestamp=decision.timestamp or utcnow())
        st.status = "completed"
        st.summary = f"{decision.decision} by {decision.approver}: {decision.reason}"
        inc.log("human_approval", decision.decision, st.summary)
        audit(inc, decision.approver, f"approval:{decision.decision}", decision.decision, reason=decision.reason)
        if decision.decision == "rejected":
            inc.status = IncidentStatus.rejected
            return "record"
        return "execute"

    def n_execute(inc: Incident) -> str:
        assert inc.proposed_action is not None
        inc.status = IncidentStatus.executing
        out = executor.run(ExecuteInput(incident_id=inc.id, action=inc.proposed_action,
                                        repository=(ctx.watch.repositories or [None])[0]))
        inc.execution_result = out.result
        st = inc.step("execute")
        st.summary = f"{out.result.status}: {out.result.details}"
        inc.log("execute", out.result.status, out.result.details)
        audit(inc, "executor", f"execute:{out.result.action_type.value}", out.result.status,
              provider=out.result.provider, details=out.result.details,
              verdict=inc.policy_decision.alternative_verdict or inc.policy_decision.verdict if inc.policy_decision else None)
        if out.result.status != "success":
            st.status = "failed"
            inc.status = IncidentStatus.needs_attention
            return "record"
        inc.status = IncidentStatus.verifying
        return "verify"

    def n_verify(inc: Incident) -> str:
        assert inc.proposed_action is not None
        uses_pipeline = inc.proposed_action.type in (ActionType.rerun_pipeline, ActionType.pull_request)
        out = monitor.verify(VerifyInput(
            incident_id=inc.id, service=inc.service or "", namespace=inc.deployment.namespace if inc.deployment else None,
            environment=inc.environment or "unknown", action=inc.proposed_action,
            pipeline=inc.pipeline if uses_pipeline else None, anomalies=inc.metric_anomalies))
        inc.verification_result = out.result
        st = inc.step("verify")
        st.summary = out.summary
        inc.log("verify", out.result.status, out.summary)
        audit(inc, "monitor", "verify", out.result.status, checks=[c.model_dump() for c in out.result.checks])
        inc.status = IncidentStatus.resolved if out.result.status == "healthy" else IncidentStatus.verification_failed
        if out.result.status != "healthy":
            st.status = "failed"
        return "record"

    def n_record(inc: Incident) -> str:
        for st in inc.steps:
            if st.status == "pending":
                st.status = "skipped"
        if not inc.is_terminal:
            inc.status = IncidentStatus.needs_attention
        if inc.time_window:
            inc.time_window.end = max(inc.time_window.end, inc.time_window.start)
        inc.metrics.analysis_wall_ms = round(sum(inc.step(a).duration_ms or 0 for a in ("log_analysis", "diagnosis")), 1)
        recorded = False
        if ctx.record_history and inc.status == IncidentStatus.resolved and inc.hypotheses and inc.proposed_action:
            h = inc.hypotheses[0]
            ctx.store.upsert_history([{
                "id": inc.id, "service": inc.service or "*",
                "time": (inc.trigger.detected_at if inc.trigger else inc.created_at).isoformat(),
                "symptoms": f"{inc.trigger.description if inc.trigger else ''} "
                            + " ".join(p.template for p in inc.log_patterns[:3]),
                "root_cause": f"{h.title}. {h.description}",
                "fix": f"{inc.proposed_action.type.value} on {inc.proposed_action.target.name}: {inc.proposed_action.expected_effect}",
                "category": h.category, "fix_action": inc.proposed_action.type.value, "source": "agent",
                "use_for_retrieval": True}])
            recorded = True
        st = inc.step("record")
        st.summary = f"Final status: {inc.status.value}" + ("; added to history store" if recorded else "")
        inc.log("record", "closed", st.summary)
        audit(inc, "orchestrator", "record", inc.status.value, recorded_in_history=recorded)
        return "end"

    g = StateGraph(GraphState)
    for name, fn in [("monitor", n_monitor), ("log_analysis", n_log_analysis), ("diagnosis", n_diagnosis),
                     ("remediation", n_remediation), ("policy_gate", n_policy), ("human_approval", n_human),
                     ("execute", n_execute), ("verify", n_verify), ("record", n_record)]:
        g.add_node(name, node(name, fn))
    g.add_edge(START, "monitor")

    def by_route(state: GraphState) -> str:
        return state["route"]

    g.add_conditional_edges("monitor", by_route, {"log_analysis": "log_analysis", "record": "record"})
    g.add_conditional_edges("log_analysis", by_route, {"diagnosis": "diagnosis", "record": "record"})
    g.add_conditional_edges("diagnosis", by_route, {"remediation": "remediation", "record": "record"})
    g.add_conditional_edges("remediation", by_route, {"policy_gate": "policy_gate", "record": "record"})
    g.add_conditional_edges("policy_gate", by_route, {"execute": "execute", "human_approval": "human_approval", "record": "record"})
    g.add_conditional_edges("human_approval", by_route, {"execute": "execute", "record": "record"})
    g.add_conditional_edges("execute", by_route, {"verify": "verify", "record": "record"})
    g.add_edge("verify", "record")
    g.add_edge("record", END)
    return g.compile(checkpointer=InMemorySaver())


GRAPH_NODES = AGENT_ORDER
