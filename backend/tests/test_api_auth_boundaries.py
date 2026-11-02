"""Provider proxy routes must require an authenticated PipeGuard account."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.github import router as github_router
from app.api.grafana import router as grafana_router
from app.api.jenkins import router as jenkins_router
from app.api.kubernetes import router as kubernetes_router
from app.api.loki import router as loki_router
from app.api.prometheus import router as prometheus_router
from app.api.sonarqube import router as sonarqube_router

app = FastAPI()
for router in (jenkins_router, github_router, loki_router, prometheus_router,
               sonarqube_router, kubernetes_router, grafana_router):
    app.include_router(router)
app.add_api_route("/api/health", lambda: {"status": "healthy"}, methods=["GET"])


def test_provider_proxy_routes_reject_unauthenticated_requests():
    client = TestClient(app)
    cases = [
        ("/api/jenkins/test", {"url": "http://127.0.0.1", "username": "u", "api_token": "t"}),
        ("/api/github/test", {"token": "t"}),
        ("/api/loki/test", {"url": "http://127.0.0.1", "project_name": "p"}),
        ("/api/prometheus/test", {"url": "http://127.0.0.1"}),
        ("/api/sonarqube/test", {"url": "http://127.0.0.1", "token": "t"}),
        ("/api/kubernetes/test", {"url": "https://127.0.0.1", "namespace": "default", "token": "t"}),
        ("/api/grafana/test", {"url": "http://127.0.0.1"}),
    ]
    for path, payload in cases:
        assert client.post(path, json=payload).status_code == 401, path


def test_health_remains_public():
    assert TestClient(app).get("/api/health").status_code == 200
