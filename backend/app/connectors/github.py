import base64
import re
from urllib.parse import quote

import httpx

GITHUB_API_URL = "https://api.github.com"


class GitHubConnectionError(Exception):
    """Raised when PipeGuard cannot communicate with GitHub."""


class GitHubConnector:
    def __init__(self, token: str):
        token = token.strip()
        if not token:
            raise ValueError("A GitHub personal access token is required.")

        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.timeout = 15.0

    def _get(self, path: str, params: dict | None = None):
        url = f"{GITHUB_API_URL}/{path.lstrip('/')}"

        try:
            response = httpx.get(
                url,
                headers=self.headers,
                params=params,
                timeout=self.timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise GitHubConnectionError(
                "GitHub did not respond within 15 seconds."
            ) from exc
        except httpx.RequestError as exc:
            raise GitHubConnectionError(
                "Could not reach GitHub. Check your network connection."
            ) from exc

        if response.status_code == 401:
            raise GitHubConnectionError(
                "GitHub rejected the token. Check that it is valid."
            )
        if response.status_code == 403:
            raise GitHubConnectionError(
                "GitHub denied access. Check token permissions and API rate limits."
            )
        if response.status_code == 404:
            raise GitHubConnectionError(
                "The requested GitHub resource was not found or is not accessible."
            )
        if response.status_code >= 400:
            raise GitHubConnectionError(
                f"GitHub returned HTTP {response.status_code}."
            )

        try:
            return response.json()
        except ValueError as exc:
            raise GitHubConnectionError(
                "GitHub returned an unexpected response."
            ) from exc

    def test_connection(self) -> dict:
        data = self._get("/user")
        return {
            "connected": True,
            "username": data.get("login"),
            "name": data.get("name"),
            "profile_url": data.get("html_url"),
        }

    def get_repositories(self, per_page: int = 30) -> list[dict]:
        data = self._get(
            "/user/repos",
            {
                "sort": "updated",
                "per_page": min(max(per_page, 1), 100),
                "page": 1,
                "affiliation": "owner,collaborator,organization_member",
            },
        )

        if not isinstance(data, list):
            raise GitHubConnectionError(
                "GitHub returned an unexpected repository list."
            )

        return [
            {
                "id": repo.get("id"),
                "name": repo.get("name"),
                "full_name": repo.get("full_name"),
                "private": repo.get("private"),
                "description": repo.get("description"),
                "default_branch": repo.get("default_branch"),
                "html_url": repo.get("html_url"),
                "updated_at": repo.get("updated_at"),
            }
            for repo in data
            if isinstance(repo, dict)
        ]

    def get_commit(self, owner: str, repository: str, sha: str) -> dict:
        import re

        owner = owner.strip()
        repository = repository.strip()
        sha = sha.strip()

        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", owner) or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,100}", repository
        ):
            raise ValueError("Repository owner and name must be valid GitHub path segments.")
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", sha):
            raise ValueError("A valid Git commit SHA is required.")

        data = self._get(f"/repos/{owner}/{repository}/commits/{sha}")
        if not isinstance(data, dict):
            raise GitHubConnectionError("GitHub returned an invalid commit response.")

        commit = data.get("commit") or {}
        author = commit.get("author") or {}
        files = data.get("files") or []

        return {
            "sha": data.get("sha"),
            "message": commit.get("message"),
            "author": author.get("name"),
            "date": author.get("date"),
            "html_url": data.get("html_url"),
            "files": [
                {
                    "filename": item.get("filename"),
                    "status": item.get("status"),
                    "additions": item.get("additions", 0),
                    "deletions": item.get("deletions", 0),
                    "changes": item.get("changes", 0),
                    # GitHub supplies a unified diff for text files. This is
                    # needed to verify code-level root-cause claims, not just
                    # infer from commit messages and change counts.
                    "patch": item.get("patch"),
                }
                for item in files
                if isinstance(item, dict)
            ],
        }

    def get_file_content(
        self,
        owner: str,
        repository: str,
        path: str,
        ref: str,
        *,
        max_bytes: int = 256_000,
    ) -> dict:
        """Read one text source file at an exact commit, never a moving branch."""
        owner = owner.strip()
        repository = repository.strip()
        path = path.strip().replace("\\", "/")
        ref = ref.strip()

        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", owner) or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,100}", repository
        ):
            raise ValueError("Repository owner and name must be valid GitHub path segments.")
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", ref):
            raise ValueError("A full build commit SHA is required for source inspection.")
        path_parts = path.split("/")
        if (
            not path
            or path.startswith("/")
            or any(part in {"", ".", ".."} for part in path_parts)
            or any(part.startswith(".") for part in path_parts)
        ):
            raise ValueError("Use a repository-relative, non-hidden file path.")
        if not path.lower().endswith((
            ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".go",
            ".rs", ".rb", ".php", ".cs", ".cpp", ".c", ".h", ".toml",
            ".yaml", ".yml", ".json",
        )):
            raise ValueError("Only source, test, and build-configuration files can be inspected.")
        lowered_path = path.lower()
        if any(
            part in lowered_path
            for part in (
                ".env", "secret", "credential", "password", "private_key",
                "api_key", "apikey", "access_token", "token",
            )
        ):
            raise ValueError("Sensitive-looking file paths cannot be sent to the remediation model.")

        encoded_path = quote(path, safe="/")
        data = self._get(
            f"/repos/{owner}/{repository}/contents/{encoded_path}",
            {"ref": ref},
        )
        if not isinstance(data, dict) or data.get("type") != "file":
            raise GitHubConnectionError("GitHub did not return a regular file for that path.")

        size = data.get("size")
        if isinstance(size, int) and size > max_bytes:
            raise GitHubConnectionError(
                f"Refusing to send {path}: file exceeds the {max_bytes}-byte inspection limit."
            )
        if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
            raise GitHubConnectionError("GitHub did not return an inspectable text file.")

        try:
            content = base64.b64decode(data["content"], validate=False).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise GitHubConnectionError("The selected repository file is not UTF-8 text.") from exc

        if len(content.encode("utf-8")) > max_bytes:
            raise GitHubConnectionError(
                f"Refusing to send {path}: decoded file exceeds the {max_bytes}-byte inspection limit."
            )
        return {
            "path": path,
            "ref": ref,
            "blob_sha": data.get("sha"),
            "content": content,
        }

    def get_commits(
        self,
        owner: str,
        repository: str,
        per_page: int = 10,
    ) -> list[dict]:
        owner = owner.strip()
        repository = repository.strip()

        if not owner or not repository:
            raise ValueError("Repository owner and name are required.")

        data = self._get(
            f"/repos/{owner}/{repository}/commits",
            {"per_page": min(max(per_page, 1), 100)},
        )

        if not isinstance(data, list):
            raise GitHubConnectionError(
                "GitHub returned an unexpected commit list."
            )

        commits = []

        for item in data:
            if not isinstance(item, dict):
                continue

            commit = item.get("commit") or {}
            author = commit.get("author") or {}

            commits.append(
                {
                    "sha": item.get("sha"),
                    "message": commit.get("message"),
                    "author": author.get("name"),
                    "date": author.get("date"),
                    "html_url": item.get("html_url"),
                }
            )

        return commits
