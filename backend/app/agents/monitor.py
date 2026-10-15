"""Monitor agent: detects failed pipeline stages, bad deployments/pods and metric anomalies; opens the incident;
re-checks health after a fix (verification)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from statistics import mean

from ..connectors.base import Commit, Pod, ProviderSet
from ..models.agents import MonitorInput, MonitorOutput, VerifyInput, VerifyOutput
from ..models.incident import (
    ChangeInfo, DeploymentInfo, MetricAnomaly, MetricPoint, PipelineInfo, PodState, Severity, TimeWindow, Trigger,
    VerificationCheck, VerificationResult, utcnow,
)
from .detectors import SIGMA_FLOOR, ewma_zscore

BAD_POD_REASONS = {"CrashLoopBackOff", "OOMKilled", "ImagePullBackOff", "ErrImagePull", "Error",
                   "CreateContainerConfigError", "RunContainerError"}
SERVICE_IN_PATH = re.compile(r"(?:src/|kubernetes-manifests/(?:overlays/\w+/)?)([a-z]+service|frontend)")


def _service_from_commit(c: Commit | None) -> str | None:
    if not c:
        return None
    for f in c.files_changed:
        m = SERVICE_IN_PATH.search(f)
        if m:
            return m.group(1)
    return None


def _change_info(c: Commit | None) -> ChangeInfo | None:
    if not c:
        return None
    return ChangeInfo(repository=c.repository, commit_sha=c.sha, author=c.author, message=c.message,
                      timestamp=c.timestamp, files_changed=c.files_changed, diff=c.diff)


class MonitorAgent:
    name = "monitor"

    def __init__(self, providers: ProviderSet) -> None:
        self.p = providers

    # -- detection --------------------------------------------------------------------------------
    def run(self, inp: MonitorInput) -> MonitorOutput:
        missing: list[str] = []
        signals: list[str] = []
        detect_times: list[datetime] = []

        # 1) pipelines
        failed_run = None
        for pid in inp.watch.pipelines:
            run = self.p.pipeline.get_pipeline_status(pid)
            if run and run.status == "failed":
                failed_run = run
                stage = next((s for s in run.stages if s.status == "failed"), None)
                signals.append(f"pipeline {run.name} run {run.run_id} failed at stage '{stage.name if stage else '?'}'")
                if stage and stage.finished_at:
                    detect_times.append(stage.finished_at)
                break

        # 2) pods / deployments / events
        bad_pods: list[Pod] = []
        events_warn = []
        namespace = inp.watch.namespaces[0] if inp.watch.namespaces else None
        for ns in inp.watch.namespaces:
            pods = self.p.orchestration.list_pods(ns)
            bad = [p for p in pods if p.reason in BAD_POD_REASONS or (not p.ready and p.phase != "Succeeded")]
            if bad:
                bad_pods += bad
                namespace = ns
            for e in self.p.orchestration.list_events(ns):
                if e.type == "Warning":
                    events_warn.append(e)
        if bad_pods:
            reasons = sorted({p.reason or "NotReady" for p in bad_pods})
            signals.append(f"{len(bad_pods)} unhealthy pod(s): {', '.join(reasons)}")
            detect_times += [e.ts for e in events_warn]

        # 3) metric anomalies (EWMA z-score)
        anomalies: list[MetricAnomaly] = []
        try:
            series = self.p.observability.watched_series()
        except Exception:  # noqa: BLE001
            series = []
            missing.append("metrics")
        for s in series:
            values = [v for _, v in s.points]
            d = ewma_zscore(values, unit=s.unit)
            if d:
                pts = [MetricPoint(ts=t, value=v) for t, v in s.points]
                anomalies.append(MetricAnomaly(metric=s.name, service=s.service, unit=s.unit, detector="ewma",
                                               baseline=d.baseline, observed=d.observed, score=d.score,
                                               threshold=d.threshold, anomaly_start=s.points[d.index][0], series=pts))
                detect_times.append(s.points[d.index][0] + timedelta(seconds=s.interval_s))  # needs a 2nd point
        anomalies.sort(key=lambda a: a.score, reverse=True)
        for a in anomalies[:3]:
            signals.append(f"{a.service}/{a.metric} anomaly z={a.score} ({a.baseline:g} → {a.observed:g}{a.unit and ' ' + a.unit})")

        if not (failed_run or bad_pods or anomalies):
            return MonitorOutput(incident_id=inp.incident_id, detected=False, summary="All watched signals healthy.",
                                 missing_data=missing)

        # -- classify trigger and locate the service ---------------------------------------------
        repo = inp.watch.repositories[0] if inp.watch.repositories else None
        commit = None
        if failed_run and failed_run.commit_sha and repo:
            commit = self.p.repository.get_commit(repo, failed_run.commit_sha)

        if failed_run and ("deploy" in failed_run.name or "cd" == failed_run.name):
            kind = "deployment_failure"
        elif failed_run:
            kind = "pipeline_failure"
        elif bad_pods:
            kind = "pod_failure"
        else:
            kind = "metric_anomaly"

        service = None
        if bad_pods:
            counts: dict[str, int] = {}
            for p in bad_pods:
                counts[p.service] = counts.get(p.service, 0) + 1
            service = max(counts, key=counts.get)  # type: ignore[arg-type]
        if service is None:
            service = _service_from_commit(commit)
        if service is None and anomalies:
            # prefer the service whose own error_rate is anomalous
            service = anomalies[0].service

        detected_at = min(detect_times) if detect_times else utcnow()

        # preceding change: latest commit before detection (within 2h) touching the service, else latest overall
        if commit is None and repo:
            recent = self.p.repository.get_recent_commits(repo, since=detected_at - timedelta(hours=2))
            recent = [c for c in recent if c.timestamp <= detected_at]
            related = [c for c in recent if service and any(service in f for f in c.files_changed)]
            commit = (related or recent or [None])[0]
        if repo is None:
            missing.append("repository")

        # deployment context
        deployment = None
        environment = None
        if namespace:
            dep = self.p.orchestration.get_deployment(namespace, service) if service else None
            if dep:
                pods = self.p.orchestration.list_pods(namespace, service)
                prev = dep.revisions[-2] if len(dep.revisions) >= 2 else None
                cur = dep.revisions[-1] if dep.revisions else None
                deployment = DeploymentInfo(
                    namespace=namespace, name=dep.name, service=dep.service, environment=dep.environment,
                    current_revision=cur.revision if cur else None, previous_revision=prev.revision if prev else None,
                    image=dep.image, previous_image=prev.image if prev else None,
                    replicas_ready=dep.replicas_ready, replicas_desired=dep.replicas_desired,
                    pods=[PodState(name=p.name, phase=p.phase, reason=p.reason, restarts=p.restarts, ready=p.ready) for p in pods],
                    recent_events=[f"{e.reason}: {e.message}" for e in events_warn if service in e.object][:8])
                environment = dep.environment
        if environment is None:
            environment = "ci" if kind == "pipeline_failure" else "production"

        pipeline = None
        if failed_run:
            stage = next((s for s in failed_run.stages if s.status == "failed"), None)
            pipeline = PipelineInfo(provider=self.p.pipeline.name, pipeline_id=failed_run.pipeline_id, name=failed_run.name,
                                    run_id=failed_run.run_id, status=failed_run.status,
                                    failed_stage=stage.name if stage else None, branch=failed_run.branch, url=failed_run.url)

        severity = self._severity(kind, environment, anomalies, bad_pods)
        titles = {
            "pipeline_failure": f"{pipeline.name if pipeline else 'Pipeline'} failed at '{pipeline.failed_stage if pipeline else '?'}' ({service})",
            "deployment_failure": f"Deployment of {service} failing in {environment}",
            "pod_failure": f"{service} pods unhealthy in {environment} ({', '.join(sorted({p.reason or 'NotReady' for p in bad_pods}))})",
            "metric_anomaly": f"{service} {anomalies[0].metric if anomalies else 'metric'} anomaly in {environment}",
        }
        trigger = Trigger(kind=kind, source=f"{self.p.orchestration.name if bad_pods else self.p.observability.name if anomalies and not failed_run else self.p.pipeline.name}",
                          description="; ".join(signals), detected_at=detected_at, signals=signals)
        window_start = min(detected_at - timedelta(minutes=10), commit.timestamp if commit else detected_at)
        window = TimeWindow(start=window_start, end=max(detected_at + timedelta(minutes=10), detected_at))
        # extend the window to "now" for metric/log queries
        if series:
            window.end = max(window.end, series[0].points[-1][0])
        return MonitorOutput(
            incident_id=inp.incident_id, detected=True, title=titles[kind], severity=severity, service=service,
            namespace=namespace, environment=environment, trigger=trigger, time_window=window,
            change=_change_info(commit), pipeline=pipeline, deployment=deployment, metric_anomalies=anomalies,
            missing_data=missing, summary=f"Opened {kind.replace('_', ' ')} incident on {service}: " + "; ".join(signals[:3]))

    @staticmethod
    def _severity(kind: str, env: str, anomalies: list[MetricAnomaly], bad_pods: list[Pod]) -> Severity:
        err = max((a.observed for a in anomalies if "error_rate" in a.metric), default=0.0)
        if env == "production":
            if err >= 0.1 or kind in {"pod_failure", "deployment_failure"}:
                return Severity.critical if err >= 0.1 else Severity.high
            return Severity.high
        if env == "staging":
            return Severity.medium
        return Severity.low if kind == "pipeline_failure" and not bad_pods else Severity.medium

    # -- verification (re-check health after a fix) -----------------------------------------------
    def verify(self, inp: VerifyInput) -> VerifyOutput:
        checks: list[VerificationCheck] = []
        target = inp.action.target
        if inp.namespace and inp.environment != "ci":
            dep_name = target.name if target.kind == "deployment" else inp.service
            dep = self.p.orchestration.get_deployment(inp.namespace, dep_name)
            if dep:
                ok = dep.replicas_ready == dep.replicas_desired
                checks.append(VerificationCheck(name=f"deployment/{dep.name} replicas",
                                                passed=ok, detail=f"{dep.replicas_ready}/{dep.replicas_desired} ready"))
            pods = self.p.orchestration.list_pods(inp.namespace, inp.service)
            bad = [p for p in pods if p.reason in BAD_POD_REASONS or not p.ready]
            checks.append(VerificationCheck(name=f"{inp.service} pods healthy", passed=not bad,
                                            detail="all pods Ready" if not bad else
                                            ", ".join(f"{p.name}:{p.reason or 'NotReady'}" for p in bad[:4])))
        for a in inp.anomalies[:4]:
            s = self.p.observability.get_metric_series(a.metric, a.service)
            if not s or len(s.points) < 5:
                continue
            recent = [v for _, v in s.points[-5:]]
            if a.metric == "restarts":  # counter: healthy when it stops increasing
                checks.append(VerificationCheck(name=f"{a.service}/restarts stopped increasing",
                                                passed=recent[-1] - recent[0] == 0,
                                                detail=f"+{recent[-1] - recent[0]:g} over last {len(recent)} samples"))
                continue
            if "memory" in a.metric:  # a raised limit legitimately changes the level; pod health covers OOMs
                continue
            sigma = max(SIGMA_FLOOR.get(a.unit, 0.0), abs(a.baseline) * 0.1)
            limit = a.baseline + 3 * sigma
            ok = mean(recent) <= limit
            checks.append(VerificationCheck(name=f"{a.service}/{a.metric} back to baseline", passed=ok,
                                            detail=f"recent mean {mean(recent):.4g} vs limit {limit:.4g}"))
        if inp.pipeline:
            run = self.p.pipeline.get_pipeline_status(inp.pipeline.pipeline_id)
            ok = bool(run and run.status == "success" and run.run_id != inp.pipeline.run_id)
            checks.append(VerificationCheck(name=f"pipeline {inp.pipeline.name} green", passed=ok,
                                            detail=f"latest run {run.run_id} on {run.branch}: {run.status}" if run else "no run"))
            q = self.p.quality.get_quality_report(run.branch if run else "main")
            if q:
                checks.append(VerificationCheck(name="test suite", passed=q.tests_failed == 0,
                                                detail=f"{q.tests_total - q.tests_failed}/{q.tests_total} tests passing"))
        if not checks:
            res = VerificationResult(status="inconclusive", checks=[], verified_at=utcnow())
            return VerifyOutput(incident_id=inp.incident_id, result=res, summary="No health signal available to verify.")
        healthy = all(c.passed for c in checks)
        res = VerificationResult(status="healthy" if healthy else "unhealthy", checks=checks, verified_at=utcnow())
        passed = sum(c.passed for c in checks)
        return VerifyOutput(incident_id=inp.incident_id, result=res,
                            summary=f"{passed}/{len(checks)} health checks passed — {'healthy' if healthy else 'still unhealthy'}.")
