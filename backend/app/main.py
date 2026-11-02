import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.jenkins import router as jenkins_router
from app.api.github import router as github_router
from app.api.loki import router as loki_router
from app.api.prometheus import router as prometheus_router
from app.api.sonarqube import router as sonarqube_router
from app.api.kubernetes import router as kubernetes_router
from app.api.grafana import router as grafana_router
from app.api.investigation import router as investigation_router
from app.api.incident_stats import router as incident_stats_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.integrations import router as integrations_router
from app.api.learning import router as learning_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
app = FastAPI(
    title="PipeGuard API",
    description="Backend API for the PipeGuard DevOps intelligence platform.",
    version="0.2.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-Request-ID",
    ],
)


app.include_router(jenkins_router)
app.include_router(github_router)
app.include_router(loki_router)


@app.get("/")
def root():
    return {
        "name": "PipeGuard API",
        "status": "running",
        "version": "0.2.0",
    }


@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "service": "pipeguard-backend",
    }
app.include_router(prometheus_router)
app.include_router(sonarqube_router)
app.include_router(kubernetes_router)
app.include_router(grafana_router)
app.include_router(incident_stats_router)
app.include_router(investigation_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(integrations_router)
app.include_router(learning_router)
