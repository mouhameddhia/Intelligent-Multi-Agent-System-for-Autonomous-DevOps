"""Run manager: starts graph runs for scenarios, keeps interrupted runs in memory and resumes them on approval.

If the process restarted while an incident was awaiting approval, the run is rehydrated from the stored incident
(LangGraph `update_state(..., as_node="policy_gate")`) and then resumed.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from langgraph.types import Command

from ..config import Settings
from ..connectors.registry import build_providers
from ..connectors.simulated import SimulatedWorld
from ..events import EventBus
from ..llm.client import LLMClient
from ..models.agents import ApprovalDecision, WatchConfig
from ..models.incident import Incident, IncidentStatus, utcnow
from ..store.db import Store
from ..store.history import HistoryIndex
from .graph import RunContext, build_graph

log = logging.getLogger(__name__)


@dataclass
class IncidentRun:
    incident_id: str
    graph: Any
    ctx: RunContext
    config: dict[str, Any]


class RunManager:
    def __init__(self, settings: Settings, store: Store, bus: EventBus, history: HistoryIndex, llm: LLMClient) -> None:
        self.settings = settings
        self.store = store
        self.bus = bus
        self.history = history
        self.llm = llm
        self.runs: dict[str, IncidentRun] = {}
        self._lock = threading.Lock()

    def _context(self, scenario_id: str, *, step_delay: float, record_history: bool,
                 exclude_history_sources: list[str], auto_approver: Callable[[Incident], ApprovalDecision] | None) -> RunContext:
        world = SimulatedWorld(self.settings.scenarios_dir, scenario_id)
        providers = build_providers(self.settings, world)
        watch = WatchConfig(**world.scenario.get("watch", {}))
        return RunContext(settings=self.settings, store=self.store, bus=self.bus, llm=self.llm, history=self.history,
                          providers=providers, watch=watch, world=world, step_delay=step_delay,
                          record_history=record_history, exclude_history_sources=exclude_history_sources,
                          auto_approver=auto_approver)

    def create(self, scenario_id: str, *, source: str = "demo", step_delay: float | None = None,
               record_history: bool = True, exclude_history_sources: list[str] | None = None,
               auto_approver: Callable[[Incident], ApprovalDecision] | None = None) -> IncidentRun:
        ctx = self._context(scenario_id, step_delay=self.settings.step_delay_s if step_delay is None else step_delay,
                            record_history=record_history, exclude_history_sources=exclude_history_sources or [],
                            auto_approver=auto_approver)
        inc = Incident(scenario_id=scenario_id, source=source,  # type: ignore[arg-type]
                       title=f"Replaying scenario: {ctx.world.scenario.get('title', scenario_id) if ctx.world else scenario_id}")
        inc.log("orchestrator", "run_started", f"Scenario '{scenario_id}' replay started",
                adapters=ctx.providers.describe(), llm_chain=self.llm.active_chain())
        self.store.save_incident(inc)
        self.store.audit(inc.id, "orchestrator", "run_started", "ok", {"scenario": scenario_id, "source": source,
                                                                         "adapters": ctx.providers.describe()})
        self.bus.publish({"type": "incident.updated", "incident_id": inc.id, "incident": inc.model_dump(mode="json")})
        run = IncidentRun(inc.id, build_graph(ctx), ctx, {"configurable": {"thread_id": inc.id}})
        with self._lock:
            self.runs[inc.id] = run
        return run

    def execute(self, run: IncidentRun) -> Incident:
        inc = self.store.get_incident(run.incident_id)
        assert inc is not None
        try:
            run.graph.invoke({"incident": inc.model_dump(mode="json"), "route": "monitor"}, run.config)
        except Exception:  # noqa: BLE001
            log.exception("run %s crashed", run.incident_id)
            self._fail(run.incident_id, "orchestrator crashed")
        return self._finish(run)

    def _finish(self, run: IncidentRun) -> Incident:
        inc = self.store.get_incident(run.incident_id)
        assert inc is not None
        if inc.is_terminal:
            with self._lock:
                self.runs.pop(run.incident_id, None)
        return inc

    def _fail(self, incident_id: str, why: str) -> None:
        inc = self.store.get_incident(incident_id)
        if inc and not inc.is_terminal:
            inc.status = IncidentStatus.needs_attention
            inc.missing_data.append(why)
            inc.log("orchestrator", "error", why)
            self.store.save_incident(inc)
            self.bus.publish({"type": "incident.updated", "incident_id": inc.id, "incident": inc.model_dump(mode="json")})

    def start_async(self, scenario_id: str, **kw: Any) -> Incident:
        run = self.create(scenario_id, **kw)
        threading.Thread(target=self.execute, args=(run,), daemon=True, name=f"run-{run.incident_id}").start()
        inc = self.store.get_incident(run.incident_id)
        assert inc is not None
        return inc

    def run_sync(self, scenario_id: str, **kw: Any) -> Incident:
        return self.execute(self.create(scenario_id, **kw))

    # -- approval ---------------------------------------------------------------------------------
    def _rehydrate(self, inc: Incident) -> IncidentRun:
        assert inc.scenario_id
        ctx = self._context(inc.scenario_id, step_delay=self.settings.step_delay_s, record_history=inc.source != "evaluation",
                            exclude_history_sources=["agent"] if inc.source == "evaluation" else [], auto_approver=None)
        run = IncidentRun(inc.id, build_graph(ctx), ctx, {"configurable": {"thread_id": inc.id}})
        run.graph.update_state(run.config, {"incident": inc.model_dump(mode="json"), "route": "human_approval"},
                               as_node="policy_gate")
        run.graph.invoke(None, run.config)  # runs up to the interrupt again
        inc.log("orchestrator", "rehydrated", "Run restored from storage after a restart")
        with self._lock:
            self.runs[inc.id] = run
        return run

    def submit_approval(self, incident_id: str, decision: ApprovalDecision, wait: bool = False) -> Incident:
        inc = self.store.get_incident(incident_id)
        if inc is None:
            raise KeyError(incident_id)
        if inc.status != IncidentStatus.awaiting_approval:
            raise ValueError(f"incident {incident_id} is not awaiting approval (status={inc.status.value})")
        decision.timestamp = decision.timestamp or utcnow()
        with self._lock:
            run = self.runs.get(incident_id)
        if run is None:
            run = self._rehydrate(inc)

        def resume() -> None:
            try:
                run.graph.invoke(Command(resume=decision.model_dump(mode="json")), run.config)
            except Exception:  # noqa: BLE001
                log.exception("resume %s crashed", incident_id)
                self._fail(incident_id, "orchestrator crashed during resume")
            self._finish(run)

        if wait:
            resume()
        else:
            threading.Thread(target=resume, daemon=True, name=f"resume-{incident_id}").start()
        out = self.store.get_incident(incident_id)
        assert out is not None
        return out
