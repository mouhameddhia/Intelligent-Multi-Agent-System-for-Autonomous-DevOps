"""Thin real adapters: GitHub Actions, Prometheus, Loki, Kubernetes.

They are intentionally small: enough read paths for the agents to work against a real system, read-only by default
(`allow_writes: false` raises ReadOnlyError on any mutation), and disabled unless enabled in config/adapters.yaml.
Credentials are read from environment variables named in the config, never from files in the repo.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .base import (
    ActionOutcome, Commit, Deployment, DeploymentRevision, K8sEvent, LoggingProvider, LogLine, MetricSeries,
    ObservabilityProvider, OrchestrationProvider, PipelineProvider, PipelineRun, PipelineStage, PipelineSummary, Pod,
    ProviderUnavailable, ReadOnlyError, RepositoryProvider,
)


def _dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


class _Real:
    def __init__(self, cfg: dict[str, Any]) -> None:
        if not cfg.get("enabled"):
            raise ProviderUnavailable(f"{self.__class__.__name__} is disabled in config/adapters.yaml")
        self.cfg = cfg
        self.read_only = not bool(cfg.get("allow_writes", False))

    def _guard_write(self, what: str) -> None:
        if self.read_only:
            raise ReadOnlyError(f"{self.name}: '{what}' refused, adapter is read-only (allow_writes: false)")

    def _token(self) -> str | None:
        env = self.cfg.get("token_env")
        return os.getenv(env) if env else None


class GitHubActionsAdapter(_Real, PipelineProvider, RepositoryProvider):
    """PipelineProvider + RepositoryProvider over the GitHub REST API."""
    name = "github_actions"

    def __init__(self, cfg: dict[str, Any]) -> None:
        super().__init__(cfg)
        headers = {"Accept": "application/vnd.github+json"}
        if tok := self._token():
            headers["Authorization"] = f"Bearer {tok}"
        self.repo = cfg["repository"]
        self.http = httpx.Client(base_url=cfg.get("api_url", "https://api.github.com"), headers=headers, timeout=15)

    def _get(self, path: str, **params: Any) -> Any:
        r = self.http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def list_pipelines(self) -> list[PipelineSummary]:
        data = self._get(f"/repos/{self.repo}/actions/workflows")
        return [PipelineSummary(id=str(w["id"]), name=w["name"], repository=self.repo) for w in data.get("workflows", [])]

    def get_pipeline(self, pipeline_id: str) -> PipelineSummary:
        w = self._get(f"/repos/{self.repo}/actions/workflows/{pipeline_id}")
        return PipelineSummary(id=str(w["id"]), name=w["name"], repository=self.repo)

    def get_pipeline_status(self, pipeline_id: str) -> PipelineRun | None:
        runs = self._get(f"/repos/{self.repo}/actions/workflows/{pipeline_id}/runs", per_page=1).get("workflow_runs", [])
        if not runs:
            return None
        r = runs[0]
        status = {"success": "success", "failure": "failed"}.get(r.get("conclusion") or "", "running")
        return PipelineRun(pipeline_id=pipeline_id, run_id=str(r["id"]), name=r["name"], status=status,
                           branch=r.get("head_branch") or "main", commit_sha=r.get("head_sha"), url=r.get("html_url"),
                           started_at=_dt(r.get("run_started_at")),
                           stages=self.get_pipeline_stages(pipeline_id, str(r["id"])))

    def get_pipeline_stages(self, pipeline_id: str, run_id: str) -> list[PipelineStage]:
        jobs = self._get(f"/repos/{self.repo}/actions/runs/{run_id}/jobs").get("jobs", [])
        return [PipelineStage(name=j["name"],
                              status={"success": "success", "failure": "failed", "skipped": "skipped"}.get(j.get("conclusion") or "", "running"),
                              started_at=_dt(j.get("started_at")), finished_at=_dt(j.get("completed_at"))) for j in jobs]

    def get_pipeline_logs(self, pipeline_id: str, run_id: str, stage: str | None = None) -> list[LogLine]:
        jobs = self._get(f"/repos/{self.repo}/actions/runs/{run_id}/jobs").get("jobs", [])
        out: list[LogLine] = []
        for j in jobs:
            if stage and j["name"] != stage:
                continue
            r = self.http.get(f"/repos/{self.repo}/actions/jobs/{j['id']}/logs", follow_redirects=True)
            if r.status_code != 200:
                continue
            for line in r.text.splitlines():
                ts_part, _, msg = line.partition(" ")
                level = "ERROR" if ("##[error]" in msg or "Error" in msg) else "INFO"
                out.append(LogLine(ts=_dt(ts_part) if ts_part[:2] == "20" else None, source=f"gha/{j['name']}",
                                   level=level, message=msg, raw=line))
        return out

    def trigger_pipeline(self, pipeline_id: str, ref: str | None = None) -> ActionOutcome:
        self._guard_write("trigger_pipeline")
        r = self.http.post(f"/repos/{self.repo}/actions/workflows/{pipeline_id}/dispatches", json={"ref": ref or "main"})
        return ActionOutcome(ok=r.status_code == 204, provider=self.name, message=f"workflow_dispatch -> {r.status_code}")

    # RepositoryProvider
    def get_recent_commits(self, repository: str, since: datetime | None = None, limit: int = 10) -> list[Commit]:
        params: dict[str, Any] = {"per_page": limit}
        if since:
            params["since"] = since.isoformat()
        data = self._get(f"/repos/{repository}/commits", **params)
        return [Commit(repository=repository, sha=c["sha"], author=(c.get("author") or {}).get("login", "unknown"),
                       message=c["commit"]["message"], timestamp=_dt(c["commit"]["author"]["date"])) for c in data]

    def get_commit(self, repository: str, sha: str) -> Commit | None:
        c = self._get(f"/repos/{repository}/commits/{sha}")
        files = c.get("files", [])
        return Commit(repository=repository, sha=c["sha"], author=(c.get("author") or {}).get("login", "unknown"),
                      message=c["commit"]["message"], timestamp=_dt(c["commit"]["author"]["date"]),
                      files_changed=[f["filename"] for f in files],
                      diff="\n".join(f"--- {f['filename']}\n{f.get('patch', '')}" for f in files)[:20000])

    def create_pull_request(self, repository: str, title: str, body: str, changes: dict[str, Any]) -> ActionOutcome:
        self._guard_write("create_pull_request")
        r = self.http.post(f"/repos/{repository}/pulls", json={"title": title, "body": body,
                                                               "head": changes["branch"], "base": changes.get("base", "main")})
        return ActionOutcome(ok=r.status_code == 201, provider=self.name, message=f"create PR -> {r.status_code}",
                             output=r.json() if r.status_code == 201 else {})


class PrometheusAdapter(_Real, ObservabilityProvider):
    name = "prometheus"

    def __init__(self, cfg: dict[str, Any]) -> None:
        super().__init__(cfg)
        self.http = httpx.Client(base_url=cfg["url"], timeout=15)
        self.watched: list[dict[str, str]] = cfg.get("watched", [])

    def _range(self, query: str, start: datetime, end: datetime, step: int = 30) -> list[tuple[datetime, float]]:
        r = self.http.get("/api/v1/query_range", params={"query": query, "start": start.timestamp(),
                                                         "end": end.timestamp(), "step": step})
        r.raise_for_status()
        result = r.json()["data"]["result"]
        if not result:
            return []
        return [(datetime.fromtimestamp(float(t), tz=timezone.utc), float(v)) for t, v in result[0]["values"]]

    def list_metrics(self, service: str | None = None) -> list[str]:
        return sorted({w["name"] for w in self.watched if service in (None, w["service"])})

    def get_metric_series(self, metric: str, service: str, start: datetime | None = None,
                          end: datetime | None = None) -> MetricSeries | None:
        end = end or datetime.now(timezone.utc)
        start = start or end - timedelta(minutes=45)
        for w in self.watched:
            if w["name"] == metric and w["service"] == service:
                return MetricSeries(name=metric, service=service, interval_s=30, points=self._range(w["query"], start, end))
        return None

    def watched_series(self) -> list[MetricSeries]:
        out = []
        for w in self.watched:
            s = self.get_metric_series(w["name"], w["service"])
            if s and s.points:
                out.append(s)
        return out


class LokiAdapter(_Real, LoggingProvider):
    name = "loki"

    def __init__(self, cfg: dict[str, Any]) -> None:
        super().__init__(cfg)
        self.http = httpx.Client(base_url=cfg["url"], timeout=15)
        self.selector = cfg.get("selector_template", '{app="%s"}')

    def query_logs(self, service: str, start: datetime, end: datetime, limit: int = 5000) -> list[LogLine]:
        r = self.http.get("/loki/api/v1/query_range", params={
            "query": self.selector % service, "start": int(start.timestamp() * 1e9), "end": int(end.timestamp() * 1e9),
            "limit": limit, "direction": "forward"})
        r.raise_for_status()
        out: list[LogLine] = []
        for stream in r.json()["data"]["result"]:
            src = stream["stream"].get("pod", stream["stream"].get("app", service))
            for t, line in stream["values"]:
                upper = line.upper()
                level = next((lv for lv in ("FATAL", "ERROR", "WARN", "DEBUG") if lv in upper), "INFO")
                out.append(LogLine(ts=datetime.fromtimestamp(int(t) / 1e9, tz=timezone.utc), source=src, level=level,
                                   message=line, raw=line))
        return out


class KubernetesAdapter(_Real, OrchestrationProvider):
    """Minimal Kubernetes REST client (no kubernetes SDK dependency)."""
    name = "kubernetes"

    def __init__(self, cfg: dict[str, Any]) -> None:
        super().__init__(cfg)
        headers = {}
        if tok := self._token():
            headers["Authorization"] = f"Bearer {tok}"
        self.http = httpx.Client(base_url=cfg["api_url"], headers=headers, verify=cfg.get("verify_tls", True), timeout=15)

    def _get(self, path: str, **params: Any) -> Any:
        r = self.http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def _dep(self, d: dict[str, Any]) -> Deployment:
        md, spec, st = d["metadata"], d["spec"], d.get("status", {})
        labels = md.get("labels", {})
        return Deployment(name=md["name"], namespace=md["namespace"], service=labels.get("app", md["name"]),
                          environment=labels.get("environment", md["namespace"]),
                          replicas_desired=spec.get("replicas", 1), replicas_ready=st.get("readyReplicas", 0),
                          image=spec["template"]["spec"]["containers"][0]["image"])

    def list_deployments(self, namespace: str | None = None) -> list[Deployment]:
        path = f"/apis/apps/v1/namespaces/{namespace}/deployments" if namespace else "/apis/apps/v1/deployments"
        return [self._dep(d) for d in self._get(path).get("items", [])]

    def get_deployment(self, namespace: str, name: str) -> Deployment | None:
        d = self._dep(self._get(f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}"))
        d.revisions = self.get_deployment_revisions(namespace, name)
        return d

    def list_pods(self, namespace: str, service: str | None = None) -> list[Pod]:
        params = {"labelSelector": f"app={service}"} if service else {}
        out = []
        for p in self._get(f"/api/v1/namespaces/{namespace}/pods", **params).get("items", []):
            cs = (p.get("status", {}).get("containerStatuses") or [{}])[0]
            waiting = (cs.get("state", {}).get("waiting") or {}).get("reason")
            terminated = (cs.get("lastState", {}).get("terminated") or {}).get("reason")
            out.append(Pod(name=p["metadata"]["name"], namespace=namespace,
                           service=p["metadata"].get("labels", {}).get("app", ""), phase=p["status"].get("phase", ""),
                           reason=waiting or terminated, restarts=cs.get("restartCount", 0), ready=cs.get("ready", False),
                           image=cs.get("image")))
        return out

    def list_events(self, namespace: str, since: datetime | None = None) -> list[K8sEvent]:
        out = []
        for e in self._get(f"/api/v1/namespaces/{namespace}/events").get("items", []):
            t = _dt(e.get("lastTimestamp") or e.get("eventTime"))
            if t is None or (since and t < since):
                continue
            io = e.get("involvedObject", {})
            out.append(K8sEvent(ts=t, namespace=namespace, object=f"{io.get('kind', '').lower()}/{io.get('name')}",
                                type=e.get("type", "Normal"), reason=e.get("reason", ""), message=e.get("message", "")))
        return out

    def get_deployment_revisions(self, namespace: str, name: str) -> list[DeploymentRevision]:
        rs = self._get(f"/apis/apps/v1/namespaces/{namespace}/replicasets").get("items", [])
        out = []
        for r in rs:
            owners = [o["name"] for o in r["metadata"].get("ownerReferences", [])]
            if name not in owners:
                continue
            ann = r["metadata"].get("annotations", {})
            out.append(DeploymentRevision(revision=int(ann.get("deployment.kubernetes.io/revision", 0)),
                                          image=r["spec"]["template"]["spec"]["containers"][0]["image"],
                                          created_at=_dt(r["metadata"]["creationTimestamp"]),
                                          change_cause=ann.get("kubernetes.io/change-cause", "")))
        return sorted(out, key=lambda x: x.revision)

    # -- writes (guarded) --------------------------------------------------------------------------
    def rollback(self, namespace: str, name: str, to_revision: int | None = None) -> ActionOutcome:
        self._guard_write("rollback")
        revs = self.get_deployment_revisions(namespace, name)
        target = next((r for r in revs if r.revision == to_revision), revs[-2] if len(revs) > 1 else None)
        if not target:
            return ActionOutcome(ok=False, provider=self.name, message="no previous revision")
        dep = self._get(f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}")
        container = dep["spec"]["template"]["spec"]["containers"][0]["name"]
        patch = {"spec": {"template": {"spec": {"containers": [{"name": container, "image": target.image}]}}}}
        r = self.http.patch(f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}", json=patch,
                            headers={"Content-Type": "application/strategic-merge-patch+json"})
        return ActionOutcome(ok=r.is_success, provider=self.name, message=f"rollback to {target.image} -> {r.status_code}")

    def restart(self, namespace: str, name: str) -> ActionOutcome:
        self._guard_write("restart")
        patch = {"spec": {"template": {"metadata": {"annotations": {
            "kubectl.kubernetes.io/restartedAt": datetime.now(timezone.utc).isoformat()}}}}}
        r = self.http.patch(f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}", json=patch,
                            headers={"Content-Type": "application/strategic-merge-patch+json"})
        return ActionOutcome(ok=r.is_success, provider=self.name, message=f"restart -> {r.status_code}")

    def patch_config(self, namespace: str, name: str, patch: dict[str, Any]) -> ActionOutcome:
        self._guard_write("patch_config")
        r = self.http.patch(f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}", json=patch,
                            headers={"Content-Type": "application/strategic-merge-patch+json"})
        return ActionOutcome(ok=r.is_success, provider=self.name, message=f"patch -> {r.status_code}")

    def scale(self, namespace: str, name: str, replicas: int) -> ActionOutcome:
        self._guard_write("scale")
        r = self.http.patch(f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}/scale",
                            json={"spec": {"replicas": replicas}},
                            headers={"Content-Type": "application/merge-patch+json"})
        return ActionOutcome(ok=r.is_success, provider=self.name, message=f"scale -> {r.status_code}")

    def delete(self, namespace: str, kind: str, name: str) -> ActionOutcome:
        self._guard_write("delete")
        raise ReadOnlyError("delete is never implemented for the real Kubernetes adapter")
