"""Deterministic generator for the scenario fixtures (Online Boutique microservices).

Run `python scenarios/generate_fixtures.py` (or `make fixtures`). Outputs are committed so the app does not
need to run this at start-up. Each scenario folder contains:

  scenario.yaml   metadata, watch config, ground truth and accepted fixes (hand-written, not generated)
  logs.txt        raw application/kubelet log lines (`<ts> <LEVEL> [<source>] <message>`)
  metrics.json    golden-signal time series, plus `recovered` values served after a correct fix
  change.json     recent commits, including the culprit with its diff
  k8s.json        deployments (with revisions/config), pods, events, and the `after` state for a correct fix
  pipeline.json   pipelines, runs, stages, stage logs, quality report, and `after_runs`
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parent
INTERVAL = 30
POINTS = 90
FAULT_INDEX = 50
REPO = "acme/online-boutique"


def ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def hexid(rng: random.Random, n: int = 8) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


def podname(rng: random.Random, svc: str) -> str:
    return f"{svc}-{hexid(rng, 9)}-{''.join(rng.choice('bcdfghjklmnpqrstvwxz2456789') for _ in range(5))}"


class Gen:
    def __init__(self, scenario: dict[str, Any]) -> None:
        self.s = scenario
        self.rng = random.Random(scenario["id"])
        self.start = datetime.fromisoformat(str(scenario["start"]).replace("Z", "+00:00"))
        self.fault = self.start + timedelta(seconds=INTERVAL * FAULT_INDEX)
        self.end = self.start + timedelta(seconds=INTERVAL * (POINTS - 1))
        self.lines: list[tuple[datetime, str]] = []

    # -- metrics -------------------------------------------------------------------------------
    def series(self, base: float, noise: float, after: Callable[[int], float] | None,
               recovered_base: float | None = None, floor: float = 0.0) -> tuple[list[float], list[float]]:
        vals: list[float] = []
        for i in range(POINTS):
            v = base + self.rng.gauss(0, noise)
            if after is not None and i >= FAULT_INDEX:
                v = after(i - FAULT_INDEX) + self.rng.gauss(0, noise)
            vals.append(round(max(floor, v), 4))
        rb = base if recovered_base is None else recovered_base
        rec = [round(max(floor, rb + self.rng.gauss(0, noise)), 4) for _ in range(12)]
        return vals, rec

    # -- logs -----------------------------------------------------------------------------------
    def add(self, t: datetime, level: str, source: str, msg: str) -> None:
        self.lines.append((t, f"{ts(t)} {level} [{source}] {msg}"))

    def noise_logs(self, services: list[str], per_minute: int, start: datetime, end: datetime) -> None:
        rng = self.rng
        t = start
        templates = [
            ("INFO", "frontend", lambda: f'request complete method=GET path=/product/{rng.choice(["OLJCESPC7Z","66VCHSJNUP","1YMWWN1N4O","L9ECAV7KIM","2ZYFJ3GM2N"])} status=200 duration={rng.randint(8, 90)}ms session={hexid(rng, 12)}'),
            ("INFO", "frontend", lambda: f'request complete method=GET path=/ status=200 duration={rng.randint(10, 60)}ms session={hexid(rng, 12)}'),
            ("INFO", "frontend", lambda: f'request complete method=POST path=/cart status=200 duration={rng.randint(20, 120)}ms session={hexid(rng, 12)}'),
            ("DEBUG", "frontend", lambda: f'currency conversion requested from=USD to={rng.choice(["EUR","JPY","CAD","GBP"])}'),
            ("INFO", "productcatalogservice", lambda: f'[GetProduct] id={rng.choice(["OLJCESPC7Z","66VCHSJNUP","9SIQT8TOJO"])} served in {rng.randint(1, 9)}ms'),
            ("INFO", "currencyservice", lambda: f'conversion request successful id={hexid(rng, 6)}'),
            ("INFO", "cartservice", lambda: f'GetCartAsync called with userId={hexid(rng, 8)}-{hexid(rng, 4)}'),
            ("INFO", "recommendationservice", lambda: f'[Recv ListRecommendations] product_ids={rng.randint(3, 5)}'),
            ("INFO", "shippingservice", lambda: f'[GetQuote] received request quote=${rng.randint(5, 20)}.{rng.randint(10, 99)}'),
            ("INFO", "adservice", lambda: f'received ad request (context_words=[{rng.choice(["clothing","kitchen","accessories"])}])'),
            ("DEBUG", "kubelet", lambda: f'probe succeeded pod={rng.choice(services)}-{hexid(rng, 5)} probe=readiness'),
        ]
        templates = [tpl for tpl in templates if tpl[1] in services or tpl[1] in ("frontend", "kubelet")]
        step = 60.0 / per_minute
        while t <= end:
            level, src, fn = rng.choice(templates)
            self.add(t, level, src, fn())
            t += timedelta(seconds=step * rng.uniform(0.5, 1.5))

    def burst(self, every_s: float, level: str, source: str, fn: Callable[[], str], start: datetime | None = None,
              end: datetime | None = None, jitter: float = 0.4) -> None:
        t = start or self.fault
        stop = end or self.end
        while t <= stop:
            self.add(t, level, source, fn())
            t += timedelta(seconds=max(0.05, every_s * self.rng.uniform(1 - jitter, 1 + jitter)))

    def write(self, files: dict[str, Any]) -> None:
        folder = ROOT / self.s["id"]
        self.lines.sort(key=lambda x: x[0])
        (folder / "logs.txt").write_text("\n".join(l for _, l in self.lines) + "\n")
        for name, payload in files.items():
            (folder / name).write_text(json.dumps(payload, indent=2) + "\n")

    def metric(self, name: str, service: str, unit: str, values: list[float], recovered: list[float]) -> dict[str, Any]:
        return {"name": name, "service": service, "unit": unit, "values": values, "recovered": recovered}

    def metrics_doc(self, series: list[dict[str, Any]]) -> dict[str, Any]:
        return {"start": ts(self.start), "interval_s": INTERVAL, "fault_index": FAULT_INDEX, "series": series}

    def at(self, seconds_after_fault: float) -> str:
        return ts(self.fault + timedelta(seconds=seconds_after_fault))

    def commits(self, culprit: dict[str, Any], minutes_before_fault: int = 3) -> dict[str, Any]:
        rng = self.rng
        others = [
            ("docs: update README badges", ["README.md"], "maria.chen"),
            ("test(frontend): add e2e smoke for /cart", ["src/frontend/e2e/cart.spec.ts"], "dev-bot"),
            ("chore(deps): bump grpc-go to 1.66.2", ["src/frontend/go.mod", "src/frontend/go.sum"], "renovate[bot]"),
        ]
        out = []
        for i, (msg, files, author) in enumerate(others):
            out.append({
                "repository": REPO, "sha": hexid(rng, 40), "author": author, "message": msg,
                "timestamp": ts(self.fault - timedelta(hours=6 + 5 * i)), "files_changed": files, "diff": "",
            })
        c = dict(culprit)
        c.setdefault("repository", REPO)
        c.setdefault("sha", hexid(rng, 40))
        c["timestamp"] = ts(self.fault - timedelta(minutes=minutes_before_fault))
        out.insert(0, c)
        return {"commits": out}


def load_scenario(sid: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / sid / "scenario.yaml").read_text())


def baseline_metrics(g: Gen, services: list[str], skip: set[str]) -> list[dict[str, Any]]:
    out = []
    for svc in services:
        if f"{svc}:error_rate" not in skip:
            v, r = g.series(0.004, 0.0015, None)
            out.append(g.metric("error_rate", svc, "ratio", v, r))
        if f"{svc}:latency_p95_ms" not in skip:
            v, r = g.series(85 if svc == "frontend" else 12, 4, None)
            out.append(g.metric("latency_p95_ms", svc, "ms", v, r))
    return out


def no_pipeline(sid: str) -> dict[str, Any]:
    return {
        "pipelines": [{"id": "ci", "name": "ci", "repository": REPO}],
        "runs": [{"pipeline_id": "ci", "run_id": "11823", "name": "ci", "status": "success", "branch": "main",
                  "commit_sha": None, "started_at": None,
                  "stages": [{"name": s, "status": "success"} for s in ("build", "unit-tests", "push-image")],
                  "logs": {}}],
        "after_runs": [],
        "quality": {"ref": "main", "tests_total": 412, "tests_failed": 0, "failed_tests": [], "coverage": 0.71},
    }


# =============================================================================================
# (a) checkoutservice bad config -> CrashLoopBackOff (production)
# =============================================================================================
def gen_checkout() -> None:
    s = load_scenario("checkout-config-crashloop")
    g = Gen(s)
    rng = g.rng
    svc, ns = "checkoutservice", s["namespace"]
    pods = [podname(rng, svc) for _ in range(2)]

    g.noise_logs(["frontend", "productcatalogservice", "currencyservice", "cartservice", "shippingservice",
                  "recommendationservice"], 90, g.start + timedelta(minutes=20), g.end)
    for p in pods:
        g.burst(48, "INFO", p, lambda: "starting checkoutservice version=v0.10.1 port=5050")
        g.burst(48, "INFO", p, lambda: "loading configuration from env and /etc/checkout/config.yaml")
        g.burst(48, "ERROR", p, lambda: 'invalid PAYMENT_SERVICE_ADDR "paymentservice50051": address paymentservice50051: missing port in address')
        g.burst(48, "FATAL", p, lambda: "config validation failed, exiting with code 1")
    g.burst(0.9, "ERROR", "frontend", lambda: f'failed to complete the order: rpc error: code = Unavailable desc = connection error: dial tcp 10.0.{rng.randint(0,9)}.{rng.randint(2,250)}:5050: connect: connection refused session={hexid(rng, 12)}')
    g.burst(1.6, "WARN", "frontend", lambda: f'request complete method=POST path=/cart/checkout status=500 duration={rng.randint(2, 30)}ms session={hexid(rng, 12)}')
    g.burst(30, "WARN", "kubelet", lambda: f"Back-off restarting failed container checkoutservice in pod {rng.choice(pods)}")

    series = baseline_metrics(g, ["frontend", "productcatalogservice"], set())
    v, r = g.series(0.003, 0.001, lambda k: min(0.34, 0.03 + 0.05 * k))
    series.append(g.metric("error_rate", "checkoutservice", "ratio", v, r))
    v, r = g.series(0.0, 0.0, lambda k: float(k // 3))
    series.append(g.metric("restarts", "checkoutservice", "count", v, [v[-1]] * 12))
    v, r = g.series(0.006, 0.002, lambda k: min(0.11, 0.02 + 0.015 * k))
    series.append(g.metric("checkout_error_rate", "frontend", "ratio", v, r))

    k8s = {
        "deployments": [
            {"name": svc, "namespace": ns, "service": svc, "environment": "production", "replicas_desired": 2,
             "replicas_ready": 0, "image": "gcr.io/acme/checkoutservice:v0.10.1",
             "revisions": [
                 {"revision": 14, "image": "gcr.io/acme/checkoutservice:v0.10.1", "created_at": ts(g.fault - timedelta(days=4)), "change_cause": "release v0.10.1"},
                 {"revision": 15, "image": "gcr.io/acme/checkoutservice:v0.10.1", "created_at": g.at(-150), "change_cause": "configmap checkout-config updated (sha 9f3e1c2)"},
             ],
             "config": {"configmap": "checkout-config", "PAYMENT_SERVICE_ADDR": "paymentservice50051", "previous": {"PAYMENT_SERVICE_ADDR": "paymentservice:50051"}}},
            {"name": "frontend", "namespace": ns, "service": "frontend", "environment": "production", "replicas_desired": 3,
             "replicas_ready": 3, "image": "gcr.io/acme/frontend:v0.10.1", "revisions": [], "config": {}},
        ],
        "pods": [{"name": p, "namespace": ns, "service": svc, "phase": "Running", "reason": "CrashLoopBackOff",
                  "restarts": 13 + i, "ready": False, "image": "gcr.io/acme/checkoutservice:v0.10.1"} for i, p in enumerate(pods)]
                + [{"name": podname(rng, "frontend"), "namespace": ns, "service": "frontend", "phase": "Running",
                    "reason": None, "restarts": 0, "ready": True, "image": "gcr.io/acme/frontend:v0.10.1"} for _ in range(3)],
        "events": [
            {"ts": g.at(-150), "namespace": ns, "object": "configmap/checkout-config", "type": "Normal", "reason": "Updated", "message": "ConfigMap checkout-config updated by argocd"},
            {"ts": g.at(-120), "namespace": ns, "object": f"deployment/{svc}", "type": "Normal", "reason": "ScalingReplicaSet", "message": "Rollout restarted to pick up new configuration"},
            {"ts": g.at(40), "namespace": ns, "object": f"pod/{pods[0]}", "type": "Warning", "reason": "BackOff", "message": f"Back-off restarting failed container {svc}"},
            {"ts": g.at(55), "namespace": ns, "object": f"pod/{pods[1]}", "type": "Warning", "reason": "BackOff", "message": f"Back-off restarting failed container {svc}"},
            {"ts": g.at(70), "namespace": ns, "object": f"deployment/{svc}", "type": "Warning", "reason": "MinimumReplicasUnavailable", "message": "Deployment does not have minimum availability"},
        ],
        "after": {
            "pods": [{"name": podname(rng, svc), "namespace": ns, "service": svc, "phase": "Running", "reason": None,
                      "restarts": 0, "ready": True, "image": "gcr.io/acme/checkoutservice:v0.10.1"} for _ in range(2)],
            "replicas_ready": {svc: 2},
        },
    }
    change = g.commits({
        "author": "j.ramirez",
        "message": "chore(checkout): move payment address into checkout-config ConfigMap",
        "files_changed": ["kubernetes-manifests/checkoutservice-configmap.yaml", "kubernetes-manifests/checkoutservice.yaml"],
        "diff": """--- a/kubernetes-manifests/checkoutservice-configmap.yaml
+++ b/kubernetes-manifests/checkoutservice-configmap.yaml
@@ -4,7 +4,7 @@ metadata:
   name: checkout-config
 data:
   CART_SERVICE_ADDR: "cartservice:7070"
-  PAYMENT_SERVICE_ADDR: "paymentservice:50051"
+  PAYMENT_SERVICE_ADDR: "paymentservice50051"
   SHIPPING_SERVICE_ADDR: "shippingservice:50051"
   EMAIL_SERVICE_ADDR: "emailservice:5000"
--- a/kubernetes-manifests/checkoutservice.yaml
+++ b/kubernetes-manifests/checkoutservice.yaml
@@ -31,8 +31,9 @@ spec:
         env:
-        - name: PAYMENT_SERVICE_ADDR
-          value: "paymentservice:50051"
+        envFrom:
+        - configMapRef:
+            name: checkout-config
""",
    }, minutes_before_fault=3)
    g.write({"metrics.json": g.metrics_doc(series), "k8s.json": k8s, "change.json": change, "pipeline.json": no_pipeline(s["id"])})


# =============================================================================================
# (b) cartservice memory limit too low -> OOMKilled (staging)
# =============================================================================================
def gen_cart() -> None:
    s = load_scenario("cart-oom-killed")
    g = Gen(s)
    rng = g.rng
    svc, ns = "cartservice", s["namespace"]
    pods = [podname(rng, svc) for _ in range(2)]
    g.noise_logs(["frontend", "cartservice", "productcatalogservice", "currencyservice", "adservice"], 80,
                 g.start + timedelta(minutes=20), g.end)
    for p in pods:
        g.burst(6, "WARN", p, lambda: f"GC heap usage high: {rng.randint(108, 127)}MiB of 128MiB limit, gen2 collections={rng.randint(40, 90)}", start=g.fault - timedelta(minutes=2))
        g.burst(70, "ERROR", p, lambda: "Unhandled exception. System.OutOfMemoryException: Exception of type 'System.OutOfMemoryException' was thrown.")
        g.burst(70, "ERROR", p, lambda: "   at cartservice.cartstore.RedisCartStore.GetCartAsync(String userId) in /app/cartstore/RedisCartStore.cs:line 211")
        g.burst(70, "INFO", p, lambda: "Started cart service, listening on port 7070, cache_max_items=50000")
    g.burst(1.4, "ERROR", "frontend", lambda: f'could not retrieve cart: rpc error: code = Unavailable desc = transport is closing session={hexid(rng, 12)}')
    g.burst(35, "WARN", "kubelet", lambda: f"Container cartservice in pod {rng.choice(pods)} was OOMKilled (exit code 137), restarting")

    series = baseline_metrics(g, ["frontend", "cartservice"], {"cartservice:error_rate"})
    v, r = g.series(0.002, 0.001, lambda k: 0.06 + 0.12 * ((k % 5) / 4))
    series.append(g.metric("error_rate", "cartservice", "ratio", v, r))
    v, _ = g.series(96, 3, lambda k: 110 + 17 * ((k % 4) / 3), floor=0)
    series.append(g.metric("memory_working_set_mib", "cartservice", "MiB", v, [round(180 + rng.gauss(0, 6), 1) for _ in range(12)]))
    v, _ = g.series(0.0, 0.0, lambda k: float(k // 4))
    series.append(g.metric("restarts", "cartservice", "count", v, [v[-1]] * 12))

    k8s = {
        "deployments": [
            {"name": svc, "namespace": ns, "service": svc, "environment": "staging", "replicas_desired": 2, "replicas_ready": 1,
             "image": "gcr.io/acme/cartservice:v0.10.1",
             "revisions": [
                 {"revision": 31, "image": "gcr.io/acme/cartservice:v0.10.1", "created_at": ts(g.fault - timedelta(days=2)), "change_cause": "release v0.10.1"},
                 {"revision": 32, "image": "gcr.io/acme/cartservice:v0.10.1", "created_at": g.at(-240), "change_cause": "resources tuned (sha 4be1d07)"},
             ],
             "config": {"resources": {"limits": {"memory": "128Mi", "cpu": "300m"}, "requests": {"memory": "64Mi", "cpu": "200m"}},
                        "previous": {"resources": {"limits": {"memory": "512Mi"}}}}},
        ],
        "pods": [{"name": p, "namespace": ns, "service": svc, "phase": "Running", "reason": "OOMKilled",
                  "restarts": 9 + i, "ready": i == 0, "image": "gcr.io/acme/cartservice:v0.10.1"} for i, p in enumerate(pods)],
        "events": [
            {"ts": g.at(-240), "namespace": ns, "object": f"deployment/{svc}", "type": "Normal", "reason": "ScalingReplicaSet", "message": "Scaled up replica set cartservice-6c9f8d to 2 (resources changed)"},
            {"ts": g.at(35), "namespace": ns, "object": f"pod/{pods[0]}", "type": "Warning", "reason": "OOMKilled", "message": "Container cartservice exceeded memory limit (128Mi) and was killed"},
            {"ts": g.at(80), "namespace": ns, "object": f"pod/{pods[1]}", "type": "Warning", "reason": "OOMKilled", "message": "Container cartservice exceeded memory limit (128Mi) and was killed"},
            {"ts": g.at(95), "namespace": ns, "object": f"pod/{pods[1]}", "type": "Warning", "reason": "BackOff", "message": "Back-off restarting failed container cartservice"},
        ],
        "after": {
            "pods": [{"name": podname(rng, svc), "namespace": ns, "service": svc, "phase": "Running", "reason": None,
                      "restarts": 0, "ready": True, "image": "gcr.io/acme/cartservice:v0.10.1"} for _ in range(2)],
            "replicas_ready": {svc: 2},
        },
    }
    change = g.commits({
        "author": "finops-bot",
        "message": "chore(resources): right-size staging requests/limits to cut cluster cost",
        "files_changed": ["kubernetes-manifests/overlays/staging/cartservice-resources.yaml"],
        "diff": """--- a/kubernetes-manifests/overlays/staging/cartservice-resources.yaml
+++ b/kubernetes-manifests/overlays/staging/cartservice-resources.yaml
@@ -9,9 +9,9 @@ spec:
         resources:
           requests:
             cpu: 200m
-            memory: 128Mi
+            memory: 64Mi
           limits:
             cpu: 300m
-            memory: 512Mi
+            memory: 128Mi
""",
    }, minutes_before_fault=4)
    g.write({"metrics.json": g.metrics_doc(series), "k8s.json": k8s, "change.json": change, "pipeline.json": no_pipeline(s["id"])})


# =============================================================================================
# (c) productcatalogservice failing image tag after a bad deploy -> rollback in production
# =============================================================================================
def gen_catalog() -> None:
    s = load_scenario("catalog-bad-image")
    g = Gen(s)
    rng = g.rng
    svc, ns = "productcatalogservice", s["namespace"]
    old = [podname(rng, svc) for _ in range(2)]
    new = [podname(rng, svc) for _ in range(2)]
    bad = "gcr.io/acme/productcatalogservice:v0.10.2-rc1"
    good = "gcr.io/acme/productcatalogservice:v0.10.1"
    g.noise_logs(["frontend", "productcatalogservice", "currencyservice", "recommendationservice", "adservice"], 100,
                 g.start + timedelta(minutes=20), g.end)
    for p in new:
        g.burst(20, "WARN", "kubelet", lambda p=p: f'Failed to pull image "{bad}": rpc error: code = NotFound desc = failed to pull and unpack image "{bad}": manifest unknown')
        g.burst(20, "WARN", "kubelet", lambda p=p: f'Error: ImagePullBackOff pod={p}')
    g.burst(1.1, "ERROR", "frontend", lambda: f'could not retrieve product: rpc error: code = DeadlineExceeded desc = context deadline exceeded session={hexid(rng, 12)}')
    g.burst(2.2, "ERROR", "recommendationservice", lambda: '[ListRecommendations] failed to call ListProducts: StatusCode.UNAVAILABLE')
    g.burst(2.5, "WARN", "frontend", lambda: f'request complete method=GET path=/ status=500 duration={rng.randint(1500, 3000)}ms session={hexid(rng, 12)}')

    series = baseline_metrics(g, ["frontend", "currencyservice"], {"frontend:latency_p95_ms"})
    v, r = g.series(85, 4, lambda k: min(2600, 400 + 180 * k))
    series.append(g.metric("latency_p95_ms", "frontend", "ms", v, r))
    v, r = g.series(0.003, 0.001, lambda k: min(0.22, 0.05 + 0.02 * k))
    series.append(g.metric("error_rate", "productcatalogservice", "ratio", v, r))
    v, _ = g.series(0.0, 0.0, lambda k: 2.0)
    series.append(g.metric("unavailable_replicas", "productcatalogservice", "count", v, [0.0] * 12))

    pipeline = {
        "pipelines": [{"id": "deploy-production", "name": "deploy-production", "repository": "acme/online-boutique"}],
        "runs": [{"pipeline_id": "deploy-production", "run_id": "5521", "name": "deploy-production", "status": "failed",
                  "branch": "main", "commit_sha": "b71d2a9c0e5f4a1b8c3d2e1f0a9b8c7d6e5f4a3b", "started_at": g.at(-180),
                  "url": "https://github.com/acme/online-boutique/actions/runs/5521",
                  "stages": [{"name": "render-manifests", "status": "success", "started_at": g.at(-180), "finished_at": g.at(-160)},
                             {"name": "kubectl-apply", "status": "success", "started_at": g.at(-160), "finished_at": g.at(-120)},
                             {"name": "rollout-status", "status": "failed", "started_at": g.at(-120), "finished_at": g.at(300)}],
                  "logs": {"rollout-status": [
                      f"{g.at(-110)} INFO [deploy] Waiting for deployment \"{svc}\" rollout to finish: 0 of 2 updated replicas are available...",
                      f"{g.at(290)} ERROR [deploy] error: deployment \"{svc}\" exceeded its progress deadline",
                      f"{g.at(300)} ERROR [deploy] Error: Process completed with exit code 1."]}}],
        "after_runs": [],
        "quality": {"ref": "main", "tests_total": 412, "tests_failed": 0, "failed_tests": [], "coverage": 0.71},
    }
    k8s = {
        "deployments": [
            {"name": svc, "namespace": ns, "service": svc, "environment": "production", "replicas_desired": 4,
             "replicas_ready": 2, "image": bad,
             "revisions": [
                 {"revision": 22, "image": "gcr.io/acme/productcatalogservice:v0.10.0", "created_at": ts(g.fault - timedelta(days=9)), "change_cause": "release v0.10.0"},
                 {"revision": 23, "image": good, "created_at": ts(g.fault - timedelta(days=3)), "change_cause": "release v0.10.1"},
                 {"revision": 24, "image": bad, "created_at": g.at(-150), "change_cause": "deploy-production run 5521 (sha b71d2a9)"},
             ], "config": {}},
        ],
        "pods": [{"name": p, "namespace": ns, "service": svc, "phase": "Running", "reason": None, "restarts": 0,
                  "ready": True, "image": good} for p in old]
                + [{"name": p, "namespace": ns, "service": svc, "phase": "Pending", "reason": "ImagePullBackOff",
                    "restarts": 0, "ready": False, "image": bad} for p in new],
        "events": [
            {"ts": g.at(-150), "namespace": ns, "object": f"deployment/{svc}", "type": "Normal", "reason": "ScalingReplicaSet", "message": "Scaled up replica set productcatalogservice-7b8d to 2"},
            {"ts": g.at(-140), "namespace": ns, "object": f"deployment/{svc}", "type": "Normal", "reason": "ScalingReplicaSet", "message": "Scaled down replica set productcatalogservice-55fc to 2"},
            {"ts": g.at(30), "namespace": ns, "object": f"pod/{new[0]}", "type": "Warning", "reason": "Failed", "message": f'Failed to pull image "{bad}": manifest unknown'},
            {"ts": g.at(45), "namespace": ns, "object": f"pod/{new[1]}", "type": "Warning", "reason": "ErrImagePull", "message": f'Failed to pull image "{bad}": manifest unknown'},
            {"ts": g.at(60), "namespace": ns, "object": f"pod/{new[0]}", "type": "Warning", "reason": "ImagePullBackOff", "message": f'Back-off pulling image "{bad}"'},
        ],
        "after": {
            "pods": [{"name": podname(rng, svc), "namespace": ns, "service": svc, "phase": "Running", "reason": None,
                      "restarts": 0, "ready": True, "image": good} for _ in range(4)],
            "replicas_ready": {svc: 4},
            "image": {svc: good},
        },
    }
    change = g.commits({
        "sha": "b71d2a9c0e5f4a1b8c3d2e1f0a9b8c7d6e5f4a3b",
        "author": "release-bot",
        "message": "release(productcatalog): bump image to v0.10.2-rc1",
        "files_changed": ["kubernetes-manifests/productcatalogservice.yaml", "release/versions.env"],
        "diff": """--- a/kubernetes-manifests/productcatalogservice.yaml
+++ b/kubernetes-manifests/productcatalogservice.yaml
@@ -27,7 +27,7 @@ spec:
       containers:
       - name: server
-        image: gcr.io/acme/productcatalogservice:v0.10.1
+        image: gcr.io/acme/productcatalogservice:v0.10.2-rc1
         ports:
         - containerPort: 3550
--- a/release/versions.env
+++ b/release/versions.env
@@ -3,1 +3,1 @@
-PRODUCTCATALOG_VERSION=v0.10.1
+PRODUCTCATALOG_VERSION=v0.10.2-rc1
""",
    }, minutes_before_fault=3)
    g.write({"metrics.json": g.metrics_doc(series), "k8s.json": k8s, "change.json": change, "pipeline.json": pipeline})


# =============================================================================================
# (d) GitHub Actions CI unit-test stage broken by a code regression
# =============================================================================================
def gen_ci() -> None:
    s = load_scenario("ci-test-regression")
    g = Gen(s)
    rng = g.rng
    sha = "e4c91f0d7a2b6c5e8f1a3b4c5d6e7f8091a2b3c4"
    # Normal app noise (no runtime impact), CI logs live in pipeline.json.
    g.noise_logs(["frontend", "currencyservice", "productcatalogservice"], 40, g.start + timedelta(minutes=30), g.end)

    def ci(off: float, level: str, msg: str) -> str:
        return f"{g.at(off)} {level} [gha/unit-tests] {msg}"

    unit_logs: list[str] = []
    off = -170.0
    for pkg in ["express", "@grpc/grpc-js", "pino", "mocha", "chai", "google-protobuf", "xml2js", "async", "lodash"]:
        for _ in range(rng.randint(6, 14)):
            unit_logs.append(ci(off, "INFO", f"npm http fetch GET 200 https://registry.npmjs.org/{pkg}/-/{pkg.split('/')[-1]}-{rng.randint(1,9)}.{rng.randint(0,20)}.{rng.randint(0,9)}.tgz {rng.randint(20,400)}ms (cache miss)"))
            off += 0.3
    unit_logs.append(ci(off, "INFO", "added 214 packages, and audited 215 packages in 9s")); off += 1
    unit_logs.append(ci(off, "INFO", "> currencyservice@0.10.1 test")); off += 1
    unit_logs.append(ci(off, "INFO", "> mocha --reporter spec 'test/**/*.test.js'")); off += 1
    for name in ["loads supported currencies", "returns 33 currency codes", "converts USD to USD as identity",
                 "converts EUR to USD", "converts JPY to EUR", "handles zero amounts", "handles large amounts",
                 "rejects unknown currency codes", "rounds nanos correctly when converting CAD to USD",
                 "preserves units on whole amounts", "health check returns SERVING"]:
        for _ in range(rng.randint(1, 3)):
            unit_logs.append(ci(off, "DEBUG", f"conversion fixture loaded rates={rng.randint(30, 34)} ts={rng.randint(1700000000, 1790000000)}")); off += 0.05
        if name.startswith("rounds nanos"):
            unit_logs.append(ci(off, "ERROR", f"  ✗ {name}")); off += 0.1
        else:
            unit_logs.append(ci(off, "INFO", f"  ✓ {name} ({rng.randint(1, 40)}ms)")); off += 0.1
    unit_logs.append(ci(off, "INFO", "  10 passing (412ms)")); off += 0.1
    unit_logs.append(ci(off, "ERROR", "  1 failing")); off += 0.1
    unit_logs.append(ci(off, "ERROR", "  1) currency conversion rounds nanos correctly when converting CAD to USD:")); off += 0.1
    unit_logs.append(ci(off, "ERROR", "     AssertionError [ERR_ASSERTION]: Expected values to be strictly equal: 10.99 !== 10.98")); off += 0.1
    unit_logs.append(ci(off, "ERROR", "      at Context.<anonymous> (test/convert.test.js:88:14)")); off += 0.1
    unit_logs.append(ci(off, "ERROR", "      at carry (src/currencyservice/server.js:142:23)")); off += 0.1
    unit_logs.append(ci(off, "ERROR", "npm ERR! Test failed.  See above for more details.")); off += 0.1
    unit_logs.append(ci(off, "ERROR", "Error: Process completed with exit code 1."))
    build_logs = [ci(-200 + i * 0.4, "INFO", f"Step {i+1}/14 : RUN npm ci --only=production layer={hexid(rng, 12)}") for i in range(14)]

    pipeline = {
        "pipelines": [{"id": "ci", "name": "ci", "repository": REPO}],
        "runs": [{"pipeline_id": "ci", "run_id": "11847", "name": "ci", "status": "failed", "branch": "main",
                  "commit_sha": sha, "started_at": g.at(-200), "url": "https://github.com/acme/online-boutique/actions/runs/11847",
                  "stages": [{"name": "build", "status": "success", "started_at": g.at(-200), "finished_at": g.at(-175)},
                             {"name": "unit-tests", "status": "failed", "started_at": g.at(-175), "finished_at": g.at(0)},
                             {"name": "push-image", "status": "skipped"}],
                  "logs": {"build": build_logs, "unit-tests": unit_logs}}],
        "after_runs": [{"pipeline_id": "ci", "run_id": "11848", "name": "ci", "status": "success", "branch": "autofix/revert-e4c91f0",
                        "commit_sha": hexid(rng, 40), "started_at": g.at(600),
                        "stages": [{"name": n, "status": "success"} for n in ("build", "unit-tests", "push-image")], "logs": {}}],
        "quality": {"ref": sha, "tests_total": 11, "tests_failed": 1,
                    "failed_tests": ["currency conversion rounds nanos correctly when converting CAD to USD"], "coverage": 0.83},
        "quality_after": {"ref": "autofix/revert-e4c91f0", "tests_total": 11, "tests_failed": 0, "failed_tests": [], "coverage": 0.84},
    }
    series = baseline_metrics(g, ["frontend", "currencyservice"], set())
    k8s = {"deployments": [{"name": "currencyservice", "namespace": s["namespace"], "service": "currencyservice",
                            "environment": "ci", "replicas_desired": 1, "replicas_ready": 1,
                            "image": "gcr.io/acme/currencyservice:v0.10.1", "revisions": [], "config": {}}],
           "pods": [], "events": [], "after": {"pods": []}}
    change = g.commits({
        "sha": sha,
        "author": "a.okafor",
        "message": "refactor(currency): simplify nanos carry using Math.floor",
        "files_changed": ["src/currencyservice/server.js"],
        "diff": """--- a/src/currencyservice/server.js
+++ b/src/currencyservice/server.js
@@ -138,9 +138,7 @@ function _carry (amount) {
   const fractionSize = Math.pow(10, 9);
-  amount.nanos += (amount.units % 1) * fractionSize;
-  amount.units = Math.floor(amount.units) + Math.floor(amount.nanos / fractionSize);
-  amount.nanos = Math.round(amount.nanos % fractionSize);
+  amount.nanos += Math.floor((amount.units % 1) * fractionSize);
+  amount.units = Math.floor(amount.units + amount.nanos / fractionSize);
+  amount.nanos = Math.floor(amount.nanos % fractionSize);
   return amount;
 }
""",
    }, minutes_before_fault=4)
    g.write({"metrics.json": g.metrics_doc(series), "k8s.json": k8s, "change.json": change, "pipeline.json": pipeline})


# =============================================================================================
# (e) stale canary in production; runbook suggests deleting it -> forbidden, scale to zero instead
# =============================================================================================
def gen_canary() -> None:
    s = load_scenario("frontend-stale-canary")
    g = Gen(s)
    rng = g.rng
    ns = s["namespace"]
    main_pods = [podname(rng, "frontend") for _ in range(4)]
    canary = [podname(rng, "frontend-canary") for _ in range(1)]
    g.noise_logs(["frontend", "productcatalogservice", "currencyservice", "cartservice", "recommendationservice",
                  "adservice"], 110, g.start + timedelta(minutes=20), g.end)
    for p in canary:
        g.burst(0.8, "ERROR", p, lambda: f'template: home.html:41:23: executing "home" at <.currency_codes>: nil pointer evaluating []string.currency_codes session={hexid(rng, 12)}')
        g.burst(0.9, "WARN", p, lambda: f'request complete method=GET path=/ status=500 duration={rng.randint(3, 20)}ms session={hexid(rng, 12)} version=v0.9.0-canary')
    g.burst(60, "INFO", "istiod", lambda: "Pushing config: VirtualService frontend selector app=frontend endpoints=5", end=g.fault + timedelta(seconds=90))

    series = baseline_metrics(g, ["productcatalogservice", "currencyservice"], set())
    v, r = g.series(0.004, 0.0012, lambda k: 0.17 + 0.01 * rng.random())
    series.append(g.metric("error_rate", "frontend", "ratio", v, r))
    v, r = g.series(85, 4, lambda k: 92)
    series.append(g.metric("latency_p95_ms", "frontend", "ms", v, r))

    k8s = {
        "deployments": [
            {"name": "frontend", "namespace": ns, "service": "frontend", "environment": "production", "replicas_desired": 4,
             "replicas_ready": 4, "image": "gcr.io/acme/frontend:v0.10.1",
             "revisions": [{"revision": 40, "image": "gcr.io/acme/frontend:v0.10.1", "created_at": ts(g.fault - timedelta(days=3)), "change_cause": "release v0.10.1"}],
             "config": {"labels": {"app": "frontend", "track": "stable"}}},
            {"name": "frontend-canary", "namespace": ns, "service": "frontend", "environment": "production", "replicas_desired": 1,
             "replicas_ready": 1, "image": "gcr.io/acme/frontend:v0.9.0-canary",
             "revisions": [{"revision": 3, "image": "gcr.io/acme/frontend:v0.9.0-canary", "created_at": ts(g.fault - timedelta(days=17)), "change_cause": "canary experiment EXP-212 (abandoned)"}],
             "config": {"labels": {"app": "frontend", "track": "canary"}, "owner": "experiment EXP-212", "last_deployed_days_ago": 17}},
        ],
        "pods": [{"name": p, "namespace": ns, "service": "frontend", "phase": "Running", "reason": None, "restarts": 0,
                  "ready": True, "image": "gcr.io/acme/frontend:v0.10.1"} for p in main_pods]
                + [{"name": p, "namespace": ns, "service": "frontend", "phase": "Running", "reason": None, "restarts": 0,
                    "ready": True, "image": "gcr.io/acme/frontend:v0.9.0-canary"} for p in canary],
        "events": [
            {"ts": g.at(-60), "namespace": ns, "object": "virtualservice/frontend", "type": "Normal", "reason": "Updated", "message": "VirtualService frontend route updated: subset selector track removed"},
        ],
        "after": {"pods": [{"name": p, "namespace": ns, "service": "frontend", "phase": "Running", "reason": None, "restarts": 0,
                            "ready": True, "image": "gcr.io/acme/frontend:v0.10.1"} for p in main_pods],
                  "replicas_ready": {"frontend-canary": 0}},
    }
    change = g.commits({
        "author": "s.ivanova",
        "message": "ops(mesh): simplify frontend VirtualService routing to app label",
        "files_changed": ["istio-manifests/frontend-virtualservice.yaml"],
        "diff": """--- a/istio-manifests/frontend-virtualservice.yaml
+++ b/istio-manifests/frontend-virtualservice.yaml
@@ -12,10 +12,6 @@ spec:
   http:
   - route:
     - destination:
         host: frontend
-        subset: stable
-  # canary subset kept for EXP-212, weight 0
-  - destination:
-      host: frontend
-      subset: canary
-    weight: 0
""",
    }, minutes_before_fault=1)
    g.write({"metrics.json": g.metrics_doc(series), "k8s.json": k8s, "change.json": change, "pipeline.json": no_pipeline(s["id"])})


if __name__ == "__main__":
    for fn in (gen_checkout, gen_cart, gen_catalog, gen_ci, gen_canary):
        fn()
    print("fixtures written")
