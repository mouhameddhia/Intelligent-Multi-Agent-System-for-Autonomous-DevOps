from fastapi import APIRouter

from agent_system.config import settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/defaults")
def get_default_credentials():
    """Default tool credentials configured server-side (.env), for the
    frontend to auto-fill and auto-connect on load. Single-user/local setup
    only — these are returned as plain JSON to the same-origin frontend, not
    gated behind any auth, so this must never be exposed beyond localhost."""
    return {
        "jenkins": {
            "url": settings.jenkins_url,
            "username": settings.jenkins_username,
            "api_token": settings.jenkins_api_token,
        },
        "github": {
            "token": settings.github_token,
        },
        "prometheus": {
            "url": settings.prometheus_url,
        },
        "grafana": {
            "url": settings.grafana_url,
            "token": settings.grafana_token,
        },
        "loki": {
            "url": settings.loki_url,
        },
        "sonarqube": {
            "url": settings.sonarqube_url,
            "token": settings.sonarqube_token,
        },
        "kubernetes": {
            "url": settings.kubernetes_url,
            "token": settings.kubernetes_token,
            "verify_tls": settings.kubernetes_verify_tls,
        },
    }
