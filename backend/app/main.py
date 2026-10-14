from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.jenkins import router as jenkins_router
from app.api.prometheus import router as prometheus_router
from app.api.investigation import router as investigation_router

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
    allow_headers=["Content-Type", "Authorization"],
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
