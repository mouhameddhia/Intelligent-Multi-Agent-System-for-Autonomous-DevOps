from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from app.connectors.github import (
    GitHubConnectionError,
    GitHubConnector,
)

router = APIRouter(prefix="/api/github", tags=["GitHub"])


class GitHubTokenRequest(BaseModel):
    token: SecretStr


class GitHubCommitsRequest(GitHubTokenRequest):
    owner: str = Field(min_length=1, max_length=100)
    repository: str = Field(min_length=1, max_length=100)
    per_page: int = Field(default=10, ge=1, le=100)


@router.post("/test")
def test_github_connection(request: GitHubTokenRequest):
    token = request.token.get_secret_value()

    try:
        return GitHubConnector(token).test_connection()
    except GitHubConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/repositories")
def get_github_repositories(request: GitHubTokenRequest):
    token = request.token.get_secret_value()

    try:
        repositories = GitHubConnector(token).get_repositories()
        return {
            "count": len(repositories),
            "repositories": repositories,
        }
    except GitHubConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class GitHubCommitRequest(GitHubCommitsRequest):
    sha: str = Field(min_length=7, max_length=64)


@router.post("/commit")
def get_github_commit(request: GitHubCommitRequest):
    token = request.token.get_secret_value()
    try:
        commit = GitHubConnector(token).get_commit(
            owner=request.owner,
            repository=request.repository,
            sha=request.sha,
        )
        return {"provider": "github", "commit": commit}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GitHubConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/commits")
def get_github_commits(request: GitHubCommitsRequest):
    token = request.token.get_secret_value()

    try:
        commits = GitHubConnector(token).get_commits(
            owner=request.owner,
            repository=request.repository,
            per_page=request.per_page,
        )
        return {
            "count": len(commits),
            "commits": commits,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GitHubConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
