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
