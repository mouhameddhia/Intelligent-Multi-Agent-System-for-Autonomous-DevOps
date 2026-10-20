from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.jenkins import router as jenkins_router
from app.api.prometheus import router as prometheus_router
from app.api.investigation import router as investigation_router
from app.api.grafana import router as grafana_router
from app.api.loki import router as loki_router
from app.api.sonarqube import router as sonarqube_router
from app.api.kubernetes import router as kubernetes_router
from app.api.docker import router as docker_router

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
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-Request-ID",
    ],
)


app.include_router(jenkins_router)


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
from app.api.github import router as github_router
app.include_router(github_router)
app.include_router(prometheus_router)
app.include_router(investigation_router)
app.include_router(grafana_router)
app.include_router(loki_router)
app.include_router(sonarqube_router)
app.include_router(kubernetes_router)
app.include_router(docker_router)
