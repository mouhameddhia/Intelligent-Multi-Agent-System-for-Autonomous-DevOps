"""Simulated adapters: one per provider interface, all backed by a SimulatedWorld that replays a scenario.

The world is mutable: write actions (rollback, patch, scale, PR, rerun…) are recorded, and if an action matches one of
the scenario's `accepted_fixes` the world "heals" — subsequent reads return the post-fix state (`after`, `recovered`
metric values, `after_runs`). A wrong fix leaves the world broken so verification genuinely fails.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .base import (
    ActionOutcome, Commit, Deployment, DeploymentRevision, K8sEvent, LoggingProvider, LogLine, MetricSeries,
    ObservabilityProvider, OrchestrationProvider, PipelineProvider, PipelineRun, PipelineStage, PipelineSummary, Pod,
    QualityProvider, QualityReport, RepositoryProvider,
)

LOG_RE = re.compile(r"^(?P<ts>\S+)\s+(?P<level>[A-Z]+)\s+\[(?P<source>[^\]]+)\]\s+(?P<msg>.*)$")


def parse_ts(v: str | None) -> datetime | None:
    if not v:
        return None
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def parse_log_line(raw: str) -> LogLine:
    m = LOG_RE.match(raw.strip())
    if not m:
        return LogLine(ts=None, source="unknown", level="INFO", message=raw.strip(), raw=raw)
    return LogLine(ts=parse_ts(m["ts"]), source=m["source"], level=m["level"], message=m["msg"], raw=raw)


def list_scenarios(scenarios_dir: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted(scenarios_dir.glob("*/scenario.yaml")):
        out.append(yaml.safe_load(p.read_text()))
    return sorted(out, key=lambda s: s.get("order", 99))


class SimulatedWorld:
    """In-memory state of one replayed scenario (shared by the six simulated adapters of a run)."""

    def __init__(self, scenarios_dir: Path, scenario_id: str) -> None:
        folder = scenarios_dir / scenario_id
        if not folder.exists():
            raise FileNotFoundError(f"unknown scenario {scenario_id}")
        self.folder = folder
        self.scenario: dict[str, Any] = yaml.safe_load((folder / "scenario.yaml").read_text())
        self.metrics: dict[str, Any] = self._json("metrics.json", {"series": []})
        self.k8s: dict[str, Any] = self._json("k8s.json", {"deployments": [], "pods": [], "events": []})
        self.changes: dict[str, Any] = self._json("change.json", {"commits": []})
        self.pipeline: dict[str, Any] = self._json("pipeline.json", {"pipelines": [], "runs": []})
        logs_path = folder / "logs.txt"
        self.raw_logs: list[str] = logs_path.read_text().splitlines() if logs_path.exists() else []
        self.applied: list[dict[str, Any]] = []
        self.healed = False
        self._lock = threading.Lock()
        self.start = parse_ts(self.metrics.get("start")) or datetime.fromisoformat("2026-01-01T00:00:00+00:00")
        self.interval = int(self.metrics.get("interval_s", 30))
        n = max((len(s["values"]) for s in self.metrics.get("series", [])), default=0)
        self.now = self.start + timedelta(seconds=self.interval * max(0, n - 1))
        self.fault_at = self.start + timedelta(seconds=self.interval * int(self.metrics.get("fault_index", 0)))

    def _json(self, name: str, default: Any) -> Any:
        p = self.folder / name
        return json.loads(p.read_text()) if p.exists() else default

    @property
    def id(self) -> str:
        return self.scenario["id"]

    def record_action(self, action: str, target: str, **params: Any) -> bool:
        with self._lock:
            self.applied.append({"action": action, "target": target, "params": params})
            for fx in self.scenario.get("accepted_fixes", []):
                if fx["type"] == action and fx["target"] == target:
                    self.healed = True
                    # time advances while the fix rolls out
                    self.now = self.now + timedelta(minutes=6)
            return self.healed


class _Sim:
    read_only = False

    def __init__(self, world: SimulatedWorld) -> None:
        self.world = world


class SimulatedPipelineProvider(_Sim, PipelineProvider):
    name = "simulated/pipeline"

    def _runs(self) -> list[dict[str, Any]]:
        runs = list(self.world.pipeline.get("runs", []))
        if self.world.healed:
            runs = list(self.world.pipeline.get("after_runs", [])) + runs
        runs += [r for r in self.world.pipeline.get("_triggered", [])]
        return runs

    def list_pipelines(self) -> list[PipelineSummary]:
        return [PipelineSummary(**p) for p in self.world.pipeline.get("pipelines", [])]

    def get_pipeline(self, pipeline_id: str) -> PipelineSummary:
        for p in self.list_pipelines():
            if p.id == pipeline_id:
                return p
        raise KeyError(pipeline_id)

    def _to_run(self, r: dict[str, Any]) -> PipelineRun:
        return PipelineRun(
            pipeline_id=r["pipeline_id"], run_id=r["run_id"], name=r.get("name", r["pipeline_id"]), status=r["status"],
            branch=r.get("branch", "main"), commit_sha=r.get("commit_sha"), url=r.get("url"),
            started_at=parse_ts(r.get("started_at")),
            stages=[PipelineStage(name=s["name"], status=s["status"], started_at=parse_ts(s.get("started_at")),
                                  finished_at=parse_ts(s.get("finished_at"))) for s in r.get("stages", [])],
        )

    def get_pipeline_status(self, pipeline_id: str) -> PipelineRun | None:
        for r in self._runs():
            if r["pipeline_id"] == pipeline_id:
                return self._to_run(r)
        return None

    def get_pipeline_stages(self, pipeline_id: str, run_id: str) -> list[PipelineStage]:
        for r in self._runs():
            if r["pipeline_id"] == pipeline_id and r["run_id"] == run_id:
                return self._to_run(r).stages
        return []

    def get_pipeline_logs(self, pipeline_id: str, run_id: str, stage: str | None = None) -> list[LogLine]:
        for r in self._runs():
            if r["pipeline_id"] == pipeline_id and r["run_id"] == run_id:
                logs: dict[str, list[str]] = r.get("logs", {})
                stages = [stage] if stage else list(logs)
                return [parse_log_line(l) for st in stages for l in logs.get(st, [])]
        return []

    def trigger_pipeline(self, pipeline_id: str, ref: str | None = None) -> ActionOutcome:
        healed = self.world.record_action("rerun_pipeline", pipeline_id, ref=ref)
        run_id = f"sim-{len(self.world.applied)}"
        status = "success" if healed else "failed"
        self.world.pipeline.setdefault("_triggered", []).insert(0, {
            "pipeline_id": pipeline_id, "run_id": run_id, "name": pipeline_id, "status": status, "branch": ref or "main",
            "stages": [{"name": "build", "status": "success"}, {"name": "unit-tests", "status": status}], "logs": {}})
        return ActionOutcome(ok=True, provider=self.name, message=f"Triggered {pipeline_id} run {run_id} on {ref or 'main'}",
                             output={"run_id": run_id})


class SimulatedLoggingProvider(_Sim, LoggingProvider):
    name = "simulated/logging"

    def query_logs(self, service: str, start: datetime, end: datetime, limit: int = 5000) -> list[LogLine]:
        # The simulated store returns every source in the namespace for the window (like a namespace-wide Loki query).
        lines = [parse_log_line(l) for l in self.world.raw_logs]
        out = [l for l in lines if l.ts is None or (start <= l.ts <= end)]
        if self.world.healed:
            out = [l for l in out if l.level not in {"ERROR", "FATAL"}]
        return out[-limit:]


class SimulatedObservabilityProvider(_Sim, ObservabilityProvider):
    name = "simulated/observability"

    def _series(self, s: dict[str, Any]) -> MetricSeries:
        values = list(s["values"])
        if self.world.healed:
            values = values + list(s.get("recovered", []))
        pts = [(self.world.start + timedelta(seconds=self.world.interval * i), float(v)) for i, v in enumerate(values)]
        return MetricSeries(name=s["name"], service=s["service"], unit=s.get("unit", ""),
                            interval_s=self.world.interval, points=pts)

    def list_metrics(self, service: str | None = None) -> list[str]:
        return sorted({s["name"] for s in self.world.metrics.get("series", []) if service in (None, s["service"])})

    def get_metric_series(self, metric: str, service: str, start: datetime | None = None,
                          end: datetime | None = None) -> MetricSeries | None:
        for s in self.world.metrics.get("series", []):
            if s["name"] == metric and s["service"] == service:
                ms = self._series(s)
                ms.points = [(t, v) for t, v in ms.points if (start is None or t >= start) and (end is None or t <= end)]
                return ms
        return None

    def watched_series(self) -> list[MetricSeries]:
        return [self._series(s) for s in self.world.metrics.get("series", [])]


class SimulatedRepositoryProvider(_Sim, RepositoryProvider):
    name = "simulated/repository"

    def _commits(self) -> list[Commit]:
        return [Commit(repository=c["repository"], sha=c["sha"], author=c["author"], message=c["message"],
                       timestamp=parse_ts(c["timestamp"]), files_changed=c.get("files_changed", []),
                       diff=c.get("diff", "")) for c in self.world.changes.get("commits", [])]

    def get_recent_commits(self, repository: str, since: datetime | None = None, limit: int = 10) -> list[Commit]:
        cs = [c for c in self._commits() if c.repository == repository and (since is None or c.timestamp >= since)]
        return sorted(cs, key=lambda c: c.timestamp, reverse=True)[:limit]

    def get_commit(self, repository: str, sha: str) -> Commit | None:
        for c in self._commits():
            if c.repository == repository and c.sha.startswith(sha):
                return c
        return None

    def create_pull_request(self, repository: str, title: str, body: str, changes: dict[str, Any]) -> ActionOutcome:
        target = str(changes.get("service") or changes.get("target") or "")
        self.world.record_action("pull_request", target, title=title)
        number = 4200 + len(self.world.applied)
        return ActionOutcome(ok=True, provider=self.name, message=f"Opened PR #{number}: {title}",
                             output={"number": number, "url": f"https://github.com/{repository}/pull/{number}",
                                     "branch": changes.get("branch", f"autofix/{target}")})


class SimulatedOrchestrationProvider(_Sim, OrchestrationProvider):
    name = "simulated/orchestration"

    def _deployments(self) -> list[Deployment]:
        after = self.world.k8s.get("after", {}) if self.world.healed else {}
        out = []
        for d in self.world.k8s.get("deployments", []):
            dd = dict(d)
            if after:
                if dd["name"] in after.get("replicas_ready", {}):
                    dd["replicas_ready"] = after["replicas_ready"][dd["name"]]
                    if dd["replicas_ready"] == 0:
                        dd["replicas_desired"] = 0
                if dd["name"] in after.get("image", {}):
                    dd["image"] = after["image"][dd["name"]]
            out.append(Deployment(
                name=dd["name"], namespace=dd["namespace"], service=dd["service"], environment=dd["environment"],
                replicas_desired=dd["replicas_desired"], replicas_ready=dd["replicas_ready"], image=dd["image"],
                revisions=[DeploymentRevision(revision=r["revision"], image=r["image"], created_at=parse_ts(r["created_at"]),
                                              change_cause=r.get("change_cause", "")) for r in dd.get("revisions", [])],
                config=dd.get("config", {})))
        return out

    def list_deployments(self, namespace: str | None = None) -> list[Deployment]:
        return [d for d in self._deployments() if namespace in (None, d.namespace)]

    def get_deployment(self, namespace: str, name: str) -> Deployment | None:
        for d in self._deployments():
            if d.namespace == namespace and d.name == name:
                return d
        return None

    def list_pods(self, namespace: str, service: str | None = None) -> list[Pod]:
        pods = self.world.k8s.get("pods", [])
        if self.world.healed and "pods" in self.world.k8s.get("after", {}):
            after = self.world.k8s["after"]["pods"]
            fixed_services = {p["service"] for p in after}
            # scenario (e) lists the full post-fix pod set for the service; others replace the broken pods only
            pods = [p for p in pods if p["service"] not in fixed_services] + after
        return [Pod(**p) for p in pods if p["namespace"] == namespace and service in (None, p["service"])]

    def list_events(self, namespace: str, since: datetime | None = None) -> list[K8sEvent]:
        evs = [K8sEvent(ts=parse_ts(e["ts"]), namespace=e["namespace"], object=e["object"], type=e["type"],
                        reason=e["reason"], message=e["message"]) for e in self.world.k8s.get("events", [])]
        return [e for e in evs if e.namespace == namespace and (since is None or e.ts >= since)]

    def get_deployment_revisions(self, namespace: str, name: str) -> list[DeploymentRevision]:
        d = self.get_deployment(namespace, name)
        return d.revisions if d else []

    def rollback(self, namespace: str, name: str, to_revision: int | None = None) -> ActionOutcome:
        self.world.record_action("rollback", name, to_revision=to_revision)
        return ActionOutcome(ok=True, provider=self.name,
                             message=f"deployment.apps/{name} rolled back to revision {to_revision or 'previous'}",
                             output={"namespace": namespace, "to_revision": to_revision})

    def restart(self, namespace: str, name: str) -> ActionOutcome:
        self.world.record_action("restart", name)
        return ActionOutcome(ok=True, provider=self.name, message=f"deployment.apps/{name} restarted",
                             output={"namespace": namespace})

    def patch_config(self, namespace: str, name: str, patch: dict[str, Any]) -> ActionOutcome:
        self.world.record_action("config_change", name, patch=patch)
        return ActionOutcome(ok=True, provider=self.name, message=f"patched {name}: {json.dumps(patch)}",
                             output={"namespace": namespace, "patch": patch})

    def scale(self, namespace: str, name: str, replicas: int) -> ActionOutcome:
        self.world.record_action("scale", name, replicas=replicas)
        return ActionOutcome(ok=True, provider=self.name, message=f"deployment.apps/{name} scaled to {replicas}",
                             output={"namespace": namespace, "replicas": replicas})

    def delete(self, namespace: str, kind: str, name: str) -> ActionOutcome:
        self.world.record_action("delete_resource", name, kind=kind)
        return ActionOutcome(ok=True, provider=self.name, message=f"{kind}/{name} deleted", output={"namespace": namespace})


class SimulatedQualityProvider(_Sim, QualityProvider):
    name = "simulated/quality"

    def get_quality_report(self, ref: str) -> QualityReport | None:
        key = "quality_after" if self.world.healed and "quality_after" in self.world.pipeline else "quality"
        q = self.world.pipeline.get(key)
        return QualityReport(**q) if q else None
